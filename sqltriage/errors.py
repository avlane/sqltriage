"""Plain advice for the errors SQL Server gives when a check cannot run."""
import re

# (pattern in the error text, advice).  The first that matches wins.
HINTS = [
    (
        re.compile(r"VIEW (SERVER|DATABASE) (PERFORMANCE )?STATE permission was denied", re.I),
        "grant VIEW SERVER STATE (VIEW SERVER PERFORMANCE STATE on SQL Server 2022 and later) "
        "and VIEW DATABASE STATE to the login; 'sqltriage --check' shows what is missing",
    ),
    (
        re.compile(r"permission was denied on (the )?object '?(\w+)'?.*msdb|msdb.*permission was denied", re.I),
        "the login needs access to msdb: add it to the SQLAgentReaderRole role there",
    ),
    (
        re.compile(r"The SELECT permission was denied on the object '([^']+)'", re.I),
        "the login needs SELECT on that object; 'sqltriage --check' shows the usual grants",
    ),
    (
        re.compile(r"not able to access the database", re.I),
        "the login cannot open that database (offline, restoring, or an AG secondary that is not readable); "
        "leave it out with --exclude-databases",
    ),
    (
        re.compile(r"Invalid (object|column) name", re.I),
        "the query uses something this version of SQL Server does not have; sqltriage supports SQL Server 2016 SP2 and later",
    ),
    (
        re.compile(r"Login failed|Cannot open database", re.I),
        "the login was refused; check the credentials, and the database named with --database",
    ),
]


def hint_for(text):
    """Advice for an error message, or None if nothing here recognises it."""
    for pattern, advice in HINTS:
        if pattern.search(text):
            return advice
    return None


def describe(check, error):
    """'check: message (hint: advice)' for the report's list of failed checks."""
    message = "{}: {}".format(check, error)
    advice = hint_for(str(error))
    return "{} (hint: {})".format(message, advice) if advice else message
