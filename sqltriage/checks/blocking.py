"""Blocking chains, rendered as trees rooted at the head blocker."""
import re
from collections import Counter

from ..fmt import span
from ..models import Finding
from ..source import SourceError

NAME = "blocking"

# Every user session that is blocked, plus every session that blocks one.  The
# head blocker is often asleep (an open transaction nobody is running anything
# in), so it has no row in sys.dm_exec_requests: hence the LEFT JOIN.
SESSIONS_SQL = """\
SET NOCOUNT ON;
SELECT s.session_id,
       r.blocking_session_id,
       r.wait_type,
       r.wait_time AS wait_time_ms,
       r.wait_resource,
       r.status AS request_status,
       r.command,
       DB_NAME(COALESCE(r.database_id, s.database_id)) AS database_name,
       s.login_name,
       s.host_name,
       s.program_name,
       s.open_transaction_count,
       st.text AS sql_text
FROM sys.dm_exec_sessions AS s
LEFT JOIN sys.dm_exec_requests AS r
       ON r.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS st
WHERE s.is_user_process = 1
  AND (r.blocking_session_id <> 0
       OR s.session_id IN (SELECT blocking_session_id
                           FROM sys.dm_exec_requests
                           WHERE blocking_session_id <> 0));"""


# What the head blocker last sent.  A sleeping session has no current request,
# so ask by session only; TOP (1) keeps MARS sessions to a single row.
INPUT_BUFFER_SQL = """\
SELECT TOP (1) event_type, parameters, event_info
FROM sys.dm_exec_input_buffer(?, NULL);"""

INPUT_BUFFER_LIMIT = 300

# Run in the blocked session's database: which table and index a KEY lock is on.
KEY_OBJECT_SQL = """\
SELECT OBJECT_SCHEMA_NAME(p.object_id) AS schema_name,
       OBJECT_NAME(p.object_id) AS object_name,
       i.name AS index_name
FROM sys.partitions AS p
JOIN sys.indexes AS i
  ON i.object_id = p.object_id
 AND i.index_id = p.index_id
WHERE p.hobt_id = ?;"""

# How long the head blocker's oldest transaction has been open.  A sleeping
# blocker has no start_time on a request, but its transaction knows.
TRANSACTION_SQL = """\
SELECT MIN(tat.transaction_begin_time) AS began_at,
       DATEDIFF(SECOND, MIN(tat.transaction_begin_time), GETDATE()) AS age_seconds,
       COUNT(*) AS open_transactions
FROM sys.dm_tran_session_transactions AS tst
JOIN sys.dm_tran_active_transactions AS tat
  ON tat.transaction_id = tst.transaction_id
WHERE tst.session_id = ?;"""

_KEY_RESOURCE = re.compile(r"^KEY:\s*(\d+):(\d+)")

class Node:
    def __init__(self, session_id, row=None):
        row = row or {}
        self.session_id = session_id
        self.placeholder = not row
        blocker = row.get("blocking_session_id") or 0
        # Negative values are not sessions (-2 orphaned DTC, -3 deferred
        # recovery, -4 latch owner unknown), so they never form a chain.
        self.system_blocker = blocker if blocker < 0 else None
        self.blocker = blocker if blocker > 0 and blocker != session_id else None
        self.wait_type = row.get("wait_type")
        self.wait_ms = row.get("wait_time_ms") or 0
        self.wait_resource = row.get("wait_resource")
        self.status = row.get("request_status") or "sleeping"
        self.command = row.get("command")
        self.database = row.get("database_name")
        self.login = row.get("login_name")
        self.host = row.get("host_name")
        self.program = row.get("program_name")
        self.open_tran = row.get("open_transaction_count") or 0
        self.sql_text = row.get("sql_text")
        self.children = []
        self.cycle = False  # True for the node chosen to start a blocking cycle

    def descendants(self):
        """Every session blocked, directly or not, by this one (cycle safe)."""
        found = []
        seen = {self.session_id}
        stack = list(self.children)
        while stack:
            node = stack.pop()
            if node.session_id in seen:
                continue
            seen.add(node.session_id)
            found.append(node)
            stack.extend(node.children)
        return found


def build_chains(rows):
    """Link sessions to the sessions blocking them; return the head blockers."""
    nodes = {row["session_id"]: Node(row["session_id"], row) for row in rows}
    for node in list(nodes.values()):
        if node.blocker is not None:
            if node.blocker not in nodes:
                nodes[node.blocker] = Node(node.blocker)
            nodes[node.blocker].children.append(node)
    heads = [n for n in nodes.values() if n.blocker is None and (n.children or n.system_blocker)]
    # Sessions that block each other in a ring have a blocker each, so none of
    # them is a head and they would vanish.  Start a chain at the session that
    # has waited longest in each ring.
    reached = set()
    for head in heads:
        reached.add(head.session_id)
        reached.update(n.session_id for n in head.descendants())
    while True:
        lost = [n for n in nodes.values() if n.session_id not in reached]
        if not lost:
            break
        start = max(lost, key=lambda n: (n.wait_ms, -n.session_id))
        start.cycle = True
        heads.append(start)
        reached.add(start.session_id)
        reached.update(n.session_id for n in start.descendants())
    for node in nodes.values():
        node.children.sort(key=lambda c: -c.wait_ms)
    heads.sort(key=lambda h: (-len(h.descendants()), h.session_id))
    return heads


def describe(node):
    who = "{}@{}".format(node.login or "?", node.host or "?")
    if node.placeholder:
        return "spid {} (system or filtered session)".format(node.session_id)
    bits = ["spid {}".format(node.session_id)]
    if node.blocker is None:
        bits.append("[{}]".format(node.status))
    elif node.wait_type:
        bits.append("{} {:.1f}s".format(node.wait_type, node.wait_ms / 1000.0))
    bits.append(who)
    if node.database:
        bits.append("db=" + node.database)
    if node.open_tran:
        bits.append("open_tran=%d" % node.open_tran)
    return " ".join(bits)


