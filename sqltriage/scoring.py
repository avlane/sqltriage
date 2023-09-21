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


def rank(findings, check_order):
    """Highest score first; ties go to the earlier check, then by title."""
    order = list(check_order)

    def key(finding):
        position = order.index(finding.check) if finding.check in order else len(order)
        return (-finding.score, position, finding.title)

    return sorted(findings, key=key)
