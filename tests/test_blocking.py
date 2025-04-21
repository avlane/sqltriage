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


class BlipTests(unittest.TestCase):
    def test_short_waits_are_dropped(self):
        rows = [session(1), session(2, 1, 300), session(3, 1, 400)]
        self.assertEqual(blocking.build_chains(rows, 2000), [])
        self.assertEqual(len(blocking.build_chains(rows)), 1)

    def test_long_waiters_stay_with_their_head(self):
        rows = [session(1), session(2, 1, 300), session(3, 1, 5000)]
        (head,) = blocking.build_chains(rows, 2000)
        self.assertEqual([n.session_id for n in head.descendants()], [3])

    def test_a_short_waiter_that_blocks_a_long_one_stays(self):
        rows = [session(1), session(2, 1, 500), session(3, 2, 6000)]
        (head,) = blocking.build_chains(rows, 2000)
        self.assertEqual(sorted(n.session_id for n in head.descendants()), [2, 3])

    def test_system_blockers_are_kept(self):
        (head,) = blocking.build_chains([session(5, -2, 100)], 2000)
        self.assertEqual(head.system_blocker, -2)

    def test_threshold_option_reaches_the_check(self):
        quiet = Context(FixtureSource(FIXTURES), options={"blocking_min_wait_ms": 10000})
        findings = blocking.run(quiet)
        self.assertEqual([f.data["victims"] for f in findings], [5])  # spid 81 (4s) and the 6 s one on spid 93 dropped out
        self.assertEqual(len(findings), 1)

    def test_default_threshold_keeps_the_fixture(self):
        self.assertEqual(len(blocking.run(Context(FixtureSource(FIXTURES)))), 2)


class CycleTests(unittest.TestCase):
    def ring(self):
        return [
            session(10, 12, 3000, wait_type="LCK_M_X"),
            session(11, 10, 9000, wait_type="LCK_M_S"),
            session(12, 11, 1000, wait_type="LCK_M_U"),
        ]

    def test_an_idle_session_is_not_mistaken_for_a_ring(self):
        self.assertEqual(blocking.build_chains([session(7, status=None)]), [])

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


class ResourceTests(unittest.TestCase):
    def test_parse_key_resource(self):
        self.assertEqual(blocking.parse_key_resource("KEY: 5:72057594043432960 (8194443284a0)"), (5, 72057594043432960))
        self.assertIsNone(blocking.parse_key_resource("PAGE: 5:1:88213"))
        self.assertIsNone(blocking.parse_key_resource(None))

    def test_objects_are_counted_per_index(self):
        ctx = Context(FixtureSource(FIXTURES))
        head = blocking.build_chains(blocking_rows())[0]
        counts = blocking.contended_objects(ctx, head)
        self.assertEqual(counts["dbo.PayRun.PK_PayRun"], 3)
        self.assertEqual(counts["dbo.PayRunLine.IX_PayRunLine_PayRunId"], 3)

    def test_unresolvable_keys_are_skipped(self):
        class Denied(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                raise SourceError("denied")

        head = blocking.build_chains(blocking_rows())[0]
        self.assertEqual(len(blocking.contended_objects(Context(Denied(FIXTURES)), head)), 0)

    def test_each_hobt_is_looked_up_once(self):
        calls = []

        class Counting(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                calls.append(name)
                return super().fetch(name, sql, params, database)

        head = blocking.build_chains(blocking_rows())[0]
        blocking.contended_objects(Context(Counting(FIXTURES)), head)
        self.assertEqual(sorted(calls), ["blocking_object_72057594043432960", "blocking_object_72057594043498496"])

    def test_finding_lists_the_objects(self):
        big, _ = blocking.run(Context(FixtureSource(FIXTURES)))
        self.assertIn("locks contended: dbo.PayRun.PK_PayRun (3 waiting), dbo.PayRunLine.IX_PayRunLine_PayRunId (3 waiting)", big.evidence)


class TransactionAgeTests(unittest.TestCase):
    def test_age_from_fixture(self):
        ctx = Context(FixtureSource(FIXTURES))
        head = blocking.build_chains(blocking_rows())[0]
        self.assertEqual(blocking.transaction_age(ctx, head), ("2023-03-14T08:51:20", 4140))

    def test_session_without_a_transaction_is_not_asked(self):
        class Boom(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                raise AssertionError("should not query")

        node = blocking.Node(5, {"session_id": 5, "open_transaction_count": 0})
        self.assertIsNone(blocking.transaction_age(Context(Boom(FIXTURES)), node))

    def test_null_aggregate_is_ignored(self):
        class Null(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                return [{"began_at": None, "age_seconds": None, "open_transactions": 0}]

        node = blocking.Node(5, {"session_id": 5, "open_transaction_count": 1})
        self.assertIsNone(blocking.transaction_age(Context(Null(FIXTURES)), node))

    def test_old_transactions_score_higher(self):
        head = blocking.build_chains(blocking_rows())[0]
        self.assertGreater(blocking.score_chain(head, 3600), blocking.score_chain(head, None))
        self.assertLessEqual(blocking.score_chain(head, 10 ** 6), 98)

    def test_finding_mentions_the_age(self):
        big, small = blocking.run(Context(FixtureSource(FIXTURES)))
        self.assertIn("oldest open transaction began 2023-03-14T08:51:20 (1h09m ago)", big.evidence)
        self.assertIn("(13m ago)", " ".join(small.evidence))


def blocking_rows():
    with open(os.path.join(FIXTURES, "blocking_sessions.json")) as handle:
        return json.load(handle)


class RunTests(unittest.TestCase):
    def test_incident_fixture(self):
        findings = blocking.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual([f.data["head_session_id"] for f in findings], [57, 93])
        big, small = findings
        self.assertEqual(big.data["victims"], 6)
        self.assertEqual(big.data["databases"], ["PayrollDB"])
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
