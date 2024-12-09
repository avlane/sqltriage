"""Link findings that are two views of one problem.

A wait finding such as LCK_M_X says *what* sessions are waiting for; the
blocking finding says *who* is holding it.  Reporting both at full strength
pushes the symptom up beside its cause.  Each rule below names a symptom and a
cause: when both are present the symptom is noted as a consequence and scored
down, and the cause gets a small boost and a pointer to what it explains.
"""
from collections import namedtuple

# pair(symptom, cause) narrows which cause goes with which symptom, phrase is
# the note added to the symptom ({} is the cause's title).
Rule = namedtuple("Rule", "name symptom cause demote boost pair phrase")
Rule.__new__.__defaults__ = (lambda symptom, cause: True, "probably the same problem as: {}")

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


def _same_database(symptom, cause):
    database = symptom.data.get("database")
    return database == cause.data.get("database") or database in cause.data.get("databases", ())


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
    Rule(
        "the log is held by an open transaction that is blocking others",
        lambda f: f.check == "vlf" and f.data.get("log_reuse_wait") == "ACTIVE_TRANSACTION",
        _check("blocking"),
        0,
        5,
        _same_database,
        "the open transaction holding the log is probably the head blocker in: {}",
    ),
    Rule(
        "the log is held by a secondary that is behind",
        lambda f: f.check == "vlf" and f.data.get("log_reuse_wait") == "AVAILABILITY_REPLICA",
        _check("ag", "log_send_queue_kb"),
        0,
        5,
        _same_database,
        "the log cannot be reused while a secondary is behind: {}",
    ),
]


def apply(findings, rules=RULES):
    """Adjust findings in place and return them."""
    demoted = set()
    for rule in rules:
        causes = [f for f in findings if rule.cause(f)]
        matched = {}  # id(cause) -> (cause, [symptoms])
        for symptom in findings:
            if not rule.symptom(symptom) or id(symptom) in demoted:
                continue
            fitting = [c for c in causes if rule.pair(symptom, c)]
            if not fitting:
                continue
            cause = max(fitting, key=lambda f: f.score)
            symptom.evidence.append(rule.phrase.format(cause.title))
            if rule.demote:
                symptom.score = max(FLOOR, symptom.score - rule.demote)
                demoted.add(id(symptom))
            matched.setdefault(id(cause), (cause, []))[1].append(symptom)
        for cause, symptoms in matched.values():
            if rule.boost:
                cause.score = min(max(cause.score, CEILING), cause.score + rule.boost)
            names = ", ".join(sorted(s.data.get("wait_type") or s.title for s in symptoms))
            cause.evidence.append("also shows up as: {}".format(names))
    return findings
