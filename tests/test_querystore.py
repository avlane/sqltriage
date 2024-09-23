import os
import unittest

from sqltriage.checks import querystore
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class SqlTests(unittest.TestCase):
    def test_defaults_are_formatted_into_the_query(self):
        sql = querystore.regression_sql(Context(FixtureSource(FIXTURES)))
        self.assertIn("DATEADD(HOUR, -1, @now)", sql)
        self.assertIn("DATEADD(HOUR, -25, @now)", sql)
        self.assertIn("SELECT TOP (5)", sql)
        self.assertIn("> 2.00 * ", sql)
        self.assertNotIn("{", sql)

    def test_options_override(self):
        ctx = Context(FixtureSource(FIXTURES), options={"qs_top": 3, "qs_ratio": 1.5, "qs_min_execs": 20})
        sql = querystore.regression_sql(ctx)
        self.assertIn("SELECT TOP (3)", sql)
        self.assertIn("> 1.50 * ", sql)
        self.assertIn(">= 20", sql)


class ScoreTests(unittest.TestCase):
    def test_bigger_slowdowns_and_more_lost_time_score_higher(self):
        self.assertLess(querystore.regression_score(2.0, 10), querystore.regression_score(4.0, 10))
        self.assertLess(querystore.regression_score(3.0, 10), querystore.regression_score(3.0, 600))

    def test_capped(self):
        self.assertLessEqual(querystore.regression_score(500.0, 1e6), 95)


def state(actual="READ_WRITE", mask=0, used=100, maximum=1000, desired="READ_WRITE"):
    return {"actual_state_desc": actual, "desired_state_desc": desired, "readonly_reason": mask,
            "current_storage_size_mb": used, "max_storage_size_mb": maximum}


class StateTests(unittest.TestCase):
    def test_healthy(self):
        self.assertIsNone(querystore.state_finding("Sales", state()))

    def test_storage_nearly_full(self):
        finding = querystore.state_finding("Sales", state(used=950))
        self.assertEqual(finding.score, 40)
        self.assertEqual(finding.evidence, ["storage is 95% used (950 of 1000 MB)"])

    def test_read_only_with_reasons(self):
        finding = querystore.state_finding("Sales", state("READ_ONLY", 65536 | 1, used=1000))
        self.assertEqual(finding.score, 55)
        self.assertIn(
            "Query Store is READ_ONLY because the database is in read-only mode and the maximum storage size was reached",
            finding.evidence,
        )

    def test_read_only_for_unknown_reason(self):
        finding = querystore.state_finding("Sales", state("READ_ONLY", 1 << 30))
        self.assertIn("not one this tool knows", finding.evidence[0])

    def test_secondary_replica_is_expected(self):
        self.assertIsNone(querystore.state_finding("Sales", state("READ_ONLY", 8)))

    def test_error_state(self):
        self.assertEqual(querystore.state_finding("Sales", state("ERROR")).score, 70)

    def test_off_is_not_reported(self):
        self.assertIsNone(querystore.state_finding("Sales", state("OFF", used=1000)))

    def test_zero_max_size(self):
        self.assertIsNone(querystore.state_finding("Sales", state(used=5, maximum=0)))

    def test_incident_flags_only_the_nearly_full_one(self):
        findings = [f for f in querystore.run(Context(FixtureSource(FIXTURES))) if "state" in f.data]
        self.assertEqual([(f.data["database"], f.score) for f in findings], [("ReportsDB", 40)])


