"""Markdown report, for pasting into a ticket."""
from .. import instance
from .common import fmt_time


def to_markdown(report):
    """Markdown that pastes cleanly into a ticket comment."""
    lines = [
        "### sqltriage report for {}".format(report.server),
        "",
        "_Generated {}_".format(fmt_time(report.generated_at)),
        "",
    ]
    if instance.summary(report.instance):
        lines.insert(3, "{}".format(instance.summary(report.instance)))
        lines.insert(4, "")
    if not report.findings:
        lines.append("Nothing to report.")
    else:
        lines.append("| # | Severity | Score | Finding |")
        lines.append("|---|----------|-------|---------|")
        for number, f in enumerate(report.findings, 1):
            lines.append("| {} | {} | {} | {} |".format(number, f.severity, f.score, f.title))
        for number, f in enumerate(report.findings, 1):
            lines.append("")
            lines.append("**{}. {}** ({}, {})".format(number, f.title, f.severity, f.score))
            if f.detail:
                lines.append("")
                lines.append(f.detail)
            if f.block:
                lines.append("")
                lines.append("```")
                lines.extend(f.block)
                lines.append("```")
            if f.evidence:
                lines.append("")
                lines.extend("- " + item for item in f.evidence)
            if f.advice:
                lines.append("")
                lines.append("Next steps:")
                lines.extend("1. " + item for item in f.advice)
    if report.hidden:
        lines.append("")
        lines.append("_{} lower ranked finding(s) hidden._".format(report.hidden))
    if report.errors:
        lines.append("")
        lines.append("**Checks that failed**")
        lines.append("")
        lines.extend("- " + e for e in report.errors)
    return "\n".join(lines) + "\n"
