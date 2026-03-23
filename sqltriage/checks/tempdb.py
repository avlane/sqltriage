"""Allocation page contention in tempdb.

Many sessions creating and dropping temp objects at once pile up on the pages
that track free space: PFS (page 1, then every 8088th page), GAM (page 2, then
every 511232nd page: 511232, 1022464, ...) and SGAM (page 3, then the page after
each GAM: 511233, 1022465, ...).
The waits show up as PAGELATCH_* on resource 2:<file>:<page>.
"""
import re
from collections import defaultdict

from ..models import Finding
from ..source import SourceError

NAME = "tempdb"

PFS_INTERVAL = 8088
GAM_INTERVAL = 511232

# Only latches on in-memory pages; PAGEIOLATCH_* is disk I/O, not contention.
LATCH_SQL = """\
SELECT wt.session_id,
       wt.wait_type,
       wt.wait_duration_ms,
       wt.resource_description
FROM sys.dm_os_waiting_tasks AS wt
WHERE wt.wait_type LIKE 'PAGELATCH[_]%'
  AND wt.resource_description LIKE '2:%';"""

# Current data files (type 0) and their sizes, from tempdb's own catalog view.
FILES_SQL = """\
SELECT f.file_id,
       f.name,
       f.physical_name,
       f.size * 8 / 1024 AS size_mb,
       CASE WHEN f.is_percent_growth = 1 THEN f.growth ELSE f.growth * 8 / 1024 END AS growth_mb,
       f.is_percent_growth,
       (SELECT cpu_count FROM sys.dm_os_sys_info) AS cpu_count
FROM tempdb.sys.database_files AS f
WHERE f.type = 0
ORDER BY f.file_id;"""

# Run inside tempdb: how its space is used right now.
SPACE_SQL = """\
SELECT SUM(total_page_count) * 8 / 1024 AS total_mb,
       SUM(unallocated_extent_page_count) * 8 / 1024 AS free_mb,
       SUM(user_object_reserved_page_count) * 8 / 1024 AS user_objects_mb,
       SUM(internal_object_reserved_page_count) * 8 / 1024 AS internal_objects_mb,
       SUM(version_store_reserved_page_count) * 8 / 1024 AS version_store_mb
FROM sys.dm_db_file_space_usage;"""

# Version store space per database (SQL Server 2017 and later).
VERSION_STORE_SQL = """\
SELECT DB_NAME(database_id) AS database_name,
       reserved_page_count,
       reserved_space_kb
FROM sys.dm_tran_version_store_space_usage
ORDER BY reserved_space_kb DESC;"""

# (free space percentage at or below, score)
FREE_SPACE_LEVELS = [(5, 90), (10, 70), (20, 45)]

MAX_RECOMMENDED_FILES = 8
SIZE_TOLERANCE = 1.1  # largest file may be this much bigger than the smallest

# SQL Server 2019 and later can say what a page belongs to.  LIMITED reads only
# the page header, so it is cheap and takes no latch.
PAGE_INFO_SQL = """\
SELECT page_type_desc,
       object_id,
       OBJECT_NAME(object_id, 2) AS object_name,
       index_id
FROM sys.dm_db_page_info(2, ?, ?, 'LIMITED');"""

PAGE_INFO_MIN_MAJOR = 15
PAGE_INFO_LIMIT = 5  # pages to look up per run

_RESOURCE = re.compile(r"^(\d+):(\d+):(\d+)")


def parse_resource(text):
    """'2:1:3' -> (2, 1, 3), or None if it is not a page address."""
    match = _RESOURCE.match(text or "")
    if not match:
        return None
    return tuple(int(g) for g in match.groups())


def page_kind(page_id):
    if page_id == 1 or (page_id > 1 and page_id % PFS_INTERVAL == 0):
        return "PFS"
    if page_id == 2 or (page_id > 2 and page_id % GAM_INTERVAL == 0):
        return "GAM"
    if page_id == 3 or (page_id > 3 and (page_id - 1) % GAM_INTERVAL == 0):
        return "SGAM"
    return "other"


