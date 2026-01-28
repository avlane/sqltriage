import json
import os
import tempfile
import unittest

from sqltriage import baseline
from sqltriage.models import Finding


class KeyTests(unittest.TestCase):
    def test_identity_not_counts(self):
        a = baseline.finding_key("blocking", "Blocking: spid 57 is blocking 6 session(s)", {"head_session_id": 57, "victims": 6})
        b = baseline.finding_key("blocking", "Blocking: spid 57 is blocking 2 session(s)", {"head_session_id": 57, "victims": 2})
        c = baseline.finding_key("blocking", "Blocking: spid 93 is blocking 1 session(s)", {"head_session_id": 93, "victims": 1})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_several_identity_parts(self):
        key = baseline.finding_key("querystore", "Regression in Sales: query 7", {"database": "Sales", "query_id": 7, "ratio": 3.0})
        self.assertEqual(key, "querystore|Regression in Sales: query #|Sales|7")

    def test_two_findings_for_one_database_do_not_collide(self):
        log = baseline.finding_key("vlf", "Log: Sales has 5,000 VLFs", {"database": "Sales"})
        backups = baseline.finding_key("vlf", "Log backups: Sales last one was 5h ago", {"database": "Sales"})
        self.assertNotEqual(log, backups)

    def test_title_without_identity_has_numbers_normalised(self):
        a = baseline.finding_key("tempdb", "tempdb file layout: 4 data file(s), sizes not even", {"data_files": 4})
        b = baseline.finding_key("tempdb", "tempdb file layout: 6 data file(s), sizes not even", {"data_files": 6})
        self.assertEqual(a, b)

    def test_checks_do_not_collide(self):
        self.assertNotEqual(
            baseline.finding_key("a", "t1", {"database": "X"}), baseline.finding_key("b", "t1", {"database": "X"})
        )

    def test_none_identity_is_ignored(self):
        self.assertEqual(baseline.finding_key("a", "Title 5", {"database": None}), "a|Title #")


class ClassifyTests(unittest.TestCase):
    def test_all_cases(self):
        self.assertEqual(baseline.classify(50, None), "new")
        self.assertEqual(baseline.classify(60, 50), "worse")
        self.assertEqual(baseline.classify(59, 50), "same")
        self.assertEqual(baseline.classify(40, 50), "better")
        self.assertEqual(baseline.classify(41, 50), "same")


class ApplyTests(unittest.TestCase):
    def test_marks_and_counts(self):
        findings = [
            Finding("vlf", "Log: Sales", 80, data={"database": "Sales"}),
            Finding("vlf", "Log: Hr", 50, data={"database": "Hr"}),
            Finding("vlf", "Log: Ops", 30, data={"database": "Ops"}),
            Finding("vlf", "Log: New", 30, data={"database": "New"}),
        ]
        previous = {
            "vlf|Log: Sales|Sales": {"check": "vlf", "title": "Log: Sales", "score": 50},
            "vlf|Log: Hr|Hr": {"check": "vlf", "title": "Log: Hr", "score": 50},
            "vlf|Log: Ops|Ops": {"check": "vlf", "title": "Log: Ops", "score": 60},
        }
        counts, resolved = baseline.apply(findings, previous)
        self.assertEqual(resolved, [])
        self.assertEqual([f.change for f in findings], ["worse", "same", "better", "new"])
        self.assertEqual(counts, {"new": 1, "worse": 1, "better": 1, "same": 1})
        self.assertIn("score was 50 in the baseline, now 80", findings[0].evidence)
        self.assertEqual(findings[1].evidence, [])


class ResolvedTests(unittest.TestCase):
    def test_findings_that_went_away(self):
        previous = {
            "a|Gone low": {"check": "a", "title": "Gone low", "score": 30},
            "a|Gone high": {"check": "a", "title": "Gone high", "score": 80},
            "a|Still here": {"check": "a", "title": "Still here", "score": 50},
        }
        counts, resolved = baseline.apply([Finding("a", "Still here", 50)], previous)
        self.assertEqual([r["title"] for r in resolved], ["Gone high", "Gone low"])
        self.assertEqual(counts["same"], 1)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text):
        path = os.path.join(self.tmp.name, "b.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_load(self):
        report = {"schema_version": 1, "findings": [{"check": "vlf", "title": "Log: Sales", "score": 70, "data": {"database": "Sales"}}]}
        self.assertEqual(
            baseline.load(self.write(json.dumps(report))),
            {"vlf|Log: Sales|Sales": {"check": "vlf", "title": "Log: Sales", "score": 70}},
        )

    def test_duplicate_keys_keep_the_higher_score(self):
        finding = {"check": "vlf", "title": "Log: Sales", "data": {"database": "Sales"}}
        report = {"schema_version": 1, "findings": [dict(finding, score=30), dict(finding, score=70), dict(finding, score=50)]}
        self.assertEqual(baseline.load(self.write(json.dumps(report)))["vlf|Log: Sales|Sales"]["score"], 70)

    def test_errors(self):
        for text in ("not json", "[]", json.dumps({"findings": []})):
            with self.assertRaises(baseline.BaselineError):
                baseline.load(self.write(text))
        with self.assertRaises(baseline.BaselineError):
            baseline.load(os.path.join(self.tmp.name, "missing.json"))


if __name__ == "__main__":
    unittest.main()
