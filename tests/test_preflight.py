import os
import unittest

from sqltriage import preflight
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class Serve(FixtureSource):
    def __init__(self, **row):
        super().__init__(FIXTURES)
        self.row = {"server_name": "S1", "view_server_state": 1, "view_server_performance_state": None,
                    "view_any_definition": 1, "msdb_access": 1}
        self.row.update(row)

    def fetch(self, name, sql, params=(), database=None):
        return [self.row]


class PreflightTests(unittest.TestCase):
    def test_everything_granted(self):
        ok, lines = preflight.run(FixtureSource(FIXTURES))
        self.assertTrue(ok)
        self.assertEqual(lines[0], "connected to SQLPROD01")
        self.assertTrue(all(l.startswith(("ok:", "connected")) for l in lines))

    def test_missing_server_state_is_fatal(self):
        ok, lines = preflight.run(Serve(view_server_state=0))
        self.assertFalse(ok)
        self.assertTrue(any(l.startswith("MISSING: VIEW SERVER STATE") for l in lines))

    def test_the_narrower_2022_grant_is_enough(self):
        ok, _ = preflight.run(Serve(view_server_state=0, view_server_performance_state=1))
        self.assertTrue(ok)

    def test_missing_definition_and_msdb_are_warnings(self):
        ok, lines = preflight.run(Serve(view_any_definition=0, msdb_access=0))
        self.assertTrue(ok)
        self.assertIn("missing: VIEW ANY DEFINITION; object names in some findings will be blank", lines)
        self.assertIn("missing: access to msdb; the Agent checks will fail", lines)

    def test_cannot_connect(self):
        class Down(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                raise SourceError("preflight: Login timeout expired")

        ok, lines = preflight.run(Down(FIXTURES))
        self.assertFalse(ok)
        self.assertEqual(lines, ["could not connect or run a query: preflight: Login timeout expired"])


if __name__ == "__main__":
    unittest.main()
