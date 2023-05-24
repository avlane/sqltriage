import os
import unittest

from sqltriage.checks import ag
from sqltriage.context import Context
from sqltriage.source import FixtureSource

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


def replica(**overrides):
    row = {
        "ag_name": "AG1",
        "replica_server_name": "R1",
        "role_desc": "SECONDARY",
        "operational_state_desc": "ONLINE",
        "connected_state_desc": "CONNECTED",
        "recovery_health_desc": "ONLINE",
        "synchronization_health_desc": "HEALTHY",
        "availability_mode_desc": "SYNCHRONOUS_COMMIT",
        "failover_mode_desc": "AUTOMATIC",
    }
    row.update(overrides)
    return row


class ProblemTests(unittest.TestCase):
    def test_healthy_replica(self):
        self.assertEqual(ag.replica_problems(replica()), [])

    def test_disconnected_sync_replica_is_worse_than_async(self):
        sync = ag.replica_problems(replica(connected_state_desc="DISCONNECTED"))
        asyn = ag.replica_problems(replica(connected_state_desc="DISCONNECTED", availability_mode_desc="ASYNCHRONOUS_COMMIT"))
        self.assertGreater(max(s for s, _ in sync), max(s for s, _ in asyn))

    def test_null_columns_from_a_secondary_view_are_skipped(self):
        row = replica(
            operational_state_desc=None,
            connected_state_desc=None,
            recovery_health_desc=None,
            synchronization_health_desc=None,
        )
        self.assertEqual(ag.replica_problems(row), [])

    def test_partially_healthy(self):
        (problem,) = ag.replica_problems(replica(synchronization_health_desc="PARTIALLY_HEALTHY"))
        self.assertEqual(problem[0], 55)


class RunTests(unittest.TestCase):
    def test_incident(self):
        (finding,) = ag.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.data, {"ag": "PayrollAG", "replica": "SQLDR02"})
        self.assertEqual(finding.score, 70)
        self.assertIn("replica is DISCONNECTED", finding.evidence)
        self.assertTrue(any("last connect error 10060" in e for e in finding.evidence))

    def test_no_availability_groups(self):
        class Standalone(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return []

        self.assertEqual(ag.run(Context(Standalone(FIXTURES))), [])


if __name__ == "__main__":
    unittest.main()
