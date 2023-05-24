"""Availability group replica health."""
from collections import OrderedDict

from ..models import Finding

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


def run(ctx):
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