class WaitTests(unittest.TestCase):
    def test_breakdown(self):
        shares = querystore.wait_breakdown([
            {"wait_category_desc": "Lock", "total_wait_ms": 300},
            {"wait_category_desc": "CPU", "total_wait_ms": 100},
        ])
        self.assertEqual(shares, [("Lock", 0.75), ("CPU", 0.25)])
        self.assertEqual(querystore.wait_breakdown([]), [])
        self.assertEqual(querystore.wait_breakdown([{"wait_category_desc": "CPU", "total_wait_ms": 0}]), [])

    def test_blocking_is_blamed_and_the_score_drops(self):
        findings = [f for f in querystore.run(Context(FixtureSource(FIXTURES))) if "ratio" in f.data]
        blocked = findings[0]
        self.assertEqual(blocked.data["query_id"], 4412)
        self.assertEqual(blocked.evidence[2], "recent waits: Lock 91%, Tran Log IO 5%, Buffer IO 4%")
        self.assertIn("slow because it is blocked", blocked.advice[0])
        plain = querystore.regression_finding("PayrollDB", FixtureSource(FIXTURES).fetch("querystore_regressions", "", database="PayrollDB")[0])
        self.assertEqual(plain.score - blocked.score, 20)

    def test_other_dominant_wait_leaves_the_score_alone(self):
        row = FixtureSource(FIXTURES).fetch("querystore_regressions", "", database="PayrollDB")[0]
        waits = [{"wait_category_desc": "CPU", "total_wait_ms": 900}, {"wait_category_desc": "Lock", "total_wait_ms": 100}]
        with_waits = querystore.regression_finding("PayrollDB", row, waits)
        without = querystore.regression_finding("PayrollDB", row)
        self.assertEqual(with_waits.score, without.score)
        self.assertIn("recent waits: CPU 90%, Lock 10%", with_waits.evidence)

    def test_query_without_wait_stats(self):
        findings = [f for f in querystore.run(Context(FixtureSource(FIXTURES))) if "ratio" in f.data]
        self.assertFalse(any("recent waits" in e for e in findings[1].evidence))

    def test_sql_uses_the_query_and_window(self):
        ctx = Context(FixtureSource(FIXTURES), options={"qs_recent_hours": 2})
        seen = []

        class Spy(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                seen.append(sql)
                return []

        querystore.recent_waits(Context(Spy(FIXTURES), options=ctx.options), "Sales", 77)
        self.assertIn("p.query_id = 77", seen[0])
        self.assertIn("DATEADD(HOUR, -2, SYSDATETIMEOFFSET())", seen[0])


class ForcedPlanTests(unittest.TestCase):
    def findings(self):
        return [f for f in querystore.run(Context(FixtureSource(FIXTURES))) if "failures" in f.data]

    def test_failures_are_reported_with_the_reason(self):
        worst, minor = self.findings()
        self.assertEqual(worst.data["plan_id"], 55)
        self.assertEqual(worst.data["reason"], "NO_INDEX")
        self.assertIn("index the forced plan uses no longer exists", worst.evidence[0])
        self.assertIn("usp_EmployeeYtd", worst.title)
        self.assertGreater(worst.score, minor.score)

    def test_unknown_reason(self):
        row = {"plan_id": 1, "query_id": 2, "force_failure_count": 1, "last_force_failure_reason_desc": "BRAND_NEW",
               "object_name": None, "query_sql_text": "SELECT 1"}
        finding = querystore.force_failure_finding("Sales", row)
        self.assertIn("BRAND_NEW", finding.evidence[0])

    def test_score_is_capped(self):
        row = {"plan_id": 1, "query_id": 2, "force_failure_count": 100000, "last_force_failure_reason_desc": "NO_PLAN",
               "object_name": None, "query_sql_text": "SELECT 1"}
        self.assertEqual(querystore.force_failure_finding("Sales", row).score, 80)


class RunTests(unittest.TestCase):
    def test_incident(self):
        findings = [f for f in querystore.run(Context(FixtureSource(FIXTURES))) if "ratio" in f.data]
        self.assertEqual([f.data["query_id"] for f in findings], [4412, 5120])
        worst = findings[0]
        self.assertEqual(worst.data["ratio"], 3.82)
        self.assertIn("usp_PayRunTotals", worst.title)
        self.assertIn("3.8x slower", worst.evidence[0])
        self.assertGreater(worst.score, findings[1].score)
        self.assertIn("ad hoc query", findings[1].title)

    def test_one_unreadable_database_does_not_hide_the_others(self):
        class Flaky(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                if database == "ReportsDB":
                    raise SourceError("The server principal is not able to access the database")
                return super().fetch(name, sql, params, database)

        findings = querystore.run(Context(Flaky(FIXTURES)))
        self.assertEqual(len(findings), 5)
        self.assertEqual(findings[-1].title, "Query Store could not be read in ReportsDB")

    def test_long_text_is_shortened(self):
        self.assertEqual(len(querystore.shorten("SELECT " + "a, " * 500)), 200)


if __name__ == "__main__":
    unittest.main()
