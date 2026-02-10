"""A directory of saved reports, one JSON file per run.

``--save DIR`` adds the report to the directory; ``--baseline-dir DIR`` compares
with the newest one saved for the same server.
"""
import os
import re

PREFIX = "sqltriage-"


def safe_name(server):
    """A server name that can sit in a file name: SQLPROD01\\INST1 -> SQLPROD01_INST1."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", server).strip("_") or "server"


def file_name(server, when):
    return "{}{}-{}.json".format(PREFIX, safe_name(server), when.strftime("%Y%m%d-%H%M%S"))


def save(directory, server, when, text):
    """Write the JSON report ``text`` and return its path."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, file_name(server, when))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def latest(directory, server):
    """Path of the newest saved report for ``server``, or None."""
    prefix = "{}{}-".format(PREFIX, safe_name(server))
    pattern = re.compile(re.escape(prefix) + r"\d{8}-\d{6}\.json$")
    try:
        names = [n for n in os.listdir(directory) if pattern.match(n)]
    except OSError:
        return None
    # the timestamp is fixed width, so the names sort in time order
    return os.path.join(directory, max(names)) if names else None
