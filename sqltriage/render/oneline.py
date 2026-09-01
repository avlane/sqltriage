"""A single line, for a chat message or a monitoring alert."""
from ..fmt import clean_text
from .common import summary_line

TOP = 3
TITLE_LIMIT = 80


def to_oneline(report):
    if not report.findings:
        line = "{}: nothing to report".format(report.server)
    else:
        top = "; ".join(
            "{}) {} [{}]".format(number, clean_text(f.title, TITLE_LIMIT), f.score)
            for number, f in enumerate(report.findings[:TOP], 1)
        )
        line = "{}: {}. Top: {}".format(report.server, summary_line(report.findings), top)
    if report.baseline_counts is not None and report.baseline_counts.get("new"):
        line += " ({} new since the baseline)".format(report.baseline_counts["new"])
    if report.errors:
        line += " ({} check(s) failed)".format(len(report.errors))
    return line + "\n"
