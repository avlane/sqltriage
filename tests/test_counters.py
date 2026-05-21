import os
import unittest

from sqltriage.checks import counters, waits
from sqltriage.context import Context, ManualClock
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


def reading(**values):
    names = {
        "deadlocks": counters.DEADLOCKS,
        "timeouts": counters.LOCK_TIMEOUTS,
        "lock_waits": counters.LOCK_WAITS,
        "batches": counters.BATCHES,
        "compiles": counters.COMPILES,
        "recompiles": counters.RECOMPILES,
    }
    base = {name: 1000 for name in names.values()}
    for key, value in values.items():
        base[names[key]] = value
    return [{"counter_name": n, "instance_name": None, "cntr_value": v} for n, v in base.items()]


class Serve(FixtureSource):
    """Two readings of the counters, with the waits left as the recorded fixture."""

    def __init__(self, before, after):
        super().__init__(FIXTURES)
        self.pair = [before, after]
        self.n = 0

    def fetch(self, name, sql, params=(), database=None):
        if name == "counters_snapshot":
            self.n += 1
            return self.pair[min(self.n, 2) - 1]
        return super().fetch(name, sql, params, database)


def run(before, after, interval=10):
    clock = ManualClock()
    ctx = Context(Serve(before, after), interval=interval, sleep=clock.sleep, clock=clock.now)
    return counters.run(ctx)


class DeltaTests(unittest.TestCase):
    def test_deltas_skip_missing_counters(self):
        self.assertEqual(counters.deltas({"a": 1, "b": 5}, {"a": 4, "c": 9}), {"a": 3})

    def test_reset(self):
        self.assertTrue(counters.reset_between({"a": 10}, {"a": 2}))
        self.assertFalse(counters.reset_between({"a": 10}, {"a": 10, "b": 1}))


class FindingTests(unittest.TestCase):
    def titles(self, findings):
        return [f.title for f in findings]

    def test_quiet_server_only_reports_nothing(self):
        self.assertEqual(run(reading(), reading()), [])

    def test_deadlocks(self):
        (finding,) = run(reading(), reading(deadlocks=1002))
        self.assertEqual(finding.title, "2 deadlock(s) during the 10s sample")
        self.assertEqual(finding.score, 70)
        self.assertEqual(finding.data, {"deadlocks": 2})

    def test_deadlock_score_is_capped(self):
        (finding,) = run(reading(), reading(deadlocks=1100))
        self.assertEqual(finding.score, 90)

    def test_lock_timeouts(self):
        (finding,) = run(reading(), reading(timeouts=1004))
        self.assertEqual((finding.title, finding.score), ("4 lock timeout(s) during the sample", 42))

    def test_compile_ratio_needs_real_traffic(self):
        quiet = run(reading(), reading(batches=1010, compiles=1010))  # 1 batch/s: too quiet to judge
        self.assertEqual([f.title for f in quiet], ["Workload during the sample: 1 batch requests/s"])
        findings = run(reading(), reading(batches=3000, compiles=1600, recompiles=1100))
        self.assertEqual(findings[0].title, "30% of batches compiled a plan")
        self.assertEqual(findings[0].score, 42)
        self.assertEqual(findings[0].evidence, ["60 compilations/s (10 of them recompilations) against 200 batch requests/s"])

    def test_workload_summary(self):
        (summary,) = run(reading(), reading(batches=1500, compiles=1010, lock_waits=1020))
        self.assertEqual(summary.title, "Workload during the sample: 50 batch requests/s")
        self.assertEqual(summary.score, 5)
        self.assertEqual(summary.evidence, ["1 compilations/s, 0 recompilations/s, 2.0 lock waits/s"])

    def test_a_restart_during_the_sample_gives_nothing(self):
        self.assertEqual(run(reading(deadlocks=5000), reading(deadlocks=3)), [])

    def test_incident(self):
        ctx = Context(FixtureSource(FIXTURES), interval=10, sleep=lambda s: None)
        clock = ManualClock()
        ctx.sleep, ctx.clock = clock.sleep, clock.now
        findings = counters.run(ctx)
        self.assertEqual(
            [(f.title, f.score) for f in findings],
            [
                ("2 deadlock(s) during the 10s sample", 70),
                ("36% of batches compiled a plan", 44),
                ("Workload during the sample: 420 batch requests/s", 5),
            ],
        )


class IsolationTests(unittest.TestCase):
    def test_a_counters_failure_does_not_stop_the_waits(self):
        class NoCounters(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "counters_snapshot":
                    raise SourceError("The SELECT permission was denied on sys.dm_os_performance_counters")
                return super().fetch(name, sql, params, database)

        clock = ManualClock()
        ctx = Context(NoCounters(FIXTURES), interval=10, sleep=clock.sleep, clock=clock.now)
        self.assertTrue(waits.run(ctx))  # the waits still work
        with self.assertRaises(SourceError):
            counters.run(ctx)


if __name__ == "__main__":
    unittest.main()
