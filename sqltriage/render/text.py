"""Plain text report."""
from .. import instance
from .common import fmt_time


def to_text(report):
    lines = [
        "sqltriage report for {} at {}".format(report.server, fmt_time(report.generated_at)),
    ]
    if instance.summary(report.instance):
        lines.append(instance.summary(report.instance))
    lines.append("")
    if not report.findings:
        lines.append("Nothing to report.")
    for number, f in enumerate(report.findings, 1):
        lines.append("{:>2}. [{} {}] {}".format(number, f.severity.upper(), f.score, f.title))
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
    if report.errors:
        lines.append("")
        lines.append("Checks that failed:")
        lines.extend("  - " + e for e in report.errors)
    return "\n".join(lines) + "\n"
