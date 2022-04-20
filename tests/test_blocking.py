import json
import os
import unittest

from sqltriage.checks import blocking
from sqltriage.context import Context
from sqltriage.source import FixtureSource

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


class RunTests(unittest.TestCase):
    def test_incident_fixture(self):
        findings = blocking.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual([f.data["head_session_id"] for f in findings], [57, 93])
        big, small = findings
        self.assertEqual(big.data["victims"], 6)
        self.assertEqual(big.data["longest_wait_s"], 94.0)
        self.assertGreater(big.score, small.score)
        self.assertGreaterEqual(big.score, 65)

    def test_fixture_is_valid_json_rows(self):
        with open(os.path.join(FIXTURES, "blocking_sessions.json")) as handle:
            self.assertIsInstance(json.load(handle), list)


if __name__ == "__main__":
    unittest.main()
