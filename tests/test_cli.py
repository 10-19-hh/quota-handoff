"""End-to-end CLI tests mapped to PRD M0 acceptance tests (AT-xx)."""

import ast
import io
import json
import os
import re
import socket
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from unittest import mock

from quota_handoff.cli import main
from quota_handoff.redact import redact

from tests import ROOT, SRC

EX = ROOT / "examples"
NOW = "2026-11-18T09:02:00+09:00"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stderr(err):  # argparse writes usage errors to sys.stderr
            code = main(list(argv), out, err)
    except SystemExit as exc:  # argparse errors
        code = exc.code
    return code, out.getvalue(), err.getvalue()


class _Forbidden(AssertionError):
    pass


def forbid_side_effects():
    """Patch process, network and registry entry points so any call fails the test (AT-08)."""

    def boom(*args, **kwargs):
        raise _Forbidden(f"forbidden call: args={args!r}")

    stack = ExitStack()
    targets = [(subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (os, "system"),
               (os, "popen"), (socket, "socket"), (socket, "create_connection")]
    for name in ("startfile", "execv", "execve", "spawnv", "spawnve", "posix_spawn", "fork"):
        if hasattr(os, name):
            targets.append((os, name))
    for module, attr in targets:
        stack.enter_context(mock.patch.object(module, attr, boom))
    return stack


class SampleCommandsTests(unittest.TestCase):
    """AT-01: the three README sample commands work with no Orca/Claude/Codex installed."""

    def test_status(self):
        code, out, _ = run("status", "--fixture", str(EX / "usage-sample.json"), "--now", NOW)
        self.assertEqual(code, 0)
        self.assertIn("claude/local-primary", out)
        self.assertIn("source: fixture", out)
        self.assertIn("not a guarantee", out)

    def test_status_json_contract(self):
        code, out, _ = run("status", "--fixture", str(EX / "usage-sample.json"), "--now", NOW, "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        for key in ("snapshots", "warnings", "evaluated_at"):
            self.assertIn(key, data)
        for s in data["snapshots"]:
            for key in ("source", "observed_at", "freshness", "effective_status"):
                self.assertIn(key, s)

    def test_plan(self):
        code, out, _ = run("plan", "--fixture", str(EX / "usage-sample.json"), "--task", "code",
                           "--prefer", "codex", "--now", NOW, "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["recommendation"]["decision"], "recommended")
        self.assertEqual(data["recommendation"]["provider"], "codex")
        self.assertTrue(data["manual_approval_required"])
        self.assertTrue(data["reason_codes"])
        self.assertEqual(len(data["candidate_evaluations"]), 2)

    def test_handoff_create(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "handoff.md"
            code, _, err = run("handoff", "create", "--input", str(EX / "task-sample.json"), "--output", str(target))
            self.assertEqual(code, 0, err)
            self.assertTrue(target.read_text(encoding="utf-8").startswith("# Handoff: "))

    def test_policy_file(self):
        code, out, _ = run("plan", "--fixture", str(EX / "usage-sample.json"), "--task", "code",
                           "--policy", str(EX / "policy-sample.json"), "--now", NOW, "--json")
        data = json.loads(out)
        self.assertEqual((code, data["preference_source"], data["recommendation"]["provider"]), (0, "policy", "codex"))
        self.assertEqual(data["policy_version"], "sample-0.1")


class AcceptanceTests(unittest.TestCase):
    def test_at02_near_limit_weekly_not_recommended(self):
        code, out, _ = run("plan", "--fixture", str(EX / "usage-near-limit.json"), "--now", NOW, "--json")
        data = json.loads(out)
        self.assertEqual((code, data["recommendation"]["decision"]), (3, "no_eligible_provider"))

    def test_at03_unknown_manual(self):
        code, out, _ = run("plan", "--fixture", str(EX / "usage-uncertain.json"), "--now", NOW, "--json")
        data = json.loads(out)
        self.assertEqual((code, data["recommendation"]["decision"]), (3, "manual_decision_required"))
        self.assertTrue(all(c["risk_pct"] is None for c in data["candidate_evaluations"]))

    def test_at04_stale_without_now_override(self):
        # Without --now the real clock is used; the fixed 2026-11-18 sample is then
        # stale (or, on a clock behind the sample, flagged as future). Never auto-recommended.
        code, out, _ = run("plan", "--fixture", str(EX / "usage-sample.json"),
                           "--now", "2026-11-18T09:10:00+09:00", "--json")
        data = json.loads(out)
        self.assertEqual((code, data["recommendation"]["decision"]), (3, "manual_decision_required"))
        self.assertTrue(all(c["effective_status"] == "stale" for c in data["candidate_evaluations"]))

    def test_at05_shuffled_fixture_same_result(self):
        doc = json.loads((EX / "usage-sample.json").read_text(encoding="utf-8"))
        doc["snapshots"].reverse()
        for s in doc["snapshots"]:
            s["windows"].reverse()
        with tempfile.TemporaryDirectory() as d:
            shuffled = Path(d) / "shuffled.json"
            shuffled.write_text(json.dumps(doc), encoding="utf-8")
            outputs = [run("plan", "--fixture", str(p), "--now", NOW, "--json")[1]
                       for p in (EX / "usage-sample.json", shuffled)]
        self.assertEqual(outputs[0], outputs[1])

    def test_at06_examples_have_no_secrets_or_abs_paths(self):
        abs_path = re.compile(r"(?i)([A-Z]:\\|/home/|/Users/|\\\\)")
        for path in sorted(EX.glob("*.json")) + [ROOT / "README.md"]:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertEqual(redact(text)[1], [], "secret-like pattern in public example")
                if path.suffix == ".json":
                    self.assertIsNone(abs_path.search(text))
                self.assertNotRegex(text, r"[\w.+-]+@[\w-]+\.[\w.]+")

    def test_at07_overwrite_refused_exit_4(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "handoff.md"
            target.write_text("keep me\n", encoding="utf-8")
            code, _, err = run("handoff", "create", "--input", str(EX / "task-sample.json"), "--output", str(target))
            self.assertEqual(code, 4)
            self.assertIn("refusing to overwrite", err)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep me\n")
            code, _, _ = run("handoff", "create", "--input", str(EX / "task-sample.json"),
                             "--output", str(target), "--force")
            self.assertEqual(code, 0)

    def test_at08_no_process_network_calls_during_cli(self):
        with tempfile.TemporaryDirectory() as d, forbid_side_effects():
            for argv in (
                ("status", "--fixture", str(EX / "usage-sample.json"), "--now", NOW),
                ("status", "--fixture", str(EX / "usage-sample.json"), "--json"),
                ("plan", "--fixture", str(EX / "usage-uncertain.json"), "--now", NOW),
                ("plan", "--fixture", str(EX / "usage-sample.json"), "--prefer", "codex", "--now", NOW, "--json"),
                ("handoff", "create", "--input", str(EX / "task-sample.json"),
                 "--output", str(Path(d) / "h.md"), "--json-output", str(Path(d) / "h.json")),
            ):
                code, _, err = run(*argv)
                self.assertIn(code, (0, 3), err)

    def test_at08_static_no_forbidden_imports(self):
        forbidden = {"subprocess", "socket", "urllib", "http", "ctypes", "winreg", "_winapi",
                     "multiprocessing", "asyncio", "ssl", "requests", "shutil"}
        for path in sorted(SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertNotIn(name.split(".")[0], forbidden)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                    self.assertNotIn(node.attr, {"system", "popen", "startfile", "execv", "spawnv", "chmod",
                                                 "remove", "rmdir", "removedirs"}, f"{path.name}: os.{node.attr}")

    def test_at09_invalid_input_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            cases = {
                '{"schema_version": "9", "snapshots": []}': "unsupported schema_version",
                '{"schema_version": "0.1", "snapshots": [{"provider": "codex", "status": "available", '
                '"observed_at": null, "windows": [{"key": "5h", "used_pct": 120}]}]}': "outside [0, 100]",
                "not json": "invalid JSON",
            }
            for content, message in cases.items():
                bad.write_text(content, encoding="utf-8")
                with self.subTest(message=message):
                    code, _, err = run("plan", "--fixture", str(bad), "--now", NOW)
                    self.assertEqual(code, 2)
                    self.assertIn(message, err)
            code, _, err = run("status", "--fixture", str(Path(d) / "missing.json"))
            self.assertEqual(code, 2)
            self.assertEqual(run("plan", "--fixture", str(EX / "usage-sample.json"), "--now", "yesterday")[0], 2)
            self.assertEqual(run("plan", "--fixture", str(EX / "usage-sample.json"), "--prefer", "../x")[0], 2)
            self.assertEqual(run("bogus")[0], 2)
            bad_task = Path(d) / "task.json"
            bad_task.write_text('{"schema_version": "0.1", "done": "x"}', encoding="utf-8")
            code, _, err = run("handoff", "create", "--input", str(bad_task))
            self.assertEqual(code, 2)
            self.assertIn("objective", err)

    def test_at10_paths_and_bom(self):
        # PowerShell 5.1 writes UTF-8 with BOM; relative paths with either separator.
        raw = (EX / "usage-sample.json").read_bytes()
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "dir with space"
            sub.mkdir()
            (sub / "u.json").write_bytes(b"\xef\xbb\xbf" + raw)
            code, _, err = run("status", "--fixture", str(sub / "u.json"), "--now", NOW)
            self.assertEqual(code, 0, err)

    def test_handoff_deterministic_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for name in ("a", "b"):
                md, js = Path(d) / f"{name}.md", Path(d) / f"{name}.json"
                run("handoff", "create", "--input", str(EX / "task-sample.json"),
                    "--output", str(md), "--json-output", str(js))
                files.append((md.read_bytes(), js.read_bytes()))
            self.assertEqual(files[0], files[1])

    def test_same_output_paths_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "h")
            code, _, _ = run("handoff", "create", "--input", str(EX / "task-sample.json"),
                             "--output", p, "--json-output", p)
            self.assertEqual(code, 2)
            self.assertFalse(Path(p).exists())

    def test_handoff_stdout_when_no_output(self):
        code, out, _ = run("handoff", "create", "--input", str(EX / "task-sample.json"))
        self.assertEqual(code, 0)
        self.assertIn("## How to verify", out)


if __name__ == "__main__":
    unittest.main()
