"""Query Store: queries that have got slower recently, and forced plans that fail."""
from ..fmt import clean_text
from ..models import Finding
from ..source import SourceError

NAME = "querystore"

DEFAULTS = {
    "qs_recent_hours": 1,     # the window we are worried about
    "qs_lookback_hours": 25,  # recent window plus the baseline before it
    "qs_min_execs": 10,       # executions needed in both windows to trust the average
    "qs_ratio": 2.0,          # recent average must be this many times the baseline
    "qs_min_delta_ms": 5,     # ... and be at least this much slower, so 0.1 ms to 0.4 ms is ignored
    "qs_top": 5,
}

DATABASES_SQL = """\
SELECT name AS database_name
FROM sys.databases
WHERE is_query_store_on = 1
  AND state = 0
  AND database_id > 4
ORDER BY name;"""

# Run inside the database being examined (the source issues USE first).
# Durations are microseconds.  Rows in the baseline window are everything from
# the lookback start up to the start of the recent window.
REGRESSION_SQL = """\
SET NOCOUNT ON;
DECLARE @now datetimeoffset = SYSDATETIMEOFFSET();
DECLARE @recent_start datetimeoffset = DATEADD(HOUR, -{recent_hours}, @now);
DECLARE @history_start datetimeoffset = DATEADD(HOUR, -{lookback_hours}, @now);

WITH stats AS (
    SELECT p.query_id,
           p.plan_id,
           CASE WHEN i.start_time >= @recent_start THEN 1 ELSE 0 END AS is_recent,
           rs.count_executions AS execs,
           rs.avg_duration * rs.count_executions AS duration_us
    FROM sys.query_store_runtime_stats AS rs
    JOIN sys.query_store_runtime_stats_interval AS i
      ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id
    JOIN sys.query_store_plan AS p
      ON p.plan_id = rs.plan_id
    WHERE i.start_time >= @history_start
),
agg AS (
    SELECT query_id,
           SUM(CASE WHEN is_recent = 1 THEN execs ELSE 0 END) AS recent_execs,
           SUM(CASE WHEN is_recent = 1 THEN duration_us ELSE 0 END) AS recent_duration_us,
           SUM(CASE WHEN is_recent = 0 THEN execs ELSE 0 END) AS history_execs,
           SUM(CASE WHEN is_recent = 0 THEN duration_us ELSE 0 END) AS history_duration_us,
           COUNT(DISTINCT plan_id) AS plan_count
    FROM stats
    GROUP BY query_id
)
SELECT TOP ({top})
       a.query_id,
       qt.query_sql_text,
       OBJECT_NAME(q.object_id) AS object_name,
       a.recent_execs,
       a.history_execs,
       a.recent_duration_us / NULLIF(a.recent_execs, 0) AS recent_avg_duration_us,
       a.history_duration_us / NULLIF(a.history_execs, 0) AS history_avg_duration_us,
       a.plan_count,
       (SELECT TOP (1) pf.plan_id
        FROM sys.query_store_plan AS pf
        WHERE pf.query_id = a.query_id AND pf.is_forced_plan = 1) AS forced_plan_id
FROM agg AS a
JOIN sys.query_store_query AS q
  ON q.query_id = a.query_id
JOIN sys.query_store_query_text AS qt
  ON qt.query_text_id = q.query_text_id
WHERE a.recent_execs >= {min_execs}
  AND a.history_execs >= {min_execs}
  AND a.recent_duration_us / a.recent_execs > {ratio} * (a.history_duration_us / a.history_execs)
  AND a.recent_duration_us / a.recent_execs - a.history_duration_us / a.history_execs >= {min_delta_us}
ORDER BY (a.recent_duration_us / a.recent_execs - a.history_duration_us / a.history_execs) * a.recent_execs DESC;"""

# Is Query Store actually collecting?  Run inside each database.
STATE_SQL = """\
SELECT actual_state_desc,
       desired_state_desc,
       readonly_reason,
       current_storage_size_mb,
       max_storage_size_mb
FROM sys.database_query_store_options;"""

# readonly_reason is a bit mask.
READONLY_REASONS = [
    (1, "the database is in read-only mode"),
    (2, "the database is in single-user mode"),
    (4, "the database is in emergency mode"),
    (65536, "the maximum storage size was reached"),
    (131072, "the number of statements limit was reached"),
    (262144, "in-memory Query Store ran out of memory"),
    (524288, "the disk is full"),
]
SECONDARY_REPLICA = 8  # expected on a readable secondary, not a problem

STORAGE_FULL_PCT = 90

