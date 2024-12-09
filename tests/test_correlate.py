import unittest

from sqltriage import correlate
from sqltriage.models import Finding


def wait(wait_type, category, score=60):
    return Finding("waits", "Wait: " + wait_type, score, data={"wait_type": wait_type, "category": category})


def blocking(score=80):
    return Finding("blocking", "Blocking: spid 57 is blocking 6 session(s)", score, data={"head_session_id": 57})


class CorrelateTests(unittest.TestCase):
    def test_lock_wait_is_demoted_and_linked(self):
        symptom, cause = wait("LCK_M_X", "locking"), blocking()
        correlate.apply([symptom, cause])
        self.assertEqual(symptom.score, 45)
        self.assertEqual(symptom.evidence, ["probably the same problem as: Blocking: spid 57 is blocking 6 session(s)"])
        self.assertEqual(cause.score, 85)
        self.assertEqual(cause.evidence, ["also shows up as: LCK_M_X"])

    def test_no_cause_no_change(self):
        symptom = wait("LCK_M_X", "locking")
        correlate.apply([symptom])
        self.assertEqual((symptom.score, symptom.evidence), (60, []))

    def test_no_symptom_no_change(self):
        cause = blocking()
        correlate.apply([cause])
        self.assertEqual((cause.score, cause.evidence), (80, []))

    def test_several_symptoms_one_cause(self):
        a, b, cause = wait("LCK_M_X", "locking"), wait("LCK_M_S", "locking"), blocking()
        correlate.apply([a, b, cause])
        self.assertEqual((a.score, b.score), (45, 45))
        self.assertEqual(cause.evidence, ["also shows up as: LCK_M_S, LCK_M_X"])

    def test_unrelated_waits_are_untouched(self):
        other = wait("WRITELOG", "log")
        correlate.apply([other, blocking()])
        self.assertEqual(other.score, 60)

    def test_floor_and_ceiling(self):
        symptom, cause = wait("LCK_M_X", "locking", score=12), blocking(score=94)
        correlate.apply([symptom, cause])
        self.assertEqual(symptom.score, correlate.FLOOR)
        self.assertEqual(cause.score, 95)

    def test_tempdb_latches(self):
        symptom = wait("PAGELATCH_UP", "latch")
        cause = Finding("tempdb", "tempdb allocation contention", 76, data={"allocation_tasks": 15})
        correlate.apply([symptom, cause])
        self.assertEqual((symptom.score, cause.score), (45, 81))

    def test_tempdb_layout_finding_is_not_a_cause(self):
        symptom = wait("PAGELATCH_UP", "latch")
        layout = Finding("tempdb", "tempdb file layout", 65, data={"data_files": 4})
        correlate.apply([symptom, layout])
        self.assertEqual(symptom.score, 60)

    def test_sync_commit_needs_a_database_queue_finding(self):
        symptom = wait("HADR_SYNC_COMMIT", "ag")
        replica = Finding("ag", "replica unhealthy", 70, data={"ag": "A", "replica": "R"})
        correlate.apply([symptom, replica])
        self.assertEqual(symptom.score, 60)
        behind = Finding("ag", "database behind", 70, data={"log_send_queue_kb": 5000})
        correlate.apply([symptom, behind])
        self.assertEqual((symptom.score, behind.score), (50, 75))

    def test_cpu_pressure_must_be_real(self):
        symptom = wait("SOS_SCHEDULER_YIELD", "cpu")
        quiet = Finding("cpu", "CPU scheduler pressure: none seen", 0, data={"online_schedulers": 8})
        correlate.apply([symptom, quiet])
        self.assertEqual(symptom.score, 60)
        busy = Finding("cpu", "CPU scheduler pressure", 70, data={"online_schedulers": 8})
        correlate.apply([symptom, busy])
        self.assertEqual((symptom.score, busy.score), (50, 70))

    def test_a_symptom_is_demoted_once(self):
        symptom = wait("PAGELATCH_UP", "locking")  # matches two rules on purpose
        findings = [symptom, blocking(), Finding("tempdb", "contention", 70, data={"allocation_tasks": 3})]
        correlate.apply(findings)
        self.assertEqual(symptom.score, 45)


class LogTests(unittest.TestCase):
    def vlf(self, wait, database="PayrollDB", score=60):
        return Finding("vlf", "Log: {} has log reuse wait {}".format(database, wait), score,
                       data={"database": database, "log_reuse_wait": wait})

    def test_open_transaction_is_tied_to_the_blocking_chain(self):
        log = self.vlf("ACTIVE_TRANSACTION")
        chain = Finding("blocking", "Blocking: spid 57", 80, data={"databases": ["PayrollDB"]})
        correlate.apply([log, chain])
        self.assertEqual(log.score, 60)  # not a symptom to score down
        self.assertEqual(log.evidence, ["the open transaction holding the log is probably the head blocker in: Blocking: spid 57"])
        self.assertEqual(chain.score, 85)
        self.assertEqual(chain.evidence, ["also shows up as: Log: PayrollDB has log reuse wait ACTIVE_TRANSACTION"])

    def test_other_databases_are_not_tied(self):
        log = self.vlf("ACTIVE_TRANSACTION", database="Other")
        chain = Finding("blocking", "Blocking: spid 57", 80, data={"databases": ["PayrollDB"]})
        correlate.apply([log, chain])
        self.assertEqual((log.evidence, chain.score), ([], 80))

    def test_secondary_behind(self):
        log = self.vlf("AVAILABILITY_REPLICA", database="Sales")
        behind = Finding("ag", "Sales on R2 is behind", 70, data={"database": "Sales", "log_send_queue_kb": 9000})
        elsewhere = Finding("ag", "Other on R2 is behind", 90, data={"database": "Other", "log_send_queue_kb": 9000})
        correlate.apply([log, behind, elsewhere])
        self.assertEqual(log.evidence, ["the log cannot be reused while a secondary is behind: Sales on R2 is behind"])
        self.assertEqual((behind.score, elsewhere.score), (75, 90))

    def test_a_low_scoring_finding_is_not_raised_by_a_zero_demotion(self):
        log = self.vlf("ACTIVE_TRANSACTION", score=3)
        chain = Finding("blocking", "Blocking", 80, data={"databases": ["PayrollDB"]})
        correlate.apply([log, chain])
        self.assertEqual(log.score, 3)


if __name__ == "__main__":
    unittest.main()
