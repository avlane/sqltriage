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
        self.assertEqual(checks, {"waits", "blocking", "querystore", "tempdb", "vlf", "agent", "ag", "cpu"})

    def test_json_has_schema_and_instance(self):
        _, text = self.run_cli("-f", "json", "--only", "vlf")
        data = json.loads(text)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["instance"]["product_name"], "SQL Server 2019")

    def test_text_header_names_the_version(self):
        _, text = self.run_cli("--only", "vlf")
        self.assertIn("SQL Server 2019 15.0.4298.1", text.splitlines()[1])

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
