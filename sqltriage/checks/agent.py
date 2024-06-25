"""SQL Server Agent jobs: recent failures and jobs that are running too long."""
from collections import OrderedDict

from ..fmt import span
from ..models import Finding
from ..source import SourceError

NAME = "agent"

DEFAULT_HOURS = 24

# step_id = 0 is the job outcome row; run_status 0 = failed.  agent_datetime
# turns the integer run_date / run_time pair into a datetime.
FAILED_SQL = """\
SELECT j.name AS job_name,
       msdb.dbo.agent_datetime(h.run_date, h.run_time) AS started_at,
       h.run_duration,
       h.message
FROM msdb.dbo.sysjobhistory AS h
JOIN msdb.dbo.sysjobs AS j
  ON j.job_id = h.job_id
WHERE h.step_id = 0
  AND h.run_status = 0
  AND msdb.dbo.agent_datetime(h.run_date, h.run_time) >= DATEADD(HOUR, -{hours}, GETDATE())
ORDER BY started_at DESC;"""

# The step that actually failed carries the SQL error; the job outcome row only
# says "the job failed".
FAILED_STEPS_SQL = """\
SELECT j.name AS job_name,
       h.step_id,
       h.step_name,
       h.sql_message_id,
       h.sql_severity,
       h.message,
       msdb.dbo.agent_datetime(h.run_date, h.run_time) AS started_at
FROM msdb.dbo.sysjobhistory AS h
JOIN msdb.dbo.sysjobs AS j
  ON j.job_id = h.job_id
WHERE h.step_id > 0
  AND h.run_status = 0
  AND msdb.dbo.agent_datetime(h.run_date, h.run_time) >= DATEADD(HOUR, -{hours}, GETDATE())
ORDER BY started_at DESC;"""

# sql_message_id -> what usually fixes it
STEP_HINTS = {
    1205: "The step was a deadlock victim; it is safe to rerun, but find the deadlock if it repeats.",
    3041: "A backup failed; the real reason is in the SQL Server error log just before this time.",
    9002: "The transaction log is full: see the log section of this report.",
    229: "The job's owner or proxy lacks a permission.",
    18456: "The job's login could not connect.",
    2627: "A key violation: the data the step loads has changed.",
}

# Jobs that have started and not stopped in the current Agent session.  The
# newest row in syssessions is the session since the last Agent restart.
RUNNING_SQL = """\
SELECT j.name AS job_name,
       ja.start_execution_date AS started_at,
       DATEDIFF(SECOND, ja.start_execution_date, GETDATE()) AS running_seconds,
       ISNULL(ja.last_executed_step_id, 0) + 1 AS current_step_id
FROM msdb.dbo.sysjobactivity AS ja
JOIN msdb.dbo.sysjobs AS j
  ON j.job_id = ja.job_id
WHERE ja.session_id = (SELECT TOP (1) session_id FROM msdb.dbo.syssessions ORDER BY agent_start_date DESC)
  AND ja.start_execution_date IS NOT NULL
  AND ja.stop_execution_date IS NULL;"""

# What a normal run looks like: successful outcomes only.
DURATION_SQL = """\
SELECT j.name AS job_name,
       COUNT(*) AS runs,
       AVG(CAST((h.run_duration / 10000) * 3600 + ((h.run_duration / 100) % 100) * 60 + (h.run_duration % 100) AS float)) AS avg_seconds,
       MAX((h.run_duration / 10000) * 3600 + ((h.run_duration / 100) % 100) * 60 + (h.run_duration % 100)) AS max_seconds
FROM msdb.dbo.sysjobhistory AS h
JOIN msdb.dbo.sysjobs AS j
  ON j.job_id = h.job_id
WHERE h.step_id = 0
  AND h.run_status = 1
GROUP BY j.name;"""

MIN_LONG_SECONDS = 600       # never call a job that has run under ten minutes "long"
NO_HISTORY_SECONDS = 3600    # without a baseline, an hour is long
MIN_HISTORY_RUNS = 3
LONG_FACTOR = 3.0            # times the average


