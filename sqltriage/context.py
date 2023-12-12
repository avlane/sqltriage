"""Shared state handed to every check."""
import time
from dataclasses import dataclass, field
from typing import Callable, Dict

from .source import Source


@dataclass
class Context:
    source: Source
    interval: float = 10.0
    top: int = 5
    sleep: Callable = time.sleep
    options: Dict = field(default_factory=dict)
    # results one check wants to share with another (for example the wait delta)
    cache: Dict = field(default_factory=dict)

    def wants_database(self, name):
        """Apply the --databases / --exclude-databases choice (case-insensitive)."""
        only = self.options.get("databases")
        if only and name.lower() not in [d.lower() for d in only]:
            return False
        skip = self.options.get("exclude_databases") or []
        return name.lower() not in [d.lower() for d in skip]
