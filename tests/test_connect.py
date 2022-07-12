import unittest

from sqltriage.connect import build_connection_string, choose_driver

D17 = "ODBC Driver 17 for SQL Server"
D18 = "ODBC Driver 18 for SQL Server"


class DriverTests(unittest.TestCase):
    def test_prefers_18(self):
        self.assertEqual(choose_driver([D17, D18, "SQL Server"]), D18)

    def test_falls_back_to_17(self):
        self.assertEqual(choose_driver([D17, "SQL Server"]), D17)

    def test_requested_driver_wins(self):
        self.assertEqual(choose_driver([D18], requested="My Driver"), "My Driver")

    def test_nothing_installed(self):
        self.assertEqual(choose_driver([]), D17)


class ConnectionStringTests(unittest.TestCase):
    def test_encrypts_by_default(self):
        cs = build_connection_string("SQLPROD01", driver=D18)
        self.assertIn("Encrypt=yes", cs)
        self.assertIn("TrustServerCertificate=no", cs)

    def test_trust_and_no_encrypt(self):
        cs = build_connection_string("SQLPROD01", driver=D18, encrypt=False, trust_server_certificate=True)
        self.assertIn("Encrypt=no", cs)
        self.assertIn("TrustServerCertificate=yes", cs)

    def test_password_with_special_characters_is_braced(self):
        cs = build_connection_string("SQLPROD01", user="triage", password="a;b}c", driver=D17)
        self.assertIn("PWD={a;b}}c}", cs)
        self.assertNotIn("Trusted_Connection", cs)


if __name__ == "__main__":
    unittest.main()
