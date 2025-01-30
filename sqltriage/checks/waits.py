"""Wait statistics delta over a sample interval.

sys.dm_os_wait_stats is cumulative since the instance started (or since the
counters were last cleared), so a single read says little about *now*.  We read
it twice, ``ctx.interval`` seconds apart, and look at the difference.
"""
from collections import namedtuple

from .. import waitinfo
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


def was_reset(before, after):
    """True if the counters went backwards between two snapshots.

    That happens when the instance restarts or someone runs
    DBCC SQLPERF('sys.dm_os_wait_stats', CLEAR).  Wait types vanish from the
    second snapshot or their totals shrink; a delta of those is meaningless.
    """
    if set(before) - set(after):
        return True
    for wait_type, now in after.items():
        then = before.get(wait_type)
        if then and (
            now["waiting_tasks_count"] < then["waiting_tasks_count"] or now["wait_time_ms"] < then["wait_time_ms"]
        ):
            return True
    return False


def sample(ctx):
    """Take the snapshots once per run and share the result.

    With ``ctx.samples`` above one, the interval is repeated that many times.
    The delta covers the whole span and ``ctx.cache["waits_intervals"]`` holds
    each interval's wait time per wait type, so a trend can be shown.  If the
    counters were reset at any point the delta is empty and
    ``ctx.cache["waits_reset"]`` is set so ``run`` can say why.
    """
    if "waits" not in ctx.cache:
        snapshots = [snapshot(ctx)]
        started = ctx.clock()
        for _ in range(max(1, ctx.samples)):
            ctx.sleep(ctx.interval)
            snapshots.append(snapshot(ctx))
        # The queries take time too, so rates use what really passed.
        ctx.cache["waits_elapsed"] = max(ctx.clock() - started, 0.001)
        reset = any(was_reset(a, b) for a, b in zip(snapshots, snapshots[1:]))
        ctx.cache["waits_reset"] = reset
        ctx.cache["waits"] = [] if reset else compute_delta(snapshots[0], snapshots[-1])
        ctx.cache["waits_intervals"] = (
            []
            if reset
            else [{d.wait_type: d.wait_ms for d in compute_delta(a, b)} for a, b in zip(snapshots, snapshots[1:])]
        )
    return ctx.cache["waits"]


RISING = 1.5
FALLING = 1 / 1.5


def trend(values):
    """'rising', 'falling' or 'steady' from per-interval wait times."""
    if len(values) < 2 or not values[0]:
        return "steady" if len(values) < 2 or values[-1] == values[0] else "rising"
    ratio = values[-1] / float(values[0])
    if ratio >= RISING:
        return "rising"
    if ratio <= FALLING:
        return "falling"
    return "steady"


def significant(deltas):
    """Drop the idle background waits."""
    return [d for d in deltas if not waitinfo.is_benign(d.wait_type)]


def score_wait(delta, share, interval_ms):
    """0-95: what the wait is, how much of the wait time it is, how many sessions it holds up."""
    info = waitinfo.explain(delta.wait_type)
    concurrency = delta.wait_ms / interval_ms  # average number of tasks waiting
    score = info.weight + min(35.0, share * 0.5) + min(15.0, concurrency * 3)
    return int(min(95, round(score)))


def run(ctx):
    meaningful = significant(sample(ctx))
    if ctx.cache.get("waits_reset"):
        return [
            Finding(
                NAME,
                "Wait statistics were reset during the sample",
                10,
                detail="The wait counters went backwards, so the instance restarted or they were cleared.",
                advice=["Run sqltriage again; if the instance restarted, check the error log for why."],
            )
        ]
    if not meaningful:
        return [Finding(NAME, "No significant waits in the sample", 0)]
    total = sum(d.wait_ms for d in meaningful)
    interval_ms = ctx.cache.get("waits_elapsed", max(ctx.interval, 0.001)) * 1000.0
    findings = []
    for d in meaningful[: ctx.top]:
        share = 100.0 * d.wait_ms / total
        info = waitinfo.explain(d.wait_type)
        avg = d.wait_ms / float(d.tasks)
        evidence = [
            "{:.0f}% of non-idle wait time ({} ms over {} waits, {:.1f} ms average)".format(
                share, d.wait_ms, d.tasks, avg
            ),
            "{:.1f} tasks waiting on average during the sample".format(d.wait_ms / interval_ms),
        ]
        intervals = ctx.cache.get("waits_intervals") or []
        direction = None
        if len(intervals) > 1:
            series = [i.get(d.wait_type, 0) for i in intervals]
            direction = trend(series)
            evidence.append("per interval: {} ms ({})".format(", ".join(str(v) for v in series), direction))
        if d.wait_ms and d.signal_ms:
            evidence.append("signal wait {:.0f}% of the wait time".format(100.0 * d.signal_ms / d.wait_ms))
        findings.append(
            Finding(
                check=NAME,
                title="Wait: {}".format(d.wait_type),
                score=min(95, score_wait(d, share, interval_ms) + (5 if direction == "rising" else 0)),
                detail=info.text,
                evidence=evidence,
                advice=list(info.advice),
                data={
                    "wait_type": d.wait_type,
                    "category": info.category,
                    "wait_ms": d.wait_ms,
                    "tasks": d.tasks,
                    "signal_ms": d.signal_ms,
                    "share_pct": round(share, 1),
                },
            )
        )
    return findings
