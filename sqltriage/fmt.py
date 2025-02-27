"""Small formatting helpers shared by the checks."""
import datetime


def span(seconds):
    """3725 -> '1h02m', 540 -> '9m'."""
    hours, rest = divmod(int(seconds), 3600)
    return "{}h{:02d}m".format(hours, rest // 60) if hours else "{}m".format(rest // 60)


def to_datetime(value):
    """A datetime from a datetime or an ISO 8601 string (recorded fixtures hold strings)."""
    if value is None or isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value))
