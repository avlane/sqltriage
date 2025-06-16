"""Building the ODBC connection string.

ODBC Driver 18 encrypts the connection by default and validates the server
certificate, so an instance with a self-signed certificate fails to connect
until you say you trust it.  Driver 17 does neither unless asked.  We always
spell both settings out so the behaviour does not depend on the driver.
"""
import importlib.util
import os

PREFERRED_DRIVERS = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]


def choose_driver(installed, requested=None):
    """Pick the driver to use from the names pyodbc reports."""
    if requested:
        return requested
    for name in PREFERRED_DRIVERS:
        if name in installed:
            return name
    # Nothing found (or pyodbc is missing): fall back to the oldest we support
    # so the error from the driver manager names something installable.
    return PREFERRED_DRIVERS[-1]


def installed_drivers():
    if not importable("pyodbc"):
        return []
    import pyodbc

    return list(pyodbc.drivers())


LIBRARIES = ("pyodbc", "mssql-python")


def importable(module):
    return importlib.util.find_spec(module) is not None


def choose_library(requested="auto", available=importable):
    """pyodbc unless it is missing and mssql-python is there.  ``available`` says if a module imports."""
    if requested != "auto":
        return requested
    if available("pyodbc") or not available("mssql_python"):
        return "pyodbc"
    return "mssql-python"


def build_connection_string(server, database="master", user=None, password=None, driver=None,
                            encrypt=True, trust_server_certificate=False, application="sqltriage",
                            library="pyodbc"):
    parts = [] if library == "mssql-python" else ["DRIVER={%s}" % (driver or choose_driver(installed_drivers()))]
    parts += [
        "SERVER=" + server,
        "DATABASE=" + database,
        "APP=" + application,
        "Encrypt=" + ("yes" if encrypt else "no"),
        "TrustServerCertificate=" + ("yes" if trust_server_certificate else "no"),
    ]
    if user:
        parts.append("UID=" + user)
        parts.append("PWD={%s}" % (password if password is not None else os.environ.get("SQLTRIAGE_PASSWORD", "")).replace("}", "}}"))
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts)
