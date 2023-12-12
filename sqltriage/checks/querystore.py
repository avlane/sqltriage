"""Query Store: queries that have got slower recently, and forced plans that fail."""
from ..models import Finding
from ..source import SourceError

NAME = "querystore"

DEFAULTS = {
    "qs_recent_hours": 1,     # the window we are worried about
    "qs_lookback_hours": 25,  # recent window plus the baseline before it
    "qs_min_execs": 10,       # executions needed in both windows to trust the average
    "qs_ratio": 2.0,          # recent average must be this many times the baseline
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
ORDER BY (a.recent_duration_us / a.recent_execs - a.history_duration_us / a.history_execs) * a.recent_execs DESC;"""

# Forced plans that the optimizer has failed to apply at least once.
FORCED_FAILURES_SQL = """\
SET NOCOUNT ON;
SELECT p.plan_id,
       p.query_id,
       p.force_failure_count,
       p.last_force_failure_reason_desc,
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
        top=int(setting(ctx, "qs_top")),
    )


def shorten(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 3] + "..."


def regression_score(ratio, extra_seconds):
    """Slowdown factor and the total time lost in the recent window."""
    return int(round(min(95, 30 + min(40.0, (ratio - 1) * 10) + min(20.0, extra_seconds / 30.0))))


def regression_finding(database, row):
    recent = float(row["recent_avg_duration_us"])
    history = float(row["history_avg_duration_us"])
    ratio = recent / history if history else float("inf")
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
        score=regression_score(ratio, extra_seconds),
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
    return Finding(
        check=NAME,
        title="Forced plan failing in {}: plan {} for query {} ({})".format(
            database, row["plan_id"], row["query_id"], label
        ),
        score=min(80, 45 + count // 10),
        detail="A plan has been forced for this query but the optimizer keeps failing to use it.",
        evidence=[
            "{} failure(s); last reason {}: {}".format(count, reason, meaning),
            "text: " + shorten(row["query_sql_text"]),
        ],
        advice=[todo],
        data={"database": database, "plan_id": row["plan_id"], "failures": count, "reason": reason},
    )


def check_database(ctx, database, sql):
    findings = []
    for row in ctx.source.fetch("querystore_regressions", sql, database=database):
        findings.append(regression_finding(database, row))
    for row in ctx.source.fetch("querystore_forced_failures", FORCED_FAILURES_SQL, database=database):
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
