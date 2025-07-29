"""Virtual log file counts and the log reuse wait for every online database."""
from ..fmt import span
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

# How long since the last log backup, and how much log has piled up since.  Only
# databases whose log is meant to be backed up (FULL and BULK_LOGGED recovery).
LOG_STATS_SQL = """\
SELECT d.name AS database_name,
       ls.log_backup_time,
       DATEDIFF(MINUTE, ls.log_backup_time, GETDATE()) AS minutes_since_log_backup,
       ls.log_since_last_log_backup_mb,
       ls.total_log_size_mb,
       ls.active_log_size_mb,
       ls.log_truncation_holdup_reason
FROM sys.databases AS d
CROSS APPLY sys.dm_db_log_stats(d.database_id) AS ls
WHERE d.state = 0
  AND d.recovery_model_desc <> 'SIMPLE'
  AND d.source_database_id IS NULL
  AND d.database_id > 4
  AND HAS_DBACCESS(d.name) = 1;"""

# (minutes since the last log backup at or above, score)
BACKUP_AGE_LEVELS = [(240, 60), (60, 40)]

# Log growth settings; with percentage growth the VLF count creeps up
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


def log_backup_findings(ctx):
    """FULL / BULK_LOGGED databases whose log has not been backed up lately."""
    try:
        rows = ctx.source.fetch("vlf_log_stats", LOG_STATS_SQL)
    except SourceError:
        return []
    findings = []
    for row in rows:
        if not ctx.wants_database(row["database_name"]):
            continue
        minutes = row.get("minutes_since_log_backup")
        total = row["total_log_size_mb"] or 0
        unbacked = row["log_since_last_log_backup_mb"] or 0
        share = 100.0 * unbacked / total if total else 0.0
        if minutes is None:
            score = 70
            evidence = ["no log backup has ever been taken in FULL or BULK_LOGGED recovery, so the log can never be truncated"]
        else:
            score = step_score(minutes, BACKUP_AGE_LEVELS)
            evidence = ["last log backup {} ago".format(span(minutes * 60))]
        if not score:
            continue
        evidence.append("{:,.0f} MB of log since then, {:.0f}% of the {:,.0f} MB log".format(unbacked, share, total))
        if share >= 80:
            score = min(95, score + 15)
            evidence.append("the log is nearly all un-backed-up and will have to grow or fill")
        findings.append(
            Finding(
                check=NAME,
                title="Log backups: {} {}".format(
                    row["database_name"], "has never had one" if minutes is None else "last one was {} ago".format(span(minutes * 60))
                ),
                score=score,
                evidence=evidence,
                advice=["Check the log backup job (see the Agent section) and the backup target."],
                data={"database": row["database_name"], "minutes_since_log_backup": minutes, "log_since_backup_mb": unbacked},
            )
        )
    return findings


def run(ctx):
    return log_findings(ctx) + log_backup_findings(ctx)


def log_findings(ctx):
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
