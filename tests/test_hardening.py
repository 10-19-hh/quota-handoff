"""T-007 / M0.1 regression tests: R1/R2 policy, window states, --demo, redaction,
error-message hygiene, write scope and static code-path checks."""

import ast
import itertools
import json
import os
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from quota_handoff import jsonio
from quota_handoff.cli import _same_path
from quota_handoff.errors import InputError
from quota_handoff.normalizer import normalize_usage_document
from quota_handoff.policy import MANUAL, NO_ELIGIBLE, RECOMMENDED, TIER_NEAR_LIMIT, assess_snapshot, plan
from quota_handoff.redact import redact

from tests import ROOT, SRC
from tests.helpers import NOW, doc, normalize, snap
from tests.test_cli import forbid_side_effects, run


def snap_states(provider, *windows, label="local-primary", **kwargs):
    """windows: (key, used_pct, state-or-None)."""
    s = snap(provider, label=label, **kwargs)
    s["windows"] = []
    for key, pct, state in windows:
        w = {"key": key, "used_pct": pct, "resets_at": None}
        if state is not None:
            w["state"] = state
        s["windows"].append(w)
    return s


class WindowStateTests(unittest.TestCase):
    def assess(self, *windows):
        return assess_snapshot(normalize(snap_states("codex", *windows))[0], NOW)

    def test_default_states(self):
        out = normalize(snap_states("codex", ("5h", 10, None), ("weekly", None, None)))[0]
        self.assertEqual([w.state for w in out.windows], ["reported", "not_reported"])

    def test_unsupported_window_does_not_make_partial(self):
        a = self.assess(("5h", 10, None), ("monthly", None, "unsupported"))
        self.assertEqual((a.confidence, a.eligible), ("complete", True))
        self.assertEqual(a.unsupported_windows, ("monthly",))
        self.assertIn("window_unsupported_ignored", a.reason_codes)

    def test_failed_and_not_reported_are_distinguished(self):
        a = self.assess(("5h", 10, None), ("weekly", None, "failed"), ("monthly", None, None))
        self.assertEqual(a.confidence, "partial")
        self.assertEqual(a.missing_windows, ("monthly", "weekly"))
        self.assertIn("window_lookup_failed", a.reason_codes)
        self.assertIn("window_not_reported", a.reason_codes)
        self.assertFalse(a.eligible)
        self.assertTrue(a.manual_review)

    def test_only_unsupported_is_unknown(self):
        a = self.assess(("weekly", None, "unsupported"))
        self.assertEqual(a.effective_status, "unknown")

    def test_state_validation(self):
        for windows, fragment in (
            ([("5h", None, "reported")], "requires a numeric used_pct"),
            ([("5h", 10, "failed")], "requires used_pct to be null"),
            ([("5h", 10, "unsupported")], "requires used_pct to be null"),
            ([("5h", None, "missing")], "state: one of"),
        ):
            with self.subTest(windows=windows), self.assertRaises(InputError) as ctx:
                normalize(snap_states("codex", *windows))
            self.assertIn(fragment, str(ctx.exception))


