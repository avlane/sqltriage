import unittest

from sqltriage.context import Context
from sqltriage.source import Source


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
