"""Run the checks and rank what they found."""
import datetime

from . import instance
from .checks import CHECKS


class Report:
    def __init__(self, server, findings, errors, generated_at, instance=None):
        self.instance = instance
        self.server = server
        self.findings = findings
        self.errors = errors
        self.generated_at = generated_at


def rank(findings):
    order = list(CHECKS)

    def key(f):
        position = order.index(f.check) if f.check in order else len(order)
        return (-f.score, position, f.title)

    return sorted(findings, key=key)


def build_report(ctx, server, names=None, now=None):
    names = list(names or CHECKS)
    findings = []
    errors = []
    for name in names:
        try:
            findings.extend(CHECKS[name].run(ctx))
        except Exception as exc:  # one broken check must not sink the report
            errors.append("{}: {}".format(name, exc))
    return Report(server, rank(findings), errors, now or datetime.datetime.now(), instance.load(ctx.source))
