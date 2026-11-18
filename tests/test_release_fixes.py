"""T-008-FIX regression tests for review findings B-1, B-2, B-3, B-5 and the schema /
reason-code policy decisions (docs/REVIEW-M0.2-INDEPENDENT.md)."""

import ast
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quota_handoff import jsonio
from quota_handoff.errors import InputError
from quota_handoff.policy import REASON_CODES, Policy, assess_snapshot, plan

from tests import ROOT, SRC
from tests.helpers import NOW, normalize, snap
from tests.test_cli import run
from tests.test_hardening import snap_states

EX = ROOT / "examples"
TASK = EX / "task-sample.json"
SECRET = "sk-" + "Zq8" * 10


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class B1SchemaVersionEchoTests(unittest.TestCase):
    def check(self, value, expected_fragment):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            usage = write_json(d / "u.json", {"schema_version": value, "snapshots": []})
            task = write_json(d / "t.json", {"schema_version": value, "objective": "x"})
            policy = write_json(d / "p.json", {"schema_version": value, "policy_version": "x"})
            for argv in (("status", "--fixture", str(usage)),
                         ("plan", "--fixture", str(usage)),
                         ("handoff", "create", "--input", str(task)),
                         ("plan", "--demo", "--policy", str(policy))):
                with self.subTest(argv=argv[0:2], value=type(value).__name__):
                    code, out, err = run(*argv)
                    self.assertEqual(code, 2)
                    self.assertIn(expected_fragment, err)
                    self.assertNotIn(SECRET, out + err)
                    self.assertLess(len(err), 600)

    def test_unsupported_string_not_echoed(self):
        self.check(SECRET, "unsupported schema_version (string of 33 characters)")

    def test_wrong_types_not_echoed(self):
        for value in (1, 0.1, True, [SECRET], {"v": SECRET}):
            self.check(value, "schema_version must be a string")

    def test_very_long_string_not_echoed(self):
        self.check(SECRET + "x" * 200_000, "unsupported schema_version (string of 200033 characters)")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_json(Path(d) / "u.json", {"snapshots": []})
            code, _, err = run("status", "--fixture", str(p))
        self.assertEqual(code, 2)
        self.assertIn("missing required field 'schema_version'", err)

    def test_duplicate_key_masked(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "u.json"
            p.write_text('{"schema_version": "0.2", "%s": 1, "%s": 2}' % (SECRET, SECRET), encoding="utf-8")
            code, _, err = run("status", "--fixture", str(p))
        self.assertEqual(code, 2)
        self.assertNotIn(SECRET, err)
        self.assertIn("duplicate JSON key", err)

    def test_security_doc_matches(self):
        text = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("String values from input files are never echoed", text)


class B2MultiOutputTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def create(self, *extra):
        return run("handoff", "create", "--input", str(TASK), *extra)

    def names(self):
        return sorted(p.name for p in self.d.iterdir())

    def test_existing_json_blocks_both_without_force(self):
        (self.d / "h.json").write_text("keep", encoding="utf-8")
        code, _, err = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"))
        self.assertEqual(code, 4)
        self.assertIn("nothing was written", err)
        self.assertEqual(self.names(), ["h.json"])
        self.assertEqual((self.d / "h.json").read_text(encoding="utf-8"), "keep")

    def test_existing_markdown_blocks_both_without_force(self):
        (self.d / "h.md").write_text("keep", encoding="utf-8")
        code, _, _ = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"))
        self.assertEqual(code, 4)
        self.assertEqual(self.names(), ["h.md"])

    def test_both_written_together(self):
        code, _, _ = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"))
        self.assertEqual((code, self.names()), (0, ["h.json", "h.md"]))
        code, _, _ = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"), "--force")
        self.assertEqual((code, self.names()), (0, ["h.json", "h.md"]))

    def test_case_insensitive_fs_without_case_folding(self):
        """Simulates default macOS APFS: normcase does not fold case, but the file system does.
        Only meaningful where this test's file system is case-insensitive."""
        probe = self.d / "Probe"
        probe.write_text("", encoding="utf-8")
        insensitive = (self.d / "probe").exists()
        probe.unlink()
        if not insensitive:
            self.skipTest("file system is case-sensitive")
        with mock.patch.object(jsonio.os.path, "normcase", lambda p: p):
            for force in ((), ("--force",)):
                with self.subTest(force=force):
                    code, _, err = self.create("--output", str(self.d / "h.md"),
                                               "--json-output", str(self.d / "H.MD"), *force)
                    self.assertEqual(code, 2, err)
                    self.assertIn("same file", err)
                    self.assertEqual(self.names(), [])

    def test_staging_failure_preserves_existing(self):
        for name in ("h.md", "h.json"):
            (self.d / name).write_text("old", encoding="utf-8")
        real = jsonio.tempfile.mkstemp
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise PermissionError(13, "Permission denied")
            return real(*args, **kwargs)

        with mock.patch.object(jsonio.tempfile, "mkstemp", failing):
            code, _, err = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"), "--force")
        self.assertEqual(code, 2)
        self.assertIn("no output was kept", err)
        self.assertEqual(self.names(), ["h.json", "h.md"])
        for name in ("h.md", "h.json"):
            self.assertEqual((self.d / name).read_text(encoding="utf-8"), "old")

    def test_second_replace_failure_removes_new_first_file(self):
        real = jsonio.os.replace
        calls = []

        def failing(src, dst):
            calls.append(1)
            if len(calls) == 2:
                raise PermissionError(13, "Permission denied")
            return real(src, dst)

        with mock.patch.object(jsonio.os, "replace", failing):
            code, _, _ = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"), "--force")
        self.assertEqual(code, 2)
        self.assertEqual(self.names(), [])  # created file and staged temp files removed

    def test_documented_limit_replaced_first_file_is_not_restored(self):
        (self.d / "h.md").write_text("old", encoding="utf-8")
        real = jsonio.os.replace
        calls = []

        def failing(src, dst):
            calls.append(1)
            if len(calls) == 2:
                raise PermissionError(13, "Permission denied")
            return real(src, dst)

        with mock.patch.object(jsonio.os, "replace", failing):
            code, _, err = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"), "--force")
        self.assertEqual(code, 2)
        self.assertIn("already replaced", err)
        self.assertEqual(self.names(), ["h.md"])
        self.assertTrue((self.d / "h.md").read_text(encoding="utf-8").startswith("# Handoff"))

    def test_no_force_write_failure_removes_created(self):
        real_open = open
        calls = []

        def failing(path, mode="r", *args, **kwargs):
            if mode == "xb":
                calls.append(path)
                if len(calls) == 2:
                    raise PermissionError(13, "Permission denied")
            return real_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", failing):
            code, _, err = self.create("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"))
        self.assertEqual(code, 2)
        self.assertIn("no output was kept", err)
        self.assertEqual(self.names(), [])


