import os
import unittest

from sqltriage.checks import waits
from sqltriage.context import Context, ManualClock
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


class Ramp(FixtureSource):
    """Cumulative counters where WRITELOG waits grow by the given amounts each interval."""

    def __init__(self, increments):
        super().__init__(FIXTURES)
        self.increments = increments
        self.calls = 0

    def fetch(self, name, sql, params=(), database=None):
        if name != "waits_snapshot":
            return []
        total = sum(self.increments[: self.calls])
        self.calls += 1
        return [row("WRITELOG", 10 * self.calls, 1000 + total, 0, 5), row("SLEEP_TASK", 1, 1 + self.calls * 1000)]


class TrendTests(unittest.TestCase):
    def run_waits(self, increments):
        ctx = Context(Ramp(increments), interval=10, samples=len(increments), sleep=lambda s: None, top=3)
        return ctx, waits.run(ctx)

    def test_trend_words(self):
        self.assertEqual(waits.trend([100, 160]), "rising")
        self.assertEqual(waits.trend([100, 60]), "falling")
        self.assertEqual(waits.trend([100, 110, 120]), "steady")
        self.assertEqual(waits.trend([5]), "steady")
        self.assertEqual(waits.trend([0, 50]), "rising")
        self.assertEqual(waits.trend([0, 0]), "steady")

    def test_rising_wait_is_shown_and_scored_up(self):
        _, rising = self.run_waits([1000, 2000, 4000])
        _, steady = self.run_waits([2000, 2000, 2000])
        self.assertIn("per interval: 1000, 2000, 4000 ms (rising)", rising[0].evidence)
        self.assertIn("per interval: 2000, 2000, 2000 ms (steady)", steady[0].evidence)
        self.assertEqual(rising[0].score - steady[0].score, 5)

    def test_the_delta_covers_the_whole_span(self):
        ctx, (finding,) = self.run_waits([1000, 2000, 4000])
        self.assertEqual(finding.data["wait_ms"], 7000)

    def test_one_sample_has_no_trend_line(self):
        _, (finding,) = self.run_waits([1000])
        self.assertFalse(any("per interval" in e for e in finding.evidence))

    def test_sleeps_once_per_interval(self):
        slept = []
        ctx = Context(Ramp([1, 1, 1]), interval=5, samples=3, sleep=slept.append)
        waits.sample(ctx)
        self.assertEqual(slept, [5, 5, 5])

    def test_a_reset_in_any_interval_discards_everything(self):
        class Reset(Ramp):
            def fetch(inner, name, sql, params=(), database=None):
                rows = super().fetch(name, sql, params, database)
                if rows and inner.calls == 3:
                    rows[0] = row("WRITELOG", 1, 5)
                return rows

        ctx = Context(Reset([1000, 1000, 1000]), interval=10, samples=3, sleep=lambda s: None)
        self.assertEqual(waits.sample(ctx), [])
        self.assertTrue(ctx.cache["waits_reset"])


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
                if name != "waits_snapshot":
                    return []
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


