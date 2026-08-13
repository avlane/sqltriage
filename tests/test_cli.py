import contextlib
import io
import json
import os
import unittest

from sqltriage import __version__
from sqltriage.cli import build_parser, connection_string, main, parse_weights

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
        self.assertEqual(checks, {"waits", "counters", "blocking", "querystore", "tempdb", "vlf", "pvs", "agent", "ag", "memory", "cpu"})

    def test_json_has_schema_and_instance(self):
        _, text = self.run_cli("-f", "json", "--only", "vlf")
        data = json.loads(text)
        self.assertEqual(data["schema_version"], 2)
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
                self.assertEqual(json.load(handle)["schema_version"], 2)

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

    def test_weights_change_the_ranking(self):
        _, plain = self.run_cli("-f", "json", "--only", "agent")
        _, quiet = self.run_cli("-f", "json", "--only", "agent", "--weight", "agent=0.5")
        before = [f["score"] for f in json.loads(plain)["findings"]]
        after = [f["score"] for f in json.loads(quiet)["findings"]]
        self.assertEqual(after, [round(s * 0.5) for s in before])

    def test_parse_weights(self):
        self.assertEqual(parse_weights(["agent=0.5", "vlf=2"], {"cpu": 1.0}), {"cpu": 1.0, "agent": 0.5, "vlf": 2.0})
        for bad in (["agent"], ["agent=x"], ["nonsense=1"], ["agent=-1"]):
            with self.assertRaises(ValueError):
                parse_weights(bad)
        with self.assertRaises(ValueError):
            parse_weights([], {"agent": True})

    def test_bad_weight_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_cli("--weight", "nonsense=1")
        self.assertEqual(code, 2)
        self.assertIn("unknown check in weights: nonsense", err.getvalue())

    def test_check_reports_permissions(self):
        code, text = self.run_cli("--check")
        self.assertEqual(code, 0)
        self.assertIn("connected to SQLPROD01", text)
        self.assertIn("ok: can read the server DMVs", text)

    def test_redact(self):
        _, plain = self.run_cli("--only", "blocking,querystore")
        _, masked = self.run_cli("--only", "blocking,querystore", "--redact-names")
        self.assertIn("svc_payroll@APP02", plain)
        self.assertIn("jmendez", plain)
        self.assertNotIn("svc_payroll", masked)
        self.assertNotIn("jmendez", masked)
        self.assertNotIn("TODO: commit after checking totals'", masked)
        self.assertIn("user1@host1", masked)

    def test_redact_alone_keeps_names(self):
        _, text = self.run_cli("--only", "blocking", "--redact")
        self.assertIn("svc_payroll@APP02", text)
        self.assertIn("Language Event: BEGIN TRAN; UPDATE dbo.Orders SET Status = '?'", text)

    def save_json(self, directory, name="before.json", edit=None):
        _, text = self.run_cli("-f", "json", "-n", "50")
        data = json.loads(text)
        if edit:
            edit(data)
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_baseline_unchanged(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self.save_json(tmp)
            _, text = self.run_cli("-n", "50", "--baseline", path)
            self.assertRegex(text, r"against the baseline: \d+ unchanged\n")
            self.assertNotIn(" NEW]", text)

    def test_baseline_new_and_worse(self):
        import tempfile

        def edit(data):
            data["findings"] = [f for f in data["findings"] if f["check"] != "memory"]
            for f in data["findings"]:
                if f["check"] == "cpu":
                    f["score"] -= 30

        with tempfile.TemporaryDirectory() as tmp:
            path = self.save_json(tmp, edit=edit)
            _, text = self.run_cli("-n", "50", "--baseline", path)
            self.assertRegex(text, r"against the baseline: \d+ new, 1 worse, \d+ unchanged")
            self.assertIn("HIGH 75 WORSE] CPU scheduler pressure", text)
            self.assertIn("score was 45 in the baseline, now 75", text)
            self.assertRegex(text, r"\[(HIGH|MEDIUM) \d+ NEW\] ")
            _, as_json = self.run_cli("-n", "50", "-f", "json", "--baseline", path)
            changes = {f["change"] for f in json.loads(as_json)["findings"]}
            self.assertEqual(changes, {"same", "new", "worse"})
            self.assertEqual(json.loads(as_json)["baseline"]["worse"], 1)

    def test_baseline_in_markdown(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self.save_json(tmp, edit=lambda d: d["findings"].pop(0))
            _, text = self.run_cli("-n", "50", "-f", "markdown", "--baseline", path)
            self.assertIn("Against the baseline: 1 new,", text)
            self.assertRegex(text, r"\*\*1\. .*\(critical, 9\d, new\)")

    def test_resolved_findings_are_listed(self):
        import tempfile

        def edit(data):
            data["findings"].append({"check": "agent", "title": "Agent job failed: Old Job (2x in 24h)", "score": 77,
                                     "data": {"job": "Old Job"}})

        with tempfile.TemporaryDirectory() as tmp:
            path = self.save_json(tmp, edit=edit)
            _, text = self.run_cli("-n", "50", "--baseline", path)
            self.assertIn("Resolved since the baseline:\n  - [77] Agent job failed: Old Job (2x in 24h)", text)
            _, as_json = self.run_cli("-n", "50", "-f", "json", "--baseline", path)
            self.assertEqual(json.loads(as_json)["resolved"][0]["title"], "Agent job failed: Old Job (2x in 24h)")
            _, md = self.run_cli("-n", "50", "-f", "markdown", "--baseline", path)
            self.assertIn("**Resolved since the baseline**\n\n- Agent job failed: Old Job (2x in 24h) (77)", md)

    def test_only_changes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self.save_json(tmp, edit=lambda d: d["findings"].pop(0))
            _, text = self.run_cli("-n", "50", "--baseline", path, "--only-changes")
            self.assertEqual(text.count(" NEW]"), 1)
            self.assertNotIn("Wait: LCK_M_X", text)
            self.assertRegex(text, r"\(\d+ lower ranked finding\(s\) hidden\)")

    def test_only_changes_needs_a_baseline(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_cli("--only-changes")
        self.assertEqual(code, 2)
        self.assertIn("--only-changes needs --baseline", err.getvalue())

    def test_save_then_compare_with_the_saved_report(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            directory = os.path.join(tmp, "history")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                _, first = self.run_cli("-n", "50", "-S", "SQLPROD01", "--save", directory, "--baseline-dir", directory)
            self.assertNotIn("against the baseline", first)  # nothing saved yet
            self.assertRegex(err.getvalue(), r"saved .*sqltriage-SQLPROD01-\d{8}-\d{6}\.json")
            self.assertEqual(len(os.listdir(directory)), 1)
            _, second = self.run_cli("-n", "50", "-S", "SQLPROD01", "--baseline-dir", directory)
            self.assertRegex(second, r"against the baseline: \d+ unchanged\n")

    def test_baseline_dir_for_another_server_is_not_used(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_cli("-S", "SQLPROD01", "--save", tmp)
            _, text = self.run_cli("-S", "SQLPROD02", "--baseline-dir", tmp)
            self.assertNotIn("against the baseline", text)

    def test_explicit_baseline_wins_over_the_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_cli("-S", "SQLPROD01", "--save", tmp)
            path = self.save_json(tmp, "mine.json", edit=lambda d: d["findings"].pop(0))
            _, text = self.run_cli("-n", "50", "-S", "SQLPROD01", "--baseline", path, "--baseline-dir", tmp)
            self.assertIn("1 new", text)

    def test_bad_baseline_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_cli("--baseline", "/nonexistent/before.json")
        self.assertEqual(code, 2)
        self.assertIn("cannot read baseline", err.getvalue())

    def test_explain_a_wait(self):
        out = io.StringIO()
        code = main(["explain", "lck_m_x"], out=out)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertTrue(text.startswith("LCK_M_X\n  Category: locking\n  Sessions are blocked waiting for a lock"))
        self.assertIn("  - Look at the blocking section for the head blocker.", text)

    def test_explain_several_and_the_special_cases(self):
        out = io.StringIO()
        main(["explain", "SLEEP_TASK", "SOMETHING_NEW"], out=out)
        text = out.getvalue()
        self.assertIn("SLEEP_TASK\n  Idle or background wait", text)
        self.assertIn("\n\nSOMETHING_NEW\n  Not one of the waits sqltriage knows how to explain.", text)

    def test_explain_needs_a_wait_type(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = main(["explain"], out=io.StringIO())
        self.assertEqual(code, 2)
        self.assertIn("usage: sqltriage explain", err.getvalue())

    def test_timeout_flags_reach_the_source(self):
        import unittest.mock

        with unittest.mock.patch("sqltriage.cli.PyodbcSource") as source_class:
            source_class.return_value.fetch.side_effect = RuntimeError("stop here")
            try:
                main(["--query-timeout", "300", "--login-timeout", "3", "--driver", "ODBC Driver 18 for SQL Server", "--library", "pyodbc"], out=io.StringIO())
            except RuntimeError:
                pass
        kwargs = source_class.call_args.kwargs
        self.assertEqual((kwargs["query_timeout"], kwargs["login_timeout"]), (300, 3))

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