class B5InputProtectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.task = self.d / "task.json"
        self.original = TASK.read_bytes()
        self.task.write_bytes(self.original)

    def tearDown(self):
        self._tmp.cleanup()

    def assertProtected(self, *outputs_args, input_arg=None):
        code, _, err = run("handoff", "create", "--input", input_arg or str(self.task), *outputs_args, "--force")
        self.assertEqual(code, 4, err)
        self.assertIn("refusing to write over an input file", err)
        self.assertEqual(self.task.read_bytes(), self.original)

    def test_same_path(self):
        self.assertProtected("--output", str(self.task))
        self.assertProtected("--json-output", str(self.task))
        self.assertProtected("--output", str(self.d / "other.md"), "--json-output", str(self.task))
        self.assertFalse((self.d / "other.md").exists())

    def test_relative_and_absolute(self):
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            self.assertProtected("--output", str(self.task.resolve()), input_arg="task.json")
            self.assertProtected("--output", os.path.join("sub", "..", "task.json"), input_arg=str(self.task))
        finally:
            os.chdir(cwd)

    def test_case_variant(self):
        upper = self.d / "TASK.JSON"
        if not upper.exists():
            self.skipTest("file system is case-sensitive; TASK.JSON is a different file")
        self.assertProtected("--output", str(upper))

    def test_symlink(self):
        link = self.d / "link.md"
        try:
            os.symlink(self.task, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted for this user on this platform")
        self.assertProtected("--output", str(link))

    def test_hard_link(self):
        link = self.d / "hard.md"
        try:
            os.link(self.task, link)
        except (OSError, NotImplementedError):
            self.skipTest("hard links not supported on this file system")
        self.assertProtected("--output", str(link))


class SchemaVersionPolicyTests(unittest.TestCase):
    def test_v01_usage_input_accepted_and_output_is_v02(self):
        doc = {"schema_version": "0.1", "snapshots": [snap("codex", ("5h", 10))]}
        with tempfile.TemporaryDirectory() as d:
            p = write_json(Path(d) / "u.json", doc)
            code, out, _ = run("plan", "--fixture", str(p), "--now", NOW.isoformat(), "--json")
        data = json.loads(out)
        self.assertEqual((code, data["schema_version"], data["recommendation"]["provider"]), (0, "0.2", "codex"))
        self.assertEqual(data["candidate_evaluations"][0]["windows"][0]["state"], "reported")

    def test_v01_null_window_defaults_to_not_reported(self):
        doc = {"schema_version": "0.1", "snapshots": [snap("codex", ("5h", 10), ("weekly", None))]}
        snaps = jsonio_normalize(doc)
        self.assertEqual([w.state for w in snaps[0].windows], ["reported", "not_reported"])

    def test_state_requires_v02(self):
        doc = {"schema_version": "0.1", "snapshots": [snap_states("codex", ("5h", 1, None), ("w", None, "failed"))]}
        with self.assertRaises(InputError) as ctx:
            jsonio_normalize(doc)
        self.assertIn("requires schema_version 0.2", str(ctx.exception))
        doc["schema_version"] = "0.2"
        self.assertEqual(jsonio_normalize(doc)[0].windows[1].state, "failed")

    def test_v01_task_and_policy_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            t = write_json(Path(d) / "t.json", {"schema_version": "0.1", "objective": "x"})
            p = write_json(Path(d) / "p.json", {"schema_version": "0.1", "policy_version": "old"})
            j = Path(d) / "h.json"
            self.assertEqual(run("handoff", "create", "--input", str(t), "--json-output", str(j))[0], 0)
            self.assertEqual(json.loads(j.read_text(encoding="utf-8"))["schema_version"], "0.2")
            code, out, _ = run("status", "--demo", "--policy", str(p), "--json")
            self.assertEqual((code, json.loads(out)["schema_version"]), (0, "0.2"))

    def test_examples_are_v02(self):
        for path in sorted(EX.glob("*.json")):
            with self.subTest(path=path.name):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], "0.2")


