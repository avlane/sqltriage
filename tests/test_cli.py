import contextlib
import io
import unittest

from sqltriage import __version__
from sqltriage.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_version_flag(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            build_parser().parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_no_arguments_prints_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn("usage: sqltriage", out.getvalue())


if __name__ == "__main__":
    unittest.main()
