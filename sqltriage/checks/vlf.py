"""Virtual log file counts and the log reuse wait for every online database."""
from ..models import Finding
from ..scoring import step_score
from ..source import SourceError

NAME = "vlf"

# sys.dm_db_log_info (SQL Server 2016 SP2 and later) returns one row per VLF.
# Database snapshots and databases the login cannot open (a non-readable AG
# secondary, say) make the CROSS APPLY fail for the whole query, so leave them out.
SQL = """\
SELECT d.name AS database_name,
       d.recovery_model_desc,
       d.log_reuse_wait_desc,
       COUNT(l.database_id) AS vlf_count,
       SUM(l.vlf_size_mb) AS total_log_mb,
       SUM(CASE WHEN l.vlf_active = 1 THEN 1 ELSE 0 END) AS active_vlfs
FROM sys.databases AS d
CROSS APPLY sys.dm_db_log_info(d.database_id) AS l
WHERE d.state = 0
  AND d.source_database_id IS NULL
  AND HAS_DBACCESS(d.name) = 1
GROUP BY d.name, d.recovery_model_desc, d.log_reuse_wait_desc;"""

# Log file growth settings; with percentage growth the VLF count creeps up
# and every growth gets bigger, so it is the usual reason a log has too many.
GROWTH_SQL = """\
SELECT DB_NAME(mf.database_id) AS database_name,
       mf.file_id,
       mf.size * 8 / 1024 AS size_mb,
       CASE WHEN mf.is_percent_growth = 1 THEN mf.growth ELSE mf.growth * 8 / 1024 END AS growth_value,
       mf.is_percent_growth,
       mf.max_size
FROM sys.master_files AS mf
WHERE mf.type = 1;"""

SMALL_GROWTH_MB = 256  # a fixed growth below this on a large log means many small growths

# (VLF count at or above, score)
VLF_LEVELS = [(10000, 80), (5000, 65), (1000, 45), (500, 20)]

# log_reuse_wait_desc -> (score, what it means, what to do)
REUSE_WAIT = {
    "LOG_BACKUP": (
        50,
        "The log cannot be truncated until the next log backup.",
        "Check that log backups are running and succeeding (see the Agent jobs section).",
    ),
    "ACTIVE_TRANSACTION": (
        60,
        "An open transaction is holding the start of the log.",
        "Find the oldest open transaction (DBCC OPENTRAN) and check the blocking section.",
    ),
    "AVAILABILITY_REPLICA": (
        65,
        "A secondary replica has not yet hardened this part of the log.",
        "Check log send and redo queues in the availability group section.",
    ),
    "REPLICATION": (
        55,
        "The log reader agent has not yet processed this part of the log.",
        "Check the Log Reader Agent and the undistributed command count.",
    ),
    "DATABASE_MIRRORING": (55, "A mirror has not yet caught up.", "Check the mirroring session state."),
    "ACTIVE_BACKUP_OR_RESTORE": (35, "A backup or restore is running.", "Wait for it, or check it is not stuck."),
    "CHECKPOINT": (20, "Waiting for a checkpoint to occur.", "Usually transient; look again in a minute."),
    "OLDEST_PAGE": (25, "Waiting for the oldest dirty page to be flushed.", "Usually transient."),
    "LOG_SCAN": (30, "A log scan is running.", "Usually transient."),
}


def vlf_score(count):
    return step_score(count, VLF_LEVELS)


def growth_notes(rows):
    """database -> (text, is_problem) for how its log file(s) grow."""
    notes = {}
    for row in rows:
        name = row["database_name"]
        if row["is_percent_growth"]:
            text, bad = "log autogrowth is {}%".format(row["growth_value"]), True
        else:
            text = "log autogrowth is {} MB".format(row["growth_value"])
            bad = row["growth_value"] < SMALL_GROWTH_MB and row["size_mb"] > 16 * row["growth_value"]
        if name not in notes or (bad and not notes[name][1]):
            notes[name] = (text, bad)
    return notes


def run(ctx):
    findings = []
    try:
        growth = growth_notes(ctx.source.fetch("vlf_growth", GROWTH_SQL))
    except SourceError:
        growth = {}
    for row in ctx.source.fetch("vlf_counts", SQL):
        if not ctx.wants_database(row["database_name"]):
            continue
        count = int(row["vlf_count"])
        wait = row.get("log_reuse_wait_desc") or "NOTHING"
        score = vlf_score(count)
        evidence = []
        advice = []
        problems = []
        if score:
            problems.append("{:,} VLFs".format(count))
            evidence.append(
                "{:,} VLFs in {:,.0f} MB of log ({} active)".format(count, row["total_log_mb"], row["active_vlfs"])
            )
            advice.append("Shrink the log once and regrow it in a few large steps to rebuild the VLFs.")
            note = growth.get(row["database_name"])
            if note and note[1]:
                evidence.append(note[0] + ", which is how it got this many")
                advice.append("Set a fixed growth increment of 1024 to 8192 MB instead.")
        if wait != "NOTHING":
            wait_score, text, todo = REUSE_WAIT.get(
                wait, (30, "Log reuse is waiting on " + wait + ".", "Look up " + wait + " in sys.databases.")
            )
            problems.append("log reuse wait " + wait)
            evidence.append("log_reuse_wait_desc = {}: {}".format(wait, text))
            advice.append(todo)
            if score and wait_score >= 50:
                score = min(95, max(score, wait_score) + 10)
            else:
                score = max(score, wait_score)
        if not problems:
            continue
        findings.append(
            Finding(
                check=NAME,
                title="Log: {} has {}".format(row["database_name"], " and ".join(problems)),
                score=score,
                evidence=evidence,
                advice=advice,
                data={
                    "database": row["database_name"],
                    "vlf_count": count,
                    "log_reuse_wait": wait,
                    "recovery_model": row["recovery_model_desc"],
                },
            )
        )
    return findings
