import os
import unittest

from sqltriage.checks import cpu, waits
from sqltriage.context import Context
from sqltriage.source import FixtureSource

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


def ctx():
    return Context(FixtureSource(FIXTURES), interval=10, sleep=lambda s: None)


class SignalTests(unittest.TestCase):
    def test_percentage_ignores_idle_waits(self):
        deltas = [
            waits.WaitDelta("SOS_SCHEDULER_YIELD", 10, 1000, 900, None),
            waits.WaitDelta("WRITELOG", 10, 1000, 100, None),
            waits.WaitDelta("SLEEP_TASK", 10, 100000, 0, None),
        ]
        self.assertAlmostEqual(cpu.signal_wait_pct(deltas), 50.0)

    def test_no_waits(self):
        self.assertEqual(cpu.signal_wait_pct([]), 0.0)


class RunTests(unittest.TestCase):
    def test_incident(self):
        (finding,) = cpu.run(ctx())
        self.assertEqual(finding.data["online_schedulers"], 16)
        self.assertEqual(finding.data["avg_runnable"], 3.62)
        self.assertEqual(finding.score, 75)
        self.assertTrue(any("VISIBLE OFFLINE" in e for e in finding.evidence))
        self.assertTrue(any("work_queue_count" in e for e in finding.evidence))

    def test_reuses_the_wait_sample(self):
        c = ctx()
        slept = []
        c.sleep = slept.append
        waits.run(c)
        cpu.run(c)
        self.assertEqual(slept, [10])

    def test_idle_server(self):
        class Idle(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return [
                    {"scheduler_id": 0, "status": "VISIBLE ONLINE", "runnable_tasks_count": 0, "work_queue_count": 0}
                ]

        c = Context(Idle(FIXTURES), sleep=lambda s: None)
        c.cache["waits"] = []
        (finding,) = cpu.run(c)
        self.assertEqual(finding.score, 0)


if __name__ == "__main__":
    unittest.main()
