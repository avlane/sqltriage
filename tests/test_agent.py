import os
import unittest

from sqltriage.checks import agent
from sqltriage.context import Context
from sqltriage.source import FixtureSource

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class DurationTests(unittest.TestCase):
    def test_hhmmss(self):
        self.assertEqual(hhmmss(5), 5)
        self.assertEqual(hhmmss(130), 90)
        self.assertEqual(hhmmss(13005), 5405)
        self.assertEqual(hhmmss(None), 0)


def hhmmss(value):
    return agent.hhmmss_to_seconds(value)


class FailedJobTests(unittest.TestCase):
    def test_grouped_per_job(self):
        findings = agent.run(Context(FixtureSource(FIXTURES)))
        by_job = {f.data["job"]: f for f in findings}
        self.assertEqual(sorted(by_job), ["DatabaseBackup - USER_DATABASES - LOG", "Nightly ETL Load"])
        self.assertEqual(by_job["DatabaseBackup - USER_DATABASES - LOG"].data["failures"], 3)

    def test_log_backup_failures_outrank_an_etl_failure(self):
        by_job = {f.data["job"]: f for f in agent.run(Context(FixtureSource(FIXTURES)))}
        self.assertGreater(by_job["DatabaseBackup - USER_DATABASES - LOG"].score, by_job["Nightly ETL Load"].score)

    def test_message_and_duration_in_evidence(self):
        by_job = {f.data["job"]: f for f in agent.run(Context(FixtureSource(FIXTURES)))}
        evidence = by_job["Nightly ETL Load"].evidence
        self.assertTrue(any("step 3 (Load Fact Tables)" in line for line in evidence))
        self.assertTrue(any("ran for 4540s" in line for line in evidence))

    def test_window_is_part_of_the_query(self):
        seen = []

        class Spy(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                seen.append(sql)
                return []

        ctx = Context(Spy(FIXTURES), options={"agent_hours": 6})
        agent.run(ctx)
        self.assertIn("DATEADD(HOUR, -6, GETDATE())", seen[0])

    def test_score_is_capped(self):
        self.assertEqual(agent.failure_score("Nightly ETL Load", 1), 35)
        self.assertLessEqual(agent.failure_score("Log Backup", 50), 90)


if __name__ == "__main__":
    unittest.main()
