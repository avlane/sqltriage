import os
import unittest

from sqltriage.checks import waits
from sqltriage.context import Context
from sqltriage.source import FixtureSource

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


def row(wait_type, tasks, wait, signal=0, max_wait=0):
    return {
        "wait_type": wait_type,
        "waiting_tasks_count": tasks,
        "wait_time_ms": wait,
        "signal_wait_time_ms": signal,
        "max_wait_time_ms": max_wait,
    }


class DeltaTests(unittest.TestCase):
    def test_difference_between_snapshots(self):
        before = {"WRITELOG": row("WRITELOG", 100, 500, 50, 20)}
        after = {"WRITELOG": row("WRITELOG", 160, 900, 70, 35)}
        (delta,) = waits.compute_delta(before, after)
        self.assertEqual((delta.tasks, delta.wait_ms, delta.signal_ms), (60, 400, 20))
        self.assertEqual(delta.max_wait_ms, 35)

    def test_unchanged_waits_are_dropped(self):
        snap = {"WRITELOG": row("WRITELOG", 100, 500)}
        self.assertEqual(waits.compute_delta(snap, snap), [])

    def test_new_wait_type_counts_in_full(self):
        after = {"THREADPOOL": row("THREADPOOL", 4, 800, 0, 300)}
        (delta,) = waits.compute_delta({}, after)
        self.assertEqual(delta.wait_ms, 800)

    def test_sorted_by_wait_time(self):
        before = {}
        after = {"A": row("A", 1, 10), "B": row("B", 1, 30), "C": row("C", 1, 20)}
        self.assertEqual([d.wait_type for d in waits.compute_delta(before, after)], ["B", "C", "A"])


class SampleTests(unittest.TestCase):
    def make_ctx(self):
        slept = []
        ctx = Context(FixtureSource(FIXTURES), interval=10, sleep=slept.append)
        return ctx, slept

    def test_sample_sleeps_once_for_the_interval(self):
        ctx, slept = self.make_ctx()
        deltas = waits.sample(ctx)
        self.assertEqual(slept, [10])
        self.assertEqual(deltas[0].wait_ms, 160000)
        waits.sample(ctx)
        self.assertEqual(slept, [10])

    def test_run_reports_top_waits(self):
        ctx, _ = self.make_ctx()
        (finding,) = waits.run(ctx)
        self.assertEqual(len(finding.evidence), ctx.top)


if __name__ == "__main__":
    unittest.main()