class R1PreferenceTests(unittest.TestCase):
    def test_tier_compared_before_preference(self):
        result = plan(normalize(snap("claude", ("5h", 79)), snap("codex", ("5h", 80))), NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.provider, "claude")
        self.assertIn("preference_ignored_riskier_tier", result.reason_codes)
        self.assertIn("riskier usage tier", result.summary)

    def test_preference_beats_lower_usage_within_same_tier(self):
        for tier_values in ((5, 79), (80, 94)):
            with self.subTest(tier_values=tier_values):
                low, high = tier_values
                result = plan(normalize(snap("claude", ("5h", low)), snap("codex", ("5h", high))), NOW, prefer="codex")
                self.assertEqual(result.selected.snapshot.provider, "codex")
                self.assertIn("preference_applied", result.reason_codes)
                self.assertNotIn("lowest_risk_selected", result.reason_codes)

    def test_preference_not_found(self):
        result = plan(normalize(snap("claude", ("5h", 5))), NOW, prefer="codex")
        self.assertIn("preference_ignored_provider_not_found", result.reason_codes)

    def test_preference_for_partial_is_ignored(self):
        snaps = normalize(snap("claude", ("5h", 50)), snap_states("codex", ("5h", 1, None), ("weekly", None, "failed")))
        result = plan(snaps, NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.provider, "claude")
        self.assertIn("preference_ignored_not_eligible", result.reason_codes)

    def test_ties_within_preferred_provider(self):
        snaps = normalize(snap("codex", ("5h", 30), label="b"), snap("codex", ("5h", 30), label="a"),
                          snap("codex", ("5h", 40), label="c"), snap("claude", ("5h", 1)))
        result = plan(snaps, NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.candidate_id, "codex/a")
        self.assertIn("tie_broken_by_stable_order", result.reason_codes)

    def test_usage_breaks_ties_before_name(self):
        snaps = normalize(snap("aaa", ("5h", 30)), snap("zzz", ("5h", 29)))
        self.assertEqual(plan(snaps, NOW).selected.snapshot.provider, "zzz")


class R2PartialTests(unittest.TestCase):
    def test_complete_preferred_over_lower_risk_partial(self):
        snaps = normalize(snap("claude", ("5h", 70), ("weekly", 70)),
                          snap_states("codex", ("5h", 1, None), ("weekly", None, "failed")))
        result = plan(snaps, NOW)
        self.assertEqual(result.decision, RECOMMENDED)
        self.assertEqual(result.selected.snapshot.provider, "claude")
        self.assertIn("partial_candidates_not_auto_selected", result.reason_codes)

    def test_complete_warning_tier_beats_partial_ok_tier(self):
        snaps = normalize(snap("claude", ("5h", 90)), snap_states("codex", ("5h", 1, None), ("weekly", None, None)))
        result = plan(snaps, NOW)
        self.assertEqual(result.selected.snapshot.provider, "claude")

    def test_partial_only_is_manual(self):
        snaps = normalize(snap_states("claude", ("5h", 1, None), ("weekly", None, "failed")),
                          snap_states("codex", ("5h", 2, None), ("weekly", None, None)))
        result = plan(snaps, NOW)
        self.assertEqual((result.decision, result.selected), (MANUAL, None))
        self.assertIn("only_partial_usage_available", result.reason_codes)
        self.assertIn("partial: missing weekly", result.summary)

    def test_partial_with_reported_near_limit_is_excluded(self):
        snaps = normalize(snap_states("codex", ("5h", 96, None), ("weekly", None, "failed")))
        a = assess_snapshot(snaps[0], NOW)
        self.assertEqual((a.tier, a.manual_review), (TIER_NEAR_LIMIT, False))
        self.assertEqual(plan(snaps, NOW).decision, NO_ELIGIBLE)

    def test_partial_plus_uncertain_is_manual(self):
        snaps = normalize(snap_states("claude", ("5h", 1, None), ("weekly", None, "failed")),
                          snap("codex", status="unknown", observed=False))
        self.assertEqual(plan(snaps, NOW).decision, MANUAL)

    def test_order_invariance_with_states(self):
        rows = [snap("claude", ("5h", 40)),
                snap_states("codex", ("5h", 1, None), ("weekly", None, "failed")),
                snap_states("other", ("5h", 40, None), ("monthly", None, "unsupported")),
                snap("stale", ("5h", 1), age=999)]
        outputs = {repr(plan(normalize(*p), NOW, prefer="codex")) for p in itertools.permutations(rows)}
        self.assertEqual(len(outputs), 1)

    def test_cli_partial_fixture(self):
        code, out, _ = run("plan", "--fixture", str(ROOT / "examples" / "usage-partial.json"),
                           "--now", "2026-11-18T09:02:00+09:00", "--json")
        data = json.loads(out)
        self.assertEqual((code, data["recommendation"]["provider"]), (0, "claude"))
        codex = [c for c in data["candidate_evaluations"] if c["provider"] == "codex"][0]
        self.assertEqual((codex["confidence"], codex["missing_windows"]), ("partial", ["weekly"]))
        self.assertTrue(any("confidence=partial" in w for w in data["warnings"]))


