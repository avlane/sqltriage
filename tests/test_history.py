import datetime
import os
import tempfile
import unittest

from sqltriage import history

WHEN = datetime.datetime(2026, 2, 10, 20, 15, 30)


class NameTests(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(history.safe_name("SQLPROD01"), "SQLPROD01")
        self.assertEqual(history.safe_name("SQLPROD01\\INST1"), "SQLPROD01_INST1")
        self.assertEqual(history.safe_name("tcp:db.example.net,1433"), "tcp_db.example.net_1433")
        self.assertEqual(history.safe_name("..."), "...")
        self.assertEqual(history.safe_name("\\\\"), "server")

    def test_file_name(self):
        self.assertEqual(history.file_name("SQLPROD01", WHEN), "sqltriage-SQLPROD01-20260210-201530.json")


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "reports")

    def test_save_creates_the_directory(self):
        path = history.save(self.dir, "SQLPROD01", WHEN, '{"a": 1}\n')
        self.assertEqual(os.path.basename(path), "sqltriage-SQLPROD01-20260210-201530.json")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"a": 1}\n')

    def test_latest_is_the_newest_for_that_server(self):
        for server, when in [
            ("SQLPROD01", WHEN),
            ("SQLPROD01", WHEN + datetime.timedelta(days=2)),
            ("SQLPROD01", WHEN - datetime.timedelta(days=9)),
            ("SQLPROD02", WHEN + datetime.timedelta(days=30)),
        ]:
            history.save(self.dir, server, when, "{}")
        self.assertTrue(history.latest(self.dir, "SQLPROD01").endswith("sqltriage-SQLPROD01-20260212-201530.json"))
        self.assertTrue(history.latest(self.dir, "SQLPROD02").endswith("20260312-201530.json"))

    def test_server_names_that_share_a_prefix(self):
        history.save(self.dir, "SQL", WHEN, "{}")
        history.save(self.dir, "SQL-DR", WHEN + datetime.timedelta(days=1), "{}")
        self.assertTrue(history.latest(self.dir, "SQL").endswith("sqltriage-SQL-20260210-201530.json"))

    def test_nothing_saved_yet(self):
        self.assertIsNone(history.latest(self.dir, "SQLPROD01"))
        os.makedirs(self.dir)
        self.assertIsNone(history.latest(self.dir, "SQLPROD01"))

    def test_other_files_are_ignored(self):
        os.makedirs(self.dir)
        for name in ("notes.txt", "sqltriage-SQLPROD01-latest.json", "sqltriage-SQLPROD01-20260210-201530.json.bak"):
            open(os.path.join(self.dir, name), "w").close()
        self.assertIsNone(history.latest(self.dir, "SQLPROD01"))


if __name__ == "__main__":
    unittest.main()
