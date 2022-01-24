"""Where query results come from.

Every check asks a ``Source`` for rows by name.  The live source runs the SQL
against a server through pyodbc; the fixture source replays recorded results
from JSON files so the checks can be tested (and demoed) without a server.
"""
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


class PyodbcSource(Source):
    """Runs queries on a live connection.

    ``connect`` is injectable so tests can hand in a fake; by default pyodbc is
    imported on first use so the rest of the tool works without it installed.
    """

    def __init__(self, connection_string, connect=None, query_timeout=60):
        self._connection_string = connection_string
        self._connect = connect
        self._query_timeout = query_timeout
        self._conn = None
        self._home = None
        self._current = None

    def _connection(self):
        if self._conn is None:
            connect = self._connect
            if connect is None:
                import pyodbc

                connect = pyodbc.connect
            self._conn = connect(self._connection_string, autocommit=True, timeout=15)
            self._conn.timeout = self._query_timeout
        return self._conn

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
            except Exception as exc:  # pyodbc.Error and friends
                raise SourceError("{}: {}".format(name, exc)) from exc
        finally:
            cursor.close()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


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