# Plan forcing the server itself suggests (SQL Server 2017 and later; the
# recommendation details are JSON).  Active ones have not been applied yet.
TUNING_SQL = """\
SELECT tr.name,
       tr.reason,
       tr.score,
       CAST(JSON_VALUE(tr.details, '$.planForceDetails.queryId') AS int) AS query_id,
       CAST(JSON_VALUE(tr.details, '$.planForceDetails.regressedPlanId') AS int) AS regressed_plan_id,
       CAST(JSON_VALUE(tr.details, '$.planForceDetails.recommendedPlanId') AS int) AS recommended_plan_id,
       JSON_VALUE(tr.state, '$.currentValue') AS state_value,
       tr.last_refresh,
       JSON_VALUE(tr.details, '$.implementationDetails.script') AS script
FROM sys.dm_db_tuning_recommendations AS tr
WHERE JSON_VALUE(tr.state, '$.currentValue') = 'Active'
ORDER BY tr.score DESC;"""

# What the query waited on in the recent window (Query Store wait stats need
# SQL Server 2017 and WAIT_STATS_CAPTURE_MODE = ON, which is the default).
WAITS_SQL = """\
SELECT ws.wait_category_desc,
       SUM(ws.total_query_wait_time_ms) AS total_wait_ms
FROM sys.query_store_wait_stats AS ws
JOIN sys.query_store_plan AS p
  ON p.plan_id = ws.plan_id
JOIN sys.query_store_runtime_stats_interval AS i
  ON i.runtime_stats_interval_id = ws.runtime_stats_interval_id
WHERE p.query_id = {query_id}
  AND i.start_time >= DATEADD(HOUR, -{recent_hours}, SYSDATETIMEOFFSET())
GROUP BY ws.wait_category_desc
ORDER BY total_wait_ms DESC;"""

# If at least this share of the waiting was for locks, blame blocking, not the plan.
LOCK_BLAME_SHARE = 0.5

# Forced plans that the optimizer has failed to apply at least once.
FORCED_FAILURES_SQL = """\
SET NOCOUNT ON;
SELECT p.plan_id,
       p.query_id,
       p.force_failure_count,
       p.last_force_failure_reason_desc,
       {forcing_type} AS plan_forcing_type_desc,
       OBJECT_NAME(q.object_id) AS object_name,
       qt.query_sql_text
FROM sys.query_store_plan AS p
JOIN sys.query_store_query AS q
  ON q.query_id = p.query_id
JOIN sys.query_store_query_text AS qt
  ON qt.query_text_id = q.query_text_id
WHERE p.is_forced_plan = 1
  AND p.force_failure_count > 0
ORDER BY p.force_failure_count DESC;"""

# plan_forcing_type_desc (MANUAL or AUTO, for automatic tuning) arrived with SQL
# Server 2017; before that there is nothing to select.
FORCING_TYPE_COLUMN = "p.plan_forcing_type_desc"
NO_FORCING_TYPE = "CAST(NULL AS nvarchar(60))"
FORCING_TYPE_MIN_MAJOR = 14
AUTO_TUNING_SCORE_DROP = 10

# last_force_failure_reason_desc -> (meaning, what to do)
FORCE_FAILURE_REASONS = {
    "NO_INDEX": (
        "An index the forced plan uses no longer exists.",
        "Recreate the index, or unforce the plan (sp_query_store_unforce_plan) and pick a new one.",
    ),
    "NO_PLAN": (
        "The optimizer could not produce a plan from the forced one.",
        "Unforce the plan and check what changed in the schema or compatibility level.",
    ),
    "ONLINE_INDEX_BUILD": (
        "An online index build on a table in the plan blocked forcing.",
        "Usually clears when the build finishes.",
    ),
    "TIME_OUT": (
        "Forcing the plan timed out in the optimizer.",
        "Check for very complex queries; unforce if it keeps happening.",
    ),
    "HINT_CONFLICT": (
        "The query hints in the text conflict with the forced plan.",
        "Remove the conflicting hint or the forced plan.",
    ),
    "INVALID_STARJOIN": (
        "The forced plan has a star join the optimizer can no longer build.",
        "Unforce the plan and re-evaluate.",
    ),
    "NO_DB": (
        "A database referenced by the plan no longer exists.",
        "Unforce the plan.",
    ),
    "VIEW_COMPILE_FAILED": (
        "A view in the plan could not be compiled.",
        "Check the views the query uses.",
    ),
    "GENERAL_FAILURE": (
        "The plan could not be forced for a reason Query Store does not break down.",
        "Look in the error log and extended events for the failure; unforce if it persists.",
    ),
}

