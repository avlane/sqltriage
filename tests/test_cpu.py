import os
import unittest

from sqltriage.checks import cpu, waits
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


def pressure(findings):
    """The runnable queue finding among whatever else the cpu check returned."""
    (found,) = [f for f in findings if f.title.startswith("CPU scheduler pressure")]
    return found


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
        finding = pressure(cpu.run(ctx()))
        self.assertTrue(any(e.startswith("spid 112 ") for e in finding.evidence))


def history_rows(pairs):
    return [{"event_time": "t%d" % i, "sql_cpu_pct": sql, "system_idle_pct": idle} for i, (sql, idle) in enumerate(pairs)]


class HistoryTests(unittest.TestCase):
    def serve(self, rows):
        class Fake(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "cpu_history":
                    return rows
                raise SourceError("not here")

        return Context(Fake(FIXTURES))

    def test_averages(self):
        sql, other, idle, minutes = cpu.history(self.serve(history_rows([(60, 30), (80, 10)])))
        self.assertEqual((sql, round(other, 1), idle, minutes), (70.0, 10.0, 20.0, 2))

    def test_rows_with_missing_values_are_skipped(self):
        rows = history_rows([(60, 30)]) + [{"event_time": "x", "sql_cpu_pct": None, "system_idle_pct": 5}]
        self.assertEqual(cpu.history(self.serve(rows))[3], 1)

    def test_no_rows_or_no_access(self):
        self.assertIsNone(cpu.history(self.serve([])))
        self.assertIsNone(cpu.history(Context(FixtureSource("/nonexistent"))))

    def test_sql_server_busy(self):
        (finding,) = cpu.history_findings((90.0, 4.0, 6.0, 10))
        self.assertEqual(finding.title, "SQL Server is using 90% of the CPU")
        self.assertEqual(finding.score, 70)
        self.assertEqual(finding.evidence, ["averaged over the last 10 minutes: SQL Server 90%, other processes 4%, idle 6%"])

    def test_something_else_busy(self):
        (finding,) = cpu.history_findings((40.0, 50.0, 10.0, 10))
        self.assertEqual(finding.title, "Something other than SQL Server is using 50% of the CPU")
        self.assertEqual(finding.score, 70)

    def test_both(self):
        self.assertEqual(len(cpu.history_findings((82.0, 26.0, 0.0, 10))), 2)

    def test_quiet(self):
        self.assertEqual(cpu.history_findings((12.0, 3.0, 85.0, 10)), [])
        self.assertEqual(cpu.history_findings(None), [])

    def test_scores_are_capped(self):
        (finding,) = cpu.history_findings((100.0, 0.0, 0.0, 10))
        self.assertEqual(finding.score, 85)

    def test_the_pressure_finding_quotes_the_history(self):
        finding = [f for f in cpu.run(ctx()) if "runnable queue" in f.title][0]
        self.assertTrue(any(e.startswith("last 10 minutes: SQL Server 58%, other processes 30%, idle 12%") for e in finding.evidence))

    def test_incident_flags_the_other_process(self):
        titles = [f.title for f in cpu.run(ctx())]
        self.assertIn("Something other than SQL Server is using 30% of the CPU", titles)
        self.assertNotIn("SQL Server is using 58% of the CPU", titles)


class QuietSignalTests(unittest.TestCase):
    def run_with(self, runnable, signal_pct):
        class Fake(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name != "cpu_schedulers":
                    return []
                return [{"scheduler_id": i, "cpu_id": i, "status": "VISIBLE ONLINE", "runnable_tasks_count": runnable[i],
                         "current_tasks_count": 5, "work_queue_count": 0} for i in range(len(runnable))]

        c = Context(Fake(FIXTURES), sleep=lambda s: None)
        c.cache["waits"] = [waits.WaitDelta("WRITELOG", 10, 1000, int(10 * signal_pct), None)]
        return cpu.run(c)

    def test_high_signal_share_with_an_empty_runnable_queue_is_ignored(self):
        (finding,) = self.run_with([0, 1, 0, 1, 0, 1, 0, 1], 60)
        self.assertEqual(finding.score, 0)
        self.assertIn("the runnable queue is short so it does not count", finding.evidence[1])

    def test_the_same_signal_share_counts_when_tasks_queue(self):
        (finding,) = self.run_with([2, 2, 2, 2, 2, 2, 2, 2], 60)
        self.assertEqual(finding.score, 80)  # signal 75 beats runnable 55, plus 5 for both
        self.assertNotIn("does not count", " ".join(finding.evidence))


class SignalTrendTests(unittest.TestCase):
    def deltas(self, signal_pct):
        return [waits.WaitDelta("WRITELOG", 10, 1000, int(10 * signal_pct), None)]

    def run_with(self, pcts):
        c = ctx()
        c.cache["waits"] = self.deltas(pcts[-1])
        c.cache["waits_deltas"] = [self.deltas(p) for p in pcts]
        return pressure(cpu.run(c))

    def test_trend_line(self):
        finding = self.run_with([10, 20, 40])
        self.assertIn("signal wait share per interval: 10%, 20%, 40% (rising)", finding.evidence)

    def test_rising_and_already_high_scores_higher(self):
        rising = self.run_with([10, 20, 40])
        steady = self.run_with([40, 40, 40])
        self.assertEqual(rising.score - steady.score, 5)

    def test_rising_but_low_does_not(self):
        low_rising = self.run_with([2, 4, 8])
        low_steady = self.run_with([8, 8, 8])
        self.assertEqual(low_rising.score, low_steady.score)

    def test_single_interval_has_no_trend_line(self):
        finding = self.run_with([30])
        self.assertFalse(any("per interval" in e for e in finding.evidence))


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
        self.assertFalse(any(f.title.startswith("CPU imbalance") for f in cpu.run(ctx())))


class RunTests(unittest.TestCase):
    def test_incident(self):
        finding = pressure(cpu.run(ctx()))
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
        finding = pressure(cpu.run(c))
        self.assertEqual(finding.score, 0)


if __name__ == "__main__":
    unittest.main()
