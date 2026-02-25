"""Pieces the renderers share."""


def fmt_time(value):
    return value.strftime("%Y-%m-%d %H:%M:%S")


SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


def severity_counts(findings):
    """{'critical': 2, 'high': 0, ...} with every severity present."""
    counts = dict.fromkeys(SEVERITY_ORDER, 0)
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def summary_line(findings):
    """'2 critical, 5 high, 3 medium' (empty severities left out) or '' without findings."""
    counts = severity_counts(findings)
    return ", ".join("{} {}".format(counts[name], name) for name in SEVERITY_ORDER if counts[name])


def describe_changes(counts):
    """'2 new, 1 worse, 3 unchanged' from the baseline counts."""
    words = [("new", "new"), ("worse", "worse"), ("better", "better"), ("same", "unchanged")]
    return ", ".join("{} {}".format(counts[key], word) for key, word in words if counts.get(key))  or "nothing to compare"
