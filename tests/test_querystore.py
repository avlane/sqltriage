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


class BaselineTests(unittest.TestCase):
    def row(self, history_us):
        return {"query_id": 1, "query_sql_text": "SELECT 1", "object_name": None, "recent_execs": 50, "history_execs": 500,
                "recent_avg_duration_us": 40000.0, "history_avg_duration_us": history_us, "plan_count": 2}

    def test_zero_baseline_does_not_give_infinity(self):
        finding = querystore.regression_finding("Sales", self.row(0.0))
        self.assertEqual(finding.data["ratio"], 40.0)
        self.assertNotIn("inf", finding.title)
        self.assertLessEqual(finding.score, 95)

    def test_normal_baseline_is_untouched(self):
        self.assertEqual(querystore.regression_finding("Sales", self.row(10000.0)).data["ratio"], 4.0)

    def test_minimum_slowdown_is_in_the_query(self):
        sql = querystore.regression_sql(Context(FixtureSource(FIXTURES)))
        self.assertIn(">= 5000", sql)
        sql = querystore.regression_sql(Context(FixtureSource(FIXTURES), options={"qs_min_delta_ms": 0.5}))
        self.assertIn(">= 500", sql)


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


class TuningTests(unittest.TestCase):
    def rec(self, query_id, score, recommended=2):
        return {"query_id": query_id, "score": score, "regressed_plan_id": 1, "recommended_plan_id": recommended,
                "reason": "CPU time changed", "script": "exec sp_query_store_force_plan {}, {};".format(query_id, recommended)}

    def test_best_recommendation_per_query(self):
        best = querystore.tuning_by_query([self.rec(7, 50, 2), self.rec(7, 90, 3), self.rec(8, 10), {"query_id": None, "score": 99}])
        self.assertEqual(sorted(best), [7, 8])
        self.assertEqual(best[7]["recommended_plan_id"], 3)

    def test_recommendation_is_attached_to_the_regression(self):
        findings = [f for f in querystore.run(Context(FixtureSource(FIXTURES))) if "ratio" in f.data]
        first, second = findings
        self.assertIn("the server recommends forcing plan 91 instead of 112 (score 87): Average query CPU time changed from 508.55ms to 1948.17ms", first.evidence)
        self.assertIn("Run: exec sp_query_store_force_plan 4412, 91;", first.advice)
        self.assertFalse(any("recommends" in e for e in second.evidence))

    def test_no_recommendation_leaves_the_finding_alone(self):
        row = FixtureSource(FIXTURES).fetch("querystore_regressions", "", database="PayrollDB")[0]
        self.assertEqual(
            querystore.regression_finding("PayrollDB", row).evidence,
            querystore.regression_finding("PayrollDB", row, (), None).evidence,
        )

    def test_old_servers_without_the_view(self):
        class Old(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "querystore_tuning":
                    raise SourceError("Invalid object name 'sys.dm_db_tuning_recommendations'")
                return super().fetch(name, sql, params, database)

        findings = [f for f in querystore.run(Context(Old(FIXTURES))) if "ratio" in f.data]
        self.assertEqual(len(findings), 2)
        self.assertFalse(any("recommends" in e for f in findings for e in f.evidence))

    def test_recommendation_without_a_script(self):
        rec = self.rec(4412, 80)
        rec["script"] = None
        row = FixtureSource(FIXTURES).fetch("querystore_regressions", "", database="PayrollDB")[0]
        finding = querystore.regression_finding("PayrollDB", row, (), rec)
        self.assertFalse(any(a.startswith("Run:") for a in finding.advice))


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

    def test_manual_and_automatic_forcing_are_told_apart(self):
        worst, minor = self.findings()
        self.assertIn("this plan was forced manually", worst.evidence)
        self.assertIn("this plan was forced by automatic tuning, not by a person", minor.evidence)
        self.assertIn("un-forces one that keeps failing", minor.advice[1])

    def test_automatic_tuning_scores_lower(self):
        base = {"plan_id": 1, "query_id": 2, "force_failure_count": 120, "last_force_failure_reason_desc": "NO_INDEX",
                "object_name": None, "query_sql_text": "SELECT 1"}
        manual = querystore.force_failure_finding("Sales", dict(base, plan_forcing_type_desc="MANUAL"))
        auto = querystore.force_failure_finding("Sales", dict(base, plan_forcing_type_desc="AUTO"))
        unknown = querystore.force_failure_finding("Sales", dict(base, plan_forcing_type_desc=None))
        self.assertEqual((manual.score, auto.score, unknown.score), (57, 47, 57))
        self.assertFalse(any("forced" in e and "plan was" in e for e in unknown.evidence))

    def test_the_column_is_only_selected_where_it_exists(self):
        def sql_for(major):
            class Fake(FixtureSource):
                def fetch(inner, name, sql, params=(), database=None):
                    rows = super().fetch(name, sql, params, database)
                    if name == "instance_info":
                        rows[0]["major_version"] = major
                    return rows

            return querystore.forced_failures_sql(Context(Fake(FIXTURES)))

        self.assertIn("p.plan_forcing_type_desc AS plan_forcing_type_desc", sql_for(14))
        self.assertIn("p.plan_forcing_type_desc AS plan_forcing_type_desc", sql_for(16))
        self.assertIn("CAST(NULL AS nvarchar(60)) AS plan_forcing_type_desc", sql_for(13))
        self.assertNotIn("{", sql_for(13))

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
