"""--check: can this login run the checks at all?"""
from .source import SourceError

SQL = """\
SELECT @@SERVERNAME AS server_name,
       HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW SERVER STATE') AS view_server_state,
       HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW SERVER PERFORMANCE STATE') AS view_server_performance_state,
       HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW ANY DEFINITION') AS view_any_definition,
       HAS_DBACCESS('msdb') AS msdb_access;"""


def run(source):
    """(ok, lines).  ok is False when something the checks need is missing."""
    try:
        rows = source.fetch("preflight", SQL)
    except SourceError as exc:
        return False, ["could not connect or run a query: {}".format(exc)]
    row = rows[0]
    lines = ["connected to {}".format(row["server_name"])]
    ok = True
    # SQL Server 2022 added VIEW SERVER PERFORMANCE STATE as the narrower grant for the DMVs;
    # on older versions HAS_PERMS_BY_NAME returns NULL for it.
    if row["view_server_state"] or row["view_server_performance_state"]:
        lines.append("ok: can read the server DMVs")
    else:
        ok = False
        lines.append("MISSING: VIEW SERVER STATE (VIEW SERVER PERFORMANCE STATE on 2022 and later); waits, blocking, CPU and memory need it")
    if row["view_any_definition"]:
        lines.append("ok: can see object definitions")
    else:
        lines.append("missing: VIEW ANY DEFINITION; object names in some findings will be blank")
    if row["msdb_access"]:
        lines.append("ok: can open msdb for the Agent checks")
    else:
        lines.append("missing: access to msdb; the Agent checks will fail")
    return ok, lines
