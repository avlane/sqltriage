import os
import unittest

from sqltriage import instance
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


class InstanceTests(unittest.TestCase):
    def test_load(self):
        info = instance.load(FixtureSource(FIXTURES))
        self.assertEqual(info["product_name"], "SQL Server 2019")
        self.assertEqual(info["cpu_count"], 16)

    def test_summary(self):
        text = instance.summary(instance.load(FixtureSource(FIXTURES)))
        self.assertEqual(
            text,
            "SQL Server 2019 15.0.4298.1 (Enterprise Edition (64-bit)), 16 CPUs, up since 2023-03-02T03:12:44",
        )

    def test_unreadable_instance_info(self):
        class Denied(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                raise SourceError("denied")

        self.assertIsNone(instance.load(Denied(FIXTURES)))
        self.assertIsNone(instance.summary(None))

    def test_unknown_version_name(self):
        self.assertEqual(instance.product_name(99), "SQL Server (version 99)")


if __name__ == "__main__":
    unittest.main()
