import os
import unittest

from sqltriage.checks import tempdb
from sqltriage.context import Context
from sqltriage.source import FixtureSource

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class PageKindTests(unittest.TestCase):
    def test_pfs(self):
        self.assertEqual(tempdb.page_kind(1), "PFS")
        self.assertEqual(tempdb.page_kind(8088), "PFS")
        self.assertEqual(tempdb.page_kind(16176), "PFS")

    def test_gam_and_sgam(self):
        self.assertEqual(tempdb.page_kind(2), "GAM")
        self.assertEqual(tempdb.page_kind(511234), "GAM")
        self.assertEqual(tempdb.page_kind(3), "SGAM")
        self.assertEqual(tempdb.page_kind(511235), "SGAM")

    def test_ordinary_page(self):
        self.assertEqual(tempdb.page_kind(1234), "other")
        self.assertEqual(tempdb.page_kind(0), "other")

    def test_parse_resource(self):
        self.assertEqual(tempdb.parse_resource("2:4:1"), (2, 4, 1))
        self.assertIsNone(tempdb.parse_resource("ACCESS_METHODS_DATASET_PARENT"))
        self.assertIsNone(tempdb.parse_resource(None))


class RunTests(unittest.TestCase):
    def test_incident(self):
        (finding,) = tempdb.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.data["allocation_tasks"], 15)
        self.assertEqual(finding.data["other_tasks"], 1)
        self.assertEqual(finding.data["longest_wait_ms"], 31)
        self.assertTrue(finding.evidence[0].startswith("PFS   2:1:1"))
        self.assertGreaterEqual(finding.score, 65)

    def test_quiet_server(self):
        class Quiet(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return []

        self.assertEqual(tempdb.run(Context(Quiet(FIXTURES))), [])

    def test_non_allocation_waits_score_low(self):
        class Meta(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return [{"session_id": 60, "wait_type": "PAGELATCH_EX", "wait_duration_ms": 3, "resource_description": "2:1:777"}]

        (finding,) = tempdb.run(Context(Meta(FIXTURES)))
        self.assertLess(finding.score, 40)


if __name__ == "__main__":
    unittest.main()
