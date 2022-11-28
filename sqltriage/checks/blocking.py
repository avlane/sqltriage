"""Blocking chains, rendered as trees rooted at the head blocker."""
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

    def descendants(self):
        found = []
        stack = list(self.children)
        while stack:
            node = stack.pop()
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
    lines = [describe(head) + "  <- head blocker"]

    def walk(node, prefix):
        for index, child in enumerate(node.children):
            last = index == len(node.children) - 1
            lines.append("{}{}- {}".format(prefix, "`" if last else "|", describe(child)))
            if child.wait_resource:
                lines.append("{}{}    {}".format(prefix, " " if last else "|", child.wait_resource))
            walk(child, prefix + ("  " if last else "| "))

    walk(head, "")
    return lines


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


def score_chain(head):
    victims = head.descendants()
    longest = max([v.wait_ms for v in victims] or [0]) / 1000.0
    score = 35 + min(30, len(victims) * 3) + min(25, longest / 4.0)
    if head.status == "sleeping" and head.open_tran:
        score += 8
    return int(min(98, round(score)))


def run(ctx):
    rows = ctx.source.fetch("blocking_sessions", SESSIONS_SQL)
    findings = []
    for head in build_chains(rows):
        victims = head.descendants()
        longest = max([v.wait_ms for v in victims] or [0]) / 1000.0
        if victims:
            title = "Blocking: spid {} is blocking {} session(s)".format(head.session_id, len(victims))
            detail = "Longest wait {:.0f}s.".format(longest)
        else:
            title = "Blocking: spid {} is stuck".format(head.session_id)
            detail = "Blocked by a system condition ({}).".format(head.system_blocker)
        evidence = []
        buffered = input_buffer(ctx, head)
        if buffered:
            evidence.append("head blocker input buffer - " + buffered)
        findings.append(
            Finding(
                check=NAME,
                title=title,
                score=score_chain(head) if victims else 40,
                detail=detail,
                evidence=evidence,
                block=tree_lines(head),
                advice=["Find out why spid {} is not finishing; KILL is the last resort.".format(head.session_id)]
                if victims
                else [],
                data={
                    "head_session_id": head.session_id,
                    "victims": len(victims),
                    "longest_wait_s": round(longest, 1),
                },
            )
        )
    return findings
