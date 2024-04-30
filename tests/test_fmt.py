import unittest

from sqltriage.fmt import span


class SpanTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(span(0), "0m")
        self.assertEqual(span(540), "9m")
        self.assertEqual(span(3600), "1h00m")
        self.assertEqual(span(3725), "1h02m")
        self.assertEqual(span(32400), "9h00m")


if __name__ == "__main__":
    unittest.main()