TEXT_LIMIT = 200


def setting(ctx, key):
    return ctx.options.get(key, DEFAULTS[key])


def regression_sql(ctx):
    return REGRESSION_SQL.format(
        recent_hours=int(setting(ctx, "qs_recent_hours")),
        lookback_hours=int(setting(ctx, "qs_lookback_hours")),
        min_execs=int(setting(ctx, "qs_min_execs")),
        ratio="{:.2f}".format(float(setting(ctx, "qs_ratio"))),
        min_delta_us=int(float(setting(ctx, "qs_min_delta_ms")) * 1000),
        top=int(setting(ctx, "qs_top")),
    )


def shorten(text):
    return clean_text(text, TEXT_LIMIT)


# A baseline that rounds to nothing would make any slowdown "infinitely" slower.
MIN_BASELINE_US = 1000


def regression_score(ratio, extra_seconds):
    """Slowdown factor and the total time lost in the recent window."""
    return int(round(min(95, 30 + min(40.0, (ratio - 1) * 10) + min(20.0, extra_seconds / 30.0))))


def wait_breakdown(waits):
    """[(category, share 0-1)], biggest first, from the wait stats rows."""
    total = float(sum(w["total_wait_ms"] for w in waits))
    if not total:
        return []
    return [(w["wait_category_desc"], w["total_wait_ms"] / total) for w in waits]


def tuning_by_query(rows):
    """query_id -> the highest scoring active recommendation for it."""
    best = {}
    for row in rows:
        if row.get("query_id") is None:
            continue
        if row["query_id"] not in best or row["score"] > best[row["query_id"]]["score"]:
            best[row["query_id"]] = row
    return best


def regression_finding(database, row, waits=(), recommendation=None):
    recent = float(row["recent_avg_duration_us"])
    history = float(row["history_avg_duration_us"])
    ratio = recent / max(history, MIN_BASELINE_US)
    extra_seconds = (recent - history) * row["recent_execs"] / 1e6
    label = row.get("object_name") or "ad hoc query"
    evidence = [
        "average duration {:.0f} ms now against {:.0f} ms before ({:.1f}x slower)".format(
            recent / 1000.0, history / 1000.0, ratio
        ),
        "{} execution(s) recently, {} in the baseline window; about {:.0f}s lost".format(
            row["recent_execs"], row["history_execs"], extra_seconds
        ),
        "{} plan(s) seen for this query".format(row["plan_count"]),
        "text: " + shorten(row["query_sql_text"]),
    ]
    advice = []
    score = regression_score(ratio, extra_seconds)
    breakdown = wait_breakdown(waits)
    if breakdown:
        evidence.insert(2, "recent waits: " + ", ".join("{} {:.0f}%".format(c, 100 * s) for c, s in breakdown[:3]))
        if breakdown[0][0] == "Lock" and breakdown[0][1] >= LOCK_BLAME_SHARE:
            score = max(score - 20, 20)
            advice.append("Mostly lock waits: the query is slow because it is blocked, not because of its plan. See the blocking section.")
    if recommendation:
        evidence.append(
            "the server recommends forcing plan {} instead of {} (score {}): {}".format(
                recommendation["recommended_plan_id"],
                recommendation["regressed_plan_id"],
                recommendation["score"],
                recommendation["reason"],
            )
        )
        if recommendation.get("script"):
            advice.append("Run: " + recommendation["script"])
    if row["plan_count"] and row["plan_count"] > 1:
        advice.append(
            "In Query Store, compare the plans for query {} and consider forcing the earlier one "
            "(sp_query_store_force_plan).".format(row["query_id"])
        )
    else:
        advice.append("One plan only: look at parameter sniffing, statistics and data growth instead.")
    return Finding(
        check=NAME,
        title="Query Store regression in {}: query {} ({}), {:.1f}x slower".format(
            database, row["query_id"], label, ratio
        ),
        score=score,
        evidence=evidence,
        advice=advice,
        data={
            "database": database,
            "query_id": row["query_id"],
            "ratio": round(ratio, 2),
            "recent_execs": row["recent_execs"],
        },
    )


