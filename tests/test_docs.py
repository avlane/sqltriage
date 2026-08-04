import os
import re
import unittest

from sqltriage.checks import CHECKS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DocsTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_every_check_has_a_section(self):
        headings = re.findall(r"^## `(\w+)`$", self.read("docs", "checks.md"), re.M)
        self.assertEqual(headings, list(CHECKS))

    def test_the_readme_table_lists_every_check(self):
        readme = self.read("README.md")
        for name in CHECKS:
            self.assertIn("| `{}`".format(name), readme)

    def test_every_dmv_named_in_the_docs_is_used_by_some_check(self):
        source = ""
        checks_dir = os.path.join(ROOT, "sqltriage", "checks")
        for filename in os.listdir(checks_dir):
            if filename.endswith(".py"):
                with open(os.path.join(checks_dir, filename), encoding="utf-8") as handle:
                    source += handle.read()
        source += self.read("sqltriage", "instance.py") + self.read("sqltriage", "preflight.py")
        names = set(re.findall(r"`((?:sys|msdb\.dbo)\.\w+)`", self.read("docs", "checks.md")))
        missing = sorted(n for n in names if n.lower() not in source.lower())
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
