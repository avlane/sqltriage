"""CPU scheduler pressure.

Two views of the same thing.  Each scheduler has a runnable queue: tasks that
have everything they need except CPU.  And the share of wait time that is
*signal* wait (time spent runnable after the resource wait ended) says how long
tasks queue for a CPU in general.
"""
from ..fmt import clean_text, span
from ..models import Finding
from ..source import SourceError
from ..scoring import clamp, step_score
from . import waits
from .. import waitinfo

NAME = "cpu"

SCHEDULER_SQL = """\
SELECT scheduler_id,
       cpu_id,
       status,
       is_online,
       current_tasks_count,
       runnable_tasks_count,
       current_workers_count,
       active_workers_count,
       work_queue_count,
       pending_disk_io_count
FROM sys.dm_os_schedulers
WHERE scheduler_id < 255;"""

# The requests that have used the most CPU so far.  statement_text is the part
# of the batch that is actually executing, cut out with the statement offsets.
TOP_REQUESTS_SQL = """\
SELECT TOP (5)
       r.session_id,
       r.cpu_time AS cpu_ms,
       r.total_elapsed_time AS elapsed_ms,
       r.logical_reads,
       r.dop,
       r.status,
       r.wait_type,
       DB_NAME(r.database_id) AS database_name,
       s.login_name,
       s.program_name,
       SUBSTRING(st.text,
                 (r.statement_start_offset / 2) + 1,
                 ((CASE r.statement_end_offset
                        WHEN -1 THEN DATALENGTH(st.text)
                        ELSE r.statement_end_offset
                   END - r.statement_start_offset) / 2) + 1) AS statement_text
FROM sys.dm_exec_requests AS r
JOIN sys.dm_exec_sessions AS s
  ON s.session_id = r.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS st
WHERE s.is_user_process = 1
  AND r.session_id <> @@SPID
ORDER BY r.cpu_time DESC;"""

STATEMENT_LIMIT = 160

# The scheduler monitor writes one record a minute with the CPU use of the SQL
# Server process and how idle the whole machine was.  The ring buffer's
# timestamp is in milliseconds of uptime, hence the arithmetic against ms_ticks.
HISTORY_SQL = """\
SELECT TOP (10)
       DATEADD(MILLISECOND, x.timestamp - si.ms_ticks, SYSDATETIME()) AS event_time,
       x.record.value('(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]', 'int') AS sql_cpu_pct,
       x.record.value('(./Record/SchedulerMonitorEvent/SystemHealth/SystemIdle)[1]', 'int') AS system_idle_pct
FROM (SELECT timestamp, CONVERT(xml, record) AS record
      FROM sys.dm_os_ring_buffers
      WHERE ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR'
        AND record LIKE N'%<SystemHealth>%') AS x
CROSS JOIN sys.dm_os_sys_info AS si
ORDER BY x.timestamp DESC;"""

SQL_BUSY_PCT = 80      # SQL Server using this much of the machine's CPU on average
OTHER_BUSY_PCT = 25    # everything else using this much

# (average runnable tasks per scheduler at or above, score)
RUNNABLE_LEVELS = [(5, 85), (3, 70), (2, 55), (1, 40)]
# (signal wait percentage at or above, score)
SIGNAL_LEVELS = [(25, 75), (15, 50)]
# Signal waits are a share of whatever waiting there was, so on an idle server a
# few milliseconds can make the share look alarming.  They only count when tasks
# really are queueing for a CPU.
SIGNAL_MIN_RUNNABLE = 1.0


# One scheduler piling up work while the rest idle points at affinity settings,
# a MAXDOP 1 workload pinned by a connection, or a single hot session.
IMBALANCE_MIN_RUNNABLE = 4
IMBALANCE_FACTOR = 3.0