def jsonio_normalize(doc):
    from quota_handoff.normalizer import normalize_usage_document

    return normalize_usage_document(doc, source="fixture")


class ReasonCodeRegistryTests(unittest.TestCase):
    def test_docs_match_registry(self):
        text = (ROOT / "docs" / "REASON_CODES.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"^\| `([a-z0-9_]+)` \|", text, re.M))
        self.assertEqual(documented, set(REASON_CODES))

    def test_every_emission_goes_through_registry(self):
        tree = ast.parse((SRC / "quota_handoff" / "policy.py").read_text(encoding="utf-8"))
        allowed_calls = {"_rc", "_preference_outcome"}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "reasons"):
                arg = node.args[0]
                ok = isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id in allowed_calls
                self.assertTrue(ok, f"policy.py:{node.lineno} appends an unregistered reason code")
            if isinstance(node, ast.FunctionDef) and node.name == "_preference_outcome":
                for ret in [n for n in ast.walk(node) if isinstance(n, ast.Return)]:
                    self.assertTrue(isinstance(ret.value, ast.Call) and ret.value.func.id == "_rc")

    def test_every_registered_code_is_reachable(self):
        def s(provider, *w, **k):
            return snap(provider, *w, **k)

        scenarios = [
            ([], {}),
            (normalize(*[s(f"p{i}", ("5h", 10), status=st) for i, st in
                         enumerate(("unknown", "stale", "unavailable", "error"))]), {}),
            (normalize(s("a", ("5h", 10), observed=False), s("b", ("5h", 10), age=-999),
                       s("c", ("5h", 10), age=999), s("d")), {}),
            (normalize(s("a", ("5h", 10)), s("b", ("5h", 85)), s("c", ("5h", 99))), {"prefer": "b"}),
            (normalize(s("a", ("5h", 10)), s("b", ("5h", 10))), {"prefer": "zz"}),
            (normalize(s("a", ("5h", 10)), s("b", ("5h", 20))), {"prefer": "b"}),
            (normalize(s("a", ("5h", 85)), s("b", ("5h", 90))), {}),
            (normalize(s("a", ("5h", 10)), snap_states("b", ("5h", 1, None), ("w", None, "failed"),
                                                       ("x", None, None), ("m", None, "unsupported")),
                       s("c", status="unknown", observed=False)), {"prefer": "b"}),
            (normalize(snap_states("b", ("5h", 1, None), ("w", None, "failed"))), {}),
            (normalize(s("a", ("5h", 99))), {}),
            (normalize(s("a", status="unavailable")), {}),
        ]
        seen = set()
        for snaps, kwargs in scenarios:
            result = plan(snaps, NOW, **kwargs)
            seen.update(result.reason_codes)
            for a in result.assessments:
                seen.update(a.reason_codes)
        self.assertEqual(seen, set(REASON_CODES), f"unreached: {set(REASON_CODES) - seen}")

    def test_unregistered_code_raises(self):
        from quota_handoff.policy import _rc

        with self.assertRaises(ValueError):
            _rc("made_up_code")


class PolicyDecisionsKeptTests(unittest.TestCase):
    def test_partial_with_reported_95_excluded(self):
        a = assess_snapshot(normalize(snap_states("codex", ("5h", 95, None), ("w", None, "failed")))[0], NOW)
        self.assertEqual((a.tier, a.eligible, a.manual_review), ("near_limit", False, False))

    def test_tie_by_provider_then_label(self):
        result = plan(normalize(snap("b", ("5h", 5), label="x"), snap("a", ("5h", 5), label="z"),
                                snap("a", ("5h", 5), label="y")), NOW, policy=Policy())
        self.assertEqual(result.selected.snapshot.candidate_id, "a/y")


class PackagingTests(unittest.TestCase):
    def test_license_metadata_consistent(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(license_text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 10-19-hh", license_text)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license = "MIT"', pyproject)
        self.assertIn('license-files = ["LICENSE"]', pyproject)
        self.assertIn('"setuptools>=77"', pyproject)

    def test_version_consistent(self):
        from quota_handoff import __version__

        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', pyproject)


if __name__ == "__main__":
    unittest.main()
