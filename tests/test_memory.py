import os
import unittest

from sqltriage.checks import memory
from sqltriage.context import Context
from sqltriage.source import FixtureSource

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "incident1")


def waiting_row(spid=1, wait_ms=1000, requested=1048576, nxt=0):
    return {"session_id": spid, "wait_time_ms": wait_ms, "requested_memory_kb": requested, "dop": 2,
            "is_next_candidate": nxt, "statement_text": "SELECT 1"}


class WaitingTests(unittest.TestCase):
    def test_nothing_waiting(self):
        self.assertIsNone(memory.waiting_finding([], []))

    def test_title_pluralises(self):
        self.assertEqual(memory.waiting_finding([waiting_row()], []).title, "1 query waiting for a memory grant")
        self.assertEqual(memory.waiting_finding([waiting_row(), waiting_row(2)], []).title, "2 queries waiting for a memory grant")

    def test_more_and_longer_waits_score_higher(self):
        few = memory.waiting_finding([waiting_row(wait_ms=1000)], [])
        many = memory.waiting_finding([waiting_row(i, wait_ms=60000) for i in range(6)], [])
        self.assertLess(few.score, many.score)
        self.assertLessEqual(many.score, 95)

    def test_incident(self):
        (finding, hog) = memory.run(Context(FixtureSource(FIXTURES)))
        self.assertEqual(finding.title, "3 queries waiting for a memory grant")
        self.assertEqual(finding.data, {"waiting": 3, "longest_wait_s": 48.0, "requested_gb": 12.0})
        self.assertIn("3 request(s) have waited up to 48s for a memory grant; together they want 12.0 GB", finding.evidence)
        self.assertTrue(any(e.startswith("pool 2 semaphore 0: 21.0 of 22.0 GB granted, 3 waiting, 4 grant timeout(s)") for e in finding.evidence))
        self.assertFalse(any("semaphore 1" in e for e in finding.evidence))
        self.assertTrue(any("(next in line)" in e for e in finding.evidence))
        self.assertEqual(finding.score, 40 + 15 + 8 + 5)


class HogTests(unittest.TestCase):
    def row(self, granted, used):
        return {"session_id": 9, "granted_memory_kb": granted, "used_memory_kb": used, "dop": 4, "statement_text": "SELECT 2"}

    def test_big_unused_grant(self):
        (finding,) = memory.hog_findings([self.row(4 * 1048576, 100)])
        self.assertEqual(finding.title, "spid 9 holds a 4.0 GB memory grant and uses 0.0 GB")
        self.assertEqual(finding.score, 42)

    def test_small_or_well_used_grants_are_fine(self):
        self.assertEqual(memory.hog_findings([self.row(512 * 1024, 10)]), [])
        self.assertEqual(memory.hog_findings([self.row(4 * 1048576, 3 * 1048576)]), [])

    def test_incident_finds_only_the_hog(self):
        (finding,) = memory.hog_findings(FixtureSource(FIXTURES).fetch("memory_grants_top", ""))
        self.assertEqual(finding.data["session_id"], 112)


class HelperTests(unittest.TestCase):
    def test_shorten(self):
        self.assertEqual(memory.shorten(None), "")
        self.assertEqual(len(memory.shorten("x " * 200)), memory.STATEMENT_LIMIT)


if __name__ == "__main__":
    unittest.main()