def imbalance_finding(online):
    runnable = sorted(r["runnable_tasks_count"] for r in online)
    if len(runnable) < 4:
        return None
    hottest = online[0]
    for row in online:
        if row["runnable_tasks_count"] > hottest["runnable_tasks_count"]:
            hottest = row
    peak = hottest["runnable_tasks_count"]
    median = runnable[len(runnable) // 2]
    if peak < IMBALANCE_MIN_RUNNABLE or peak < IMBALANCE_FACTOR * max(median, 1):
        return None
    return Finding(
        check=NAME,
        title="CPU imbalance: scheduler {} has {} runnable tasks, the median is {}".format(
            hottest["scheduler_id"], peak, median
        ),
        score=45 + min(25, peak),
        detail="One scheduler is overloaded while most of the others are not.",
        evidence=["scheduler {} (cpu {}) has {} runnable and {} current tasks".format(
            hottest["scheduler_id"], hottest.get("cpu_id"), peak, hottest.get("current_tasks_count"))],
        advice=[
            "Check the CPU affinity mask and any Resource Governor pool affinity.",
            "Look for a single connection running many serial queries that all land on one scheduler.",
        ],
        data={"scheduler_id": hottest["scheduler_id"], "runnable": peak, "median": median},
    )


def history(ctx):
    """(sql %, other %, idle %) averaged over the minutes the ring buffer holds, or None."""
    try:
        rows = ctx.source.fetch("cpu_history", HISTORY_SQL)
    except SourceError:
        return None
    rows = [r for r in rows if r.get("sql_cpu_pct") is not None and r.get("system_idle_pct") is not None]
    if not rows:
        return None
    sql = sum(r["sql_cpu_pct"] for r in rows) / float(len(rows))
    idle = sum(r["system_idle_pct"] for r in rows) / float(len(rows))
    return sql, max(0.0, 100.0 - sql - idle), idle, len(rows)


def history_findings(usage):
    """Findings for SQL Server using most of the machine, or something else doing so."""
    if usage is None:
        return []
    sql, other, idle, minutes = usage
    findings = []
    if sql >= SQL_BUSY_PCT:
        findings.append(
            Finding(
                check=NAME,
                title="SQL Server is using {:.0f}% of the CPU".format(sql),
                score=int(min(85, 50 + (sql - SQL_BUSY_PCT) * 2)),
                evidence=["averaged over the last {} minutes: SQL Server {:.0f}%, other processes {:.0f}%, idle {:.0f}%".format(minutes, sql, other, idle)],
                advice=["Find the top CPU consumers (sys.dm_exec_query_stats or Query Store); see the heaviest requests above."],
                data={"sql_cpu_pct": round(sql, 1), "other_cpu_pct": round(other, 1)},
            )
        )
    if other >= OTHER_BUSY_PCT:
        findings.append(
            Finding(
                check=NAME,
                title="Something other than SQL Server is using {:.0f}% of the CPU".format(other),
                score=int(min(80, 45 + other / 2.0)),
                detail="SQL Server is being starved of CPU by another process on the host.",
                evidence=["averaged over the last {} minutes: SQL Server {:.0f}%, other processes {:.0f}%, idle {:.0f}%".format(minutes, sql, other, idle)],
                advice=["Find what else runs on the host: antivirus scans, backups agents, another SQL Server instance, monitoring."],
                data={"sql_cpu_pct": round(sql, 1), "other_cpu_pct": round(other, 1)},
            )
        )
    return findings


def top_requests(ctx):
    """One evidence line per heavy request; nothing if the DMV cannot be read."""
    try:
        rows = ctx.source.fetch("cpu_top_requests", TOP_REQUESTS_SQL)
    except SourceError:
        return []
    lines = []
    for row in rows[:3]:
        text = clean_text(row.get("statement_text"), STATEMENT_LIMIT)
        lines.append(
            "spid {} {}@{} used {} of CPU in {} (dop {}, {}): {}".format(
                row["session_id"],
                row["login_name"],
                row["database_name"],
                span(row["cpu_ms"] / 1000.0),
                span(row["elapsed_ms"] / 1000.0),
                row["dop"],
                row["status"],
                text,
            )
        )
    return lines


def signal_wait_pct(deltas):
    total = sum(d.wait_ms for d in deltas if not waitinfo.is_benign(d.wait_type))
    signal = sum(d.signal_ms for d in deltas if not waitinfo.is_benign(d.wait_type))
    return 100.0 * signal / total if total else 0.0


def run(ctx):
    rows = ctx.source.fetch("cpu_schedulers", SCHEDULER_SQL)
    online = [r for r in rows if r["status"] == "VISIBLE ONLINE"]
    offline = [r for r in rows if r["status"] == "VISIBLE OFFLINE"]
    if not online:
        return []
    runnable = [r["runnable_tasks_count"] for r in online]
    average = sum(runnable) / float(len(runnable))
    queued = sum(1 for r in online if r["work_queue_count"] > 0)
    signal = signal_wait_pct(waits.sample(ctx))

    per_interval = [signal_wait_pct(deltas) for deltas in ctx.cache.get("waits_deltas", [])]
    direction = waits.trend(per_interval) if len(per_interval) > 1 else None

    runnable_score = step_score(average, RUNNABLE_LEVELS)
    signal_score = step_score(signal, SIGNAL_LEVELS) if average >= SIGNAL_MIN_RUNNABLE else 0
    score = max(runnable_score, signal_score)
    if runnable_score and signal_score:
        score += 5
    if queued:
        score += 5
    if direction == "rising" and per_interval[-1] >= SIGNAL_LEVELS[-1][0]:
        score += 5  # signal waits are climbing through the sample and already high
    score = clamp(score, high=95)

    evidence = [
        "{} online schedulers, runnable queue average {:.1f}, maximum {}".format(len(online), average, max(runnable)),
        "signal waits are {:.1f}% of non-idle wait time (15% or more is a concern{})".format(
            signal, "" if average >= SIGNAL_MIN_RUNNABLE else ", but the runnable queue is short so it does not count"
        ),
    ]
    if direction:
        evidence.append(
            "signal wait share per interval: {} ({})".format(", ".join("{:.0f}%".format(p) for p in per_interval), direction)
        )
    if queued:
        evidence.append("{} scheduler(s) have tasks queued for a worker (work_queue_count > 0)".format(queued))
    if offline:
        evidence.append(
            "{} scheduler(s) are VISIBLE OFFLINE: check edition core limits and affinity".format(len(offline))
        )
    usage = history(ctx)
    extra = history_findings(usage)
    imbalance = imbalance_finding(online)
    if imbalance:
        extra.append(imbalance)
    if usage and score:
        evidence.append("last {} minutes: SQL Server {:.0f}%, other processes {:.0f}%, idle {:.0f}%".format(usage[3], usage[0], usage[1], usage[2]))
    if not score:
        return [Finding(NAME, "CPU scheduler pressure: none seen", 0, evidence=evidence)] + extra
    evidence.extend(top_requests(ctx))
    return extra + [
        Finding(
            check=NAME,
            title="CPU scheduler pressure: runnable queue averages {:.1f} per scheduler".format(average),
            score=score,
            detail="Tasks are waiting for CPU time.",
            evidence=evidence,
            advice=[
                "Find the top CPU consumers (sys.dm_exec_query_stats or Query Store).",
                "Check that the host is not CPU-starved by something outside SQL Server.",
            ],
            data={
                "online_schedulers": len(online),
                "avg_runnable": round(average, 2),
                "signal_wait_pct": round(signal, 1),
            },
        )
    ]
