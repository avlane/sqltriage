import os
import unittest

from sqltriage.checks import vlf
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class LogBackupTests(unittest.TestCase):
    def stats(self, minutes, since=100.0, total=1000.0):
        class Fake(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                return [{"database_name": "Sales", "minutes_since_log_backup": minutes, "log_since_last_log_backup_mb": since,
                         "total_log_size_mb": total, "active_log_size_mb": since}]

        return vlf.log_backup_findings(Context(Fake(FIXTURES)))

    def test_recent_backup_is_fine(self):
        self.assertEqual(self.stats(20), [])

    def test_levels(self):
        (hour,) = self.stats(75)
        (long_ago,) = self.stats(300)
        self.assertEqual((hour.score, long_ago.score), (40, 60))
        self.assertEqual(hour.title, "Log backups: Sales last one was 1h15m ago")

    def test_never_backed_up(self):
        (finding,) = self.stats(None)
        self.assertEqual(finding.score, 70)
        self.assertEqual(finding.title, "Log backups: Sales has never had one")

    def test_nearly_all_unbacked_up_log_scores_higher(self):
        (finding,) = self.stats(75, since=900.0)
        self.assertEqual(finding.score, 55)
        self.assertIn("the log is nearly all un-backed-up and will have to grow or fill", finding.evidence)

    def test_incident(self):
        findings = vlf.log_backup_findings(Context(FixtureSource(FIXTURES)))
        self.assertEqual([(f.data["database"], f.score) for f in findings], [("PayrollDB", 40 + 0), ("ReportsDB", 60 + 15)])
        self.assertEqual(findings[1].evidence[1], "21,500 MB of log since then, 87% of the 24,576 MB log")

    def test_unreadable(self):
        class Denied(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                raise SourceError("denied")

        self.assertEqual(vlf.log_backup_findings(Context(Denied(FIXTURES))), [])

    def test_database_filter(self):
        ctx = Context(FixtureSource(FIXTURES), options={"databases": ["ReportsDB"]})
        self.assertEqual([f.data["database"] for f in vlf.log_backup_findings(ctx)], ["ReportsDB"])


class VlfTests(unittest.TestCase):
    def test_query_leaves_out_snapshots_and_inaccessible_databases(self):
        self.assertIn("d.source_database_id IS NULL", vlf.SQL)
        self.assertIn("HAS_DBACCESS(d.name) = 1", vlf.SQL)

    def test_score_levels(self):
        self.assertEqual(vlf.vlf_score(12), 0)
        self.assertEqual(vlf.vlf_score(500), 20)
        self.assertEqual(vlf.vlf_score(1000), 45)
        self.assertEqual(vlf.vlf_score(5000), 65)
        self.assertEqual(vlf.vlf_score(20000), 80)

    def test_incident(self):
        findings = vlf.log_findings(Context(FixtureSource(FIXTURES)))
        by_db = {f.data["database"]: f for f in findings}
        self.assertEqual(sorted(by_db), ["PayrollDB", "ReportsDB", "Staging"])
        payroll = by_db["PayrollDB"]
        self.assertEqual(payroll.severity, "critical")
        self.assertIn("18,432 VLFs", payroll.title)
        self.assertIn("ACTIVE_TRANSACTION", payroll.title)

    def test_growth_is_blamed_when_it_is_the_cause(self):
        findings = vlf.log_findings(Context(FixtureSource(FIXTURES)))
        by_db = {f.data["database"]: f for f in findings}
        self.assertIn("log autogrowth is 10%, which is how it got this many", by_db["PayrollDB"].evidence)
        self.assertIn("log autogrowth is 64 MB, which is how it got this many", by_db["ReportsDB"].evidence)
        self.assertFalse(any("autogrowth" in e for e in by_db["Staging"].evidence))
        self.assertIn("Set a fixed growth increment", " ".join(by_db["PayrollDB"].advice))

    def test_growth_notes(self):
        notes = vlf.growth_notes(
            [
                {"database_name": "A", "size_mb": 100, "growth_value": 10, "is_percent_growth": 1},
                {"database_name": "B", "size_mb": 100, "growth_value": 64, "is_percent_growth": 0},
                {"database_name": "C", "size_mb": 10240, "growth_value": 64, "is_percent_growth": 0},
                {"database_name": "D", "size_mb": 10240, "growth_value": 4096, "is_percent_growth": 0},
            ]
        )
        self.assertEqual(notes["A"], ("log autogrowth is 10%", True))
        self.assertEqual(notes["B"], ("log autogrowth is 64 MB", False))  # small log, small growth is fine
        self.assertTrue(notes["C"][1])
        self.assertFalse(notes["D"][1])

    def test_worst_file_wins(self):
        notes = vlf.growth_notes(
            [
                {"database_name": "A", "size_mb": 10240, "growth_value": 4096, "is_percent_growth": 0},
                {"database_name": "A", "size_mb": 10240, "growth_value": 10, "is_percent_growth": 1},
            ]
        )
        self.assertEqual(notes["A"], ("log autogrowth is 10%", True))

    def test_missing_growth_information_is_not_fatal(self):
        class NoGrowth(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "vlf_growth":
                    raise SourceError("denied")
                return super().fetch(name, sql, params, database)

        self.assertEqual(len(vlf.log_findings(Context(NoGrowth(FIXTURES)))), 3)

    def test_reuse_wait_alone_is_reported(self):
        class One(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                if name != "vlf_counts":
                    return []
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

        (finding,) = vlf.log_findings(Context(One(FIXTURES)))
        self.assertEqual(finding.score, 50)
        self.assertEqual(finding.title, "Log: Sales has log reuse wait LOG_BACKUP")

    def test_unknown_reuse_wait_still_reported(self):
        class One(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                if name != "vlf_counts":
                    return []
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

        (finding,) = vlf.log_findings(Context(One(FIXTURES)))
        self.assertEqual(finding.score, 30)


if __name__ == "__main__":
    unittest.main()
