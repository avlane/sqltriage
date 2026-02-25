"""JSON report."""
import json

from .. import SCHEMA_VERSION, __version__
from ..models import severity_for
from .common import fmt_time, severity_counts


def to_dict(report):
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "sqltriage " + __version__,
        "server": report.server,
        "instance": report.instance,
        "generated_at": fmt_time(report.generated_at),
        "summary": severity_counts(report.findings),
        "findings": [
            {
                "check": f.check,
                "title": f.title,
                "severity": severity_for(f.score),
                "score": f.score,
                "change": f.change or None,
                "detail": f.detail,
                "block": f.block,
                "evidence": f.evidence,
                "advice": f.advice,
                "data": f.data,
            }
            for f in report.findings
        ],
        "baseline": report.baseline_counts,
        "resolved": report.resolved,
        "hidden": report.hidden,
        "errors": report.errors,
    }


def to_json(report):
    return json.dumps(to_dict(report), indent=2, default=str) + "\n"
