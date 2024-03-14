"""Allocation page contention in tempdb.

Many sessions creating and dropping temp objects at once pile up on the pages
that track free space: PFS (page 1 and every 8088th page after), GAM (page 2
and every 511232nd page after) and SGAM (page 3 and every 511232nd after).
The waits show up as PAGELATCH_* on resource 2:<file>:<page>.
"""
import re
from collections import defaultdict

from ..models import Finding

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

MAX_RECOMMENDED_FILES = 8
SIZE_TOLERANCE = 1.1  # largest file may be this much bigger than the smallest

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
    if page_id == 2 or (page_id > 2 and (page_id - 2) % GAM_INTERVAL == 0):
        return "GAM"
    if page_id == 3 or (page_id > 3 and (page_id - 3) % GAM_INTERVAL == 0):
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


def run(ctx):
    layout = layout_finding(ctx.source.fetch("tempdb_files", FILES_SQL))
    findings = [layout] if layout else []
    return findings + contention_findings(ctx)


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
    for (kind, file_id, page_id), waits in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        evidence.append(
            "{:<5} 2:{}:{}  {} task(s) waiting, longest {} ms".format(kind, file_id, page_id, len(waits), max(waits))
        )
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
        advice = ["Check the object being hit with sys.dm_db_page_info or DBCC PAGE."]
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
