"""Memory: query grants waiting or oversized, and signs the instance or the OS is short."""
from ..fmt import span
from ..models import Finding
from ..source import SourceError

NAME = "memory"

_STATEMENT = """\
       SUBSTRING(st.text,
                 (r.statement_start_offset / 2) + 1,
                 ((CASE r.statement_end_offset
                        WHEN -1 THEN DATALENGTH(st.text)
                        ELSE r.statement_end_offset
                   END - r.statement_start_offset) / 2) + 1) AS statement_text"""

# grant_time is NULL while the request is still queued for its grant.
WAITING_SQL = """\
SELECT mg.session_id,
       mg.wait_time_ms,
       mg.requested_memory_kb,
       mg.granted_memory_kb,
       mg.used_memory_kb,
       mg.ideal_memory_kb,
       mg.dop,
       mg.wait_order,
       mg.is_next_candidate,
""" + _STATEMENT + """
FROM sys.dm_exec_query_memory_grants AS mg
JOIN sys.dm_exec_requests AS r
  ON r.session_id = mg.session_id
 AND r.request_id = mg.request_id
OUTER APPLY sys.dm_exec_sql_text(mg.sql_handle) AS st
WHERE mg.grant_time IS NULL
ORDER BY mg.wait_order;"""

# The biggest grants already given out, with how much of each is in use.
TOP_GRANTS_SQL = """\
SELECT TOP (5)
       mg.session_id,
       mg.wait_time_ms,
       mg.requested_memory_kb,
       mg.granted_memory_kb,
       mg.used_memory_kb,
       mg.ideal_memory_kb,
       mg.dop,
       mg.wait_order,
       mg.is_next_candidate,
""" + _STATEMENT + """
FROM sys.dm_exec_query_memory_grants AS mg
JOIN sys.dm_exec_requests AS r
  ON r.session_id = mg.session_id
 AND r.request_id = mg.request_id
OUTER APPLY sys.dm_exec_sql_text(mg.sql_handle) AS st
WHERE mg.grant_time IS NOT NULL
ORDER BY mg.granted_memory_kb DESC;"""

SEMAPHORE_SQL = """\
SELECT pool_id,
       resource_semaphore_id,
       target_memory_kb,
       available_memory_kb,
       granted_memory_kb,
       waiter_count,
       timeout_error_count,
       forced_grant_count
FROM sys.dm_exec_query_resource_semaphores;"""

# One row each: what the OS and the SQL Server process say about memory.
STATE_SQL = """\
SELECT sm.total_physical_memory_kb,
       sm.available_physical_memory_kb,
       sm.system_memory_state_desc,
       sm.system_low_memory_signal_state,
       pm.physical_memory_in_use_kb,
       pm.process_physical_memory_low,
       pm.process_virtual_memory_low,
       pm.memory_utilization_percentage
FROM sys.dm_os_sys_memory AS sm
CROSS JOIN sys.dm_os_process_memory AS pm;"""

# 2147483647 is the default, meaning "no limit".
CONFIG_SQL = """\
SELECT CAST(value_in_use AS bigint) AS max_server_memory_mb
FROM sys.configurations
WHERE name = 'max server memory (MB)';"""

# Buffer Manager lives under an instance specific object name (SQLServer: or MSSQL$name:).
PLE_SQL = """\
SELECT cntr_value AS page_life_expectancy
FROM sys.dm_os_performance_counters
WHERE counter_name = 'Page life expectancy'
  AND object_name LIKE '%:Buffer Manager%';"""

UNLIMITED_MB = 2147483647
LOW_PLE_SECONDS = 300
HOG_MIN_KB = 1048576     # a grant under 1 GB is not worth a finding
HOG_USED_FRACTION = 0.25  # using less than a quarter of the grant
STATEMENT_LIMIT = 140


def gb(kb):
    return kb / 1048576.0


