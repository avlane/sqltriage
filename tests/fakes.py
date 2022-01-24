"""Small stand-ins for a pyodbc connection."""


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql, *params):
        self.conn.executed.append((sql, params))
        if sql.startswith("USE ") or sql == "SELECT DB_NAME()":
            self.description = [("name",)]
            self._rows = [(self.conn.database,)] if sql == "SELECT DB_NAME()" else []
            if sql.startswith("USE "):
                self.description = None
            return self
        result = self.conn.results.pop(0)
        if isinstance(result, Exception):
            raise result
        columns, rows = result
        self.description = [(c,) for c in columns]
        self._rows = rows
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, results, database="master"):
        self.results = list(results)
        self.database = database
        self.executed = []
        self.timeout = None
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True