def force_failure_finding(database, row):
    reason = row.get("last_force_failure_reason_desc") or "NONE"
    meaning, todo = FORCE_FAILURE_REASONS.get(
        reason, ("Query Store reports reason " + reason + ".", "Look the reason up in sys.query_store_plan.")
    )
    count = row["force_failure_count"]
    label = row.get("object_name") or "ad hoc query"
    automatic = row.get("plan_forcing_type_desc") == "AUTO"
    evidence = [
        "{} failure(s); last reason {}: {}".format(count, reason, meaning),
        "text: " + shorten(row["query_sql_text"]),
    ]
    advice = [todo]
    score = min(80, 45 + count // 10)
    if automatic:
        evidence.append("this plan was forced by automatic tuning, not by a person")
        advice.append("Automatic tuning checks forced plans and un-forces one that keeps failing, so this may clear itself.")
        score = max(10, score - AUTO_TUNING_SCORE_DROP)
    elif row.get("plan_forcing_type_desc") == "MANUAL":
        evidence.append("this plan was forced manually")
    return Finding(
        check=NAME,
        title="Forced plan failing in {}: plan {} for query {} ({})".format(
            database, row["plan_id"], row["query_id"], label
        ),
        score=score,
        detail="A plan has been forced for this query but the optimizer keeps failing to use it.",
        evidence=evidence,
        advice=advice,
        data={"database": database, "plan_id": row["plan_id"], "failures": count, "reason": reason},
    )


def recent_waits(ctx, database, query_id):
    sql = WAITS_SQL.format(query_id=int(query_id), recent_hours=int(setting(ctx, "qs_recent_hours")))
    try:
        return ctx.source.fetch("querystore_waits_{}".format(query_id), sql, database=database)
    except SourceError:
        return []  # wait capture off, or SQL Server 2016


def readonly_reasons(mask):
    return [text for bit, text in READONLY_REASONS if mask & bit]


def state_finding(database, row):
    """A finding if Query Store is not collecting or is nearly out of space."""
    actual = row["actual_state_desc"]
    mask = int(row.get("readonly_reason") or 0)
    if actual == "OFF" or (actual == "READ_ONLY" and mask == SECONDARY_REPLICA):
        return None
    problems = []
    score = 0
    if actual == "ERROR":
        problems.append("Query Store is in an ERROR state and needs to be cleared or reset")
        score = 70
    elif actual == "READ_ONLY":
        reasons = readonly_reasons(mask) or ["the reason is not one this tool knows"]
        problems.append("Query Store is READ_ONLY because " + " and ".join(reasons))
        score = 55
    used_pct = 100.0 * row["current_storage_size_mb"] / row["max_storage_size_mb"] if row["max_storage_size_mb"] else 0
    if used_pct >= STORAGE_FULL_PCT:
        problems.append("storage is {:.0f}% used ({} of {} MB)".format(used_pct, row["current_storage_size_mb"], row["max_storage_size_mb"]))
        score = max(score, 40)
    if not problems:
        return None
    return Finding(
        check=NAME,
        title="Query Store in {} is not healthy".format(database),
        score=score,
        detail="While Query Store is not collecting, the regression figures for this database go stale.",
        evidence=problems,
        advice=["Raise MAX_STORAGE_SIZE_MB or lower the retention (STALE_QUERY_THRESHOLD_DAYS), then set OPERATION_MODE = READ_WRITE."],
        data={"database": database, "state": actual, "storage_used_pct": round(used_pct, 1)},
    )


def forced_failures_sql(ctx):
    major = ctx.server_major() or 0
    return FORCED_FAILURES_SQL.format(forcing_type=FORCING_TYPE_COLUMN if major >= FORCING_TYPE_MIN_MAJOR else NO_FORCING_TYPE)


def check_database(ctx, database, sql):
    findings = []
    for row in ctx.source.fetch("querystore_state", STATE_SQL, database=database):
        finding = state_finding(database, row)
        if finding:
            findings.append(finding)
    try:
        tuning = tuning_by_query(ctx.source.fetch("querystore_tuning", TUNING_SQL, database=database))
    except SourceError:
        tuning = {}  # SQL Server 2016, or the view is not readable
    for row in ctx.source.fetch("querystore_regressions", sql, database=database):
        findings.append(
            regression_finding(database, row, recent_waits(ctx, database, row["query_id"]), tuning.get(row["query_id"]))
        )
    for row in ctx.source.fetch("querystore_forced_failures", forced_failures_sql(ctx), database=database):
        findings.append(force_failure_finding(database, row))
    return findings


def run(ctx):
    findings = []
    databases = [
        r["database_name"]
        for r in ctx.source.fetch("querystore_databases", DATABASES_SQL)
        if ctx.wants_database(r["database_name"])
    ]
    sql = regression_sql(ctx)
    for database in databases:
        try:
            findings.extend(check_database(ctx, database, sql))
        except SourceError as exc:
            findings.append(
                Finding(NAME, "Query Store could not be read in {}".format(database), 10, evidence=[str(exc)])
            )
    return findings
