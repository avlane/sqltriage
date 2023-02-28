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


class RunTests(unittest.TestCase):
    def test_incident(self):
        findings = querystore.run(Context(FixtureSource(FIXTURES)))
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
        self.assertEqual(len(findings), 3)
        self.assertEqual(findings[-1].title, "Query Store could not be read in ReportsDB")

    def test_long_text_is_shortened(self):
        self.assertEqual(len(querystore.shorten("SELECT " + "a, " * 500)), 200)


if __name__ == "__main__":
    unittest.main()
