"""Small formatting helpers shared by the checks."""
import datetime
import re


def span(seconds):
    """3725 -> '1h02m', 540 -> '9m'."""
    hours, rest = divmod(int(seconds), 3600)
    return "{}h{:02d}m".format(hours, rest // 60) if hours else "{}m".format(rest // 60)


def to_datetime(value):
    """A datetime from a datetime or an ISO 8601 string (recorded fixtures hold strings)."""
    if value is None or isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value))


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")


def clean_text(value, limit=None):
    """One printable line from text that came out of SQL Server.

    Batches can hold NUL and other control characters, and the statement offsets
    in sys.dm_exec_requests are byte positions that may cut a surrogate pair in
    half.  A lone surrogate cannot be encoded as UTF-8, so writing the report
    would crash; they become U+FFFD instead.  Whitespace is collapsed to single
    spaces and the result is cut to ``limit`` characters with "..." if longer.
    """
    text = _LONE_SURROGATE.sub("\ufffd", _CONTROL.sub(" ", str(value or "")))
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text

