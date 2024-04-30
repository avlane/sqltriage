"""Small formatting helpers shared by the checks."""


def span(seconds):
    """3725 -> '1h02m', 540 -> '9m'."""
    hours, rest = divmod(int(seconds), 3600)
    return "{}h{:02d}m".format(hours, rest // 60) if hours else "{}m".format(rest // 60)
