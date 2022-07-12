"""Building the ODBC connection string.

ODBC Driver 18 encrypts the connection by default and validates the server
certificate, so an instance with a self-signed certificate fails to connect
until you say you trust it.  Driver 17 does neither unless asked.  We always
spell both settings out so the behaviour does not depend on the driver.
"""
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
    try:
        import pyodbc
    except ImportError:
        return []
    return list(pyodbc.drivers())


def build_connection_string(server, database="master", user=None, password=None, driver=None,
                            encrypt=True, trust_server_certificate=False, application="sqltriage"):
    parts = [
        "DRIVER={%s}" % (driver or choose_driver(installed_drivers())),
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