def shorten(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= STATEMENT_LIMIT else text[: STATEMENT_LIMIT - 3] + "..."


def waiting_finding(waiting, semaphores):
    if not waiting:
        return None
    longest = max(w["wait_time_ms"] for w in waiting) / 1000.0
    total_gb = sum(w["requested_memory_kb"] for w in waiting) / 1048576.0
    score = 40 + min(40, len(waiting) * 5) + min(15, longest / 6.0)
    evidence = [
        "{} request(s) have waited up to {} for a memory grant; together they want {:.1f} GB".format(
            len(waiting), span(longest) if longest >= 60 else "{:.0f}s".format(longest), total_gb
        )
    ]
    for row in semaphores:
        if row["waiter_count"]:
            evidence.append(
                "pool {} semaphore {}: {:.1f} of {:.1f} GB granted, {} waiting, {} grant timeout(s), {} forced grant(s)".format(
                    row["pool_id"],
                    row["resource_semaphore_id"],
                    gb(row["granted_memory_kb"]),
                    gb(row["target_memory_kb"]),
                    row["waiter_count"],
                    row["timeout_error_count"],
                    row["forced_grant_count"],
                )
            )
    for row in waiting[:3]:
        evidence.append(
            "spid {} wants {:.1f} GB at dop {}{}: {}".format(
                row["session_id"],
                gb(row["requested_memory_kb"]),
                row["dop"],
                " (next in line)" if row["is_next_candidate"] else "",
                shorten(row["statement_text"]),
            )
        )
    if any(s["timeout_error_count"] for s in semaphores):
        score += 5
    return Finding(
        check=NAME,
        title="{} quer{} waiting for a memory grant".format(len(waiting), "y" if len(waiting) == 1 else "ies"),
        score=int(min(95, round(score))),
        detail="Queries cannot start until memory is granted; this shows as RESOURCE_SEMAPHORE waits.",
        evidence=evidence,
        advice=[
            "Look at what holds the granted memory (below, if it is over-granted) and at big sorts and hashes with bad estimates.",
            "Update statistics or add the missing index so the optimizer asks for less.",
        ],
        data={"waiting": len(waiting), "longest_wait_s": round(longest, 1), "requested_gb": round(total_gb, 2)},
    )


def hog_findings(top):
    findings = []
    for row in top:
        granted, used = row["granted_memory_kb"] or 0, row["used_memory_kb"] or 0
        if granted < HOG_MIN_KB or used >= HOG_USED_FRACTION * granted:
            continue
        findings.append(
            Finding(
                check=NAME,
                title="spid {} holds a {:.1f} GB memory grant and uses {:.1f} GB".format(
                    row["session_id"], gb(granted), gb(used)
                ),
                score=int(min(70, 30 + gb(granted) * 3)),
                detail="An over-sized grant starves other queries of workspace memory.",
                evidence=["dop {}; {}".format(row["dop"], shorten(row["statement_text"]))],
                advice=["An estimate far above reality caused this: check statistics and parameter sniffing for this query."],
                data={"session_id": row["session_id"], "granted_kb": granted, "used_kb": used},
            )
        )
    return findings


def pressure_findings(state, config, ple):
    """Findings from the memory state, the max memory setting and page life expectancy."""
    findings = []
    if state:
        row = state[0]
        total_gb = gb(row["total_physical_memory_kb"])
        problems = []
        if row["system_low_memory_signal_state"]:
            problems.append("the OS is signalling low physical memory ({})".format(row["system_memory_state_desc"]))
        if row["process_physical_memory_low"]:
            problems.append("the SQL Server process reports low physical memory")
        if row["process_virtual_memory_low"]:
            problems.append("the SQL Server process reports low virtual address space")
        if problems:
            findings.append(
                Finding(
                    check=NAME,
                    title="Memory pressure: " + problems[0],
                    score=75 if len(problems) > 1 else 65,
                    evidence=problems
                    + ["{:.1f} of {:.1f} GB in use by SQL Server, {:.1f} GB available to the OS".format(
                        gb(row["physical_memory_in_use_kb"]), total_gb, gb(row["available_physical_memory_kb"]))],
                    advice=["Check max server memory against what else runs on the host, and look for a memory leak outside SQL Server."],
                    data={"total_gb": round(total_gb, 1)},
                )
            )
        limit = config[0]["max_server_memory_mb"] if config else None
        if limit == UNLIMITED_MB and total_gb >= 8:
            findings.append(
                Finding(
                    check=NAME,
                    title="max server memory is not set on a {:.0f} GB host".format(total_gb),
                    score=30,
                    detail="SQL Server will grow until the OS has to push back, which is a bad time to find out.",
                    evidence=["max server memory (MB) is the default 2147483647"],
                    advice=["Set it to leave several GB (more on big hosts) for the OS and anything else running there."],
                    data={"max_server_memory_mb": limit, "total_gb": round(total_gb, 1)},
                )
            )
    if ple and ple[0]["page_life_expectancy"] < LOW_PLE_SECONDS:
        value = ple[0]["page_life_expectancy"]
        findings.append(
            Finding(
                check=NAME,
                title="Page life expectancy is {} seconds".format(value),
                score=25 if value >= 60 else 45,
                detail="Pages are leaving the buffer pool quickly, so reads go to disk. A sudden drop matters more than the number.",
                evidence=["Buffer Manager: Page life expectancy = {}".format(value)],
                advice=["Look for a scan that churned the cache (see PAGEIOLATCH waits) before adding memory."],
                data={"page_life_expectancy": value},
            )
        )
    return findings


def run(ctx):
    findings = []
    try:
        state = ctx.source.fetch("memory_state", STATE_SQL)
        config = ctx.source.fetch("memory_config", CONFIG_SQL)
        ple = ctx.source.fetch("memory_ple", PLE_SQL)
        findings.extend(pressure_findings(state, config, ple))
    except SourceError:
        pass  # the grant queries below still say something useful
    waiting = ctx.source.fetch("memory_grants_waiting", WAITING_SQL)
    semaphores = ctx.source.fetch("memory_semaphores", SEMAPHORE_SQL)
    top = ctx.source.fetch("memory_grants_top", TOP_GRANTS_SQL)
    if waiting:
        findings.append(waiting_finding(waiting, semaphores))
    return findings + hog_findings(top)