class DemoTests(unittest.TestCase):
    def test_plan_demo_without_now_recommends(self):
        code, out, err = run("plan", "--demo", "--task", "code", "--prefer", "codex")
        self.assertEqual(code, 0, err)
        self.assertIn("DEMO DATA", out.splitlines()[0])
        self.assertIn("Decision: recommended", out)
        self.assertIn("codex/demo", out)

    def test_status_demo_json_marked(self):
        code, out, _ = run("status", "--demo", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(data["demo"])
        self.assertTrue(data["warnings"][0].startswith("DEMO DATA"))
        self.assertTrue(all(s["source"] == "demo" and s["account_label"] == "demo" for s in data["snapshots"]))
        self.assertTrue(all(s["freshness"] == "fresh" for s in data["snapshots"]))

    def test_demo_reproducible_with_now(self):
        argv = ("plan", "--demo", "--json", "--now", "2030-01-01T00:00:00Z")
        first, second = run(*argv), run(*argv)
        self.assertEqual(first, second)
        data = json.loads(first[1])
        self.assertEqual(data["candidate_evaluations"][0]["observed_at"], "2029-12-31T23:59:30+00:00")

    def test_fixture_output_not_marked_demo(self):
        _, out, _ = run("plan", "--fixture", str(ROOT / "examples" / "usage-sample.json"),
                        "--now", "2026-11-18T09:02:00+09:00", "--json")
        self.assertFalse(json.loads(out)["demo"])
        self.assertNotIn("DEMO", out)

    def test_demo_and_fixture_mutually_exclusive(self):
        self.assertEqual(run("plan", "--demo", "--fixture", "x.json")[0], 2)
        self.assertEqual(run("plan")[0], 2)

    def test_demo_has_no_side_effects(self):
        with forbid_side_effects(), mock.patch("builtins.open", side_effect=AssertionError("file access")):
            self.assertEqual(run("plan", "--demo")[0], 0)
            self.assertEqual(run("status", "--demo", "--json")[0], 0)


class RedactionT007Tests(unittest.TestCase):
    """Synthetic strings only. Masking is pattern-based and not a security guarantee."""

    def assertMasked(self, text, kind, secret):
        masked, kinds = redact(text)
        self.assertIn(kind, kinds, text)
        self.assertNotIn(secret, masked)
        return masked

    def test_github_token_formats(self):
        for token in ("ghp_" + "A1b2C3d4E5" * 3, "gho_" + "Z9" * 12, "github_pat_" + "11ABCDEFG_" * 3):
            with self.subTest(token=token[:8]):
                self.assertMasked(f"export GH={token}", "github_token", token)

    def test_jwt_format(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZW1vIn0.c2lnbmF0dXJlLXN5bnRoZXRpYw"
        self.assertMasked(f"cookie={jwt}", "jwt", "eyJzdWIiOiJkZW1vIn0")

    def test_api_key_formats(self):
        for key in ("sk-" + "x1Y2z3" * 4, "sk-proj-" + "AbC123" * 4, "sk-ant-api03-" + "q" * 24):
            with self.subTest(key=key[:7]):
                self.assertMasked(f"key {key} end", "api_key", key)

    def test_url_credentials(self):
        for url, secret in (("https://demo-user:Synthetic9Pass@example.invalid/repo.git", "Synthetic9Pass"),
                            ("redis://:p4ssw0rd-demo@localhost:6379/0", "p4ssw0rd-demo")):
            with self.subTest(url=url[:8]):
                masked = self.assertMasked(url, "url_credentials", secret)
                self.assertIn("@", masked)

    def test_token_assignment_masked(self):
        for text, secret in (("token=a1b2c3d4e5f6", "a1b2c3d4e5f6"),
                             ("API_TOKEN: 'Zx9Zx9Zx9Zx9'", "Zx9Zx9Zx9Zx9"),
                             ("password = correcthorsebatterystaple", "correcthorsebatterystaple")):
            with self.subTest(text=text):
                self.assertMasked(text, "secret_assignment", secret)

    def test_token_prose_false_positives(self):
        for text in (
            "Set token= in the config later",
            "the token=value syntax is documented",
            "Rotate the API token: done",
            "max_tokens: 4096",
            "token=$GITHUB_TOKEN is read from the environment",
            "password: <your-password>",
            "Refresh token handling: see docs",
            "secret=placeholder",
        ):
            with self.subTest(text=text):
                self.assertEqual(redact(text), (text, []))

    def test_path_usernames(self):
        for text in (r"C:\Users\someone\src\app.py", "c:/users/someone/src", r"D:\USERS\someone",
                     "/home/someone/.config/app", "open /Users/someone/Desktop/x", '"/home/someone"'):
            with self.subTest(text=text):
                masked, kinds = redact(text)
                self.assertNotIn("someone", masked)
                self.assertIn("<user>", masked)
                self.assertEqual(kinds, ["home_path_username"])

    def test_non_home_paths_untouched(self):
        for text in (r"C:\Program Files\App", "/usr/local/bin", "src/home/page.py", "docs/Users/guide.md",
                     "~/project", r"C:\Users\<user>\x"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), (text, []))

    def test_disclaimer_documented(self):
        for path in (ROOT / "README.md", ROOT / "docs" / "SECURITY.md"):
            with self.subTest(path=path.name):
                self.assertIn("not a guarantee", path.read_text(encoding="utf-8"))


class ErrorHygieneTests(unittest.TestCase):
    SECRET = "sk-" + "Q7" * 12

    def test_invalid_values_not_echoed(self):
        s = snap("codex", ("5h", 1))
        s["observed_at"] = self.SECRET
        s["source"] = self.SECRET
        s[self.SECRET + "x" * 60] = 1
        with self.assertRaises(InputError) as ctx:
            normalize_usage_document(doc(s), source="fixture")
        self.assertNotIn(self.SECRET + "x" * 60, str(ctx.exception))
        # T-008-FIX: field names are masked before truncation, so the secret no longer appears at all.
        self.assertEqual(str(ctx.exception).count(self.SECRET), 0)

    def test_cli_error_does_not_echo_timestamp_value(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "u.json"
            s = snap("codex", ("5h", 1))
            s["observed_at"] = self.SECRET
            p.write_text(json.dumps(doc(s)), encoding="utf-8")
            code, out, err = run("status", "--fixture", str(p))
        self.assertEqual(code, 2)
        self.assertNotIn(self.SECRET, out + err)

    def test_handoff_type_error_does_not_echo(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text(json.dumps({"schema_version": "0.1", "objective": "x", "done": self.SECRET}), encoding="utf-8")
            code, out, err = run("handoff", "create", "--input", str(p))
        self.assertEqual(code, 2)
        self.assertNotIn(self.SECRET, out + err)


class WriteScopeTests(unittest.TestCase):
    def test_force_write_leaves_no_temp_and_stays_in_target_dir(self):
        created = []
        real = tempfile.mkstemp

        def spy(*args, **kwargs):
            created.append(kwargs.get("dir"))
            return real(*args, **kwargs)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(jsonio.tempfile, "mkstemp", spy):
            target = Path(d) / "h.md"
            target.write_text("old", encoding="utf-8")
            jsonio.write_text_file(target, "new\n", force=True)
            self.assertEqual([Path(c) for c in created], [Path(d)])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["h.md"])

    def test_same_path_detection(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "h.md")
            self.assertTrue(_same_path(a, os.path.join(d, "sub", "..", "h.md")))
            self.assertFalse(_same_path(a, os.path.join(d, "h.json")))
            if os.name == "nt":
                self.assertTrue(_same_path(a, os.path.join(d, "H.MD")))

    def test_case_variant_outputs_rejected_on_windows(self):
        if os.name != "nt":
            self.skipTest("case-insensitive check is Windows-specific")
        with tempfile.TemporaryDirectory() as d:
            code, _, _ = run("handoff", "create", "--input", str(ROOT / "examples" / "task-sample.json"),
                             "--output", os.path.join(d, "h.md"), "--json-output", os.path.join(d, "H.MD"), "--force")
            self.assertEqual(code, 2)
            self.assertEqual(list(Path(d).iterdir()), [])


class StaticCodePathTests(unittest.TestCase):
    """AST checks over src/. They catch direct uses only; they are not a sandbox."""

    def trees(self):
        for path in sorted(SRC.rglob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_no_dynamic_code_or_imports(self):
        banned_calls = {"eval", "exec", "compile", "__import__", "breakpoint", "input"}
        banned_modules = {"importlib", "runpy", "pty", "pickle", "marshal", "code", "webbrowser", "ftplib",
                          "smtplib", "telnetlib", "xmlrpc", "imaplib", "poplib", "platform", "getpass"}
        for path, tree in self.trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, banned_calls, f"{path.name}:{node.lineno}")
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    self.assertNotIn(name.split(".")[0], banned_modules, f"{path.name}:{node.lineno}")

    def test_file_writes_only_in_jsonio(self):
        writers = {"open", "write_text", "write_bytes", "mkstemp", "replace", "rename", "unlink",
                   "mkdir", "makedirs", "touch", "fdopen"}
        for path, tree in self.trees():
            if path.name == "jsonio.py":
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                    if name == "replace" and isinstance(fn, ast.Attribute) and not (
                            isinstance(fn.value, ast.Name) and fn.value.id in ("os", "Path")):
                        continue  # str.replace
                    self.assertNotIn(name, writers, f"{path.name}:{node.lineno}")

    def test_no_environment_or_home_reads(self):
        for path, tree in self.trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"environ", "getenv", "expanduser", "home", "getlogin"},
                                     f"{path.name}:{node.lineno}")


