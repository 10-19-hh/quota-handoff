"""T-008.5 regression tests: output-path conflicts are refused before anything is written or
deleted, with or without --force. Synthetic data in temporary directories only.

Platform note: these run on whatever OS executes them. Case-only differences are rejected
by the conservative path key on every OS, so the case tests do not depend on the file
system. Real macOS (APFS) has not been exercised unless CI ran there.
"""

import ntpath
import os
import posixpath
import tempfile
import unicodedata
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from quota_handoff import jsonio

from tests import ROOT
from tests.test_cli import run

TASK = ROOT / "examples" / "task-sample.json"
OLD = b"pre-existing bytes \x00\xff\r\n keep\n"


def no_write_or_delete():
    """Fail the test if anything is opened for writing, staged, replaced or deleted."""
    real_open = open

    def guarded_open(path, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wxa+"):
            raise AssertionError(f"unexpected write: {path} ({mode})")
        return real_open(path, mode, *args, **kwargs)

    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected file operation: {args!r}")

    stack = ExitStack()
    stack.enter_context(mock.patch("builtins.open", guarded_open))
    stack.enter_context(mock.patch.object(jsonio.tempfile, "mkstemp", boom))
    stack.enter_context(mock.patch.object(jsonio.os, "replace", boom))
    stack.enter_context(mock.patch.object(jsonio.os, "unlink", boom))
    return stack


class OutputConflictTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def snapshot(self):
        return {p.name: p.read_bytes() for p in sorted(self.d.iterdir()) if p.is_file()}

    def refused(self, *args, code=2, input_path=TASK):
        before = self.snapshot()
        with no_write_or_delete():
            result = run("handoff", "create", "--input", str(input_path), *args)
        self.assertEqual(result[0], code, result[2])
        self.assertEqual(self.snapshot(), before, "files changed although the command was refused")
        return result

    def test_new_case_variant_pair(self):
        for force in ((), ("--force",)):
            with self.subTest(force=force):
                _, _, err = self.refused("--output", str(self.d / "h.md"), "--json-output", str(self.d / "H.md"), *force)
                self.assertIn("refer to the same file", err)
                self.assertEqual(self.snapshot(), {})

    def test_existing_file_with_force_case_variant(self):
        (self.d / "h.md").write_bytes(OLD)
        self.refused("--output", str(self.d / "h.md"), "--json-output", str(self.d / "H.md"), "--force")
        self.refused("--output", str(self.d / "H.MD"), "--json-output", str(self.d / "h.md"), "--force")
        self.assertEqual(self.snapshot(), {"h.md": OLD})

    def test_existing_identical_path_with_force(self):
        (self.d / "h.md").write_bytes(OLD)
        self.refused("--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.md"), "--force")
        self.assertEqual(self.snapshot(), {"h.md": OLD})

    def test_relative_and_absolute(self):
        (self.d / "h.md").write_bytes(OLD)
        (self.d / "sub").mkdir()
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            self.refused("--output", "h.md", "--json-output", str(self.d / "h.md"), "--force")
            self.refused("--output", os.path.join(".", "h.md"), "--json-output", os.path.join("sub", "..", "h.md"),
                         "--force")
        finally:
            os.chdir(cwd)
        self.assertEqual((self.d / "h.md").read_bytes(), OLD)

    def test_hard_link(self):
        (self.d / "a.md").write_bytes(OLD)
        try:
            os.link(self.d / "a.md", self.d / "b.md")
        except (OSError, NotImplementedError):
            self.skipTest("hard links not supported on this file system")
        self.refused("--output", str(self.d / "a.md"), "--json-output", str(self.d / "b.md"), "--force")
        self.assertEqual((self.d / "a.md").read_bytes(), OLD)
        self.assertEqual((self.d / "b.md").read_bytes(), OLD)

    def test_symlink(self):
        (self.d / "a.md").write_bytes(OLD)
        try:
            os.symlink(self.d / "a.md", self.d / "link.md")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted for this user on this platform")
        self.refused("--output", str(self.d / "a.md"), "--json-output", str(self.d / "link.md"), "--force")
        self.assertEqual((self.d / "a.md").read_bytes(), OLD)

    def test_input_conflicts(self):
        task = self.d / "task.json"
        task.write_bytes(TASK.read_bytes())
        original = task.read_bytes()
        for target in (task, self.d / "TASK.JSON", self.d / "sub" / ".." / "task.json"):
            with self.subTest(target=target.name):
                self.refused("--output", str(target), "--force", code=4, input_path=task)
                self.refused("--output", str(self.d / "ok.md"), "--json-output", str(target), "--force",
                             code=4, input_path=task)
        self.assertEqual(task.read_bytes(), original)
        self.assertFalse((self.d / "ok.md").exists())

    def test_distinct_outputs_still_written(self):
        code, _, err = run("handoff", "create", "--input", str(TASK),
                           "--output", str(self.d / "h.md"), "--json-output", str(self.d / "h.json"))
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.snapshot()), ["h.json", "h.md"])


class PathKeyTests(unittest.TestCase):
    def test_case_and_unicode_forms(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(jsonio.same_file(os.path.join(d, "h.md"), os.path.join(d, "H.MD")))
            nfc = unicodedata.normalize("NFC", "caf\u00e9.md")
            nfd = unicodedata.normalize("NFD", "caf\u00e9.md")
            self.assertTrue(jsonio.same_file(os.path.join(d, nfc), os.path.join(d, nfd)))
            self.assertFalse(jsonio.same_file(os.path.join(d, "h.md"), os.path.join(d, "h.json")))

    def test_platform_separators(self):
        with tempfile.TemporaryDirectory() as d:
            if os.name == "nt":
                self.assertTrue(jsonio.same_file(d + "\\x\\h.md", d + "/x/H.md"))
            else:
                self.assertTrue(jsonio.same_file(d + "/x/h.md", d + "//x/./H.md"))

    def test_os_path_modules_behave_as_assumed(self):
        # Documents the assumption behind path_key: only Windows normcase folds case and
        # separators; casefold() is what makes the comparison case-insensitive on POSIX.
        self.assertEqual(ntpath.normcase("C:/A/B.md"), "c:\\a\\b.md")
        self.assertEqual(posixpath.normcase("/A/B.md"), "/A/B.md")


class SecurityDocTests(unittest.TestCase):
    def test_storage_guarantees_documented(self):
        text = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("refused before anything is written or deleted", text)
        self.assertIn("Pre-existing files are never deleted", text)
        self.assertNotIn("the first is removed", text)


if __name__ == "__main__":
    unittest.main()
