import json
import os
import tempfile
import unittest

from sqltriage.source import (
    DbApiSource,
    FixtureSource,
    MssqlPythonSource,
    PyodbcSource,
    RecordingSource,
    SourceError,
    quote_name,
)
from tests.fakes import FakeConnection


class PyodbcSourceTests(unittest.TestCase):
    def make(self, results):
        conn = FakeConnection(results)
        seen = {}

        def connect(cs, **kwargs):
            seen["cs"] = cs
            seen["kwargs"] = kwargs
            return conn

        return PyodbcSource("DSN=x", connect=connect), conn, seen

    def test_rows_become_dicts(self):
        source, conn, seen = self.make([(["a", "b"], [(1, "x"), (2, "y")])])
        rows = source.fetch("rows", "SELECT a, b FROM tbl")
        self.assertEqual(rows, [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
        self.assertTrue(seen["kwargs"]["autocommit"])
        self.assertEqual(conn.timeout, 60)

    def test_connection_is_reused_and_closed(self):
        source, conn, _ = self.make([(["a"], [(1,)]), (["a"], [(2,)])])
        source.fetch("rows", "SELECT 1")
        source.fetch("rows", "SELECT 2")
        source.close()
        self.assertTrue(conn.closed)

    def test_database_switch_and_restore(self):
        source, conn, _ = self.make([(["a"], [(1,)])] * 3)
        source.fetch("rows", "SELECT 1")
        source.fetch("rows", "SELECT 2", database="Sales")
        source.fetch("rows", "SELECT 3")
        used = [sql for sql, _ in conn.executed if sql.startswith("USE ")]
        self.assertEqual(used, ["USE [Sales]", "USE [master]"])

    def test_errors_are_wrapped(self):
        source, _, _ = self.make([RuntimeError("permission denied")])
        with self.assertRaises(SourceError) as cm:
            source.fetch("waits", "SELECT 1")
        self.assertIn("waits", str(cm.exception))

    def test_a_missing_driver_is_a_clear_error(self):
        class Missing(PyodbcSource):
            def _import_connect(self):
                raise ImportError("No module named 'pyodbc'")

        with self.assertRaises(SourceError) as cm:
            Missing("DSN=x").fetch("rows", "SELECT 1")
        self.assertEqual(str(cm.exception), "pyodbc is not installed: pip install pyodbc, or use --library mssql-python")

        class MissingMssql(MssqlPythonSource):
            def _import_connect(self):
                raise ImportError("No module named 'mssql_python'")

        with self.assertRaises(SourceError) as cm:
            MissingMssql("x").open()
        self.assertEqual(str(cm.exception), "mssql-python is not installed: pip install mssql-python, or use --library pyodbc")

    def test_a_refused_login_is_a_source_error(self):
        def refuse(cs, **kwargs):
            raise RuntimeError("Login failed for user 'triage'.")

        source = PyodbcSource("DSN=x", connect=refuse)
        with self.assertRaises(SourceError) as cm:
            source.open()
        self.assertEqual(str(cm.exception), "could not connect: Login failed for user 'triage'.")

    def test_open_connects_once(self):
        calls = []

        def connect(cs, **kwargs):
            calls.append(cs)
            return FakeConnection([])

        source = PyodbcSource("DSN=x", connect=connect)
        source.open()
        source.open()
        self.assertEqual(calls, ["DSN=x"])

    def test_a_database_that_cannot_be_opened_is_a_source_error(self):
        class Refuses(FakeConnection):
            def cursor(inner):
                cursor = super().cursor()
                original = cursor.execute

                def execute(sql, *params):
                    if sql.startswith("USE "):
                        raise RuntimeError("The server principal is not able to access the database")
                    return original(sql, *params)

                cursor.execute = execute
                return cursor

        source = PyodbcSource("DSN=x", connect=lambda cs, **kw: Refuses([]))
        with self.assertRaises(SourceError) as cm:
            source.fetch("querystore_state", "SELECT 1", database="Locked")
        self.assertIn("querystore_state: The server principal is not able to access the database", str(cm.exception))

    def test_the_base_source_opens_trivially(self):
        FixtureSource("x").open()

    def test_recording_source_passes_open_through(self):
        class Inner(FixtureSource):
            opened = False

            def open(self):
                self.opened = True

        inner = Inner("x")
        with tempfile.TemporaryDirectory() as tmp:
            RecordingSource(inner, tmp).open()
        self.assertTrue(inner.opened)

    def test_timeouts_are_passed_to_the_driver(self):
        seen = {}

        def connect(cs, **kwargs):
            seen.update(kwargs)
            return FakeConnection([(["a"], [(1,)])])

        source = PyodbcSource("DSN=x", connect=connect, query_timeout=120, login_timeout=5)
        source.fetch("rows", "SELECT 1")
        self.assertEqual(seen["timeout"], 5)
        self.assertEqual(source._conn.timeout, 120)

    def test_defaults(self):
        source, conn, seen = self.make([(["a"], [(1,)])])
        source.fetch("rows", "SELECT 1")
        self.assertEqual((seen["kwargs"]["timeout"], conn.timeout), (15, 60))

    def test_timeout_errors_say_which_option_to_raise(self):
        source, _, _ = self.make([RuntimeError("Query timeout expired")])
        with self.assertRaises(SourceError) as cm:
            source.fetch("waits", "SELECT 1")
        self.assertIn("longer than --query-timeout, 60 s", str(cm.exception))

    def test_other_errors_do_not_mention_the_timeout(self):
        source, _, _ = self.make([RuntimeError("permission denied")])
        with self.assertRaises(SourceError) as cm:
            source.fetch("waits", "SELECT 1")
        self.assertNotIn("--query-timeout", str(cm.exception))

    def test_driver_without_a_settable_timeout(self):
        class NoTimeout(FakeConnection):
            ready = False

            def __setattr__(self, name, value):
                if name == "timeout" and self.ready:
                    raise AttributeError("read only")
                super().__setattr__(name, value)

        conn = NoTimeout([(["a"], [(1,)])])
        conn.ready = True
        source = PyodbcSource("DSN=x", connect=lambda cs, **kw: conn)
        self.assertEqual(source.fetch("rows", "SELECT 1"), [{"a": 1}])

    def test_each_driver_imports_its_own_module(self):
        import sys
        import types

        calls = []
        for module, source_class in (("pyodbc", PyodbcSource), ("mssql_python", MssqlPythonSource)):
            fake = types.ModuleType(module)
            fake.connect = lambda cs, _m=module, **kw: calls.append(_m) or FakeConnection([(["a"], [(1,)])])
            self.addCleanup(sys.modules.pop, module, None)
            sys.modules[module] = fake
            source_class("x").fetch("rows", "SELECT 1")
        self.assertEqual(calls, ["pyodbc", "mssql_python"])

    def test_base_class_needs_a_driver(self):
        with self.assertRaises(NotImplementedError):
            DbApiSource("x").fetch("rows", "SELECT 1")

    def test_quote_name_escapes_brackets(self):
        self.assertEqual(quote_name("odd]name"), "[odd]]name]")


class FixtureSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, filename, rows):
        with open(os.path.join(self.tmp.name, filename), "w", encoding="utf-8") as handle:
            json.dump(rows, handle)

    def test_plain_fixture(self):
        self.write("q.json", [{"a": 1}])
        self.assertEqual(FixtureSource(self.tmp.name).fetch("q", ""), [{"a": 1}])

    def test_call_numbers(self):
        self.write("q.1.json", [{"a": 1}])
        self.write("q.2.json", [{"a": 2}])
        source = FixtureSource(self.tmp.name)
        self.assertEqual(source.fetch("q", "")[0]["a"], 1)
        self.assertEqual(source.fetch("q", "")[0]["a"], 2)

    def test_database_specific_wins(self):
        self.write("q.json", [{"a": "generic"}])
        self.write("q.Sales.json", [{"a": "sales"}])
        source = FixtureSource(self.tmp.name)
        self.assertEqual(source.fetch("q", "", database="Sales")[0]["a"], "sales")
        self.assertEqual(source.fetch("q", "", database="Other")[0]["a"], "generic")

    def test_missing_fixture(self):
        with self.assertRaises(SourceError):
            FixtureSource(self.tmp.name).fetch("nope", "")


class RecordingSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_recording_replays_through_fixture_source(self):
        import datetime
        import decimal

        class Fake(FixtureSource):
            def __init__(inner):
                inner.n = 0

            def fetch(inner, name, sql, params=(), database=None):
                inner.n += 1
                return [{"n": inner.n, "at": datetime.datetime(2024, 2, 13, 20, 0), "mb": decimal.Decimal("1.5"), "raw": b"\x01\xff"}]

            def close(inner):
                inner.closed = True

        inner = Fake()
        rec = RecordingSource(inner, os.path.join(self.tmp.name, "out"))
        rec.fetch("waits", "SELECT 1")
        rec.fetch("waits", "SELECT 1")
        rec.fetch("vlf", "SELECT 2", database="Sales")
        rec.close()
        self.assertTrue(inner.closed)
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp.name, "out"))), ["vlf.Sales.json", "waits.2.json", "waits.json"])
        replay = FixtureSource(os.path.join(self.tmp.name, "out"))
        first = replay.fetch("waits", "")
        second = replay.fetch("waits", "")
        self.assertEqual((first[0]["n"], second[0]["n"]), (1, 2))
        self.assertEqual(first[0]["at"], "2024-02-13T20:00:00")
        self.assertEqual(first[0]["mb"], 1.5)
        self.assertEqual(first[0]["raw"], "01ff")
        self.assertEqual(replay.fetch("vlf", "", database="Sales")[0]["n"], 3)


if __name__ == "__main__":
    unittest.main()
