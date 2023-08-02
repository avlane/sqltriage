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


class ResetTests(unittest.TestCase):
    def test_counters_going_backwards(self):
        before = {"WRITELOG": row("WRITELOG", 100, 500)}
        after = {"WRITELOG": row("WRITELOG", 3, 20)}
        self.assertTrue(waits.was_reset(before, after))

    def test_wait_type_disappearing(self):
        before = {"WRITELOG": row("WRITELOG", 100, 500), "LCK_M_X": row("LCK_M_X", 5, 900)}
        after = {"WRITELOG": row("WRITELOG", 120, 600)}
        self.assertTrue(waits.was_reset(before, after))

    def test_normal_growth(self):
        before = {"WRITELOG": row("WRITELOG", 100, 500)}
        after = {"WRITELOG": row("WRITELOG", 120, 600), "NEW_WAIT": row("NEW_WAIT", 1, 5)}
        self.assertFalse(waits.was_reset(before, after))

    def test_run_explains_a_reset(self):
        class Cleared(FixtureSource):
            calls = 0

            def fetch(self, name, sql, params=(), database=None):
                Cleared.calls += 1
                if Cleared.calls == 1:
                    return [row("WRITELOG", 100, 500)]
                return [row("WRITELOG", 2, 10)]

        ctx = Context(Cleared(FIXTURES), interval=1, sleep=lambda s: None)
        (finding,) = waits.run(ctx)
        self.assertEqual(finding.title, "Wait statistics were reset during the sample")
        self.assertEqual(finding.score, 10)

    def test_fixture_sample_is_not_a_reset(self):
        ctx = Context(FixtureSource(FIXTURES), interval=10, sleep=lambda s: None)
        waits.sample(ctx)
        self.assertFalse(ctx.cache["waits_reset"])


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

    def test_run_skips_idle_waits(self):
        ctx, _ = self.make_ctx()
        findings = waits.run(ctx)
        names = [f.data["wait_type"] for f in findings]
        self.assertEqual(len(findings), ctx.top)
        self.assertNotIn("SLEEP_TASK", names)
        self.assertNotIn("XE_TIMER_EVENT", names)

    def test_locking_outranks_background_noise(self):
        ctx, _ = self.make_ctx()
        ctx.top = 20
        by_name = {f.data["wait_type"]: f for f in waits.run(ctx)}
        self.assertGreater(by_name["LCK_M_X"].score, by_name["ASYNC_NETWORK_IO"].score)
        self.assertEqual(by_name["LCK_M_X"].data["category"], "locking")

    def test_nothing_significant(self):
        ctx = Context(FixtureSource(FIXTURES), interval=1, sleep=lambda s: None)
        ctx.cache["waits"] = [waits.WaitDelta("SLEEP_TASK", 5, 100, 1, None)]
        (finding,) = waits.run(ctx)
        self.assertEqual(finding.score, 0)


class ExplainTests(unittest.TestCase):
    def test_exact_prefix_and_unknown(self):
        from sqltriage import waitinfo

        self.assertEqual(waitinfo.explain("WRITELOG").category, "log")
        self.assertEqual(waitinfo.explain("LCK_M_RIn_NL").category, "locking")
        self.assertEqual(waitinfo.explain("PAGEIOLATCH_EX").category, "io")
        self.assertEqual(waitinfo.explain("SOMETHING_NEW"), waitinfo.UNKNOWN)

    def test_benign(self):
        from sqltriage import waitinfo

        self.assertTrue(waitinfo.is_benign("LAZYWRITER_SLEEP"))
        self.assertFalse(waitinfo.is_benign("WRITELOG"))


if __name__ == "__main__":
    unittest.main()
