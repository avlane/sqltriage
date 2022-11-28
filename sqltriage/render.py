"""Turn a Report into text or JSON."""
import json

from .models import severity_for


def _fmt_time(value):
    return value.strftime("%Y-%m-%d %H:%M:%S")


def to_text(report):
    lines = [
        "sqltriage report for {} at {}".format(report.server, _fmt_time(report.generated_at)),
        "",
    ]
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
    if report.errors:
        lines.append("")
        lines.append("Checks that failed:")
        lines.extend("  - " + e for e in report.errors)
    return "\n".join(lines) + "\n"


def to_dict(report):
    return {
        "server": report.server,
        "generated_at": _fmt_time(report.generated_at),
        "findings": [
            {
                "check": f.check,
                "title": f.title,
                "severity": severity_for(f.score),
                "score": f.score,
                "detail": f.detail,
                "block": f.block,
                "evidence": f.evidence,
                "advice": f.advice,
                "data": f.data,
            }
            for f in report.findings
        ],
        "errors": report.errors,
    }


def to_json(report):
    return json.dumps(to_dict(report), indent=2, default=str) + "\n"


FORMATS = {"text": to_text, "json": to_json}
