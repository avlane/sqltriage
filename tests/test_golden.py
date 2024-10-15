import os
import unittest

from sqltriage.render import FORMATS
from tests.golden_support import GOLDEN, incident_report
from tests.regen_golden import EXTENSIONS


class GoldenTests(unittest.TestCase):
    """The whole incident report, byte for byte.

    If an intended change moves the output, run ``python -m tests.regen_golden``
    and review the diff.
    """

    @classmethod
    def setUpClass(cls):
        cls.report = incident_report()

    def check(self, name):
        path = os.path.join(GOLDEN, "incident1." + EXTENSIONS[name])
        with open(path, encoding="utf-8") as handle:
            expected = handle.read()
        self.assertEqual(FORMATS[name](self.report), expected)

    def test_text(self):
        self.check("text")

    def test_json(self):
        self.check("json")

    def test_markdown(self):
        self.check("markdown")


if __name__ == "__main__":
    unittest.main()