def tree_lines(head):
    lines = [describe(head) + ("  <- blocking cycle starts here" if head.cycle else "  <- head blocker")]

    def walk(node, prefix, path):
        for index, child in enumerate(node.children):
            last = index == len(node.children) - 1
            branch = "{}{}- ".format(prefix, "`" if last else "|")
            if child.session_id in path:
                lines.append("{}(back to spid {}: the cycle closes)".format(branch, child.session_id))
                continue
            lines.append(branch + describe(child))
            if child.wait_resource:
                lines.append("{}{}    {}".format(prefix, " " if last else "|", child.wait_resource))
            walk(child, prefix + ("  " if last else "| "), path | {child.session_id})

    walk(head, "", {head.session_id})
    return lines


def parse_key_resource(text):
    """'KEY: 5:72057594043432960 (8194443284a0)' -> (5, 72057594043432960), else None."""
    match = _KEY_RESOURCE.match(text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def contended_objects(ctx, head):
    """Count the victims waiting on each table/index, for KEY locks we can resolve."""
    counts = Counter()
    resolved = {}
    for victim in head.descendants():
        key = parse_key_resource(victim.wait_resource)
        if key is None or not victim.database:
            continue
        hobt_id = key[1]
        if (victim.database, hobt_id) not in resolved:
            try:
                rows = ctx.source.fetch(
                    "blocking_object_{}".format(hobt_id), KEY_OBJECT_SQL, (hobt_id,), database=victim.database
                )
            except SourceError:
                rows = []
            if rows:
                row = rows[0]
                resolved[(victim.database, hobt_id)] = "{}.{}.{}".format(row["schema_name"], row["object_name"], row["index_name"])
            else:
                resolved[(victim.database, hobt_id)] = None
        name = resolved[(victim.database, hobt_id)]
        if name:
            counts[name] += 1
    return counts


def input_buffer(ctx, head):
    """The head blocker's last batch as one trimmed line, or None."""
    if head.placeholder and not head.children:
        return None
    try:
        rows = ctx.source.fetch(
            "blocking_inputbuffer_{}".format(head.session_id), INPUT_BUFFER_SQL, (head.session_id,)
        )
    except SourceError:
        return None
    if not rows or not rows[0].get("event_info"):
        return None
    text = " ".join(str(rows[0]["event_info"]).split())
    if len(text) > INPUT_BUFFER_LIMIT:
        text = text[: INPUT_BUFFER_LIMIT - 3] + "..."
    return "{}: {}".format(rows[0].get("event_type") or "input buffer", text)


def transaction_age(ctx, head):
    """(began_at, age_seconds) of the head blocker's oldest open transaction, or None."""
    if head.placeholder or not head.open_tran:
        return None
    try:
        rows = ctx.source.fetch(
            "blocking_transaction_{}".format(head.session_id), TRANSACTION_SQL, (head.session_id,)
        )
    except SourceError:
        return None
    if not rows or rows[0].get("began_at") is None:
        return None
    return rows[0]["began_at"], int(rows[0]["age_seconds"])


def score_chain(head, age_seconds=None):
    victims = head.descendants()
    longest = max([v.wait_ms for v in victims] or [0]) / 1000.0
    score = 35 + min(30, len(victims) * 3) + min(25, longest / 4.0)
    if head.status == "sleeping" and head.open_tran:
        score += 8
    if age_seconds:
        score += min(10, age_seconds / 600.0)
    return int(min(98, round(score)))


def run(ctx):
    rows = ctx.source.fetch("blocking_sessions", SESSIONS_SQL)
    findings = []
    for head in build_chains(rows):
        victims = head.descendants()
        longest = max([v.wait_ms for v in victims] or [0]) / 1000.0
        if head.cycle:
            title = "Blocking: {} sessions are blocking each other in a cycle".format(len(victims) + 1)
            detail = "Longest wait {:.0f}s; nothing in the ring can proceed.".format(longest)
        elif victims:
            title = "Blocking: spid {} is blocking {} session(s)".format(head.session_id, len(victims))
            detail = "Longest wait {:.0f}s.".format(longest)
        else:
            title = "Blocking: spid {} is stuck".format(head.session_id)
            detail = "Blocked by a system condition ({}).".format(head.system_blocker)
        evidence = []
        objects = contended_objects(ctx, head)
        if objects:
            evidence.append(
                "locks contended: " + ", ".join("{} ({} waiting)".format(n, c) for n, c in objects.most_common())
            )
        age = transaction_age(ctx, head)
        if age:
            evidence.append("oldest open transaction began {} ({} ago)".format(age[0], span(age[1])))
        buffered = input_buffer(ctx, head)
        if buffered:
            evidence.append("head blocker input buffer - " + buffered)
        findings.append(
            Finding(
                check=NAME,
                title=title,
                score=score_chain(head, age[1] if age else None) if victims else 40,
                detail=detail,
                evidence=evidence,
                block=tree_lines(head),
                advice=(
                    ["A ring of waits that does not clear is not a lock deadlock; look at the wait types involved."]
                    if head.cycle
                    else ["Find out why spid {} is not finishing; KILL is the last resort.".format(head.session_id)]
                )
                if victims
                else [],
                data={
                    "head_session_id": head.session_id,
                    "databases": sorted({n.database for n in [head] + victims if n.database}),
                    "victims": len(victims),
                    "longest_wait_s": round(longest, 1),
                },
            )
        )
    return findings