def summarize(rows):
    """Group waiting tasks by (kind, file, page)."""
    groups = defaultdict(list)
    for row in rows:
        parsed = parse_resource(row.get("resource_description"))
        if parsed is None or parsed[0] != 2:
            continue
        _, file_id, page_id = parsed
        groups[(page_kind(page_id), file_id, page_id)].append(row["wait_duration_ms"])
    return groups


def layout_finding(rows):
    """Too few data files, or files of different sizes (SQL Server fills them in proportion)."""
    if not rows:
        return None
    sizes = [r["size_mb"] for r in rows]
    wanted = min(MAX_RECOMMENDED_FILES, int(rows[0]["cpu_count"]))
    evidence = ["{} data file(s): {}".format(len(rows), ", ".join("{} MB".format(s) for s in sizes))]
    advice = []
    score = 0
    if len(rows) < wanted:
        score += 35
        evidence.append("{} logical CPUs, so {} files is the usual starting point".format(rows[0]["cpu_count"], wanted))
        advice.append("Add data files until there are {}, all the same size.".format(wanted))
    if min(sizes) and max(sizes) / float(min(sizes)) > SIZE_TOLERANCE:
        score += 30 if score else 40
        evidence.append("largest file is {:.1f}x the smallest".format(max(sizes) / float(min(sizes))))
        advice.append("Resize the files to the same size and growth increment so allocations spread evenly.")
    if any(r["is_percent_growth"] for r in rows):
        score += 10
        evidence.append("at least one file grows by a percentage")
    if not score:
        return None
    return Finding(
        check=NAME,
        title="tempdb file layout: {} data file(s), sizes not even".format(len(rows))
        if min(sizes) and max(sizes) / float(min(sizes)) > SIZE_TOLERANCE
        else "tempdb file layout: only {} data file(s) for {} CPUs".format(len(rows), rows[0]["cpu_count"]),
        score=score,
        evidence=evidence,
        advice=advice,
        data={"data_files": len(rows), "cpu_count": rows[0]["cpu_count"], "sizes_mb": sizes},
    )


def space_finding(ctx):
    rows = ctx.source.fetch("tempdb_space", SPACE_SQL, database="tempdb")
    if not rows or not rows[0].get("total_mb"):
        return None
    row = rows[0]
    free_pct = 100.0 * row["free_mb"] / row["total_mb"]
    score = 0
    for ceiling, level_score in FREE_SPACE_LEVELS:
        if free_pct <= ceiling:
            score = level_score
            break
    if not score:
        return None
    uses = [
        ("version store", row["version_store_mb"]),
        ("internal objects (sorts, hashes, spools)", row["internal_objects_mb"]),
        ("user objects (temp tables)", row["user_objects_mb"]),
    ]
    uses.sort(key=lambda u: -u[1])
    evidence = [
        "{:.1f}% free: {} MB of {} MB".format(free_pct, row["free_mb"], row["total_mb"]),
        "used by " + ", ".join("{} {} MB".format(name, mb) for name, mb in uses),
    ]
    advice = []
    top = uses[0][0]
    if top == "version store":
        advice.append("The oldest active snapshot transaction stops the version store being cleaned: see the blocking section.")
        try:
            by_db = ctx.source.fetch("tempdb_version_store", VERSION_STORE_SQL)
        except SourceError:
            by_db = []
        for db in by_db[:3]:
            evidence.append("version store: {} holds {:.0f} MB".format(db["database_name"], db["reserved_space_kb"] / 1024.0))
    elif top.startswith("internal"):
        advice.append("Find queries spilling to tempdb: look for large sorts and hash joins with bad estimates.")
    else:
        advice.append("Find sessions with big temp tables: sys.dm_db_session_space_usage.")
    return Finding(
        check=NAME,
        title="tempdb is {:.0f}% full".format(100 - free_pct),
        score=score,
        evidence=evidence,
        advice=advice,
        data={"free_pct": round(free_pct, 1), "largest_use": top},
    )


def run(ctx):
    findings = []
    for finding in (layout_finding(ctx.source.fetch("tempdb_files", FILES_SQL)), space_finding(ctx)):
        if finding:
            findings.append(finding)
    return findings + contention_findings(ctx)


METADATA_MIN_MAJOR = 15  # memory-optimized tempdb metadata arrived with SQL Server 2019