class ElapsedTests(unittest.TestCase):
    def concurrency_line(self, drift):
        clock = ManualClock(drift)
        ctx = Context(FixtureSource(FIXTURES), interval=10, sleep=clock.sleep, clock=clock.now, top=20)
        finding = [f for f in waits.run(ctx) if f.data["wait_type"] == "LCK_M_X"][0]
        return finding.evidence[1]

    def test_on_time_sample(self):
        self.assertEqual(self.concurrency_line(0), "9.2 tasks waiting on average during the sample")

    def test_slow_sample_uses_the_real_elapsed_time(self):
        # 92000 ms of waiting over 11.5 s rather than 10 s
        self.assertEqual(self.concurrency_line(1.5), "8.0 tasks waiting on average during the sample")


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

    def test_a_wait_few_tasks_were_stuck_on_is_background_noise(self):
        ctx = Context(FixtureSource(FIXTURES), interval=10, sleep=lambda s: None)
        quiet = waits.WaitDelta("WRITELOG", 600, 900, 60, None)   # 0.09 tasks waiting on average
        busy = waits.WaitDelta("WRITELOG", 600, 90000, 60, None)  # 9 tasks waiting on average
        self.assertEqual(waits.score_wait(quiet, 90.0, 10000), waits.QUIET_CAP)
        self.assertGreater(waits.score_wait(busy, 90.0, 10000), 50)

    def test_the_cap_only_lowers(self):
        small = waits.WaitDelta("ASYNC_NETWORK_IO", 1, 10, 0, None)
        self.assertLessEqual(waits.score_wait(small, 0.1, 10000), waits.QUIET_CAP)

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

    def test_newer_waits_are_explained(self):
        from sqltriage import waitinfo

        for name, category in [
            ("RESOURCE_SEMAPHORE_QUERY_COMPILE", "memory"),
            ("PREEMPTIVE_OS_WRITEFILEGATHER", "io"),
            ("HADR_THROTTLE_LOG_RATE_SEND_RECV_QUEUE_SIZE", "ag"),
            ("TRANSACTION_MUTEX", "locking"),
        ]:
            self.assertEqual(waitinfo.explain(name).category, category, name)

    def test_more_waits_explained(self):
        from sqltriage import waitinfo

        for name, category in [
            ("LOGMGR_RESERVE_APPEND", "log"),
            ("HADR_DATABASE_FLOW_CONTROL", "ag"),
            ("PREEMPTIVE_HADR_LEASE_MECHANISM", "ag"),
            ("PREEMPTIVE_OS_AUTHENTICATIONOPS", "client"),
            ("WAIT_ON_SYNC_STATISTICS_REFRESH", "statistics"),
            ("ACCESS_METHODS_DATASET_PARENT", "parallelism"),
            ("DTC_STATE", "client"),
            ("PREEMPTIVE_OS_CRYPTOPS", "preemptive"),
        ]:
            self.assertEqual(waitinfo.explain(name).category, category, name)

    def test_specific_entries_beat_the_prefixes(self):
        from sqltriage import waitinfo

        self.assertEqual(waitinfo.explain("PREEMPTIVE_OS_WRITEFILEGATHER").category, "io")
        self.assertEqual(waitinfo.explain("HADR_SYNC_COMMIT").category, "ag")
        self.assertEqual(waitinfo.explain("HADR_DATABASE_FLOW_CONTROL").weight, 30)

    def test_every_explanation_is_complete(self):
        from sqltriage import waitinfo

        entries = list(waitinfo.WAIT_INFO.items()) + [(p, i) for p, i in waitinfo.PREFIX_INFO]
        for name, info in entries:
            self.assertTrue(info.text.endswith("."), name)
            self.assertTrue(info.advice, name)
            self.assertTrue(0 <= info.weight <= 50, name)
            self.assertFalse(waitinfo.is_benign(name), name)

    def test_background_families_are_idle(self):
        from sqltriage import waitinfo

        for name in ("QDS_SOMETHING_NEW", "PARALLEL_REDO_FUTURE_WAIT", "XE_FILE_TARGET_TVF", "SLEEP_BUFFERPOOL_HELPLW"):
            self.assertTrue(waitinfo.is_benign(name), name)
        self.assertFalse(waitinfo.is_benign("PAGEIOLATCH_SH"))

    def test_cxconsumer_is_idle(self):
        from sqltriage import waitinfo

        self.assertTrue(waitinfo.is_benign("CXCONSUMER"))
        self.assertFalse(waitinfo.is_benign("CXPACKET"))

    def test_benign(self):
        from sqltriage import waitinfo

        self.assertTrue(waitinfo.is_benign("LAZYWRITER_SLEEP"))
        self.assertFalse(waitinfo.is_benign("WRITELOG"))


if __name__ == "__main__":
    unittest.main()
