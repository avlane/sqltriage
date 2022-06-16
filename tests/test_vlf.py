import os
import unittest

from sqltriage.checks import vlf
from sqltriage.context import Context
from sqltriage.source import FixtureSource

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class VlfTests(unittest.TestCase):
    def test_score_levels(self):
        self.assertEqual(vlf.vlf_score(12), 0)
        self.assertEqual(vlf.vlf_score(500), 20)
        self.assertEqual(vlf.vlf_score(1000), 45)
        self.assertEqual(vlf.vlf_score(5000), 65)
        self.assertEqual(vlf.vlf_score(20000), 80)

    def test_incident(self):
        findings = vlf.run(Context(FixtureSource(FIXTURES)))
        by_db = {f.data["database"]: f for f in findings}
        self.assertEqual(sorted(by_db), ["PayrollDB", "ReportsDB", "Staging"])
        payroll = by_db["PayrollDB"]
        self.assertEqual(payroll.severity, "critical")
        self.assertIn("18,432 VLFs", payroll.title)
        self.assertIn("ACTIVE_TRANSACTION", payroll.title)

    def test_reuse_wait_alone_is_reported(self):
        class One(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return [
                    {
                        "database_name": "Sales",
                        "recovery_model_desc": "FULL",
                        "log_reuse_wait_desc": "LOG_BACKUP",
                        "vlf_count": 40,
                        "total_log_mb": 1024.0,
                        "active_vlfs": 30,
                    }
                ]

        (finding,) = vlf.run(Context(One(FIXTURES)))
        self.assertEqual(finding.score, 50)
        self.assertEqual(finding.title, "Log: Sales has log reuse wait LOG_BACKUP")

    def test_unknown_reuse_wait_still_reported(self):
        class One(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return [
                    {
                        "database_name": "Sales",
                        "recovery_model_desc": "FULL",
                        "log_reuse_wait_desc": "SOMETHING_NEW",
                        "vlf_count": 40,
                        "total_log_mb": 1024.0,
                        "active_vlfs": 30,
                    }
                ]

        (finding,) = vlf.run(Context(One(FIXTURES)))
        self.assertEqual(finding.score, 30)


if __name__ == "__main__":
    unittest.main()
