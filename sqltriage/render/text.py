"""Plain text report."""
from .. import instance
from .common import describe_changes, fmt_time, summary_line


def to_text(report):
    lines = [
        "sqltriage report for {} at {}".format(report.server, fmt_time(report.generated_at)),
    ]
    if instance.summary(report.instance):
        lines.append(instance.summary(report.instance))
    if report.findings:
        lines.append("{} finding(s): {}".format(len(report.findings), summary_line(report.findings)))
    if report.baseline_counts is not None:
        lines.append("against the baseline: " + describe_changes(report.baseline_counts))
    lines.append("")
    if not report.findings:
        lines.append("Nothing to report.")
    for number, f in enumerate(report.findings, 1):
        tag = " {}".format(f.change.upper()) if f.change in ("new", "worse", "better") else ""
        lines.append("{:>2}. [{} {}{}] {}".format(number, f.severity.upper(), f.score, tag, f.title))
        if f.detail:
            lines.append("    " + f.detail)
        for item in f.block:
            lines.append("      " + item)
        for item in f.evidence:
            lines.append("      - " + item)
        for item in f.advice:
            lines.append("      > " + item)
    if report.hidden:
        lines.append("")
        lines.append("({} lower ranked finding(s) hidden)".format(report.hidden))
    if report.resolved:
        lines.append("")
        lines.append("Resolved since the baseline:")
        lines.extend("  - [{}] {}".format(r["score"], r["title"]) for r in report.resolved)
    if report.errors:
        lines.append("")
        lines.append("Checks that failed:")
        lines.extend("  - " + e for e in report.errors)
    return "\n".join(lines) + "\n"
