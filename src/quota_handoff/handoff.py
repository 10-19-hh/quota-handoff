"""User-authored checkpoint JSON -> deterministic handoff Markdown / JSON (PRD F3).

This is a template transformation, not model inference. It never reads Git, file
contents, diffs or credentials; it only uses what the user wrote in the input file.
No timestamps are generated, so identical input always yields identical bytes.
"""

from dataclasses import dataclass

from quota_handoff.errors import InputError
from quota_handoff.models import SCHEMA_VERSION
from quota_handoff.normalizer import check_schema_version, safe_name
from quota_handoff.redact import redact

TEST_STATUSES = ("pass", "fail", "not_run", "unknown")

STRING_LIST_FIELDS = ("done", "remaining", "blockers", "next_steps", "notes", "read_first", "do_not")
OPTIONAL_STRING_FIELDS = ("title", "from_agent", "to_agent", "created_at", "expected_outcome")
TASK_KEYS = {"schema_version", "objective", "tests", *STRING_LIST_FIELDS, *OPTIONAL_STRING_FIELDS}
TEST_KEYS = {"name", "command", "status", "notes"}

BASELINE_RESTRICTIONS = (
    "Do not commit, push or merge without explicit approval from the user.",
    "Do not open or copy credential, token or auth files.",
    "Do not change OS settings, services, ACLs or the registry as part of this task.",
)

MAX_TEXT = 4000
MAX_ITEMS = 200


@dataclass(frozen=True)
class HandoffTask:
    objective: str
    title: str
    from_agent: str
    to_agent: str
    created_at: str
    expected_outcome: str
    done: tuple
    remaining: tuple
    blockers: tuple
    next_steps: tuple
    notes: tuple
    read_first: tuple
    do_not: tuple
    tests: tuple  # tuple[dict]
    redactions: tuple  # tuple[(path, kind)]


def _text(value, path, issues, redactions, *, required=False):
    if value is None:
        if required:
            issues.append(f"{path}: required non-empty string")
        return ""
    if not isinstance(value, str):
        issues.append(f"{path}: expected string, got {type(value).__name__}")
        return ""
    value = value.strip()
    if required and not value:
        issues.append(f"{path}: required non-empty string")
    if len(value) > MAX_TEXT:
        issues.append(f"{path}: longer than {MAX_TEXT} characters")
        return ""
    masked, kinds = redact(value)
    for kind in kinds:
        redactions.append((path, kind))
    return masked


def _text_list(value, path, issues, redactions):
    if value is None:
        return ()
    if not isinstance(value, list):
        issues.append(f"{path}: expected array of strings")
        return ()
    if len(value) > MAX_ITEMS:
        issues.append(f"{path}: more than {MAX_ITEMS} items")
        return ()
    items = []
    for index, item in enumerate(value):
        text = _text(item, f"{path}[{index}]", issues, redactions, required=True)
        if text:
            items.append(text)
    return tuple(items)


def load_task(doc, label="task"):
    check_schema_version(doc, label)
    issues, redactions = [], []
    for key in sorted(set(doc) - TASK_KEYS):
        issues.append(f"{label}: unknown field {safe_name(key)}")
    fields = {"objective": _text(doc.get("objective"), "objective", issues, redactions, required=True)}
    for name in OPTIONAL_STRING_FIELDS:
        fields[name] = _text(doc.get(name), name, issues, redactions)
    for name in STRING_LIST_FIELDS:
        fields[name] = _text_list(doc.get(name), name, issues, redactions)

    tests = []
    raw_tests = doc.get("tests")
    if raw_tests is not None and not isinstance(raw_tests, list):
        issues.append("tests: expected array of objects")
        raw_tests = []
    for index, raw in enumerate(raw_tests or []):
        path = f"tests[{index}]"
        if not isinstance(raw, dict):
            issues.append(f"{path}: expected object")
            continue
        for key in sorted(set(raw) - TEST_KEYS):
            issues.append(f"{path}: unknown field {safe_name(key)}")
        status = raw.get("status")
        if status not in TEST_STATUSES:
            issues.append(f"{path}.status: required, one of {', '.join(TEST_STATUSES)}")
        tests.append({
            "name": _text(raw.get("name"), f"{path}.name", issues, redactions, required=True),
            "command": _text(raw.get("command"), f"{path}.command", issues, redactions),
            "status": status,
            "notes": _text(raw.get("notes"), f"{path}.notes", issues, redactions),
        })

    if issues:
        raise InputError(f"{label}: invalid handoff input", issues)
    return HandoffTask(tests=tuple(tests), redactions=tuple(redactions), **fields)