def metadata_advice(ctx, descriptions):
    """Suggest memory-optimized tempdb metadata when system table pages are the hot ones.

    Only on 2019 and later, and not if it is already on.
    """
    info = ctx.instance_info() or {}
    if (info.get("major_version") or 0) < METADATA_MIN_MAJOR or info.get("tempdb_metadata_memory_optimized"):
        return None
    if not any("system table" in text for text in descriptions):
        return None
    return (
        "System table pages are the hot ones: memory-optimized tempdb metadata "
        "(ALTER SERVER CONFIGURATION SET MEMORY_OPTIMIZED TEMPDB_METADATA = ON, needs a restart) removes that contention."
    )


def describe_page(row):
    """'DATA_PAGE of system table object_id 34' and the like."""
    object_id = row.get("object_id")
    if row.get("object_name"):
        owner = "object " + row["object_name"]
    elif object_id is None:
        owner = "an unknown object"
    elif 0 < object_id < 100:
        owner = "system table object_id {}".format(object_id)
    elif object_id < 0:
        owner = "temp object_id {}".format(object_id)
    else:
        owner = "object_id {}".format(object_id)
    return "{} of {}".format(row.get("page_type_desc") or "page", owner)


def page_descriptions(ctx, groups):
    """(file, page) -> description, for the busiest non-allocation pages, where the server can say."""
    major = ctx.server_major()
    if major is None or major < PAGE_INFO_MIN_MAJOR:
        return {}
    others = sorted(((k, v) for k, v in groups.items() if k[0] == "other"), key=lambda kv: -len(kv[1]))
    found = {}
    for (kind, file_id, page_id), _ in others[:PAGE_INFO_LIMIT]:
        try:
            rows = ctx.source.fetch("tempdb_page_info_{}_{}".format(file_id, page_id), PAGE_INFO_SQL, (file_id, page_id))
        except SourceError:
            continue
        if rows:
            found[(file_id, page_id)] = describe_page(rows[0])
    return found


def contention_findings(ctx):
    rows = ctx.source.fetch("tempdb_latch_waits", LATCH_SQL)
    groups = summarize(rows)
    if not groups:
        return []
    allocation = {k: v for k, v in groups.items() if k[0] != "other"}
    alloc_tasks = sum(len(v) for v in allocation.values())
    other_tasks = sum(len(v) for k, v in groups.items() if k[0] == "other")
    longest = max(max(v) for v in groups.values())
    evidence = []
    described = page_descriptions(ctx, groups)
    for (kind, file_id, page_id), waits in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        line = "{:<5} 2:{}:{}  {} task(s) waiting, longest {} ms".format(kind, file_id, page_id, len(waits), max(waits))
        if (file_id, page_id) in described:
            line += " - " + described[(file_id, page_id)]
        evidence.append(line)
    if alloc_tasks:
        score = 30 + min(40, alloc_tasks * 4) + min(20, longest / 5.0)
        title = "tempdb allocation contention: {} task(s) waiting on PFS/GAM/SGAM pages".format(alloc_tasks)
        detail = "Sessions are queueing on tempdb's allocation bitmaps while creating or dropping temp objects."
        advice = [
            "Make sure tempdb has several equally sized data files (one per core, up to 8, is the usual start).",
            "Look for code that creates and drops temp tables at a very high rate, or large table variables.",
        ]
    else:
        score = 15 + min(20, other_tasks * 2)
        title = "tempdb page latch waits on {} task(s), none on allocation pages".format(other_tasks)
        detail = "Latch waits on other tempdb pages, which are usually system table (metadata) pages."
        advice = ["Latches on tempdb's own system tables mean heavy temp object churn; see which statements create and drop the most."]
    metadata_tip = metadata_advice(ctx, described.values())
    if metadata_tip:
        advice.append(metadata_tip)
    return [
        Finding(
            check=NAME,
            title=title,
            score=int(round(score)),
            detail=detail,
            evidence=evidence,
            advice=advice,
            data={
                "allocation_tasks": alloc_tasks,
                "other_tasks": other_tasks,
                "longest_wait_ms": longest,
            },
        )
    ]
