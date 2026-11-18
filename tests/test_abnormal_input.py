"""T-010-FIX: abnormal input ends with a controlled error and exit 2, never a traceback.

Each case also checks that the error message does not echo the input text.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quota_handoff import jsonio
from quota_handoff.errors import InputError
from quota_handoff.normalizer import is_number

from tests import ROOT
from tests.helpers import doc, snap
from tests.test_cli import run

NOW = "2026-11-18T09:02:00+09:00"
SECRET = "sk-" + "Q7" * 12
HUGE = 10 ** 400  # json accepts it; float(HUGE) raises OverflowError


class _Dir:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()

    def write(self, name, text):
        target = self.path / name
        target.write_text(text, encoding="utf-8")
        return str(target)


class AbnormalInputTests(unittest.TestCase):
    def assert_controlled(self, code, out, err, *, forbidden=()):
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("error: "), err)
        self.assertNotIn("Traceback", err)
        for text in (SECRET, *forbidden):
            self.assertNotIn(text, err)
            self.assertNotIn(text, out)

    def test_deeply_nested_fixture(self):
        with _Dir() as d:
            path = d.write("deep.json", "[" * 200_000 + json.dumps(SECRET) + "]" * 200_000)
            code, out, err = run("plan", "--fixture", path)
        self.assert_controlled(code, out, err)
        self.assertIn("nested too deeply", err)

    def test_deeply_nested_task(self):
        with _Dir() as d:
            path = d.write("deep.json", '{"a":' * 100_000 + "1" + "}" * 100_000)
            code, out, err = run("handoff", "create", "--input", path, "--output", str(d.path / "h.md"))
            self.assertFalse((d.path / "h.md").exists())
        self.assert_controlled(code, out, err)

    def test_nesting_error_from_loads_strict(self):
        with self.assertRaises(InputError):
            jsonio.loads_strict("[" * 200_000 + "]" * 200_000)

    def test_huge_used_pct(self):
        s = snap("codex", ("5h", 1))
        s["windows"][0]["used_pct"] = HUGE
        with _Dir() as d:
            path = d.write("big.json", json.dumps(doc(s)))
            code, out, err = run("plan", "--fixture", path, "--now", NOW)
        self.assert_controlled(code, out, err, forbidden=(str(HUGE)[:30],))
        self.assertIn("used_pct", err)
        self.assertIn("outside [0, 100]", err)

    def test_huge_negative_used_pct(self):
        s = snap("codex", ("5h", 1))
        s["windows"][0]["used_pct"] = -HUGE
        with _Dir() as d:
            code, out, err = run("status", "--fixture", d.write("big.json", json.dumps(doc(s))), "--now", NOW)
        self.assert_controlled(code, out, err, forbidden=(str(HUGE)[:30],))

    def test_huge_policy_values(self):
        for section, key in (("freshness", "max_age_seconds"), ("freshness", "future_tolerance_seconds"),
                             ("thresholds", "warning"), ("thresholds", "near_limit")):
            with self.subTest(key=key), _Dir() as d:
                policy = {"schema_version": "0.2", "policy_version": "p", section: {key: HUGE}}
                code, out, err = run("plan", "--demo", "--policy", d.write("p.json", json.dumps(policy)))
                self.assert_controlled(code, out, err, forbidden=(str(HUGE)[:30],))
                self.assertIn(key, err)

    def test_large_but_valid_max_age_still_accepted(self):
        with _Dir() as d:
            policy = {"schema_version": "0.2", "policy_version": "p", "freshness": {"max_age_seconds": 86400}}
            code, _, err = run("plan", "--demo", "--policy", d.write("p.json", json.dumps(policy)))
        self.assertEqual(code, 0, err)

    def test_extreme_now(self):
        for value in ("0001-01-01T00:00:00+00:00", "0001-01-01T00:00:10+00:00", "0001-01-01T00:00:00+14:00",
                      "9999-12-31T23:59:59+00:00", "9999-12-31T23:59:59-14:00"):
            for command in (("status", "--demo"), ("plan", "--demo"),
                            ("plan", "--fixture", str(ROOT / "examples" / "usage-sample.json"))):
                with self.subTest(now=value, command=command[0:2]):
                    code, out, err = run(*command, "--now", value)
                    self.assert_controlled(code, out, err, forbidden=(value,))
                    self.assertIn("--now", err)

    def test_extreme_fixture_timestamps(self):
        for field in ("observed_at", "resets_at"):
            for value in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:00-14:00"):
                s = snap("codex", ("5h", 1))
                if field == "observed_at":
                    s["observed_at"] = value
                else:
                    s["windows"][0]["resets_at"] = value
                with self.subTest(field=field, value=value), _Dir() as d:
                    code, out, err = run("plan", "--fixture", d.write("f.json", json.dumps(doc(s))), "--now", NOW)
                    self.assert_controlled(code, out, err, forbidden=(value,))
                    self.assertIn(field, err)

    def test_normal_timestamps_unaffected(self):
        code, _, err = run("plan", "--fixture", str(ROOT / "examples" / "usage-sample.json"), "--now", NOW)
        self.assertEqual(code, 0, err)

    def test_read_permission_error(self):
        for command in (("plan", "--fixture"), ("status", "--demo", "--policy"), ("handoff", "create", "--input")):
            with self.subTest(command=command), _Dir() as d:
                path = d.write("in.json", json.dumps({"schema_version": "0.2", "objective": SECRET}))
                denied = PermissionError(13, "Permission denied")
                with mock.patch.object(jsonio.Path, "read_bytes", side_effect=denied):
                    code, out, err = run(*command, path)
                self.assert_controlled(code, out, err)
                self.assertIn("cannot read", err)
                self.assertIn("Permission denied", err)

    def test_last_resort_guard_shows_only_exception_type(self):
        for exc in (RecursionError(SECRET), OverflowError(SECRET), OSError(5, SECRET)):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch("quota_handoff.cli.cmd_status", side_effect=exc):
                code, out, err = run("status", "--demo")
            self.assert_controlled(code, out, err)
            self.assertIn(type(exc).__name__, err)

    def test_is_number_rules(self):
        self.assertTrue(is_number(HUGE))
        self.assertTrue(is_number(1.5))
        for value in (True, False, float("inf"), float("nan"), "1", None):
            with self.subTest(value=value):
                self.assertFalse(is_number(value))


class UntrustedHandoffDocsTests(unittest.TestCase):
    """README and SECURITY.md state that handoff content is untrusted and must be reviewed."""

    def test_untrusted_content_documented(self):
        for path in (ROOT / "README.md", ROOT / "docs" / "SECURITY.md"):
            text = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.name):
                for phrase in ("untrusted", "prompt injection", "review", "does not run agents"):
                    self.assertIn(phrase, text)

    def test_reporting_path_documented_without_claiming_it_is_enabled(self):
        text = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("security/advisories/new", text)
        self.assertIn("not enabled yet", text)
        checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("Private vulnerability reporting", checklist)


if __name__ == "__main__":
    unittest.main()