def hhmmss_to_seconds(value):
    """run_duration is stored as the integer HHMMSS: 13005 means 1:30:05."""
    value = int(value or 0)
    return (value // 10000) * 3600 + ((value // 100) % 100) * 60 + value % 100


def failed_jobs(ctx):
    hours = int(ctx.options.get("agent_hours", DEFAULT_HOURS))
    rows = ctx.source.fetch("agent_failed_jobs", FAILED_SQL.format(hours=hours))
    jobs = OrderedDict()
    for row in rows:
        if ctx.job_ignored(row["job_name"]):
            continue
        jobs.setdefault(row["job_name"], []).append(row)
    return hours, jobs


def failed_steps(ctx, hours):
    """job name -> its failing step rows, newest first; empty if history cannot be read."""
    try:
        rows = ctx.source.fetch("agent_failed_steps", FAILED_STEPS_SQL.format(hours=hours))
    except SourceError:
        return {}
    steps = {}
    for row in rows:
        steps.setdefault(row["job_name"], []).append(row)
    return steps


def failure_score(job_name, count):
    score = 35 + min(25, 8 * (count - 1))
    lowered = job_name.lower()
    if "backup" in lowered:
        score += 15
        if "log" in lowered:
            score += 5
    return min(score, 90)


def long_running(ctx):
    """Running jobs that have gone well past their usual duration."""
    running = ctx.source.fetch("agent_running_jobs", RUNNING_SQL)
    if not running:
        return []
    history = {r["job_name"]: r for r in ctx.source.fetch("agent_job_durations", DURATION_SQL)}
    found = []
    for job in running:
        if ctx.job_ignored(job["job_name"]):
            continue
        seconds = job["running_seconds"]
        past = history.get(job["job_name"])
        if past and past["runs"] >= MIN_HISTORY_RUNS:
            usual = float(past["avg_seconds"])
            threshold = max(MIN_LONG_SECONDS, LONG_FACTOR * usual, float(past["max_seconds"]))
            ratio = seconds / usual if usual else None
        else:
            usual = None
            threshold = NO_HISTORY_SECONDS
            ratio = None
        if seconds > threshold:
            found.append((job, usual, ratio))
    return found


def long_running_findings(ctx):
    findings = []
    for job, usual, ratio in long_running(ctx):
        seconds = job["running_seconds"]
        if usual:
            evidence = [
                "running for {} against a usual {} ({:.1f}x)".format(span(seconds), span(usual), ratio),
            ]
            score = 40 + min(40, (ratio - 1) * 8)
        else:
            evidence = ["running for {} and there is no run history to compare with".format(span(seconds))]
            score = 40
        evidence.append("started {}, now on step {}".format(job["started_at"], job["current_step_id"]))
        findings.append(
            Finding(
                check=NAME,
                title="Agent job running long: {} ({})".format(job["job_name"], span(seconds)),
                score=int(round(score)),
                evidence=evidence,
                advice=["Check what the job's session is waiting on before deciding to stop it."],
                data={"job": job["job_name"], "running_seconds": seconds},
            )
        )
    return findings


def failed_job_findings(ctx):
    hours, jobs = failed_jobs(ctx)
    steps = failed_steps(ctx, hours) if jobs else {}
    findings = []
    for job_name, rows in jobs.items():
        latest = rows[0]
        evidence = [
            "{} failure(s) in the last {} hours, most recent at {}".format(len(rows), hours, latest["started_at"]),
            "ran for {}s before failing".format(hhmmss_to_seconds(latest["run_duration"])),
        ]
        advice = ["Open the job history for the failing step and read its error message."]
        step = steps.get(job_name, [None])[0]
        if step:
            evidence.append(
                "failing step {} ({}): error {}, severity {}".format(
                    step["step_id"], step["step_name"], step["sql_message_id"], step["sql_severity"]
                )
            )
            evidence.append(" ".join(str(step["message"]).split()))
            if step["sql_message_id"] in STEP_HINTS:
                advice = [STEP_HINTS[step["sql_message_id"]]]
        elif latest.get("message"):
            evidence.append(" ".join(str(latest["message"]).split()))
        findings.append(
            Finding(
                check=NAME,
                title="Agent job failed: {} ({}x in {}h)".format(job_name, len(rows), hours),
                score=failure_score(job_name, len(rows)),
                evidence=evidence,
                advice=advice,
                data={"job": job_name, "failures": len(rows), "window_hours": hours},
            )
        )
    return findings


def run(ctx):
    return failed_job_findings(ctx) + long_running_findings(ctx)
