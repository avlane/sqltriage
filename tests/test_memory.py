import os
import unittest

from sqltriage.checks import memory
from sqltriage.context import Context
from sqltriage.source import FixtureSource, SourceError

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
        finding = [f for f in memory.run(Context(FixtureSource(FIXTURES))) if f.title.endswith("memory grant")][0]
        self.assertEqual(finding.title, "3 queries waiting for a memory grant")
        self.assertEqual(finding.data, {"waiting": 3, "longest_wait_s": 48.0, "requested_gb": 12.0})
        self.assertIn("3 request(s) have waited up to 48s for a memory grant; together they want 12.0 GB", finding.evidence)
        self.assertTrue(any(e.startswith("pool 2 semaphore 0: 21.0 of 22.0 GB granted, 3 waiting, 4 grant timeout(s)") for e in finding.evidence))
        self.assertFalse(any("semaphore 1" in e for e in finding.evidence))
        self.assertTrue(any("(next in line)" in e for e in finding.evidence))
        self.assertEqual(finding.score, 40 + 15 + 8 + 5)


def state(**overrides):
    row = {"total_physical_memory_kb": 64 * 1048576, "available_physical_memory_kb": 8 * 1048576,
           "system_memory_state_desc": "Available physical memory is high", "system_low_memory_signal_state": 0,
           "physical_memory_in_use_kb": 50 * 1048576, "process_physical_memory_low": 0, "process_virtual_memory_low": 0}
    row.update(overrides)
    return [row]


LIMITED = [{"max_server_memory_mb": 49152}]
UNLIMITED = [{"max_server_memory_mb": 2147483647}]


class PressureTests(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(memory.pressure_findings(state(), LIMITED, [{"page_life_expectancy": 5000}]), [])

    def test_os_low_memory_signal(self):
        (finding,) = memory.pressure_findings(state(system_low_memory_signal_state=1, system_memory_state_desc="Available physical memory is low"), LIMITED, [])
        self.assertEqual(finding.title, "Memory pressure: the OS is signalling low physical memory (Available physical memory is low)")
        self.assertEqual(finding.score, 65)
        self.assertIn("50.0 of 64.0 GB in use by SQL Server, 8.0 GB available to the OS", finding.evidence)

    def test_both_signals_score_higher(self):
        (finding,) = memory.pressure_findings(state(system_low_memory_signal_state=1, process_physical_memory_low=1), LIMITED, [])
        self.assertEqual(finding.score, 75)

    def test_virtual_address_space(self):
        (finding,) = memory.pressure_findings(state(process_virtual_memory_low=1), LIMITED, [])
        self.assertIn("virtual address space", finding.evidence[0])

    def test_max_server_memory_unset(self):
        (finding,) = memory.pressure_findings(state(), UNLIMITED, [])
        self.assertEqual(finding.title, "max server memory is not set on a 64 GB host")
        self.assertEqual(finding.score, 30)

    def test_small_hosts_are_left_alone(self):
        self.assertEqual(memory.pressure_findings(state(total_physical_memory_kb=4 * 1048576), UNLIMITED, []), [])

    def test_page_life_expectancy(self):
        (low,) = memory.pressure_findings(state(), LIMITED, [{"page_life_expectancy": 180}])
        (very_low,) = memory.pressure_findings(state(), LIMITED, [{"page_life_expectancy": 20}])
        self.assertEqual((low.score, very_low.score), (25, 45))
        self.assertEqual(low.title, "Page life expectancy is 180 seconds")

    def test_no_data(self):
        self.assertEqual(memory.pressure_findings([], [], []), [])

    def test_incident_run_includes_pressure_findings(self):
        titles = [f.title for f in memory.run(Context(FixtureSource(FIXTURES)))]
        self.assertEqual(titles[0], "max server memory is not set on a 64 GB host")
        self.assertIn("Page life expectancy is 180 seconds", titles)
        self.assertIn("3 queries waiting for a memory grant", titles)

    def test_unreadable_state_does_not_stop_the_grant_checks(self):
        class Denied(FixtureSource):
            def fetch(inner, name, sql, params=(), database=None):
                if name in ("memory_state", "memory_config", "memory_ple"):
                    raise SourceError("denied")
                return super().fetch(name, sql, params, database)

        titles = [f.title for f in memory.run(Context(Denied(FIXTURES)))]
        self.assertIn("3 queries waiting for a memory grant", titles)
        self.assertEqual(len(titles), 2)


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
