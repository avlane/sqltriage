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


def files(sizes, cpus=16, percent=False):
    return [
        {"file_id": i + 1, "name": "f%d" % i, "size_mb": s, "growth_mb": 64, "is_percent_growth": int(percent), "cpu_count": cpus}
        for i, s in enumerate(sizes)
    ]


class LayoutTests(unittest.TestCase):
    def test_good_layout(self):
        self.assertIsNone(tempdb.layout_finding(files([1024] * 8)))

    def test_few_cores_few_files_is_fine(self):
        self.assertIsNone(tempdb.layout_finding(files([1024] * 4, cpus=4)))

    def test_single_file_on_a_big_box(self):
        finding = tempdb.layout_finding(files([4096]))
        self.assertEqual(finding.score, 35)
        self.assertIn("only 1 data file(s) for 16 CPUs", finding.title)

    def test_uneven_sizes(self):
        finding = tempdb.layout_finding(files([8192, 1024, 1024, 1024, 8192, 1024, 1024, 1024]))
        self.assertEqual(finding.score, 40)
        self.assertIn("largest file is 8.0x the smallest", finding.evidence)

    def test_both_problems_and_percent_growth(self):
        finding = tempdb.layout_finding(files([8192, 1024], percent=True))
        self.assertEqual(finding.score, 35 + 30 + 10)

    def test_no_files(self):
        self.assertIsNone(tempdb.layout_finding([]))

    def test_incident(self):
        (finding,) = [f for f in tempdb.run(Context(FixtureSource(FIXTURES))) if "data_files" in f.data]
        self.assertEqual(finding.data["data_files"], 4)
        self.assertEqual(finding.score, 65)


class RunTests(unittest.TestCase):
    def test_incident(self):
        (finding,) = tempdb.contention_findings(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.data["allocation_tasks"], 15)
        self.assertEqual(finding.data["other_tasks"], 1)
        self.assertEqual(finding.data["longest_wait_ms"], 31)
        self.assertTrue(finding.evidence[0].startswith("PFS   2:1:1"))
        self.assertGreaterEqual(finding.score, 65)

    def test_quiet_server(self):
        class Quiet(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return []

        self.assertEqual(tempdb.contention_findings(Context(Quiet(FIXTURES))), [])
        self.assertEqual(tempdb.run(Context(Quiet(FIXTURES))), [])

    def test_non_allocation_waits_score_low(self):
        class Meta(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return [{"session_id": 60, "wait_type": "PAGELATCH_EX", "wait_duration_ms": 3, "resource_description": "2:1:777"}]

        (finding,) = tempdb.contention_findings(Context(Meta(FIXTURES)))
        self.assertLess(finding.score, 40)


if __name__ == "__main__":
    unittest.main()
