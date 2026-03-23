import os
import unittest

from sqltriage.checks import tempdb
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class PageKindTests(unittest.TestCase):
    def test_pfs(self):
        self.assertEqual(tempdb.page_kind(1), "PFS")
        self.assertEqual(tempdb.page_kind(8088), "PFS")
        self.assertEqual(tempdb.page_kind(16176), "PFS")

    def test_gam_and_sgam(self):
        self.assertEqual(tempdb.page_kind(2), "GAM")
        self.assertEqual(tempdb.page_kind(511232), "GAM")
        self.assertEqual(tempdb.page_kind(1022464), "GAM")
        self.assertEqual(tempdb.page_kind(3), "SGAM")
        self.assertEqual(tempdb.page_kind(511233), "SGAM")
        self.assertEqual(tempdb.page_kind(1022465), "SGAM")

    def test_pages_the_old_arithmetic_got_wrong(self):
        # GAM and SGAM follow the 511232 interval from page 0, not from their first page
        self.assertEqual(tempdb.page_kind(511234), "other")
        self.assertEqual(tempdb.page_kind(511235), "other")

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


class PageInfoTests(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(tempdb.describe_page({"page_type_desc": "DATA_PAGE", "object_id": 34}), "DATA_PAGE of system table object_id 34")
        self.assertEqual(tempdb.describe_page({"page_type_desc": "DATA_PAGE", "object_id": -98765}), "DATA_PAGE of temp object_id -98765")
        self.assertEqual(tempdb.describe_page({"page_type_desc": "INDEX_PAGE", "object_id": 5000}), "INDEX_PAGE of object_id 5000")
        self.assertEqual(tempdb.describe_page({"page_type_desc": "DATA_PAGE", "object_id": 7, "object_name": "sysallocunits"}), "DATA_PAGE of object sysallocunits")
        self.assertEqual(tempdb.describe_page({}), "page of an unknown object")

    def test_incident_names_the_other_page(self):
        (finding,) = tempdb.contention_findings(Context(FixtureSource(FIXTURES)))
        self.assertIn("other 2:1:1234  1 task(s) waiting, longest 4 ms - DATA_PAGE of system table object_id 34", finding.evidence)
        self.assertFalse(any(" - " in e for e in finding.evidence if e.startswith(("PFS", "GAM", "SGAM"))))

    def test_old_servers_are_not_asked(self):
        class Old(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "instance_info":
                    return [{"major_version": 14, "product_version": "14.0.1", "edition": "x", "cpu_count": 4,
                             "sqlserver_start_time": "2025-03-01T00:00:00", "server_name": "S", "is_hadr_enabled": 0}]
                if name.startswith("tempdb_page_info"):
                    raise AssertionError("dm_db_page_info does not exist before 2019")
                return super().fetch(name, sql, params, database)

        (finding,) = tempdb.contention_findings(Context(Old(FIXTURES)))
        self.assertTrue(any(e.startswith("other") for e in finding.evidence))

    def test_unknown_version_is_not_asked(self):
        class Silent(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "instance_info":
                    raise SourceError("denied")
                if name.startswith("tempdb_page_info"):
                    raise AssertionError("should not ask")
                return super().fetch(name, sql, params, database)

        tempdb.contention_findings(Context(Silent(FIXTURES)))

    def test_lookup_failure_is_not_fatal(self):
        class Failing(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name.startswith("tempdb_page_info"):
                    raise SourceError("denied")
                return super().fetch(name, sql, params, database)

        (finding,) = tempdb.contention_findings(Context(Failing(FIXTURES)))
        self.assertTrue(any(e.startswith("other 2:1:1234  1 task(s) waiting, longest 4 ms") and " - " not in e for e in finding.evidence))


class MetadataAdviceTests(unittest.TestCase):
    SYSTEM = ["DATA_PAGE of system table object_id 34"]

    def ctx(self, major=15, enabled=0):
        class Fake(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                rows = super().fetch(name, sql, params, database)
                if name == "instance_info":
                    rows[0].update(major_version=major, tempdb_metadata_memory_optimized=enabled)
                return rows

        return Context(Fake(FIXTURES))

    def test_advised_on_2019_when_system_tables_are_hot(self):
        self.assertIn("MEMORY_OPTIMIZED TEMPDB_METADATA = ON", tempdb.metadata_advice(self.ctx(), self.SYSTEM))

    def test_not_if_already_on(self):
        self.assertIsNone(tempdb.metadata_advice(self.ctx(enabled=1), self.SYSTEM))

    def test_not_before_2019(self):
        self.assertIsNone(tempdb.metadata_advice(self.ctx(major=14, enabled=None), self.SYSTEM))

    def test_not_for_ordinary_pages(self):
        self.assertIsNone(tempdb.metadata_advice(self.ctx(), ["INDEX_PAGE of object_id 5000"]))
        self.assertIsNone(tempdb.metadata_advice(self.ctx(), []))

    def test_unknown_version(self):
        class Silent(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "instance_info":
                    raise SourceError("denied")
                return super().fetch(name, sql, params, database)

        self.assertIsNone(tempdb.metadata_advice(Context(Silent(FIXTURES)), self.SYSTEM))

    def test_only_metadata_contention_gets_it_in_the_finding(self):
        # the incident is mostly allocation contention, with a single system table page
        (finding,) = tempdb.contention_findings(self.ctx())
        self.assertFalse(any("TEMPDB_METADATA" in a for a in finding.advice[:2]))
        self.assertTrue(any("TEMPDB_METADATA" in a for a in finding.advice))


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
        findings = tempdb.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual(len(findings), 3)
        (finding,) = [f for f in findings if "data_files" in f.data]
        self.assertEqual(finding.data["data_files"], 4)
        self.assertEqual(finding.score, 65)


class SpaceTests(unittest.TestCase):
    def finding(self, row, by_db=None):
        class Fake(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "tempdb_space":
                    return [row]
                if name == "tempdb_version_store":
                    return by_db or []
                return []

        return tempdb.space_finding(Context(Fake(FIXTURES)))

    def test_plenty_of_space(self):
        row = {"total_mb": 10000, "free_mb": 6000, "user_objects_mb": 1, "internal_objects_mb": 1, "version_store_mb": 1}
        self.assertIsNone(self.finding(row))

    def test_levels(self):
        base = {"total_mb": 1000, "user_objects_mb": 1, "internal_objects_mb": 1, "version_store_mb": 1}
        self.assertEqual(self.finding(dict(base, free_mb=190)).score, 45)
        self.assertEqual(self.finding(dict(base, free_mb=100)).score, 70)
        self.assertEqual(self.finding(dict(base, free_mb=49)).score, 90)

    def test_internal_objects_advice(self):
        row = {"total_mb": 1000, "free_mb": 40, "user_objects_mb": 10, "internal_objects_mb": 900, "version_store_mb": 5}
        finding = self.finding(row)
        self.assertEqual(finding.data["largest_use"], "internal objects (sorts, hashes, spools)")
        self.assertIn("spilling", finding.advice[0])

    def test_empty_result(self):
        class Empty(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                return []

        self.assertIsNone(tempdb.space_finding(Context(Empty(FIXTURES))))

    def test_incident_blames_the_version_store(self):
        finding = tempdb.space_finding(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.title, "tempdb is 90% full")
        self.assertEqual(finding.score, 70)
        self.assertEqual(finding.data["largest_use"], "version store")
        self.assertIn("version store: PayrollDB holds 7100 MB", finding.evidence)


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
                if name != "tempdb_latch_waits":
                    return []
                return [{"session_id": 60, "wait_type": "PAGELATCH_EX", "wait_duration_ms": 3, "resource_description": "2:1:777"}]

        (finding,) = tempdb.contention_findings(Context(Meta(FIXTURES)))
        self.assertLess(finding.score, 40)


if __name__ == "__main__":
    unittest.main()
