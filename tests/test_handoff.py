import json
import tempfile
import unittest
from pathlib import Path

from quota_handoff.errors import InputError, SafetyGateError
from quota_handoff.handoff import BASELINE_RESTRICTIONS, load_task, render_markdown, to_json_dict
from quota_handoff.jsonio import write_text_file
from quota_handoff.redact import redact

from tests import ROOT


def task(**over):
    d = {"schema_version": "0.1", "objective": "Ship it"}
    d.update(over)
    return d


class ValidationTests(unittest.TestCase):
    def test_minimal(self):
        md = render_markdown(load_task(task()))
        self.assertIn("## Objective\n\nShip it", md)
        for heading in ("## Previous state summary", "## Read first", "## How to verify",
                        "## Do not", "## Expected outcome"):
            self.assertIn(heading, md)

    def test_missing_objective(self):
        for bad in ({"schema_version": "0.1"}, task(objective="   "), task(objective=5)):
            with self.subTest(bad=bad), self.assertRaises(InputError):
                load_task(bad)

    def test_wrong_types(self):
        cases = [task(done="x"), task(done=[1]), task(done=[""]), task(tests={"a": 1}),
                 task(tests=[{"name": "t", "status": "passed"}]), task(tests=[{"status": "pass"}]),
                 task(tests=["t"]), task(extra=1), task(schema_version="2")]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(InputError):
                load_task(bad)

    def test_json_contract_fields(self):
        data = to_json_dict(load_task(task(done=["a"], remaining=["b"], next_steps=["c"], do_not=["d"])))
        for key in ("schema_version", "objective", "completed", "pending", "blockers", "tests",
                    "next_action", "restrictions"):
            self.assertIn(key, data)
        self.assertEqual(data["restrictions"], list(BASELINE_RESTRICTIONS) + ["d"])


class DeterminismTests(unittest.TestCase):
    def test_same_input_same_output(self):
        doc = json.loads((ROOT / "examples" / "task-sample.json").read_text(encoding="utf-8"))
        first = render_markdown(load_task(doc))
        second = render_markdown(load_task(json.loads(json.dumps(doc))))
        self.assertEqual(first, second)
        self.assertNotIn("\r", first)
        self.assertTrue(first.endswith("\n") and not first.endswith("\n\n"))

    def test_backtick_in_command(self):
        md = render_markdown(load_task(task(tests=[{"name": "t", "status": "pass", "command": "echo `x`"}])))
        self.assertIn("`` echo `x` ``", md)


class RedactionTests(unittest.TestCase):
    SAMPLES = {
        "api_key": "key is sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",
        "github_token": "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
        "aws_access_key": "AKIAABCDEFGHIJKLMNOP",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        "bearer_token": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "secret_assignment": "password=hunter2hunter2",
        "url_credentials": "postgres://admin:s3cretpass@db.local/app",
        "private_key_block": "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
        "slack_token": "xoxb-1234567890-abcdefghij",
    }

    def test_each_pattern_masked(self):
        secrets = {"api_key": "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV", "github_token": "ghp_ABCDEFGHIJ",
                   "aws_access_key": "AKIAABCDEFGHIJKLMNOP", "jwt": "eyJhbGciOiJIUzI1NiJ9",
                   "bearer_token": "abcdefghijklmnopqrstuvwxyz", "secret_assignment": "hunter2hunter2",
                   "url_credentials": "s3cretpass", "private_key_block": "MIIabc", "slack_token": "xoxb-"}
        for kind, text in self.SAMPLES.items():
            with self.subTest(kind=kind):
                masked, kinds = redact(text)
                self.assertIn(kind, kinds)
                self.assertNotIn(secrets[kind], masked)
                self.assertIn(f"[REDACTED:{kind}]", masked)

    def test_url_keeps_host(self):
        masked, _ = redact("postgres://admin:s3cretpass@db.local/app")
        self.assertEqual(masked, "postgres://admin:[REDACTED:url_credentials]@db.local/app")

    def test_home_paths(self):
        for text in (r"C:\Users\someone\proj\a.py", "/home/someone/proj", "see /Users/someone/x"):
            with self.subTest(text=text):
                masked, kinds = redact(text)
                self.assertNotIn("someone", masked)
                self.assertEqual(kinds, ["home_path_username"])

    def test_plain_text_untouched(self):
        text = "Run python -m pytest tests/export and check token handling docs"
        self.assertEqual(redact(text), (text, []))

    def test_idempotent(self):
        once, _ = redact("api_key: abcdef123456")
        self.assertEqual(redact(once)[0], once)

    def test_task_redaction_recorded_at_06(self):
        t = load_task(task(notes=["token=abcdef123456789"], tests=[{"name": "t", "status": "pass",
                                                                      "command": "curl -H 'Authorization: Bearer abcdefghijklmnopqrstu'"}]))
        md = render_markdown(t)
        self.assertNotIn("abcdef123456789", md)
        self.assertNotIn("abcdefghijklmnopqrstu", md)
        self.assertIn("## Redactions applied", md)
        self.assertEqual(sorted(p for p, _ in t.redactions), ["notes[0]", "tests[0].command"])


class WriteTests(unittest.TestCase):
    def test_overwrite_refused_by_default_at_07(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "handoff.md"
            write_text_file(target, "first\n")
            with self.assertRaises(SafetyGateError):
                write_text_file(target, "second\n")
            self.assertEqual(target.read_bytes(), b"first\n")
            write_text_file(target, "second\n", force=True)
            self.assertEqual(target.read_bytes(), b"second\n")
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["handoff.md"])

    def test_missing_parent_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(InputError):
                write_text_file(Path(d) / "nope" / "h.md", "x")
            self.assertFalse((Path(d) / "nope").exists())


if __name__ == "__main__":
    unittest.main()