class PublicContentTests(unittest.TestCase):
    """Generic scan of every project file for personal data. Project-specific private terms are
    checked outside the repository so this public test does not have to list them."""

    HOME = re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?!<user>|someone\b)[A-Za-z0-9._-]+"
                      r"|(?<![\w.])/(?:home|Users)/(?!someone\b|<user>)[A-Za-z0-9._-]+")
    EMAIL = re.compile(r"[\w.+-]+@(?!example\.)[\w-]+\.(?:com|net|org|io|dev|kr)\b")  # RFC 2606 allowed

    # T-008-FIX (B-3): scan only files that would be published, never local environments
    # or build output (.venv, build, dist, __pycache__, .git, *.egg-info).
    PUBLIC_DIRS = ("src", "tests", "docs", "examples", ".github")
    PUBLIC_FILES = ("README.md", "LICENSE", "pyproject.toml", ".gitignore", "MANIFEST.in")
    EXCLUDED_PARTS = {".venv", "venv", "build", "dist", "__pycache__", ".git", ".mypy_cache", ".pytest_cache"}

    def files(self):
        for name in self.PUBLIC_FILES:
            if (ROOT / name).is_file():
                yield ROOT / name
        for directory in self.PUBLIC_DIRS:
            for path in sorted((ROOT / directory).rglob("*")):
                rel = path.relative_to(ROOT).parts
                if (path.is_file() and not self.EXCLUDED_PARTS.intersection(rel)
                        and not any(p.endswith(".egg-info") for p in rel)):
                    yield path

    def read(self, path):
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.fail(f"public file is not UTF-8 text: {path.relative_to(ROOT)}")

    def test_no_personal_paths_or_emails(self):
        for path in self.files():
            with self.subTest(path=str(path.relative_to(ROOT))):
                text = self.read(path)
                self.assertIsNone(self.HOME.search(text))
                self.assertIsNone(self.EMAIL.search(text))

    def test_no_secret_patterns_outside_tests(self):
        for path in self.files():
            if path.parts[len(ROOT.parts)] == "tests" or path.name == "redact.py":
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertEqual(redact(self.read(path))[1], [])


if __name__ == "__main__":
    unittest.main()
