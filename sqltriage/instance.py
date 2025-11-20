"""Basic facts about the instance, for the report header."""
from .source import SourceError

SQL = """\
SELECT @@SERVERNAME AS server_name,
       CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(32)) AS product_version,
       CAST(SERVERPROPERTY('ProductMajorVersion') AS int) AS major_version,
       CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition,
       CAST(SERVERPROPERTY('IsHadrEnabled') AS int) AS is_hadr_enabled,
       si.sqlserver_start_time,
       si.cpu_count
FROM sys.dm_os_sys_info AS si;"""

MARKETING_NAMES = {
    11: "SQL Server 2012",
    12: "SQL Server 2014",
    13: "SQL Server 2016",
    14: "SQL Server 2017",
    15: "SQL Server 2019",
    16: "SQL Server 2022",
    17: "SQL Server 2025",
}


def product_name(major_version):
    return MARKETING_NAMES.get(major_version, "SQL Server (version {})".format(major_version))


def load(source):
    """Instance facts as a dict, or None when they cannot be read."""
    try:
        rows = source.fetch("instance_info", SQL)
    except SourceError:
        return None
    if not rows:
        return None
    info = dict(rows[0])
    info["product_name"] = product_name(info.get("major_version"))
    return info


def summary(info):
    if not info:
        return None
    return "{} {} ({}), {} CPUs, up since {}".format(
        info["product_name"],
        info["product_version"],
        info["edition"],
        info["cpu_count"],
        info["sqlserver_start_time"],
    )
