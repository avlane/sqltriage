import unittest

from sqltriage.context import Context
from sqltriage.source import Source


class JobIgnoredTests(unittest.TestCase):
    def test_patterns(self):
        ctx = Context(Source(), options={"ignore_jobs": ["Purge*", "*TEST*"]})
        self.assertTrue(ctx.job_ignored("purge old rows"))
        self.assertTrue(ctx.job_ignored("my test job"))
        self.assertFalse(ctx.job_ignored("Nightly ETL"))

    def test_nothing_ignored_by_default(self):
        self.assertFalse(Context(Source()).job_ignored("anything"))


class WantsDatabaseTests(unittest.TestCase):
    def ctx(self, **options):
        return Context(Source(), options=options)

    def test_everything_by_default(self):
        self.assertTrue(self.ctx().wants_database("Anything"))

    def test_only(self):
        ctx = self.ctx(databases=["Sales", "hr"])
        self.assertTrue(ctx.wants_database("sales"))
        self.assertTrue(ctx.wants_database("HR"))
        self.assertFalse(ctx.wants_database("Other"))

    def test_exclude_wins(self):
        ctx = self.ctx(databases=["Sales"], exclude_databases=["sales"])
        self.assertFalse(ctx.wants_database("Sales"))


if __name__ == "__main__":
    unittest.main()
