"""Run the checks and rank what they found."""
import datetime

from . import instance
from .checks import CHECKS
from .models import LEVELS
from .scoring import rank


class Report:
    def __init__(self, server, findings, errors, generated_at, instance=None):
        self.hidden = 0  # findings dropped by filter()
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
    return result


def build_report(ctx, server, names=None, now=None):
    names = list(names or CHECKS)
    findings = []
    errors = []
    for name in names:
        try:
            findings.extend(CHECKS[name].run(ctx))
        except Exception as exc:  # one broken check must not sink the report
            errors.append("{}: {}".format(name, exc))
    return Report(server, rank(findings, CHECKS), errors, now or datetime.datetime.now(), instance.load(ctx.source))
