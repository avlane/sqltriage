import unittest

from sqltriage import sampling
from sqltriage.context import Context, ManualClock
from sqltriage.source import Source


class SamplingTests(unittest.TestCase):
    def setUp(self):
        saved = dict(sampling.PROBES)
        self.addCleanup(lambda: (sampling.PROBES.clear(), sampling.PROBES.update(saved)))
        sampling.PROBES.clear()
        self.log = []

        @sampling.probe("a")
        def probe_a(ctx):
            self.log.append("a")
            return len([x for x in self.log if x == "a"])

        @sampling.probe("b")
        def probe_b(ctx):
            self.log.append("b")
            return "b" + str(len([x for x in self.log if x == "b"]))

    def ctx(self, **kwargs):
        clock = ManualClock()
        return Context(Source(), interval=10, sleep=lambda s: (self.log.append("sleep"), clock.sleep(s)), clock=clock.now, **kwargs)

    def test_readings_bracket_each_sleep(self):
        sampling.take(self.ctx())
        self.assertEqual(self.log, ["a", "b", "sleep", "a", "b"])

    def test_series_per_probe(self):
        taken = sampling.take(self.ctx())
        self.assertEqual(taken.series, {"a": [1, 2], "b": ["b1", "b2"]})

    def test_several_intervals(self):
        taken = sampling.take(self.ctx(samples=3))
        self.assertEqual(taken.series["a"], [1, 2, 3, 4])
        self.assertEqual(self.log.count("sleep"), 3)
        self.assertEqual(taken.elapsed, 30)

    def test_taken_once_and_shared(self):
        ctx = self.ctx()
        first = sampling.take(ctx)
        second = sampling.take(ctx)
        self.assertIs(first, second)
        self.assertEqual(self.log.count("sleep"), 1)

    def test_elapsed_includes_slow_queries(self):
        clock = ManualClock(drift=1.5)
        ctx = Context(Source(), interval=10, sleep=clock.sleep, clock=clock.now)
        self.assertEqual(sampling.take(ctx).elapsed, 11.5)

    def test_a_failing_probe_does_not_stop_the_others(self):
        from sqltriage.source import SourceError

        @sampling.probe("broken")
        def broken(ctx):
            raise SourceError("denied")

        taken = sampling.take(self.ctx())
        self.assertEqual(taken.snapshots("a"), [1, 2])
        with self.assertRaises(SourceError):
            taken.snapshots("broken")

    def test_samples_below_one_still_take_one_interval(self):
        taken = sampling.take(self.ctx(samples=0))
        self.assertEqual(len(taken.series["a"]), 2)

    def test_elapsed_is_never_zero(self):
        ctx = Context(Source(), interval=0, sleep=lambda s: None, clock=lambda: 5.0)
        self.assertGreater(sampling.take(ctx).elapsed, 0)

    def test_registered_probes_include_the_waits(self):
        sampling.PROBES.clear()
        import importlib

        from sqltriage.checks import waits

        importlib.reload(waits)
        self.assertIn("waits", sampling.PROBES)


if __name__ == "__main__":
    unittest.main()
