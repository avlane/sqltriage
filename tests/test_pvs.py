import os
import unittest

from sqltriage.checks import pvs
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")
GB = 1048576


def row(pvs_gb, data_gb=1000, **extra):
    base = {"database_name": "Sales", "persistent_version_store_size_kb": int(pvs_gb * GB),
            "online_index_version_store_size_kb": 0, "current_aborted_transaction_count": 0,
            "oldest_active_transaction_id": 0, "oldest_aborted_transaction_id": 0, "data_size_kb": data_gb * GB}
    base.update(extra)
    return base


class FindingTests(unittest.TestCase):
    def test_small_store_is_fine(self):
        self.assertIsNone(pvs.pvs_finding(row(2)))
        self.assertIsNone(pvs.pvs_finding(row(9.9, data_gb=1000)))

    def test_by_size(self):
        self.assertEqual(pvs.pvs_finding(row(10)).score, 40)
        self.assertEqual(pvs.pvs_finding(row(120, data_gb=5000)).score, 65)

    def test_by_share_of_the_database(self):
        self.assertEqual(pvs.pvs_finding(row(3, data_gb=20)).score, 40)  # 15%
        self.assertEqual(pvs.pvs_finding(row(3, data_gb=10)).score, 55)  # 30%
        self.assertEqual(pvs.pvs_finding(row(6, data_gb=10)).score, 65)  # 60%

    def test_unknown_data_size_uses_the_size_alone(self):
        self.assertIsNone(pvs.pvs_finding(row(5, data_size_kb=None)))
        self.assertEqual(pvs.pvs_finding(row(15, data_size_kb=None)).score, 40)

    def test_aborted_transactions_and_the_blocker(self):
        finding = pvs.pvs_finding(row(15, current_aborted_transaction_count=3, oldest_active_transaction_id=991))
        self.assertEqual(finding.score, 50)
        self.assertIn("oldest active transaction id 991 is holding back cleanup", finding.evidence)
        self.assertIn("3 aborted transaction(s) are waiting for the version cleaner", finding.evidence)
        self.assertEqual(len(finding.advice), 3)

    def test_online_index_share_is_mentioned(self):
        finding = pvs.pvs_finding(row(15, online_index_version_store_size_kb=2 * GB))
        self.assertIn("2.0 GB of that is for online index builds", finding.evidence)


class RunTests(unittest.TestCase):
    def test_incident(self):
        (finding,) = pvs.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.title, "Persistent version store in PayrollDB is 31 GB")
        self.assertEqual(finding.score, 40)
        self.assertEqual(finding.data["share_pct"], 15.5)

    def test_older_servers_are_not_asked(self):
        class Old(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "pvs_stats":
                    raise AssertionError("the view does not exist before 2019")
                rows = super().fetch(name, sql, params, database)
                if name == "instance_info":
                    rows[0]["major_version"] = 14
                return rows

        self.assertEqual(pvs.run(Context(Old(FIXTURES))), [])

    def test_unknown_version_is_not_asked(self):
        class Silent(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "instance_info":
                    raise SourceError("denied")
                raise AssertionError("should not ask")

        self.assertEqual(pvs.run(Context(Silent(FIXTURES))), [])

    def test_database_filter(self):
        ctx = Context(FixtureSource(FIXTURES), options={"exclude_databases": ["PayrollDB"]})
        self.assertEqual(pvs.run(ctx), [])


if __name__ == "__main__":
    unittest.main()
