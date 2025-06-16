import unittest

from sqltriage.connect import build_connection_string, choose_driver, choose_library

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


class LibraryTests(unittest.TestCase):
    def available(self, *names):
        return lambda module: module in names

    def test_explicit_choice_wins(self):
        self.assertEqual(choose_library("mssql-python", self.available("pyodbc")), "mssql-python")
        self.assertEqual(choose_library("pyodbc", self.available("mssql_python")), "pyodbc")

    def test_auto_prefers_pyodbc(self):
        self.assertEqual(choose_library("auto", self.available("pyodbc", "mssql_python")), "pyodbc")

    def test_auto_falls_back_to_mssql_python_only_when_pyodbc_is_missing(self):
        self.assertEqual(choose_library("auto", self.available("mssql_python")), "mssql-python")

    def test_auto_with_nothing_installed_reports_pyodbc(self):
        # the missing-module error should name the usual driver
        self.assertEqual(choose_library("auto", self.available()), "pyodbc")


class ConnectionStringTests(unittest.TestCase):
    def test_mssql_python_string_has_no_odbc_driver(self):
        cs = build_connection_string("SQLPROD01", library="mssql-python", trust_server_certificate=True)
        self.assertNotIn("DRIVER", cs)
        self.assertTrue(cs.startswith("SERVER=SQLPROD01;DATABASE=master"))
        self.assertIn("TrustServerCertificate=yes", cs)

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
