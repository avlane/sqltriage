import unittest

from sqltriage.fmt import clean_text, span


class SpanTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(span(0), "0m")
        self.assertEqual(span(540), "9m")
        self.assertEqual(span(3600), "1h00m")
        self.assertEqual(span(3725), "1h02m")
        self.assertEqual(span(32400), "9h00m")


class CleanTextTests(unittest.TestCase):
    def test_whitespace_is_collapsed(self):
        self.assertEqual(clean_text("SELECT  1\r\n  FROM\tt2"), "SELECT 1 FROM t2")

    def test_none_and_empty(self):
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text(""), "")

    def test_control_characters_become_spaces(self):
        self.assertEqual(clean_text("SELECT\x00 1\x07\x1b"), "SELECT 1")

    def test_a_lone_surrogate_is_replaced_so_the_text_can_be_written(self):
        text = clean_text("SELECT '\ud83d' FROM t2")
        self.assertEqual(text, "SELECT '\ufffd' FROM t2")
        text.encode("utf-8")

    def test_a_whole_pair_is_a_real_character(self):
        self.assertEqual(clean_text("caf\u00e9 \U0001f600"), "caf\u00e9 \U0001f600")

    def test_limit(self):
        self.assertEqual(clean_text("abcdefghij", 8), "abcde...")
        self.assertEqual(clean_text("abcdefgh", 8), "abcdefgh")
        self.assertEqual(clean_text("ab cd ef gh ij", 9), "ab cd...")  # no dangling space before the dots

    def test_non_strings(self):
        self.assertEqual(clean_text(1234), "1234")


if __name__ == "__main__":
    unittest.main()
