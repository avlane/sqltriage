"""Availability group health: replicas, and the send and redo queues of each database."""
from collections import OrderedDict

from ..fmt import span, to_datetime
from ..models import Finding
from ..scoring import step_score

NAME = "ag"

# On the primary this has a row, with live state, for every replica.  On a
# secondary the other replicas' columns are mostly NULL, which we skip over.
REPLICA_SQL = """\
SELECT ag.name AS ag_name,
       ar.replica_server_name,
       ars.role_desc,
       ars.operational_state_desc,
       ars.connected_state_desc,
       ars.recovery_health_desc,
       ars.synchronization_health_desc,
       ar.availability_mode_desc,
       ar.failover_mode_desc,
       ars.last_connect_error_number,
       ars.last_connect_error_description,
       ars.last_connect_error_timestamp
FROM sys.availability_groups AS ag
JOIN sys.availability_replicas AS ar
  ON ar.group_id = ag.group_id
JOIN sys.dm_hadr_availability_replica_states AS ars
  ON ars.replica_id = ar.replica_id
 AND ars.group_id = ar.group_id
ORDER BY ag.name, ars.role_desc, ar.replica_server_name;"""

# Per database and secondary replica.  Queue sizes are in KB, rates in KB/s.
DATABASE_SQL = """\
SELECT ag.name AS ag_name,
       ar.replica_server_name,
       DB_NAME(drs.database_id) AS database_name,
       drs.synchronization_state_desc,
       drs.synchronization_health_desc,
       drs.is_suspended,
       drs.suspend_reason_desc,
       drs.log_send_queue_size,
       drs.log_send_rate,
       drs.redo_queue_size,
       drs.redo_rate,
       drs.last_commit_time,
       (SELECT MAX(p.last_commit_time)
        FROM sys.dm_hadr_database_replica_states AS p
        WHERE p.group_id = drs.group_id
          AND p.group_database_id = drs.group_database_id
          AND p.is_primary_replica = 1) AS primary_last_commit_time,
       drs.secondary_lag_seconds
FROM sys.dm_hadr_database_replica_states AS drs
JOIN sys.availability_replicas AS ar
  ON ar.replica_id = drs.replica_id
 AND ar.group_id = drs.group_id
JOIN sys.availability_groups AS ag
  ON ag.group_id = drs.group_id
WHERE drs.is_primary_replica = 0
ORDER BY ag.name, ar.replica_server_name, database_name;"""

# (queue KB at or above, score)
QUEUE_LEVELS = [(1048576, 70), (102400, 50), (10240, 25)]
# (lag seconds at or above, score)
LAG_LEVELS = [(600, 70), (60, 45)]


def replica_problems(row):
    """(score, text) for each thing wrong with one replica."""
    problems = []
    sync_commit = row.get("availability_mode_desc") == "SYNCHRONOUS_COMMIT"
    connected = row.get("connected_state_desc")
    if connected and connected != "CONNECTED":
        # a disconnected synchronous replica can stall commits on the primary
        problems.append((90 if sync_commit else 70, "replica is {}".format(connected)))
    operational = row.get("operational_state_desc")
    if operational and operational != "ONLINE":
        problems.append((80, "operational state is {}".format(operational)))
    sync = row.get("synchronization_health_desc")
    if sync == "NOT_HEALTHY":
        problems.append((75 if sync_commit else 60, "synchronization health is NOT_HEALTHY"))
    elif sync == "PARTIALLY_HEALTHY":
        problems.append((55, "synchronization health is PARTIALLY_HEALTHY"))
    recovery = row.get("recovery_health_desc")
    if recovery and recovery not in ("ONLINE",):
        problems.append((50, "recovery health is {}".format(recovery)))
    return problems


def _size(kb):
    if kb >= 1048576:
        return "{:.1f} GB".format(kb / 1048576.0)
    if kb >= 1024:
        return "{:.0f} MB".format(kb / 1024.0)
    return "{} KB".format(kb)


def _catch_up(queue_kb, rate_kb_s):
    if not queue_kb:
        return None
    if not rate_kb_s:
        return "no movement, so no estimate"
    seconds = queue_kb / float(rate_kb_s)
    return "about {:.0f} min at the current rate".format(seconds / 60.0) if seconds >= 90 else "under 2 min at the current rate"


