"""Shared state handed to every check."""
import fnmatch
import time
from dataclasses import dataclass, field
from typing import Callable, Dict

from . import instance
from .source import Source


class ManualClock:
    """A clock that only moves when asked to sleep; for replayed samples and tests."""

    def __init__(self, drift=0.0):
        self.t = 0.0
        self.drift = drift  # extra seconds every sleep takes beyond what was asked

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds + self.drift


@dataclass
class Context:
    source: Source
    interval: float = 10.0
    top: int = 5
    samples: int = 1  # consecutive intervals for the wait stats
    sleep: Callable = time.sleep
    clock: Callable = time.monotonic
    options: Dict = field(default_factory=dict)
    # results one check wants to share with another (for example the wait delta)
    cache: Dict = field(default_factory=dict)

    def instance_info(self):
        """The instance facts (see instance.py), fetched once; None if unreadable."""
        if "instance" not in self.cache:
            self.cache["instance"] = instance.load(self.source)
        return self.cache["instance"]

    def server_major(self):
        """Major version number (15 for SQL Server 2019), or None if unknown."""
        info = self.instance_info()
        return info.get("major_version") if info else None

    def job_ignored(self, name):
        """True if an Agent job name matches an --ignore-jobs pattern (shell style, any case)."""
        patterns = self.options.get("ignore_jobs") or []
        return any(fnmatch.fnmatchcase(name.lower(), p.lower()) for p in patterns)

    def wants_database(self, name):
        """Apply the --databases / --exclude-databases choice (case-insensitive)."""
        only = self.options.get("databases")
        if only and name.lower() not in [d.lower() for d in only]:
            return False
        skip = self.options.get("exclude_databases") or []
        return name.lower() not in [d.lower() for d in skip]
