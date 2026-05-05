"""One shared sampling window for everything that needs a before and an after.

Wait stats and performance counters are cumulative, so each needs two readings
a while apart.  Rather than every check sleeping for its own interval, checks
register a *probe* here: a function that returns a snapshot of one thing.
``take`` reads every probe, sleeps, reads them all again (``ctx.samples``
times over) and hands back the series, so the whole run waits only once.
"""
from collections import namedtuple

PROBES = {}

Samples = namedtuple("Samples", "series elapsed")


def probe(name):
    """Register ``fn(ctx) -> snapshot`` as the probe called ``name``."""

    def register(fn):
        PROBES[name] = fn
        return fn

    return register


def take(ctx):
    """The samples for this run, taken on the first call and shared after.

    ``series[name]`` is the list of that probe's snapshots, ``ctx.samples + 1``
    of them.  ``elapsed`` is the time that really passed between the first
    readings and the last, which is longer than the interval when the queries
    are slow.
    """
    if "samples" not in ctx.cache:
        names = list(PROBES)
        series = {name: [PROBES[name](ctx)] for name in names}
        started = ctx.clock()
        for _ in range(max(1, ctx.samples)):
            ctx.sleep(ctx.interval)
            for name in names:
                series[name].append(PROBES[name](ctx))
        ctx.cache["samples"] = Samples(series, max(ctx.clock() - started, 0.001))
    return ctx.cache["samples"]