def database_problems(row):
    problems = []
    if row.get("is_suspended"):
        problems.append((75, "data movement is suspended ({})".format(row.get("suspend_reason_desc"))))
    send = row.get("log_send_queue_size") or 0
    score = step_score(send, QUEUE_LEVELS)
    if score:
        text = "log send queue {}".format(_size(send))
        eta = _catch_up(send, row.get("log_send_rate"))
        problems.append((score, text + (", " + eta if eta else "")))
    redo = row.get("redo_queue_size") or 0
    score = step_score(redo, QUEUE_LEVELS)
    if score:
        text = "redo queue {}".format(_size(redo))
        eta = _catch_up(redo, row.get("redo_rate"))
        problems.append((score - 5, text + (", " + eta if eta else "")))
    score = step_score(row.get("secondary_lag_seconds"), LAG_LEVELS)
    if score:
        problems.append((score, "secondary is {} s behind".format(row["secondary_lag_seconds"])))
    return problems


def estimated_data_loss(row):
    """Seconds of committed work missing on the secondary, or None if unknown.

    The gap between the last commit the primary has and the last one this
    secondary has hardened is what a forced failover to it would lose.
    """
    primary = to_datetime(row.get("primary_last_commit_time"))
    secondary = to_datetime(row.get("last_commit_time"))
    if primary is None or secondary is None:
        return None
    return max(0, int((primary - secondary).total_seconds()))


def estimated_recovery(row):
    """Seconds the secondary needs to redo what it already has, or None if it is not redoing."""
    queue, rate = row.get("redo_queue_size") or 0, row.get("redo_rate") or 0
    if not queue:
        return 0
    return int(queue / float(rate)) if rate else None


def database_findings(ctx):
    findings = []
    for row in ctx.source.fetch("ag_databases", DATABASE_SQL):
        if not ctx.wants_database(row["database_name"]):
            continue
        problems = database_problems(row)
        if not problems:
            continue
        evidence = [text for _, text in problems]
        loss = estimated_data_loss(row)
        if loss:
            evidence.append("a failover to this replica now would lose about {}".format(span(loss) if loss >= 60 else "{} s".format(loss)))
        recovery = estimated_recovery(row)
        if recovery:
            evidence.append("it would need about {} to finish redo before the database is available".format(span(recovery)))
        elif recovery is None:
            evidence.append("redo is not moving, so there is no recovery time estimate")
        evidence.append("synchronization state {} ({})".format(
            row["synchronization_state_desc"], row["synchronization_health_desc"]))
        findings.append(
            Finding(
                check=NAME,
                title="Availability group {}: {} on {} is behind".format(
                    row["ag_name"], row["database_name"], row["replica_server_name"]
                ),
                score=max(s for s, _ in problems),
                evidence=evidence,
                advice=[
                    "Send queue growing: look at the network and the primary's log write rate.",
                    "Redo queue growing: look at I/O and CPU on the secondary, and long running queries on a readable secondary.",
                ],
                data={
                    "ag": row["ag_name"],
                    "replica": row["replica_server_name"],
                    "database": row["database_name"],
                    "log_send_queue_kb": row.get("log_send_queue_size"),
                    "redo_queue_kb": row.get("redo_queue_size"),
                    "estimated_data_loss_s": loss,
                    "estimated_recovery_s": recovery,
                },
            )
        )
    return findings


def replica_findings(ctx):
    rows = ctx.source.fetch("ag_replicas", REPLICA_SQL)
    groups = OrderedDict()
    for row in rows:
        groups.setdefault(row["ag_name"], []).append(row)
    findings = []
    for ag_name, replicas in groups.items():
        for row in replicas:
            problems = replica_problems(row)
            if not problems:
                continue
            score = max(s for s, _ in problems)
            evidence = ["{}, {} ({} failover)".format(
                row["role_desc"], row["availability_mode_desc"], row["failover_mode_desc"].lower())]
            evidence.extend(text for _, text in problems)
            if row.get("last_connect_error_number"):
                evidence.append(
                    "last connect error {} at {}: {}".format(
                        row["last_connect_error_number"],
                        row.get("last_connect_error_timestamp"),
                        row.get("last_connect_error_description"),
                    )
                )
            findings.append(
                Finding(
                    check=NAME,
                    title="Availability group {}: replica {} is unhealthy".format(ag_name, row["replica_server_name"]),
                    score=score,
                    evidence=evidence,
                    advice=[
                        "Check the network path and the endpoint (port 5022 by default) between the replicas.",
                        "Look at the replica's error log around the time it disconnected.",
                    ],
                    data={"ag": ag_name, "replica": row["replica_server_name"]},
                )
            )
    return findings


def run(ctx):
    return replica_findings(ctx) + database_findings(ctx)
