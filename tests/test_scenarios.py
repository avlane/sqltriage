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


if __name__ == "__main__":
    unittest.main()
