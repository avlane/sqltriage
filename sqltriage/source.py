"""Where query results come from.

Every check asks a ``Source`` for rows by name.  The live source runs the SQL
against a server through pyodbc; the fixture source replays recorded results
from JSON files so the checks can be tested (and demoed) without a server.
"""
import datetime
import decimal
import json
import os


class SourceError(Exception):
    """A query failed or a fixture could not be found."""


def quote_name(name):
    """Quote a database name for use in USE [...]."""
    return "[" + name.replace("]", "]]") + "]"


class Source:
    def fetch(self, name, sql, params=(), database=None):
        """Return the result of ``sql`` as a list of dicts keyed by column."""
        raise NotImplementedError

    def close(self):
        pass


class DbApiSource(Source):
    """Runs queries on a live connection from any DB-API style SQL Server driver.

    ``connect`` is injectable so tests can hand in a fake.  Without one, the
    subclass imports its driver on first use, so the rest of the tool works
    without any driver installed.
    """

    def __init__(self, connection_string, connect=None, query_timeout=60, login_timeout=15):
        self._connection_string = connection_string
        self._connect = connect
        self._query_timeout = query_timeout
        self._login_timeout = login_timeout
        self._conn = None
        self._home = None
        self._current = None

    def _connection(self):
        if self._conn is None:
            connect = self._connect or self._import_connect()
            self._conn = connect(self._connection_string, autocommit=True, timeout=self._login_timeout)
            try:
                self._conn.timeout = self._query_timeout
            except AttributeError:  # a driver without a settable query timeout
                pass
        return self._conn

    def _import_connect(self):
        raise NotImplementedError

    def _use(self, cursor, database):
        if self._home is None:
            cursor.execute("SELECT DB_NAME()")
            self._home = cursor.fetchone()[0]
            self._current = self._home
        wanted = database or self._home
        if wanted != self._current:
            cursor.execute("USE " + quote_name(wanted))
            self._current = wanted

    def fetch(self, name, sql, params=(), database=None):
        conn = self._connection()
        cursor = conn.cursor()
        try:
            self._use(cursor, database)
            try:
                cursor.execute(sql, *params)
                columns = [col[0] for col in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            except Exception as exc:  # the driver's own Error classes, and friends
                message = "{}: {}".format(name, exc)
                if "timeout" in str(exc).lower():
                    message += " (the query took longer than --query-timeout, {} s)".format(self._query_timeout)
                raise SourceError(message) from exc
        finally:
            cursor.close()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class PyodbcSource(DbApiSource):
    def _import_connect(self):
        import pyodbc

        return pyodbc.connect


class MssqlPythonSource(DbApiSource):
    """Microsoft's own Python driver, which needs no ODBC driver installed."""

    def _import_connect(self):
        import mssql_python

        return mssql_python.connect


class FixtureSource(Source):
    """Replays recorded results from ``<dir>/<name>[.<database>][.<n>].json``.

    ``n`` is the 1-based call number for that name, which lets a fixture set
    hold the before and after samples of a delta.  The most specific file wins.
    """

    def __init__(self, directory):
        self.directory = directory
        self._calls = {}

    def _candidates(self, name, database, call):
        stems = []
        if database:
            stems.append("{}.{}.{}".format(name, database, call))
            stems.append("{}.{}".format(name, database))
        stems.append("{}.{}".format(name, call))
        stems.append(name)
        return [os.path.join(self.directory, stem + ".json") for stem in stems]

    def fetch(self, name, sql, params=(), database=None):
        key = (name, database)
        call = self._calls.get(key, 0) + 1
        self._calls[key] = call
        for path in self._candidates(name, database, call):
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
        raise SourceError("no fixture for {} (database={})".format(name, database))


def _jsonable(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value)


class RecordingSource(Source):
    """Passes queries to ``inner`` and saves every result as a fixture file.

    The first call for a name is written as ``<name>.json`` and later ones as
    ``<name>.<n>.json``, which is exactly what FixtureSource reads back.  A
    recording holds real query text, logins and host names: look it over before
    sharing it.
    """

    def __init__(self, inner, directory):
        self.inner = inner
        self.directory = directory
        self._calls = {}
        os.makedirs(directory, exist_ok=True)

    def fetch(self, name, sql, params=(), database=None):
        rows = self.inner.fetch(name, sql, params, database)
        key = (name, database)
        call = self._calls.get(key, 0) + 1
        self._calls[key] = call
        stem = name + ("." + database if database else "") + ("." + str(call) if call > 1 else "")
        with open(os.path.join(self.directory, stem + ".json"), "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=2, default=_jsonable)
            handle.write("\n")
        return rows

    def close(self):
        self.inner.close()
