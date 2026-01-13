"""Result types shared by the checks and the renderers."""
from dataclasses import dataclass, field
from typing import Dict, List

# (minimum score, label); a finding's score runs from 0 to 100.
LEVELS = [(85, "critical"), (65, "high"), (40, "medium"), (15, "low"), (0, "info")]


def severity_for(score):
    for floor, label in LEVELS:
        if score >= floor:
            return label
    return "info"


def severity_rank(label):
    """0 (info) to 4 (critical), for comparing severities."""
    return [name for _, name in reversed(LEVELS)].index(label)


@dataclass
class Finding:
    check: str
    title: str
    score: int
    detail: str = ""
    evidence: List[str] = field(default_factory=list)
    advice: List[str] = field(default_factory=list)
    data: Dict = field(default_factory=dict)
    # preformatted lines (a tree, say) that a renderer must not reflow or bullet
    block: List[str] = field(default_factory=list)
    # against a baseline report: "new", "worse", "better" or "same"; empty without one
    change: str = ""

    @property
    def severity(self):
        return severity_for(self.score)

    @property
    def severity_rank(self):
        return severity_rank(self.severity)
