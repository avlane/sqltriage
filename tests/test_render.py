import datetime
import json
import unittest

from sqltriage.models import Finding
from sqltriage.render import to_dict, to_json, to_markdown, to_text
from sqltriage.report import Report

WHEN = datetime.datetime(2023, 4, 12, 20, 0, 0)


def sample_report(errors=None):
    findings = [
        Finding(
            "blocking",
            "Blocking: spid 57 is blocking 2 session(s)",
            84,
            detail="Longest wait 94s.",
            evidence=["input buffer: BEGIN TRAN"],
            advice=["Find out why spid 57 is not finishing."],
            block=["spid 57 [sleeping]  <- head blocker", "`- spid 61 LCK_M_X 94.0s"],
        ),
        Finding("vlf", "Log: Sales has 1,200 VLFs", 45),
    ]
    return Report("SQLPROD01", findings, errors or [], WHEN)


class TextTests(unittest.TestCase):
    def test_layout(self):
        text = to_text(sample_report())
        self.assertIn("sqltriage report for SQLPROD01 at 2023-04-12 20:00:00", text)
        self.assertIn(" 1. [HIGH 84] Blocking: spid 57", text)
        self.assertIn("      `- spid 61 LCK_M_X 94.0s", text)
        self.assertIn("      > Find out why", text)

    def test_empty(self):
        self.assertIn("Nothing to report.", to_text(Report("S", [], [], WHEN)))

    def test_errors_are_listed(self):
        self.assertIn("  - vlf: boom", to_text(sample_report(["vlf: boom"])))


class FilterTests(unittest.TestCase):
    def test_filter_report(self):
        from sqltriage.report import filter_report

        filtered = filter_report(sample_report(), "high")
        self.assertEqual([f.check for f in filtered.findings], ["blocking"])
        self.assertEqual(filtered.hidden, 1)
        self.assertIn("(1 lower ranked finding(s) hidden)", to_text(filtered))
        self.assertIn("_1 lower ranked finding(s) hidden._", to_markdown(filtered))

    def test_nothing_hidden_by_default(self):
        self.assertNotIn("hidden", to_text(sample_report()))


class JsonTests(unittest.TestCase):
    def test_round_trip(self):
        data = json.loads(to_json(sample_report()))
        self.assertEqual(data["server"], "SQLPROD01")
        self.assertEqual(data["findings"][0]["severity"], "high")
        self.assertEqual(data["findings"][1]["severity"], "medium")
        self.assertEqual(to_dict(sample_report())["errors"], [])


class MarkdownTests(unittest.TestCase):
    def test_summary_table_and_details(self):
        md = to_markdown(sample_report())
        self.assertIn("### sqltriage report for SQLPROD01", md)
        self.assertIn("| 1 | high | 84 | Blocking: spid 57 is blocking 2 session(s) |", md)
        self.assertIn("**2. Log: Sales has 1,200 VLFs** (medium, 45)", md)
        self.assertIn("Next steps:\n1. Find out why spid 57 is not finishing.", md)

    def test_tree_is_fenced(self):
        md = to_markdown(sample_report())
        self.assertIn("```\nspid 57 [sleeping]  <- head blocker\n`- spid 61 LCK_M_X 94.0s\n```", md)

    def test_empty(self):
        self.assertIn("Nothing to report.", to_markdown(Report("S", [], [], WHEN)))

    def test_errors(self):
        self.assertIn("- vlf: boom", to_markdown(sample_report(["vlf: boom"])))


if __name__ == "__main__":
    unittest.main()
