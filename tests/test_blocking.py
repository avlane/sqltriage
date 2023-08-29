import json
import os
import unittest

from sqltriage.checks import blocking
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


def session(spid, blocker=None, wait_ms=0, status="suspended", **extra):
    row = {"session_id": spid, "blocking_session_id": blocker, "wait_time_ms": wait_ms, "request_status": status}
    row.update(extra)
    return row


class ChainTests(unittest.TestCase):
    def test_simple_chain(self):
        rows = [session(10), session(11, 10, 5000), session(12, 11, 3000)]
        (head,) = blocking.build_chains(rows)
        self.assertEqual(head.session_id, 10)
        self.assertEqual(sorted(n.session_id for n in head.descendants()), [11, 12])

    def test_two_separate_chains_are_ordered_by_size(self):
        rows = [session(1), session(2, 1, 100), session(20), session(21, 20, 100), session(22, 20, 100)]
        heads = blocking.build_chains(rows)
        self.assertEqual([h.session_id for h in heads], [20, 1])

    def test_missing_blocker_becomes_placeholder(self):
        heads = blocking.build_chains([session(5, 99, 1000)])
        self.assertEqual(heads[0].session_id, 99)
        self.assertTrue(heads[0].placeholder)

    def test_negative_blocker_is_not_a_chain_link(self):
        (head,) = blocking.build_chains([session(5, -2, 1000)])
        self.assertEqual(head.session_id, 5)
        self.assertEqual(head.system_blocker, -2)
        self.assertEqual(head.children, [])

    def test_tree_lines(self):
        rows = [
            session(10, status=None, login_name="app", host_name="A1", open_transaction_count=1),
            session(11, 10, 5000, wait_type="LCK_M_X", wait_resource="KEY: 5:1 (abc)"),
        ]
        (head,) = blocking.build_chains(rows)
        lines = blocking.tree_lines(head)
        self.assertTrue(lines[0].endswith("<- head blocker"))
        self.assertIn("LCK_M_X 5.0s", lines[1])
        self.assertIn("KEY: 5:1 (abc)", lines[2])


class CycleTests(unittest.TestCase):
    def ring(self):
        return [
            session(10, 12, 3000, wait_type="LCK_M_X"),
            session(11, 10, 9000, wait_type="LCK_M_S"),
            session(12, 11, 1000, wait_type="LCK_M_U"),
        ]

    def test_ring_is_not_lost(self):
        (head,) = blocking.build_chains(self.ring())
        self.assertTrue(head.cycle)
        self.assertEqual(head.session_id, 11)  # waited longest
        self.assertEqual(sorted(n.session_id for n in head.descendants()), [10, 12])

    def test_tree_stops_where_the_ring_closes(self):
        (head,) = blocking.build_chains(self.ring())
        lines = blocking.tree_lines(head)
        self.assertIn("<- blocking cycle starts here", lines[0])
        self.assertTrue(any("the cycle closes" in line for line in lines))
        self.assertLess(len(lines), 10)

    def test_chain_with_a_ring_hanging_off_a_head(self):
        rows = [session(1), session(2, 1, 100)] + [
            session(10, 12, 3000), session(11, 10, 9000), session(12, 11, 1000), session(13, 12, 500)
        ]
        heads = blocking.build_chains(rows)
        self.assertEqual(sorted(h.session_id for h in heads), [1, 11])

    def test_finding_title(self):
        class Ring(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name == "blocking_sessions":
                    return self.ring()
                raise SourceError("none")

        (finding,) = blocking.run(Context(Ring(FIXTURES)))
        self.assertEqual(finding.title, "Blocking: 3 sessions are blocking each other in a cycle")


class RunTests(unittest.TestCase):
    def test_incident_fixture(self):
        findings = blocking.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual([f.data["head_session_id"] for f in findings], [57, 93])
        big, small = findings
        self.assertEqual(big.data["victims"], 6)
        self.assertEqual(big.data["longest_wait_s"], 94.0)
        self.assertGreater(big.score, small.score)
        self.assertGreaterEqual(big.score, 65)

    def test_head_blocker_input_buffer_is_shown(self):
        big, small = blocking.run(Context(FixtureSource(FIXTURES)))
        self.assertTrue(any("Object Id = 1134627161" in line for line in big.evidence))
        self.assertTrue(big.block[0].endswith("<- head blocker"))
        self.assertTrue(any("BEGIN TRAN; UPDATE dbo.Orders" in line for line in small.evidence))

    def test_missing_input_buffer_is_not_fatal(self):
        class Flaky(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                if name.startswith("blocking_inputbuffer"):
                    raise SourceError("denied")
                return super().fetch(name, sql, params, database)

        findings = blocking.run(Context(Flaky(FIXTURES)))
        self.assertEqual(len(findings), 2)
        self.assertFalse(any("input buffer" in line for line in findings[0].evidence))

    def test_long_input_buffer_is_trimmed(self):
        head = blocking.Node(1, {"session_id": 1})
        head.children.append(blocking.Node(2, {"session_id": 2, "blocking_session_id": 1}))

        class Big(FixtureSource):
            def fetch(self, name, sql, params=(), database=None):
                return [{"event_type": "Language Event", "event_info": "SELECT " + "x, " * 400}]

        line = blocking.input_buffer(Context(Big(FIXTURES)), head)
        self.assertTrue(line.endswith("..."))
        self.assertLess(len(line), 330)

    def test_fixture_is_valid_json_rows(self):
        with open(os.path.join(FIXTURES, "blocking_sessions.json")) as handle:
            self.assertIsInstance(json.load(handle), list)


if __name__ == "__main__":
    unittest.main()
