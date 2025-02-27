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
        (finding,) = ag.replica_findings(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.data, {"ag": "PayrollAG", "replica": "SQLDR02"})
        self.assertEqual(finding.score, 70)
        self.assertIn("replica is DISCONNECTED", finding.evidence)
        self.assertTrue(any("last connect error 10060" in e for e in finding.evidence))

    def test_no_availability_groups(self):
        class Standalone(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return []

        self.assertEqual(ag.run(Context(Standalone(FIXTURES))), [])


def database(**overrides):
    row = {
        "ag_name": "AG1",
        "replica_server_name": "R2",
        "database_name": "Sales",
        "synchronization_state_desc": "SYNCHRONIZED",
        "synchronization_health_desc": "HEALTHY",
        "is_suspended": 0,
        "suspend_reason_desc": None,
        "log_send_queue_size": 0,
        "log_send_rate": 1000,
        "redo_queue_size": 0,
        "redo_rate": 1000,
        "secondary_lag_seconds": 0,
    }
    row.update(overrides)
    return row


class DatabaseProblemTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(ag.database_problems(database()), [])

    def test_small_queues_are_ignored(self):
        self.assertEqual(ag.database_problems(database(log_send_queue_size=2048, redo_queue_size=4096)), [])

    def test_send_queue_with_estimate(self):
        ((score, text),) = ag.database_problems(database(log_send_queue_size=204800, log_send_rate=1000))
        self.assertEqual(score, 50)
        self.assertEqual(text, "log send queue 200 MB, about 3 min at the current rate")

    def test_stalled_queue_has_no_estimate(self):
        ((score, text),) = ag.database_problems(database(log_send_queue_size=4200000, log_send_rate=None))
        self.assertEqual(score, 70)
        self.assertEqual(text, "log send queue 4.0 GB, no movement, so no estimate")

    def test_redo_scores_a_little_below_send(self):
        ((score, _),) = ag.database_problems(database(redo_queue_size=2000000))
        self.assertEqual(score, 65)

    def test_suspended(self):
        ((score, text),) = ag.database_problems(database(is_suspended=1, suspend_reason_desc="SUSPEND_FROM_USER"))
        self.assertEqual(score, 75)
        self.assertIn("SUSPEND_FROM_USER", text)

    def test_lag(self):
        ((score, _),) = ag.database_problems(database(secondary_lag_seconds=700))
        self.assertEqual(score, 70)


class EstimateTests(unittest.TestCase):
    def test_data_loss(self):
        row = database(last_commit_time="2025-02-25T10:00:00", primary_last_commit_time="2025-02-25T10:07:30")
        self.assertEqual(ag.estimated_data_loss(row), 450)

    def test_data_loss_accepts_datetimes_and_never_goes_negative(self):
        import datetime

        early = datetime.datetime(2025, 2, 25, 10, 0, 0)
        row = database(last_commit_time=early + datetime.timedelta(seconds=5), primary_last_commit_time=early)
        self.assertEqual(ag.estimated_data_loss(row), 0)

    def test_data_loss_unknown_without_a_primary_time(self):
        self.assertIsNone(ag.estimated_data_loss(database(last_commit_time="2025-02-25T10:00:00")))
        self.assertIsNone(ag.estimated_data_loss(database(primary_last_commit_time="2025-02-25T10:00:00")))

    def test_recovery(self):
        self.assertEqual(ag.estimated_recovery(database(redo_queue_size=120000, redo_rate=40)), 3000)
        self.assertEqual(ag.estimated_recovery(database(redo_queue_size=0, redo_rate=None)), 0)
        self.assertIsNone(ag.estimated_recovery(database(redo_queue_size=500, redo_rate=None)))


class DatabaseFindingTests(unittest.TestCase):
    def test_incident(self):
        findings = ag.database_findings(Context(FixtureSource(FIXTURES)))
        keys = [(f.data["replica"], f.data["database"]) for f in findings]
        self.assertEqual(keys, [("SQLPROD02", "ReportsDB"), ("SQLDR02", "PayrollDB")])
        self.assertEqual([f.score for f in findings], [75, 70])
        self.assertIn("synchronization state NOT SYNCHRONIZING (NOT_HEALTHY)", findings[1].evidence)
        dr = findings[1]
        self.assertIn("a failover to this replica now would lose about 1h30m", dr.evidence)
        self.assertIn("it would need about 50m to finish redo before the database is available", dr.evidence)
        self.assertEqual((dr.data["estimated_data_loss_s"], dr.data["estimated_recovery_s"]), (5400, 3000))
        self.assertTrue(any("would lose about 38m" in e for e in findings[0].evidence))


if __name__ == "__main__":
    unittest.main()
