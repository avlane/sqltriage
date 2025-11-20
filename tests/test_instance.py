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

    def test_version_names(self):
        self.assertEqual(instance.product_name(13), "SQL Server 2016")
        self.assertEqual(instance.product_name(16), "SQL Server 2022")
        self.assertEqual(instance.product_name(17), "SQL Server 2025")

    def test_a_2025_instance_gets_the_newer_checks(self):
        from sqltriage.context import Context

        class V17(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                rows = super().fetch(name, sql, params, database)
                if name == "instance_info":
                    rows[0].update(major_version=17, product_version="17.0.1000.7")
                return rows

        ctx = Context(V17(FIXTURES))
        self.assertEqual(ctx.server_major(), 17)
        self.assertEqual(ctx.instance_info()["product_name"], "SQL Server 2025")


if __name__ == "__main__":
    unittest.main()
