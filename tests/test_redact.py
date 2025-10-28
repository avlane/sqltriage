import unittest

from sqltriage.models import Finding
from sqltriage.redact import NameMasker, redact_findings, redact_literals


class LiteralTests(unittest.TestCase):
    def test_strings(self):
        self.assertEqual(redact_literals("WHERE Name = 'Alice Smith' AND X = N'it''s'"), "WHERE Name = '?' AND X = '?'")

    def test_hex_and_long_numbers(self):
        self.assertEqual(redact_literals("0x1A2B3C4D5E and 123456789"), "0x? and ?")

    def test_short_numbers_and_identifiers_survive(self):
        text = "TOP (50) spid 57 plan 4412 on dbo.Table2 at 2:1:8088 with 19 sessions"
        self.assertEqual(redact_literals(text), text)

    def test_email(self):
        self.assertEqual(redact_literals("owner jo.smith@example.com"), "owner <email>")

    def test_version_like_numbers_survive(self):
        self.assertEqual(redact_literals("SQL Server 15.0.4298.1 build 4298"), "SQL Server 15.0.4298.1 build 4298")


class NameTests(unittest.TestCase):
    def test_tree_login_host(self):
        masker = NameMasker()
        self.assertEqual(
            masker.mask("spid 61 svc_payroll@APP02 and spid 70 svc_payroll@APP02 and rpt@RPT01"),
            "spid 61 user1@host1 and spid 70 user1@host1 and user2@host2",
        )

    def test_domain_user(self):
        masker = NameMasker()
        self.assertEqual(masker.mask(r"CORP\jmendez@LAPTOP-JM07"), "user1@host1")
        self.assertEqual(masker.mask(r"run as CORP\svc_sql"), "run as user2")

    def test_same_name_any_case_is_one_token(self):
        masker = NameMasker()
        self.assertEqual(masker.mask("Bob@h1 bob@H1"), "user1@host1 user1@host1")


class FindingTests(unittest.TestCase):
    def finding(self):
        return Finding(
            "blocking",
            "Blocking: spid 57",
            80,
            detail="login 'sa'",
            evidence=["text: SELECT * FROM t WHERE ssn = '123-45-6789'", r"spid 57 CORP\jmendez@LAPTOP-JM07"],
            advice=["ask jo@example.com"],
            block=["spid 61 svc@APP02"],
        )

    def test_literals_only(self):
        (f,) = redact_findings([self.finding()])
        self.assertEqual(f.detail, "login '?'")
        self.assertEqual(f.evidence[0], "text: SELECT * FROM t WHERE ssn = '?'")
        self.assertEqual(f.advice, ["ask <email>"])
        self.assertIn("LAPTOP-JM07", f.evidence[1])  # names untouched

    def test_names_too(self):
        (f,) = redact_findings([self.finding()], names=True)
        self.assertEqual(f.evidence[1], "spid 57 user1@host1")
        self.assertEqual(f.block, ["spid 61 user2@host2"])

    def test_numbers_that_look_like_data_are_masked_in_titles_too(self):
        (f,) = redact_findings([Finding("c", "Query for account 12345678", 10)])
        self.assertEqual(f.title, "Query for account ?")


if __name__ == "__main__":
    unittest.main()
