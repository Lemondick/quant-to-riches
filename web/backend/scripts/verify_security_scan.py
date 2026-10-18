"""Acceptance for the repository secret scanner, pinned to its rule boundaries.

The scanner refuses to look at its own fixtures: every credential below is
assembled at runtime from fragments, so this file never contains a matchable
literal. The tests still exercise the real patterns, because the scanner reads
the reconstructed text back from disk exactly as it would read a real leak.

Both halves matter. A rule that stops matching is a silent pass, and a rule
that starts matching keyword arguments is a false alarm on every request body
carrying a ``password`` field.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from quant_assistant.security_scan import scan_tracked_files_for_secrets

# Credential fragments. Joined at runtime so this source stays scanner-clean.
_HOST_KEYS = "host=localhost port=5432 dbname=quant_assistant user=postgres"
_URL_PREFIX = "postgresql://"
_URL_USER = "admin"
_URL_PASSWORD = "hunter2"
_URL_SUFFIX = "@localhost:5432/quant_assistant"

# ``git ls-files`` dominates the runtime, so each test scans once.
KEYWORD_DSN = f"{_HOST_KEYS} " + "password=" + "postgres"
URL_DSN = _URL_PREFIX + _URL_USER + ":" + _URL_PASSWORD + _URL_SUFFIX

# Lines that must surface a ``connection_password`` finding.
LEAKING_LINES = (
    f'PG_DSN = "{KEYWORD_DSN}"',
    f'dsn = "{_HOST_KEYS} ' + "pwd=" + 'letmein"',
    f'dsn = "{URL_DSN}"',
)

# Lines that carry a ``password`` token but must stay silent: a keyword
# argument, a default value, or a placeholder rather than a leaked DSN.
BENIGN_LINES = (
    'self.redeem(token, password="short")',
    'def redeem(self, token, username="alice", password=NEW_PASSWORD):',
    'raise ValueError("connection needs user=<user> password=<secret>")',
    'payload = {"username": "alice", "password": "correct horse"}',
    'password = os.environ["QUANT_POSTGRES_PASSWORD"]',
    'dsn = "host=localhost dbname=quant_assistant password=${QUANT_DB_PASSWORD}"',
    'var body = Post("/auth/login", new { username = '
    + "account.Name, password = account.Password });",
)

# Named credentials assigned directly. The finding kind is the lowercased name.
OPENAI_VAR = "OPENAI_API_KEY"
TUSHARE_VAR = "TUSHARE_TOKEN"
OPENAI_VALUE = "sk-proj-" + "abcdefghijklmnopqrstuvwx"
ASSIGNMENT_LEAKS = (
    (f'{OPENAI_VAR} = "{OPENAI_VALUE}"', {"openai_api_key", "openai_key"}),
    (f'{TUSHARE_VAR} = "' + "0123456789" * 4 + '"', {"tushare_token"}),
    ('ALIPAY_PASSWORD = "' + "realsecret123" + '"', {"alipay_password"}),
    ('BROKER_COOKIE = "' + "session=abc123" + '"', {"broker_cookie"}),
)

# Sensitive-looking names bound to obvious placeholders must stay silent.
ASSIGNMENT_PLACEHOLDERS = (
    f'{OPENAI_VAR} = "your-api-key"',
    f'{TUSHARE_VAR} = "<set-in-environment>"',
    'ALIPAY_PASSWORD = "test"',
    'BROKER_TOKEN = "changeme"',
)


class SecurityScanAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One repository for the whole class: ``git init`` costs far more than
        # the scan itself, and every case writes to the same single file.
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        subprocess.run(["git", "init", "-q"], cwd=cls.root, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _scan(self, body: str) -> list[str]:
        (self.root / "sample.py").write_text(body + "\n", encoding="utf-8")
        return list(scan_tracked_files_for_secrets(self.root))

    def test_connection_string_credentials_are_reported(self):
        findings = self._scan("\n".join(LEAKING_LINES))
        expected = [
            f"sample.py:{line_number}:connection_password"
            for line_number in range(1, len(LEAKING_LINES) + 1)
        ]
        self.assertEqual(findings, expected)

    def test_keyword_arguments_and_placeholders_stay_quiet(self):
        findings = self._scan("\n".join(BENIGN_LINES))
        self.assertEqual(
            findings,
            [],
            "benign password tokens must not be reported as connection strings",
        )

    def test_findings_are_deduplicated_and_sorted(self):
        findings = self._scan("\n".join(LEAKING_LINES))
        self.assertEqual(findings, sorted(set(findings)))
        self.assertEqual(len(findings), len(LEAKING_LINES))

    def test_placeholder_connection_string_is_not_reported(self):
        body = f'dsn = "{_HOST_KEYS} password=changeme"'
        self.assertEqual(self._scan(body), [])

    def test_named_credentials_are_reported(self):
        for line, kinds in ASSIGNMENT_LEAKS:
            with self.subTest(line=line):
                findings = self._scan(line)
                self.assertTrue(findings, f"expected a finding for: {line}")
                reported = {item.rsplit(":", 1)[-1] for item in findings}
                self.assertTrue(
                    kinds <= reported,
                    f"expected kinds {kinds} within {reported} for: {line}",
                )

    def test_named_credential_placeholders_stay_quiet(self):
        for line in ASSIGNMENT_PLACEHOLDERS:
            with self.subTest(line=line):
                self.assertEqual(
                    self._scan(line),
                    [],
                    f"placeholder assignment must stay silent: {line}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
