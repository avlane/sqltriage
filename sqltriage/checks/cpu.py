"""CPU scheduler pressure.

Two views of the same thing.  Each scheduler has a runnable queue: tasks that
have everything they need except CPU.  And the share of wait time that is
*signal* wait (time spent runnable after the resource wait ended) says how long
tasks queue for a CPU in general.
"""
from ..models import Finding
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

# (average runnable tasks per scheduler at or above, score)
RUNNABLE_LEVELS = [(5, 85), (3, 70), (2, 55), (1, 40)]
# (signal wait percentage at or above, score)
SIGNAL_LEVELS = [(25, 75), (15, 50)]


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

    runnable_score = step_score(average, RUNNABLE_LEVELS)
    signal_score = step_score(signal, SIGNAL_LEVELS)
    score = max(runnable_score, signal_score)
    if runnable_score and signal_score:
        score += 5
    if queued:
        score += 5
    score = clamp(score, high=95)

    evidence = [
        "{} online schedulers, runnable queue average {:.1f}, maximum {}".format(len(online), average, max(runnable)),
        "signal waits are {:.1f}% of non-idle wait time (15% or more is a concern)".format(signal),
    ]
    if queued:
        evidence.append("{} scheduler(s) have tasks queued for a worker (work_queue_count > 0)".format(queued))
    if offline:
        evidence.append(
            "{} scheduler(s) are VISIBLE OFFLINE: check edition core limits and affinity".format(len(offline))
        )
    extra = imbalance_finding(online)
    extra = [extra] if extra else []
    if not score:
        return [Finding(NAME, "CPU scheduler pressure: none seen", 0, evidence=evidence)] + extra
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
