"""Persisted version store (Accelerated Database Recovery) growth.

With ADR on, row versions live in the user database instead of tempdb.  A long
open transaction, or aborted transactions the cleaner cannot get past, stops
the cleanup, and the version store grows inside the database.
"""
from ..models import Finding
from ..scoring import step_score

NAME = "pvs"

MIN_MAJOR = 15  # sys.dm_tran_persistent_version_store_stats arrived with SQL Server 2019

SQL = """\
SELECT DB_NAME(pvss.database_id) AS database_name,
       pvss.persistent_version_store_size_kb,
       pvss.online_index_version_store_size_kb,
       pvss.current_aborted_transaction_count,
       pvss.oldest_active_transaction_id,
       pvss.oldest_aborted_transaction_id,
       (SELECT SUM(CAST(mf.size AS bigint)) * 8
        FROM sys.master_files AS mf
        WHERE mf.database_id = pvss.database_id
          AND mf.type = 0) AS data_size_kb
FROM sys.dm_tran_persistent_version_store_stats AS pvss
WHERE pvss.persistent_version_store_size_kb > 0
ORDER BY pvss.persistent_version_store_size_kb DESC;"""

GB_KB = 1048576

# (size in GB at or above, score)
SIZE_LEVELS = [(100, 65), (10, 40)]
# (share of the data size, as a percentage, at or above, score)
SHARE_LEVELS = [(50, 65), (25, 55), (10, 40)]
ABORTED_BONUS = 10


def gb(kb):
    return kb / float(GB_KB)


def pvs_finding(row):
    size_kb = row["persistent_version_store_size_kb"]
    data_kb = row.get("data_size_kb") or 0
    share = 100.0 * size_kb / data_kb if data_kb else 0.0
    score = max(step_score(gb(size_kb), SIZE_LEVELS), step_score(share, SHARE_LEVELS))
    if not score:
        return None
    evidence = ["persistent version store is {:.1f} GB, {:.0f}% of the {:.0f} GB of data files".format(gb(size_kb), share, gb(data_kb))]
    advice = []
    if row.get("oldest_active_transaction_id"):
        evidence.append(
            "oldest active transaction id {} is holding back cleanup".format(row["oldest_active_transaction_id"])
        )
        advice.append("Find and finish that transaction: see the blocking section for sessions with open transactions.")
    aborted = row.get("current_aborted_transaction_count") or 0
    if aborted:
        score = min(95, score + ABORTED_BONUS)
        evidence.append("{} aborted transaction(s) are waiting for the version cleaner".format(aborted))
        advice.append("Check the cleaner is running (sys.dm_tran_persistent_version_store_stats) and that nothing is stuck rolling back.")
    if row.get("online_index_version_store_size_kb"):
        evidence.append("{:.1f} GB of that is for online index builds".format(gb(row["online_index_version_store_size_kb"])))
    advice.append("Do not turn ADR off to shrink it: finish the old transactions, then let the cleaner catch up.")
    return Finding(
        check=NAME,
        title="Persistent version store in {} is {:.0f} GB".format(row["database_name"], gb(size_kb)),
        score=score,
        detail="Row versions are piling up in the database because old transactions keep them alive.",
        evidence=evidence,
        advice=advice,
        data={"database": row["database_name"], "pvs_kb": size_kb, "share_pct": round(share, 1)},
    )


def run(ctx):
    major = ctx.server_major()
    if major is None or major < MIN_MAJOR:
        return []
    findings = []
    for row in ctx.source.fetch("pvs_stats", SQL):
        if not ctx.wants_database(row["database_name"]):
            continue
        finding = pvs_finding(row)
        if finding:
            findings.append(finding)
    return findings
