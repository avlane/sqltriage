import os
import re
import unittest

from sqltriage import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ReleaseTests(unittest.TestCase):
    def test_version_looks_like_a_version(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_the_changelog_covers_the_current_version(self):
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
            headings = re.findall(r"^## (\d+\.\d+\.\d+) - ", handle.read(), re.M)
        self.assertEqual(headings[0], __version__)

    def test_changelog_versions_descend(self):
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
            headings = re.findall(r"^## (\d+\.\d+\.\d+) - ", handle.read(), re.M)
        as_tuples = [tuple(int(p) for p in h.split(".")) for h in headings]
        self.assertEqual(as_tuples, sorted(as_tuples, reverse=True))


if __name__ == "__main__":
    unittest.main()
