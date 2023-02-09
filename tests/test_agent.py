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
        findings = agent.failed_job_findings(Context(FixtureSource(FIXTURES)))
        by_job = {f.data["job"]: f for f in findings}
        self.assertEqual(sorted(by_job), ["DatabaseBackup - USER_DATABASES - LOG", "Nightly ETL Load"])
        self.assertEqual(by_job["DatabaseBackup - USER_DATABASES - LOG"].data["failures"], 3)

    def test_log_backup_failures_outrank_an_etl_failure(self):
        by_job = {f.data["job"]: f for f in agent.failed_job_findings(Context(FixtureSource(FIXTURES)))}
        self.assertGreater(by_job["DatabaseBackup - USER_DATABASES - LOG"].score, by_job["Nightly ETL Load"].score)

    def test_message_and_duration_in_evidence(self):
        by_job = {f.data["job"]: f for f in agent.failed_job_findings(Context(FixtureSource(FIXTURES)))}
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
        agent.failed_job_findings(ctx)
        self.assertIn("DATEADD(HOUR, -6, GETDATE())", seen[0])

    def test_score_is_capped(self):
        self.assertEqual(agent.failure_score("Nightly ETL Load", 1), 35)
        self.assertLessEqual(agent.failure_score("Log Backup", 50), 90)


class LongRunningTests(unittest.TestCase):
    def test_normal_job_is_not_flagged(self):
        findings = agent.long_running_findings(Context(FixtureSource(FIXTURES)))
        self.assertNotIn("Update Statistics - Reporting", [f.data["job"] for f in findings])

    def test_runaway_and_unbaselined_jobs_are_flagged(self):
        findings = agent.long_running_findings(Context(FixtureSource(FIXTURES)))
        self.assertEqual([f.data["job"] for f in findings], ["IndexOptimize - USER_DATABASES", "Purge Old Audit Rows"])
        self.assertIn("no run history", findings[1].evidence[0])
        finding = findings[0]
        self.assertIn("9h00m", finding.title)
        self.assertIn("4.5x", finding.evidence[0])
        self.assertGreaterEqual(finding.score, 65)

    def test_job_without_history_needs_an_hour(self):
        class Fresh(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                if name == "agent_running_jobs":
                    return [
                        {"job_name": "A", "started_at": "x", "running_seconds": 3000, "current_step_id": 1},
                        {"job_name": "B", "started_at": "x", "running_seconds": 4000, "current_step_id": 1},
                    ]
                return []

        findings = agent.long_running_findings(Context(Fresh(FIXTURES)))
        self.assertEqual([f.data["job"] for f in findings], ["B"])
        self.assertEqual(findings[0].score, 40)

    def test_nothing_running(self):
        class Idle(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return []

        self.assertEqual(agent.run(Context(Idle(FIXTURES))), [])


if __name__ == "__main__":
    unittest.main()
