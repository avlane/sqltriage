"""SQL Server Agent jobs: recent failures."""
from collections import OrderedDict

from ..models import Finding

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


def hhmmss_to_seconds(value):
    """run_duration is stored as the integer HHMMSS: 13005 means 1:30:05."""
    value = int(value or 0)
    return (value // 10000) * 3600 + ((value // 100) % 100) * 60 + value % 100


def failed_jobs(ctx):
    hours = int(ctx.options.get("agent_hours", DEFAULT_HOURS))
    rows = ctx.source.fetch("agent_failed_jobs", FAILED_SQL.format(hours=hours))
    jobs = OrderedDict()
    for row in rows:
        jobs.setdefault(row["job_name"], []).append(row)
    return hours, jobs


def failure_score(job_name, count):
    score = 35 + min(25, 8 * (count - 1))
    lowered = job_name.lower()
    if "backup" in lowered:
        score += 15
        if "log" in lowered:
            score += 5
    return min(score, 90)


def run(ctx):
    hours, jobs = failed_jobs(ctx)
    findings = []
    for job_name, rows in jobs.items():
        latest = rows[0]
        evidence = [
            "{} failure(s) in the last {} hours, most recent at {}".format(len(rows), hours, latest["started_at"]),
            "ran for {}s before failing".format(hhmmss_to_seconds(latest["run_duration"])),
        ]
        if latest.get("message"):
            evidence.append(" ".join(str(latest["message"]).split()))
        findings.append(
            Finding(
                check=NAME,
                title="Agent job failed: {} ({}x in {}h)".format(job_name, len(rows), hours),
                score=failure_score(job_name, len(rows)),
                evidence=evidence,
                advice=["Open the job history for the failing step and read its error message."],
                data={"job": job_name, "failures": len(rows), "window_hours": hours},
            )
        )
    return findings
