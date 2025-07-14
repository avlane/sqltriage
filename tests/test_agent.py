import os
import unittest

from sqltriage.checks import agent
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

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

    def test_failing_step_and_error_number(self):
        by_job = {f.data["job"]: f for f in agent.failed_job_findings(Context(FixtureSource(FIXTURES)))}
        etl = by_job["Nightly ETL Load"]
        self.assertIn("failing step 3 (Load Fact Tables): error 1205, severity 13", etl.evidence)
        self.assertTrue(any("chosen as the deadlock victim" in e for e in etl.evidence))
        self.assertEqual(etl.advice, [agent.STEP_HINTS[1205]])

    def test_unknown_error_keeps_the_generic_advice(self):
        class Odd(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                rows = super().fetch(name, sql, params, database)
                if name == "agent_failed_steps":
                    for row in rows:
                        row["sql_message_id"] = 99999
                return rows

        by_job = {f.data["job"]: f for f in agent.failed_job_findings(Context(Odd(FIXTURES)))}
        self.assertIn("Open the job history", by_job["Nightly ETL Load"].advice[0])

    def test_step_history_failure_falls_back_to_the_outcome_message(self):
        class NoSteps(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "agent_failed_steps":
                    raise SourceError("denied")
                return super().fetch(name, sql, params, database)

        by_job = {f.data["job"]: f for f in agent.failed_job_findings(Context(NoSteps(FIXTURES)))}
        self.assertTrue(any("The last step to run was step 3" in e for e in by_job["Nightly ETL Load"].evidence))

    def test_ignored_jobs_are_skipped_everywhere(self):
        ctx = Context(FixtureSource(FIXTURES), options={"ignore_jobs": ["databasebackup*", "Purge *"]})
        self.assertEqual([f.data["job"] for f in agent.failed_job_findings(ctx)], ["Nightly ETL Load"])
        ctx = Context(FixtureSource(FIXTURES), options={"ignore_jobs": ["Purge *"]})
        self.assertEqual([f.data["job"] for f in agent.long_running_findings(ctx)], ["IndexOptimize - USER_DATABASES"])

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


class ServiceTests(unittest.TestCase):
    def test_incident_has_overdue_jobs_but_a_running_agent(self):
        (finding,) = agent.service_findings(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.title, "2 enabled job(s) have missed their schedule")
        self.assertEqual(finding.score, 46)
        self.assertEqual(finding.evidence[0], "Stats Refresh - Hourly was due 2023-03-14T07:00:00 (178 min ago)")

    def serve(self, service_rows, overdue_rows):
        class Fake(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                return {"agent_service": service_rows, "agent_overdue_jobs": overdue_rows}.get(name, [])

        return Context(Fake(FIXTURES))

    def test_agent_stopped(self):
        stopped = [{"servicename": "SQL Server Agent (MSSQLSERVER)", "status_desc": "Stopped",
                    "startup_type_desc": "Automatic", "last_startup_time": "2025-07-01T03:00:00"}]
        overdue = [{"job_name": "J1", "next_run": "x", "minutes_late": 90}]
        down, missed = agent.service_findings(self.serve(stopped, overdue))
        self.assertEqual((down.title, down.score), ("SQL Server Agent (MSSQLSERVER) is stopped", 90))
        self.assertIn("expected while the Agent service is not running", missed.evidence)
        self.assertEqual(missed.score, 43)

    def test_healthy(self):
        running = [{"servicename": "SQL Server Agent", "status_desc": "Running", "startup_type_desc": "Automatic", "last_startup_time": "x"}]
        self.assertEqual(agent.service_findings(self.serve(running, [])), [])

    def test_ignored_jobs_do_not_count_as_missed(self):
        ctx = self.serve([], [{"job_name": "Purge Old Rows", "next_run": "x", "minutes_late": 90}])
        ctx.options["ignore_jobs"] = ["Purge*"]
        self.assertEqual(agent.service_findings(ctx), [])

    def test_grace_period_is_in_the_query(self):
        seen = []

        class Spy(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                seen.append((name, sql))
                return []

        agent.service_findings(Context(Spy(FIXTURES), options={"agent_overdue_grace": 90}))
        sql = dict(seen)["agent_overdue_jobs"]
        self.assertIn("DATEADD(MINUTE, -90, GETDATE())", sql)

    def test_unreadable_service_view_is_skipped(self):
        class NoService(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "agent_service":
                    raise SourceError("denied")
                return super().fetch(name, sql, params, database)

        self.assertEqual(len(agent.service_findings(Context(NoService(FIXTURES)))), 1)

    def test_many_overdue_jobs_are_capped(self):
        rows = [{"job_name": "J%d" % i, "next_run": "x", "minutes_late": 60} for i in range(20)]
        (finding,) = agent.service_findings(self.serve([], rows))
        self.assertEqual(finding.score, 70)
        self.assertEqual(len(finding.evidence), 5)


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
