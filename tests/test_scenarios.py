"""Whole-report checks against recorded scenarios, through the command line."""
import contextlib
import io
import json
import os
import unittest

from sqltriage.cli import main

HERE = os.path.dirname(os.path.abspath(__file__))


def run(scenario, *argv):
    out = io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):
        code = main(["--fixtures", os.path.join(HERE, "fixtures", scenario), "-n", "50", "-f", "json"] + list(argv), out=out)
    return code, json.loads(out.getvalue())


class HealthyServerTests(unittest.TestCase):
    """A quiet server must not read like an incident."""

    def test_nothing_above_info(self):
        code, report = run("healthy1")
        self.assertEqual(code, 0)
        self.assertEqual(report["errors"], [])
        worst = max(f["score"] for f in report["findings"])
        self.assertLess(worst, 15, [(f["score"], f["title"]) for f in report["findings"]])

    def test_summary_counts_only_info(self):
        _, report = run("healthy1")
        summary = report["summary"]
        self.assertEqual({k: v for k, v in summary.items() if v}, {"info": len(report["findings"])})

    def test_fail_on_low_does_not_trip(self):
        code, _ = run("healthy1", "--fail-on", "low")
        self.assertEqual(code, 0)

    def test_every_check_ran_without_error(self):
        _, report = run("healthy1")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["instance"]["product_name"], "SQL Server 2022")

    def test_the_cpu_check_says_why_it_is_not_worried(self):
        _, report = run("healthy1", "--only", "cpu")
        (finding,) = report["findings"]
        self.assertEqual(finding["score"], 0)
        self.assertTrue(any("runnable queue is short so it does not count" in e for e in finding["evidence"]))

    def test_the_busiest_waits_are_still_listed_as_info(self):
        _, report = run("healthy1", "--only", "waits")
        titles = {f["title"] for f in report["findings"]}
        self.assertTrue({"Wait: SOS_SCHEDULER_YIELD", "Wait: WRITELOG"} <= titles)
        self.assertEqual({f["severity"] for f in report["findings"]}, {"info"})


class IncidentTests(unittest.TestCase):
    def test_incident_one_is_loud(self):
        code, report = run("incident1", "--fail-on", "high")
        self.assertEqual(code, 1)
        self.assertGreaterEqual(report["summary"]["critical"], 2)
        self.assertEqual(report["errors"], [])


class LaggingSecondaryTests(unittest.TestCase):
    """incident2: a synchronous secondary falls behind and the primary's log cannot be reused."""

    @classmethod
    def setUpClass(cls):
        cls.code, cls.report = run("incident2", "--fail-on", "high")
        cls.by_title = {f["title"]: f for f in cls.report["findings"]}

    def find(self, start):
        (match,) = [f for t, f in self.by_title.items() if t.startswith(start)]
        return match

    def test_exit_status_and_no_errors(self):
        self.assertEqual(self.code, 1)
        self.assertEqual(self.report["errors"], [])

    def test_the_log_is_the_top_finding(self):
        top = self.report["findings"][0]
        self.assertTrue(top["title"].startswith("Log: SalesDB has 12,400 VLFs and log reuse wait AVAILABILITY_REPLICA"))
        self.assertEqual(top["severity"], "critical")

    def test_the_log_finding_points_at_the_lagging_secondary(self):
        evidence = self.report["findings"][0]["evidence"]
        self.assertIn("the log cannot be reused while a secondary is behind: Availability group SalesAG: SalesDB on SQLTEST02 is behind", evidence)
        self.assertIn("log autogrowth is 10%, which is how it got this many", evidence)

    def test_the_ag_finding_estimates_the_cost_of_a_failover(self):
        behind = self.find("Availability group SalesAG: SalesDB on SQLTEST02 is behind")
        self.assertIn("a failover to this replica now would lose about 19m", behind["evidence"])
        self.assertIn("log send queue 180.0 GB, about 350 min at the current rate", behind["evidence"])
        self.assertEqual(behind["data"]["log_send_queue_kb"], 188743680)

    def test_automatic_failover_is_flagged_as_not_ready(self):
        ready = self.find("Availability group SalesAG: failover to SQLTEST02 is not ready")
        self.assertIn("automatic failover to this replica would NOT happen", ready["evidence"])

    def test_sync_commit_waits_are_the_same_problem_and_ranked_below_it(self):
        wait = self.find("Wait: HADR_SYNC_COMMIT")
        behind = self.find("Availability group SalesAG: SalesDB on SQLTEST02 is behind")
        self.assertIn("probably the same problem as: " + behind["title"], wait["evidence"])
        self.assertLess(wait["score"], behind["score"])
        self.assertIn("also shows up as: HADR_SYNC_COMMIT", behind["evidence"])

    def test_nothing_unrelated_is_loud(self):
        loud = {f["check"] for f in self.report["findings"] if f["score"] >= 40}
        self.assertEqual(loud, {"vlf", "ag", "waits"})

    def test_a_baseline_from_the_healthy_server_marks_it_all_new(self):
        import tempfile

        _, healthy = run("healthy1")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "healthy.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(healthy, handle)
            _, report = run("incident2", "--baseline", path, "--min-severity", "high")
        changes = {f["title"].split(":")[0]: f["change"] for f in report["findings"]}
        self.assertEqual(set(changes.values()), {"new"})


if __name__ == "__main__":
    unittest.main()
