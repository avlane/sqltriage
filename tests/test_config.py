import contextlib
import io
import os
import tempfile
import unittest

from sqltriage import config
from sqltriage.cli import main

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")

SAMPLE = """\
[connection]
server = "SQLPROD01"
trust_server_cert = true

[sample]
interval = 15
top = 8

[checks]
only = ["waits", "blocking"]

[agent]
hours = 12

[querystore]
min_execs = 20
ratio = 2.5
"""


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, text):
        path = os.path.join(self.tmp.name, "sqltriage.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_argument_defaults(self):
        data = config.load(self.write(SAMPLE))
        self.assertEqual(
            config.argument_defaults(data),
            {"server": "SQLPROD01", "trust_server_cert": True, "interval": 15, "top": 8, "only": "waits,blocking"},
        )

    def test_ignore_list(self):
        data = config.load(self.write('[agent]\nignore = ["Purge*"]\n'))
        self.assertEqual(config.context_options(data), {"ignore_jobs": ["Purge*"]})

    def test_database_lists(self):
        data = config.load(self.write('[checks]\ndatabases = ["A", "B"]\nexclude_databases = ["tempdb"]\n'))
        self.assertEqual(config.argument_defaults(data), {"databases": "A,B", "exclude_databases": "tempdb"})

    def test_context_options(self):
        data = config.load(self.write(SAMPLE))
        self.assertEqual(
            config.context_options(data), {"agent_hours": 12, "qs_min_execs": 20, "qs_ratio": 2.5}
        )

    def test_unknown_section_and_key(self):
        with self.assertRaisesRegex(config.ConfigError, "unknown section"):
            config.load(self.write("[nonsense]\na = 1\n"))
        with self.assertRaisesRegex(config.ConfigError, "unknown key 'serber'"):
            config.load(self.write('[connection]\nserber = "x"\n'))

    def test_bad_toml_and_missing_file(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write("[connection\n"))
        with self.assertRaises(config.ConfigError):
            config.load(os.path.join(self.tmp.name, "missing.toml"))

    def test_find_prefers_explicit(self):
        self.assertEqual(config.find("x.toml"), "x.toml")


class CliConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "c.toml")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(SAMPLE)

    def run_cli(self, *argv):
        out = io.StringIO()
        code = main(["--fixtures", FIXTURES, "--config", self.path] + list(argv), out=out)
        return code, out.getvalue()

    def test_file_settings_apply(self):
        _, text = self.run_cli()
        self.assertIn("report for SQLPROD01", text)
        self.assertIn("Wait:", text)
        self.assertNotIn("Log:", text)  # only = waits, blocking

    def test_flags_win(self):
        _, text = self.run_cli("-S", "OTHER", "--only", "vlf")
        self.assertIn("report for OTHER", text)
        self.assertIn("Log:", text)

    def test_broken_config_is_an_error(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("[oops]\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code, _ = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("unknown section", err.getvalue())


if __name__ == "__main__":
    unittest.main()
