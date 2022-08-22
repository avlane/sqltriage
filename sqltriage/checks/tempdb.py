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


def run(ctx):
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
