"""Link findings that are two views of one problem.

A wait finding such as LCK_M_X says *what* sessions are waiting for; the
blocking finding says *who* is holding it.  Reporting both at full strength
pushes the symptom up beside its cause.  Each rule below names a symptom and a
cause: when both are present the symptom is noted as a consequence and scored
down, and the cause gets a small boost and a pointer to what it explains.
"""
from collections import namedtuple

Rule = namedtuple("Rule", "name symptom cause demote boost")

FLOOR = 10  # a demoted symptom never goes below this
CEILING = 95


def _wait(prefixes=(), category=None, exact=()):
    def test(f):
        if f.check != "waits":
            return False
        wait_type = f.data.get("wait_type", "")
        if category and f.data.get("category") == category:
            return True
        return wait_type in exact or (bool(prefixes) and wait_type.startswith(tuple(prefixes)))

    return test


def _check(name, key=None, minimum=0):
    def test(f):
        return f.check == name and (key is None or f.data.get(key)) and f.score >= minimum

    return test


RULES = [
    Rule("lock waits are the blocking chain", _wait(category="locking"), _check("blocking"), 15, 5),
    Rule(
        "tempdb latch waits are allocation contention",
        _wait(prefixes=("PAGELATCH_",)),
        _check("tempdb", "allocation_tasks"),
        15,
        5,
    ),
    Rule(
        "sync commit waits are a lagging secondary",
        _wait(exact=("HADR_SYNC_COMMIT",)),
        _check("ag", "log_send_queue_kb"),
        10,
        5,
    ),
    Rule(
        "scheduler yields are CPU pressure",
        _wait(exact=("SOS_SCHEDULER_YIELD",)),
        _check("cpu", "online_schedulers", minimum=40),
        10,
        0,
    ),
]


def apply(findings, rules=RULES):
    """Adjust findings in place and return them."""
    demoted = set()
    for rule in rules:
        symptoms = [f for f in findings if rule.symptom(f) and id(f) not in demoted]
        causes = [f for f in findings if rule.cause(f)]
        if not symptoms or not causes:
            continue
        cause = max(causes, key=lambda f: f.score)
        for symptom in symptoms:
            symptom.evidence.append("probably the same problem as: {}".format(cause.title))
            symptom.score = max(FLOOR, symptom.score - rule.demote)
            demoted.add(id(symptom))
        if rule.boost:
            cause.score = min(CEILING if cause.score <= CEILING else cause.score, cause.score + rule.boost)
        names = ", ".join(sorted(s.data.get("wait_type", s.title) for s in symptoms))
        cause.evidence.append("also shows up as: {}".format(names))
    return findings
