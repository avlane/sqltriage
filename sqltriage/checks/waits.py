"""Wait statistics delta over a sample interval.

sys.dm_os_wait_stats is cumulative since the instance started (or since the
counters were last cleared), so a single read says little about *now*.  We read
it twice, ``ctx.interval`` seconds apart, and look at the difference.
"""
from collections import namedtuple

from ..models import Finding

NAME = "waits"

SNAPSHOT_SQL = """\
SELECT wait_type,
       waiting_tasks_count,
       wait_time_ms,
       max_wait_time_ms,
       signal_wait_time_ms
FROM sys.dm_os_wait_stats
WHERE waiting_tasks_count > 0;"""

WaitDelta = namedtuple("WaitDelta", "wait_type tasks wait_ms signal_ms max_wait_ms")


def snapshot(ctx):
    rows = ctx.source.fetch("waits_snapshot", SNAPSHOT_SQL)
    return {row["wait_type"]: row for row in rows}


def compute_delta(before, after):
    """Waits that accrued between two snapshots, biggest total wait first."""
    deltas = []
    for wait_type, now in after.items():
        then = before.get(wait_type)
        if then is None:
            tasks, wait, signal, max_wait = (
                now["waiting_tasks_count"],
                now["wait_time_ms"],
                now["signal_wait_time_ms"],
                now["max_wait_time_ms"],
            )
        else:
            tasks = now["waiting_tasks_count"] - then["waiting_tasks_count"]
            wait = now["wait_time_ms"] - then["wait_time_ms"]
            signal = now["signal_wait_time_ms"] - then["signal_wait_time_ms"]
            # max_wait_time_ms is a high-water mark since startup; it only
            # says something about this interval if it moved.
            max_wait = now["max_wait_time_ms"] if now["max_wait_time_ms"] > then["max_wait_time_ms"] else None
        if tasks <= 0 or wait <= 0:
            continue
        deltas.append(WaitDelta(wait_type, tasks, wait, signal, max_wait))
    deltas.sort(key=lambda d: d.wait_ms, reverse=True)
    return deltas


def sample(ctx):
    """Take the two snapshots once per run and share the result."""
    if "waits" not in ctx.cache:
        before = snapshot(ctx)
        ctx.sleep(ctx.interval)
        after = snapshot(ctx)
        ctx.cache["waits"] = compute_delta(before, after)
    return ctx.cache["waits"]


def run(ctx):
    deltas = sample(ctx)
    lines = []
    for d in deltas[: ctx.top]:
        lines.append("{:<32} {:>8} ms over {:>6} waits".format(d.wait_type, d.wait_ms, d.tasks))
    return [
        Finding(
            check=NAME,
            title="Top waits over a {:g}s sample".format(ctx.interval),
            score=20,
            evidence=lines,
        )
    ]
