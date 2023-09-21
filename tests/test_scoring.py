import unittest

from sqltriage.models import Finding
from sqltriage.scoring import clamp, rank, step_score

LEVELS = [(100, 80), (10, 40), (1, 10)]


class StepScoreTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(step_score(500, LEVELS), 80)
        self.assertEqual(step_score(100, LEVELS), 80)
        self.assertEqual(step_score(99, LEVELS), 40)
        self.assertEqual(step_score(1, LEVELS), 10)
        self.assertEqual(step_score(0, LEVELS), 0)

    def test_none(self):
        self.assertEqual(step_score(None, LEVELS), 0)


class ClampTests(unittest.TestCase):
    def test_bounds_and_rounding(self):
        self.assertEqual(clamp(-5), 0)
        self.assertEqual(clamp(140), 100)
        self.assertEqual(clamp(61.6), 62)
        self.assertEqual(clamp(99, high=95), 95)


class RankTests(unittest.TestCase):
    def test_score_then_check_order_then_title(self):
        findings = [
            Finding("b", "zeta", 50),
            Finding("a", "beta", 50),
            Finding("a", "alpha", 50),
            Finding("b", "top", 90),
            Finding("unknown", "x", 50),
        ]
        ranked = rank(findings, ["a", "b"])
        self.assertEqual([f.title for f in ranked], ["top", "alpha", "beta", "zeta", "x"])


if __name__ == "__main__":
    unittest.main()
