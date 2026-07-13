"""Run the checks and rank what they found."""
import datetime

from . import correlate
from .errors import describe as describe_error
from .checks import CHECKS
from .models import LEVELS
from .scoring import apply_weights, rank


class Report:
    def __init__(self, server, findings, errors, generated_at, instance=None):
        self.hidden = 0  # findings dropped by filter()
        self.baseline_counts = None  # {"new": n, "worse": n, ...} when compared with a baseline
        self.resolved = []  # baseline findings that are gone, as {"check", "title", "score"}
        self.instance = instance
        self.server = server
        self.findings = findings
        self.errors = errors
        self.generated_at = generated_at


def filter_report(report, minimum):
    """Keep findings at or above the severity label ``minimum``."""
    floor = dict((label, score) for score, label in LEVELS)[minimum]
    kept = [f for f in report.findings if f.score >= floor]
    result = Report(report.server, kept, report.errors, report.generated_at, report.instance)
    result.hidden = len(report.findings) - len(kept)
    result.baseline_counts = report.baseline_counts
    result.resolved = report.resolved
    return result


def only_changes(report):
    """Keep what is new, worse or better since the baseline; drop the unchanged."""
    kept = [f for f in report.findings if f.change != "same"]
    result = Report(report.server, kept, report.errors, report.generated_at, report.instance)
    result.hidden = report.hidden + len(report.findings) - len(kept)
    result.baseline_counts = report.baseline_counts
    result.resolved = report.resolved
    return result


def build_report(ctx, server, names=None, now=None):
    names = list(names or CHECKS)
    findings = []
    errors = []
    for name in names:
        try:
            findings.extend(CHECKS[name].run(ctx))
        except Exception as exc:  # one broken check must not sink the report
            errors.append(describe_error(name, exc))
    return Report(server, rank(apply_weights(correlate.apply(findings), ctx.options.get("weights") or {}), CHECKS), errors, now or datetime.datetime.now(), ctx.instance_info())
