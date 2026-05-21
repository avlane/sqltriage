"""Performance counters that tell something about what the workload did during the sample.

These are cumulative "per second" counters: the value only ever goes up, and
the rate is the change over the time between two readings.
"""
from .. import sampling
from ..models import Finding
from ..source import SourceError

NAME = "counters"

SQL = """\
SELECT RTRIM(counter_name) AS counter_name,
       RTRIM(instance_name) AS instance_name,
       cntr_value
FROM sys.dm_os_performance_counters
WHERE (counter_name IN ('Number of Deadlocks/sec', 'Lock Timeouts/sec', 'Lock Waits/sec')
       AND instance_name = '_Total')
   OR counter_name IN ('Batch Requests/sec', 'SQL Compilations/sec', 'SQL Re-Compilations/sec');"""

DEADLOCKS = "Number of Deadlocks/sec"
LOCK_TIMEOUTS = "Lock Timeouts/sec"
LOCK_WAITS = "Lock Waits/sec"
BATCHES = "Batch Requests/sec"
COMPILES = "SQL Compilations/sec"
RECOMPILES = "SQL Re-Compilations/sec"

MIN_BATCHES_PER_SECOND = 5     # below this the ratios mean little
COMPILE_RATIO_LEVEL = 0.2      # compilations per batch


@sampling.probe("counters")
def snapshot(ctx):
    return {row["counter_name"]: row["cntr_value"] for row in ctx.source.fetch("counters_snapshot", SQL)}


def deltas(before, after):
    """{counter: increase}.  Counters missing from either reading are left out."""
    return {name: after[name] - before[name] for name in after if name in before}


def reset_between(before, after):
    return any(after[name] < before[name] for name in after if name in before)


def run(ctx):
    taken = sampling.take(ctx)
    readings = taken.snapshots("counters")
    first, last = readings[0], readings[-1]
    if reset_between(first, last):
        return []  # the instance restarted during the sample; the waits check says so too
    change = deltas(first, last)
    elapsed = taken.elapsed
    findings = []

    deadlocks = change.get(DEADLOCKS, 0)
    if deadlocks > 0:
        findings.append(
            Finding(
                check=NAME,
                title="{} deadlock(s) during the {:.0f}s sample".format(deadlocks, elapsed),
                score=min(90, 50 + deadlocks * 10),
                detail="Deadlock victims are rolled back with error 1205; applications that do not retry lose that work.",
                evidence=["Number of Deadlocks/sec (_Total) went up by {}".format(deadlocks)],
                advice=[
                    "Read the xml_deadlock_report events in the system_health session for the graphs.",
                    "Look at access order, missing indexes on the keys involved, and long transactions.",
                ],
                data={"deadlocks": deadlocks},
            )
        )

    timeouts = change.get(LOCK_TIMEOUTS, 0)
    if timeouts > 0:
        findings.append(
            Finding(
                check=NAME,
                title="{} lock timeout(s) during the sample".format(timeouts),
                score=min(70, 30 + timeouts * 3),
                detail="Sessions that set LOCK_TIMEOUT gave up waiting for a lock (error 1222).",
                evidence=["Lock Timeouts/sec (_Total) went up by {}".format(timeouts)],
                advice=["Find the blocker (see the blocking section); the applications involved may not be retrying."],
                data={"lock_timeouts": timeouts},
            )
        )

    batches_per_second = change.get(BATCHES, 0) / elapsed
    compiles = change.get(COMPILES, 0)
    recompiles = change.get(RECOMPILES, 0)
    batches = change.get(BATCHES, 0)
    if batches_per_second >= MIN_BATCHES_PER_SECOND and batches and compiles / float(batches) >= COMPILE_RATIO_LEVEL:
        ratio = compiles / float(batches)
        findings.append(
            Finding(
                check=NAME,
                title="{:.0f}% of batches compiled a plan".format(100 * ratio),
                score=int(min(60, 30 + ratio * 40)),
                detail="Compiling is CPU heavy and takes plan cache memory: plans are not being reused.",
                evidence=[
                    "{:.0f} compilations/s ({:.0f} of them recompilations) against {:.0f} batch requests/s".format(
                        compiles / elapsed, recompiles / elapsed, batches_per_second
                    )
                ],
                advice=[
                    "Look for ad hoc statements with literals (forced parameterization, or optimize for ad hoc workloads).",
                    "A high recompilation rate points at temp tables, statistics updates or SET options changing.",
                ],
                data={"compile_ratio": round(ratio, 3), "batches_per_second": round(batches_per_second, 1)},
            )
        )

    if batches_per_second:
        findings.append(
            Finding(
                check=NAME,
                title="Workload during the sample: {:.0f} batch requests/s".format(batches_per_second),
                score=5,
                evidence=[
                    "{:.0f} compilations/s, {:.0f} recompilations/s, {:.1f} lock waits/s".format(
                        compiles / elapsed, recompiles / elapsed, change.get(LOCK_WAITS, 0) / elapsed
                    )
                ],
                data={"batches_per_second": round(batches_per_second, 1)},
            )
        )
    return findings
