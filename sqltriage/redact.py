"""Mask data in a report before it goes into a ticket or a chat.

Query text carries literals that are often real values (names, account
numbers), and sessions carry real logins and host names.  This is a safety net,
not a guarantee: read the report before you share it.
"""
import re

# 'text', N'text' and E-style doubled quotes: it''s
_STRING = re.compile(r"(?<![\w'])[Nn]?'(?:[^']|'')*'")
_HEX = re.compile(r"\b0x[0-9A-Fa-f]{6,}\b")
_LONG_NUMBER = re.compile(r"(?<![\w.])\d{5,}(?![\w.])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# login@host as the blocking tree prints it, where the login may be DOMAIN\user;
# and a bare DOMAIN\user as it appears in Agent step messages.
_LOGIN_HOST = re.compile(r"(?<![\w.+-])((?:[A-Za-z][\w-]*\\)?[\w$.-]+)@([\w-]+(?:\.[\w-]+)*)")
_DOMAIN_USER = re.compile(r"\b[A-Za-z][\w-]*\\[\w.$-]+")


def redact_literals(text):
    """Strings, long hex values, long numbers and e-mail addresses."""
    text = _STRING.sub("'?'", text)
    text = _HEX.sub("0x?", text)
    text = _EMAIL.sub("<email>", text)
    return _LONG_NUMBER.sub("?", text)


class NameMasker:
    """Replaces logins and hosts with stable tokens, so the same person is the same token."""

    def __init__(self):
        self._users = {}
        self._hosts = {}

    def _token(self, table, prefix, value):
        key = value.lower()
        if key not in table:
            table[key] = "{}{}".format(prefix, len(table) + 1)
        return table[key]

    def mask(self, text):
        def login_host(match):
            return "{}@{}".format(
                self._token(self._users, "user", match.group(1)), self._token(self._hosts, "host", match.group(2))
            )

        # login@host first, so DOMAIN\user@host is one user and one host
        text = _LOGIN_HOST.sub(login_host, text)
        return _DOMAIN_USER.sub(lambda m: self._token(self._users, "user", m.group(0)), text)


def redact_findings(findings, literals=True, names=False):
    """Mask every text field of the findings in place."""
    masker = NameMasker() if names else None

    def clean(text):
        if masker:
            text = masker.mask(text)
        return redact_literals(text) if literals else text

    for finding in findings:
        finding.title = clean(finding.title)
        finding.detail = clean(finding.detail)
        finding.evidence = [clean(line) for line in finding.evidence]
        finding.block = [clean(line) for line in finding.block]
        finding.advice = [clean(line) for line in finding.advice]
    return findings
