"""Helpers for turning measurements into 0-100 scores, and ranking findings."""


def step_score(value, levels):
    """Score for the first (floor, score) level whose floor ``value`` reaches.

    ``levels`` is ordered from the highest floor down.  ``None`` and anything
    below every floor score 0.
    """
    if value is None:
        return 0
    for floor, score in levels:
        if value >= floor:
            return score
    return 0


def clamp(score, low=0, high=100):
    return int(max(low, min(high, round(score))))


def apply_weights(findings, weights):
    """Scale scores by check: {"agent": 0.5} halves every Agent finding.

    Lets a site turn down checks that are noisy for it (or turn one up) without
    editing the tool.  Scores stay within 0-100.
    """
    for finding in findings:
        factor = weights.get(finding.check)
        if factor is not None:
            finding.score = clamp(finding.score * factor)
    return findings


def rank(findings, check_order):
    """Highest score first; ties go to the earlier check, then by title."""
    order = list(check_order)

    def key(finding):
        position = order.index(finding.check) if finding.check in order else len(order)
        return (-finding.score, position, finding.title)

    return sorted(findings, key=key)