def restrictions(task):
    return BASELINE_RESTRICTIONS + tuple(r for r in task.do_not if r not in BASELINE_RESTRICTIONS)


def to_json_dict(task):
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "handoff",
        "title": task.title or None,
        "from_agent": task.from_agent or None,
        "to_agent": task.to_agent or None,
        "created_at": task.created_at or None,
        "objective": task.objective,
        "completed": list(task.done),
        "pending": list(task.remaining),
        "blockers": list(task.blockers),
        "read_first": list(task.read_first),
        "tests": [dict(t) for t in task.tests],
        "next_action": list(task.next_steps),
        "restrictions": list(restrictions(task)),
        "expected_outcome": task.expected_outcome or None,
        "notes": list(task.notes),
        "redactions": [{"path": p, "kind": k} for p, k in task.redactions],
    }


def _code(value):
    fence = "``" if "`" in value else "`"
    pad = " " if fence == "``" else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _one_line(value):
    return " ".join(value.split())


def _bullets(items, empty="_None recorded._"):
    if not items:
        return [empty]
    return [f"- {_one_line(item)}" for item in items]


def render_markdown(task):
    title = _one_line(task.title or task.objective)
    if len(title) > 80:
        title = title[:77] + "..."
    lines = [f"# Handoff: {title}", ""]
    lines.append("> Generated by quota-handoff from user-provided checkpoint data (template, no model).")
    lines.append("> Review before sharing: secret masking is pattern-based and is not a guarantee.")
    lines.append("> Start the next session yourself in the official agent app; this file does not run anything.")
    lines.append("")
    meta = [
        ("From", task.from_agent),
        ("To", task.to_agent),
        ("Checkpoint time (user-provided)", task.created_at),
    ]
    meta = [f"- **{k}:** {_one_line(v)}" for k, v in meta if v]
    if meta:
        lines += meta + [""]

    lines += ["## Objective", "", task.objective, ""]
    lines += ["## Previous state summary", "", "### Completed", ""] + _bullets(task.done) + [""]
    lines += ["### Remaining", ""] + _bullets(task.remaining) + [""]
    lines += ["### Blockers", ""] + _bullets(task.blockers) + [""]
    lines += ["## Read first", ""] + _bullets(task.read_first) + [""]

    lines += ["## How to verify", ""]
    if task.tests:
        for test in task.tests:
            entry = f"- **{_one_line(test['name'])}**: last status `{test['status']}`"
            if test["command"]:
                entry += f"; run {_code(_one_line(test['command']))}"
            if test["notes"]:
                entry += f"; {_one_line(test['notes'])}"
            lines.append(entry)
    else:
        lines.append("_No verification steps recorded._")
    lines.append("")

    lines += ["## Next steps", ""]
    if task.next_steps:
        lines += [f"{i}. {_one_line(step)}" for i, step in enumerate(task.next_steps, 1)]
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines += ["## Do not", ""] + _bullets(restrictions(task)) + [""]
    lines += ["## Expected outcome", "", task.expected_outcome or "_Not specified._", ""]
    lines += ["## Notes", ""] + _bullets(task.notes) + [""]

    if task.redactions:
        lines += ["## Redactions applied", ""]
        lines += [f"- `{path}`: {kind}" for path, kind in task.redactions]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
