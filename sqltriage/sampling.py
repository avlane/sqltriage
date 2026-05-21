"""One shared sampling window for everything that needs a before and an after.

Wait stats and performance counters are cumulative, so each needs two readings
a while apart.  Rather than every check sleeping for its own interval, checks
register a *probe* here: a function that returns a snapshot of one thing.
``take`` reads every probe, sleeps, reads them all again (``ctx.samples``
times over) and hands back the series, so the whole run waits only once.
"""
from collections import namedtuple

from .source import SourceError

PROBES = {}


class Samples(namedtuple("Samples", "series elapsed")):
    """The readings of every probe, plus the time that passed between first and last."""

    def snapshots(self, name):
        """The snapshots for one probe; raises the SourceError if that probe's query failed.

        A probe that cannot be read must not stop the others, so the error is
        kept with the series and only raised by whoever asks for that probe.
        """
        values = self.series[name]
        for value in values:
            if isinstance(value, SourceError):
                raise value
        return values


def probe(name):
    """Register ``fn(ctx) -> snapshot`` as the probe called ``name``."""

    def register(fn):
        PROBES[name] = fn
        return fn

    return register


def _read(name, ctx):
    try:
        return PROBES[name](ctx)
    except SourceError as exc:
        return exc


def take(ctx):
    """The samples for this run, taken on the first call and shared after.

    ``series[name]`` is the list of that probe's snapshots, ``ctx.samples + 1``
    of them (use ``snapshots(name)`` to get them with errors raised).  ``elapsed`` is the time that really passed between the first
    readings and the last, which is longer than the interval when the queries
    are slow.
    """
    if "samples" not in ctx.cache:
        names = list(PROBES)
        series = {name: [_read(name, ctx)] for name in names}
        started = ctx.clock()
        for _ in range(max(1, ctx.samples)):
            ctx.sleep(ctx.interval)
            for name in names:
                series[name].append(_read(name, ctx))
        ctx.cache["samples"] = Samples(series, max(ctx.clock() - started, 0.001))
    return ctx.cache["samples"]
