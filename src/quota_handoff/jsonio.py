"""Strict JSON reading and checked file writing.

Reading: size-limited, UTF-8 (BOM tolerated), duplicate keys and NaN/Infinity rejected.
Read errors and too-deep nesting are reported as input errors (exit 2).

Writing (`write_text_files`), for one or more explicit output paths:
1. Every target is checked before anything is written or deleted: target is not one of the
   protected (input) files, targets do not name the same file (`same_file`: absolute path,
   `..`, case-only differences, symlinks, hard links), parent directory exists, target is
   not a directory, and without `force` no target exists yet. These checks apply with
   `force` too. Any failure writes and deletes nothing.
2. Without `force`, each file is created exclusively; existing files are never touched.
3. With `force`, all contents are first staged as temporary files next to their targets,
   then each target is replaced with `os.replace`.
4. If a write itself fails (I/O error), files that did not exist before this call are
   removed again. Pre-existing files are never deleted.

This is NOT a multi-file transaction: with `force`, if replacing the second target fails
after the first was replaced, the first target keeps its new content.
"""

import json
import os
import tempfile
import unicodedata
from pathlib import Path

from quota_handoff.errors import InputError, SafetyGateError
from quota_handoff.redact import safe_name

MAX_INPUT_BYTES = 1024 * 1024


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {safe_name(key)}")
        result[key] = value
    return result


def _reject_constant(name):
    raise ValueError(f"non-finite number {name} is not allowed")


def loads_strict(text, label="input"):
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise InputError(f"{label}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from None
    except ValueError as exc:
        raise InputError(f"{label}: {exc}") from None
    except RecursionError:
        raise InputError(f"{label}: JSON is nested too deeply") from None


def read_json_file(path, label):
    path = Path(path)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise InputError(f"{label}: file not found: {path}") from None
    except OSError as exc:
        raise InputError(f"{label}: cannot access {path}: {exc.strerror}") from None
    if not path.is_file():
        raise InputError(f"{label}: not a regular file: {path}")
    if size > MAX_INPUT_BYTES:
        raise InputError(f"{label}: file is larger than {MAX_INPUT_BYTES} bytes")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError(f"{label}: cannot read {path}: {exc.strerror or type(exc).__name__}") from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(f"{label}: file is not valid UTF-8") from None
    return loads_strict(text, label)


def path_key(path):
    """Conservative comparison key for a path, computed without touching the file.

    Absolute, symlink-resolved (as far as the path exists), separator/case-normalized by the
    OS, Unicode NFC-normalized and casefolded. Two paths with the same key are treated as the
    same target on every OS, even on case-sensitive file systems.
    """
    resolved = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    return unicodedata.normalize("NFC", resolved).casefold()


def same_file(a, b):
    """True if two paths may refer to the same file (checked before any write).

    True when the conservative keys match (relative/absolute, `..`, case-only differences),
    or when both exist and `os.path.samefile` reports the same file (symlinks, hard links,
    case-insensitive file systems).
    """
    if path_key(a) == path_key(b):
        return True
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return False


def _check_targets(paths, force, protected):
    for path in paths:  # input protection first: it is the most important refusal
        for source in protected:
            if same_file(path, source):
                raise SafetyGateError(f"refusing to write over an input file: {path}; nothing was written")
    for path in paths:
        parent = path.parent if str(path.parent) else Path(".")
        if not parent.is_dir():
            raise InputError(f"output directory does not exist: {parent}")
        if path.is_dir():
            raise InputError(f"output path is a directory: {path}")
    for i, first in enumerate(paths):
        for second in paths[i + 1:]:
            if same_file(first, second):
                raise InputError(f"output paths refer to the same file: {first} and {second}; nothing was written")
    existed = [os.path.lexists(p) for p in paths]
    if not force:
        for path, exists in zip(paths, existed):
            if exists:
                raise SafetyGateError(
                    f"refusing to overwrite existing file: {path} (pass --force to replace it); nothing was written"
                )
    return existed


def _stage(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent if str(path.parent) else ".",
                                     prefix=".quota-handoff-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _remove(temporary)
        raise
    return temporary


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def write_text_files(outputs, *, force=False, protected=()):
    """Write [(path, text), ...]. See the module docstring for guarantees and limits.

    Line endings are always LF so output bytes are identical across OSes.
    """
    paths = [Path(p) for p, _ in outputs]
    payloads = [text.encode("utf-8") for _, text in outputs]
    existed = _check_targets(paths, force, [Path(p) for p in protected])
    created, written, staged = [], [], []
    try:
        if force:
            for path, data in zip(paths, payloads):
                staged.append(_stage(path, data))  # appended one by one so a failure cleans up earlier ones
        for index, (path, data) in enumerate(zip(paths, payloads)):
            if force:
                os.replace(staged[index], path)
                staged[index] = None
                if not existed[index]:
                    created.append(path)
            else:
                try:
                    handle = open(path, "xb")
                except FileExistsError:
                    raise SafetyGateError(
                        f"refusing to overwrite existing file: {path} (created concurrently); no output was kept"
                    ) from None
                created.append(path)  # recorded before writing so a failed write is rolled back
                with handle:
                    handle.write(data)
            written.append(path)
    except BaseException as exc:
        for path in created:
            _remove(path)
        for temporary in staged:
            if temporary:
                _remove(temporary)
        if isinstance(exc, OSError):
            replaced = [str(p) for p in written if p not in created]
            note = f"; already replaced: {', '.join(replaced)}" if replaced else "; no output was kept"
            raise InputError(f"cannot write output: {exc.strerror or type(exc).__name__}{note}") from None
        raise


def write_text_file(path, content, *, force=False):
    """Write a single file; see `write_text_files`."""
    write_text_files([(path, content)], force=force)
