import contextlib
import io
import json
import os
import unittest

from sqltriage import __version__
from sqltriage.cli import build_parser, connection_string, main

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        code = main(["--fixtures", FIXTURES] + list(argv), out=out)
        return code, out.getvalue()

    def test_text_report(self):
        code, text = self.run_cli("-S", "SQLPROD01")
        self.assertEqual(code, 0)
        self.assertIn("sqltriage report for SQLPROD01", text)
        self.assertIn("Wait: LCK_M_X", text)

    def test_json_report(self):
        code, text = self.run_cli("-f", "json")
        data = json.loads(text)
        self.assertEqual(code, 0)
        scores = [f["score"] for f in data["findings"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_every_check_contributes(self):
        code, text = self.run_cli("-f", "json", "-n", "50")
        checks = {f["check"] for f in json.loads(text)["findings"]}
        self.assertEqual(checks, {"waits", "blocking", "querystore", "tempdb", "vlf", "agent", "ag", "memory", "cpu"})

    def test_json_has_schema_and_instance(self):
        _, text = self.run_cli("-f", "json", "--only", "vlf")
        data = json.loads(text)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["instance"]["product_name"], "SQL Server 2019")

    def test_text_header_names_the_version(self):
        _, text = self.run_cli("--only", "vlf")
        self.assertIn("SQL Server 2019 15.0.4298.1", text.splitlines()[1])

    def test_min_severity_hides_the_rest(self):
        _, text = self.run_cli("--min-severity", "high")
        self.assertIn("[CRITICAL", text)
        self.assertNotIn("[MEDIUM", text)
        self.assertRegex(text, r"\(\d+ lower ranked finding\(s\) hidden\)")

    def test_min_severity_in_json(self):
        _, text = self.run_cli("-f", "json", "--min-severity", "critical")
        data = json.loads(text)
        self.assertTrue(all(f["severity"] == "critical" for f in data["findings"]))
        self.assertGreater(data["hidden"], 0)

    def test_database_filter(self):
        _, text = self.run_cli("--only", "vlf", "--databases", "ReportsDB,Staging")
        self.assertIn("Log: ReportsDB", text)
        self.assertNotIn("PayrollDB", text)

    def test_exclude_databases(self):
        _, text = self.run_cli("--only", "vlf,querystore", "--exclude-databases", "payrolldb")
        self.assertNotIn("PayrollDB", text)
        self.assertIn("ReportsDB", text)

    def test_record_needs_a_live_connection(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.run_cli("--record", "somewhere")
        self.assertEqual(code, 2)

    def test_ignore_jobs(self):
        _, text = self.run_cli("--only", "agent", "--ignore-jobs", "DatabaseBackup*,Nightly*,IndexOptimize*,Purge*")
        self.assertNotIn("Agent job", text)

    def test_fail_on_sets_the_exit_status(self):
        code, _ = self.run_cli("--fail-on", "critical", "--only", "vlf")
        self.assertEqual(code, 1)
        code, _ = self.run_cli("--fail-on", "critical", "--only", "agent")
        self.assertEqual(code, 0)

    def test_fail_on_looks_past_min_severity(self):
        code, text = self.run_cli("--fail-on", "critical", "--min-severity", "critical", "--only", "waits")
        self.assertEqual(code, 0)
        code, _ = self.run_cli("--fail-on", "high", "--min-severity", "critical", "--only", "blocking")
        self.assertEqual(code, 1)

    def test_default_exit_status_is_zero(self):
        code, _ = self.run_cli()
        self.assertEqual(code, 0)

    def test_samples_option(self):
        _, text = self.run_cli("--only", "waits", "--samples", "2")
        # the recorded fixture holds two snapshots, so a third one cannot be replayed
        self.assertIn("no fixture for waits_snapshot", text)
        code, _ = self.run_cli("--only", "waits", "--samples", "0")
        self.assertEqual(code, 0)

    def test_output_file_and_format_from_extension(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.md")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code, stdout = self.run_cli("--only", "vlf", "-o", path)
            self.assertEqual((code, stdout), (0, ""))
            self.assertIn("wrote " + path, err.getvalue())
            with open(path, encoding="utf-8") as handle:
                self.assertTrue(handle.read().startswith("### sqltriage report"))
            self.assertEqual(os.listdir(tmp), ["report.md"])  # no temporary file left behind

    def test_explicit_format_beats_the_extension(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.md")
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_cli("--only", "vlf", "-o", path, "-f", "json")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["schema_version"], 1)

    def test_choose_format(self):
        from sqltriage.cli import choose_format

        parse = build_parser().parse_args
        self.assertEqual(choose_format(parse([])), "text")
        self.assertEqual(choose_format(parse(["-o", "x.JSON"])), "json")
        self.assertEqual(choose_format(parse(["-o", "x.out"])), "text")
        self.assertEqual(choose_format(parse(["-o", "x.md", "-f", "text"])), "text")

    def test_blocking_min_wait_flag(self):
        _, text = self.run_cli("--only", "blocking", "--blocking-min-wait", "30")
        self.assertIn("spid 57 is blocking 3 session(s)", text)
        self.assertNotIn("spid 93", text)

    def test_markdown_format(self):
        _, text = self.run_cli("-f", "markdown", "--only", "vlf")
        self.assertTrue(text.startswith("### sqltriage report for localhost"))

    def test_tree_is_not_bulleted(self):
        _, text = self.run_cli("--only", "blocking")
        self.assertIn("      spid 57 [sleeping]", text)
        self.assertNotIn("- |-", text)

    def test_wait_concurrency_uses_the_interval(self):
        _, text = self.run_cli("--only", "waits", "-i", "10")
        self.assertIn("9.2 tasks waiting on average", text)

    def test_unknown_check_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.run_cli("--only", "nonsense")
        self.assertEqual(code, 2)

    def test_connection_string_trusted(self):
        args = build_parser().parse_args(["-S", "SQLPROD01", "--driver", "ODBC Driver 17 for SQL Server"])
        cs = connection_string(args)
        self.assertIn("SERVER=SQLPROD01", cs)
        self.assertIn("Trusted_Connection=yes", cs)

    def test_connection_string_sql_login(self):
        os.environ["SQLTRIAGE_PASSWORD"] = "hunter2"
        self.addCleanup(os.environ.pop, "SQLTRIAGE_PASSWORD")
        args = build_parser().parse_args(["-U", "triage", "--driver", "ODBC Driver 18 for SQL Server"])
        cs = connection_string(args)
        self.assertIn("UID=triage", cs)
        self.assertIn("PWD={hunter2}", cs)

    def test_version_flag(self):
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            build_parser().parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertTrue(__version__)


if __name__ == "__main__":
    unittest.main()
