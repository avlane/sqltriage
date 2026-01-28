"""Compare a report with an earlier one.

Save a report with ``-f json -o before.json`` (ideally on a good day, or just
before a change), then run again with ``--baseline before.json``.  Each finding
is marked new, worse, better or unchanged against the same finding last time.
"""
import json
import re

# Data entries that say *which* thing a finding is about, as opposed to how bad
# it is.  Counts and sizes change run to run, so they must not be in the key.
IDENTITY_KEYS = (
    "wait_type",
    "head_session_id",
    "session_id",
    "service",
    "job",
    "ag",
    "replica",
    "database",
    "query_id",
    "plan_id",
    "scheduler_id",
)

WORSE_BY = 10   # score points that make a finding "worse" or "better"


class BaselineError(Exception):
    pass


def finding_key(check, title, data):
    """A string that stays the same for the same problem across runs.

    The title with its numbers blanked tells two findings of one check apart
    (a log with too many VLFs, and a log with no recent backup); the identity
    entries from ``data`` tell apart two of the same kind (two blocking chains).
    """
    parts = [str(data[k]) for k in IDENTITY_KEYS if data.get(k) is not None]
    return "|".join([check, re.sub(r"\d[\d,.]*", "#", title)] + parts)


def key_of(finding):
    return finding_key(finding.check, finding.title, finding.data)


def load(path):
    """The findings of a saved JSON report as {key: {"check", "title", "score"}}."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise BaselineError("cannot read baseline {}: {}".format(path, exc)) from exc
    except ValueError as exc:
        raise BaselineError("baseline {} is not valid JSON: {}".format(path, exc)) from exc
    if not isinstance(data, dict) or "findings" not in data or "schema_version" not in data:
        raise BaselineError("baseline {} is not a sqltriage JSON report".format(path))
    found = {}
    for f in data["findings"]:
        key = finding_key(f["check"], f["title"], f.get("data") or {})
        if key not in found or f["score"] > found[key]["score"]:
            found[key] = {"check": f["check"], "title": f["title"], "score": f["score"]}
    return found


def classify(score, before):
    if before is None:
        return "new"
    if score - before >= WORSE_BY:
        return "worse"
    if before - score >= WORSE_BY:
        return "better"
    return "same"


def apply(findings, previous):
    """Set ``finding.change`` on each finding.

    Returns ``(counts, resolved)``: a count of each kind of change, and the
    baseline findings that no longer appear, highest score first.
    """
    counts = {"new": 0, "worse": 0, "better": 0, "same": 0}
    seen = set()
    for finding in findings:
        key = key_of(finding)
        seen.add(key)
        before = previous[key]["score"] if key in previous else None
        finding.change = classify(finding.score, before)
        if finding.change in ("worse", "better"):
            finding.evidence.append("score was {} in the baseline, now {}".format(before, finding.score))
        counts[finding.change] += 1
    resolved = [previous[k] for k in previous if k not in seen]
    resolved.sort(key=lambda r: (-r["score"], r["title"]))
    return counts, resolved
