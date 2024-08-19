import os
import unittest

from sqltriage.checks import cpu, waits
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

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


def sched(i, runnable):
    return {"scheduler_id": i, "cpu_id": i, "status": "VISIBLE ONLINE", "runnable_tasks_count": runnable,
            "current_tasks_count": runnable + 5, "work_queue_count": 0}


class TopRequestsTests(unittest.TestCase):
    def test_lines(self):
        lines = cpu.top_requests(ctx())
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("spid 112 rpt_reader@ReportsDB used 1h10m of CPU in 10m (dop 8, running): SELECT c.CostCentre"))

    def test_long_statements_are_trimmed(self):
        class Wordy(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                return [{"session_id": 1, "cpu_ms": 1000, "elapsed_ms": 1000, "dop": 1, "status": "running",
                         "login_name": "a", "database_name": "d", "statement_text": "SELECT " + "x, " * 200}]

        (line,) = cpu.top_requests(Context(Wordy(FIXTURES)))
        self.assertTrue(line.endswith("..."))

    def test_unreadable(self):
        class Denied(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                raise SourceError("VIEW SERVER STATE denied")

        self.assertEqual(cpu.top_requests(Context(Denied(FIXTURES))), [])

    def test_pressure_finding_includes_them(self):
        (finding,) = cpu.run(ctx())
        self.assertTrue(any(e.startswith("spid 112 ") for e in finding.evidence))


class ImbalanceTests(unittest.TestCase):
    def test_hot_scheduler(self):
        online = [sched(0, 12)] + [sched(i, 1) for i in range(1, 8)]
        finding = cpu.imbalance_finding(online)
        self.assertEqual(finding.data, {"scheduler_id": 0, "runnable": 12, "median": 1})
        self.assertIn("scheduler 0 has 12 runnable tasks", finding.title)

    def test_evenly_busy_is_not_imbalance(self):
        self.assertIsNone(cpu.imbalance_finding([sched(i, 6) for i in range(8)]))

    def test_small_peaks_are_ignored(self):
        self.assertIsNone(cpu.imbalance_finding([sched(0, 3)] + [sched(i, 0) for i in range(1, 8)]))

    def test_too_few_schedulers(self):
        self.assertIsNone(cpu.imbalance_finding([sched(0, 20), sched(1, 0)]))

    def test_run_returns_both_findings(self):
        class Skewed(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name != "cpu_schedulers":
                    return []
                return [sched(0, 12)] + [sched(i, 1) for i in range(1, 8)]

        c = Context(Skewed(FIXTURES), sleep=lambda s: None)
        c.cache["waits"] = []
        titles = [f.title for f in cpu.run(c)]
        self.assertEqual(len(titles), 2)
        self.assertTrue(titles[0].startswith("CPU imbalance"))

    def test_incident_has_none(self):
        self.assertEqual(len(cpu.run(ctx())), 1)


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
