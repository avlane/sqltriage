"""Run the checks and rank what they found."""
import datetime

from . import instance
from .checks import CHECKS
from .scoring import rank


class Report:
    def __init__(self, server, findings, errors, generated_at, instance=None):
        self.instance = instance
        self.server = server
        self.findings = findings
        self.errors = errors
        self.generated_at = generated_at


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
