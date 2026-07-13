import unittest

from sqltriage import errors


class HintTests(unittest.TestCase):
    def test_view_server_state(self):
        text = "waits_snapshot: ('42000', \"[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]VIEW SERVER STATE permission was denied on object 'server', database 'master'. (300)\")"
        self.assertIn("grant VIEW SERVER STATE", errors.hint_for(text))
        self.assertIn("VIEW SERVER PERFORMANCE STATE on SQL Server 2022", errors.hint_for(text))

    def test_view_database_state(self):
        self.assertIn("VIEW DATABASE STATE", errors.hint_for("The VIEW DATABASE STATE permission was denied in database 'Sales'."))
        self.assertIsNotNone(errors.hint_for("VIEW DATABASE PERFORMANCE STATE permission was denied"))

    def test_select_denied(self):
        hint = errors.hint_for("The SELECT permission was denied on the object 'dm_os_performance_counters', database 'mssqlsystemresource'")
        self.assertIn("needs SELECT on that object", hint)

    def test_msdb(self):
        hint = errors.hint_for("The SELECT permission was denied on the object 'sysjobs', database 'msdb', schema 'dbo'.")
        self.assertIsNotNone(hint)

    def test_msdb_access_hint_is_for_msdb_only(self):
        hint = errors.hint_for("permission was denied on object 'sysjobs' in msdb")
        self.assertIn("SQLAgentReaderRole", hint)

    def test_cannot_open_database(self):
        hint = errors.hint_for("The server principal \"triage\" is not able to access the database \"Sales\" under the current security context.")
        self.assertIn("--exclude-databases", hint)

    def test_version_mismatch(self):
        self.assertIn("2016 SP2", errors.hint_for("Invalid object name 'sys.dm_db_log_stats'."))
        self.assertIn("2016 SP2", errors.hint_for("Invalid column name 'is_contained'."))

    def test_login_failed(self):
        self.assertIn("credentials", errors.hint_for("Login failed for user 'triage'."))

    def test_unrecognised(self):
        self.assertIsNone(errors.hint_for("Something else went wrong"))

    def test_describe(self):
        self.assertEqual(errors.describe("cpu", "boom"), "cpu: boom")
        text = errors.describe("agent", "Invalid object name 'msdb.dbo.nothing'")
        self.assertTrue(text.startswith("agent: Invalid object name"))
        self.assertIn("(hint: the query uses something this version", text)


class ReportTests(unittest.TestCase):
    def test_a_denied_check_is_reported_with_its_hint(self):
        import os

        from sqltriage.context import Context
        from sqltriage.render import to_text
        from sqltriage.report import build_report
        from sqltriage.source import FixtureSource, SourceError

        class Denied(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "waits_snapshot":
                    raise SourceError("waits_snapshot: VIEW SERVER STATE permission was denied on object 'server', database 'master'.")
                return super().fetch(name, sql, params, database)

        fixtures = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")
        report = build_report(Context(Denied(fixtures), sleep=lambda s: None), "SQLPROD01", ["waits", "vlf"])
        self.assertEqual(len(report.errors), 1)
        self.assertTrue(report.errors[0].startswith("waits: waits_snapshot: VIEW SERVER STATE permission was denied"))
        self.assertIn("(hint: grant VIEW SERVER STATE", report.errors[0])
        self.assertTrue(report.findings)  # the vlf check still ran
        self.assertIn("Checks that failed:\n  - waits:", to_text(report))


if __name__ == "__main__":
    unittest.main()
