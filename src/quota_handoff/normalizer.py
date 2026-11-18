"""Validate and normalize usage documents into `UsageSnapshot`s.

Rules (PRD F1):
- `used_pct` must be a finite number in [0, 100] or null. Out-of-range values are
  rejected, never clamped. Booleans are not numbers.
- Unknown fields are rejected so that typos are not silently ignored and no field
  is guessed.
- Timestamps must be ISO 8601 with an explicit UTC offset.
- Output order is (provider, account_label), so input row order never matters.
"""

import math
import re
from datetime import datetime

from quota_handoff.errors import InputError
from quota_handoff.redact import safe_name  # noqa: F401  (re-exported for policy/handoff)
from quota_handoff.models import (
    STATUSES,
    SUPPORTED_SCHEMA_VERSIONS,
    WINDOW_STATES,
    UsageSnapshot,
    UsageWindow,
)

PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WINDOW_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")

DOCUMENT_KEYS = {"schema_version", "snapshots", "description"}
SNAPSHOT_KEYS = {"provider", "account_label", "source", "observed_at", "status", "windows"}
WINDOW_KEYS_V01 = {"key", "used_pct", "resets_at"}
WINDOW_KEYS = WINDOW_KEYS_V01 | {"state"}  # schema 0.2


def is_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def parse_timestamp(value, path, issues):
    if not isinstance(value, str):
        issues.append(f"{path}: expected ISO 8601 string, got {type(value).__name__}")
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        issues.append(f"{path}: not a valid ISO 8601 timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        issues.append(f"{path}: timestamp must include a UTC offset (e.g. +09:00 or Z)")
        return None
    return parsed


def check_schema_version(doc, label):
    """Validate and return the schema version. The rejected value itself is never echoed."""
    if not isinstance(doc, dict):
        raise InputError(f"{label}: top level must be a JSON object")
    if "schema_version" not in doc or doc["schema_version"] is None:
        raise InputError(f"{label}: missing required field 'schema_version'")
    version = doc["schema_version"]
    supported = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
    if not isinstance(version, str):
        raise InputError(f"{label}: schema_version must be a string (got {type(version).__name__}); "
                         f"supported: {supported}")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise InputError(f"{label}: unsupported schema_version (string of {len(version)} characters); "
                         f"supported: {supported}")
    return version


def _unknown_keys(obj, allowed, path, issues):
    for key in sorted(set(obj) - allowed):
        issues.append(f"{path}: unknown field {safe_name(key)}")


def _normalize_window(raw, path, issues, version):
    if not isinstance(raw, dict):
        issues.append(f"{path}: expected object")
        return None
    if version == "0.1" and "state" in raw:
        issues.append(f"{path}.state: requires schema_version 0.2")
        raw = {k: v for k, v in raw.items() if k != "state"}
    _unknown_keys(raw, WINDOW_KEYS if version != "0.1" else WINDOW_KEYS_V01, path, issues)
    key = raw.get("key")
    if not isinstance(key, str) or not WINDOW_KEY_RE.match(key):
        issues.append(f"{path}.key: required string matching {WINDOW_KEY_RE.pattern}")
        key = None
    if "used_pct" not in raw:
        issues.append(f"{path}.used_pct: required (use null when not reported)")
    used = raw.get("used_pct")
    if used is not None:
        if not is_number(used):
            issues.append(f"{path}.used_pct: expected number or null, got {type(used).__name__}")
            used = None
        elif not 0 <= used <= 100:
            issues.append(f"{path}.used_pct: {used} is outside [0, 100]")
            used = None
    state = raw.get("state")
    if state is None:
        state = "reported" if used is not None else "not_reported"
    elif state not in WINDOW_STATES:
        issues.append(f"{path}.state: one of {', '.join(WINDOW_STATES)}")
        state = None
    elif state == "reported" and used is None and raw.get("used_pct") is None:
        issues.append(f"{path}: state 'reported' requires a numeric used_pct")
    elif state != "reported" and raw.get("used_pct") is not None:
        issues.append(f"{path}: state {state!r} requires used_pct to be null")
    resets = raw.get("resets_at")
    if resets is not None:
        resets = parse_timestamp(resets, f"{path}.resets_at", issues)
    if key is None or state is None:
        return None
    return UsageWindow(key=key, used_pct=used, resets_at=resets, state=state)


def normalize_snapshot(raw, path, source, issues, version="0.2"):
    if not isinstance(raw, dict):
        issues.append(f"{path}: expected object")
        return None
    start = len(issues)
    _unknown_keys(raw, SNAPSHOT_KEYS, path, issues)

    provider = raw.get("provider")
    if not isinstance(provider, str) or not PROVIDER_RE.match(provider):
        issues.append(f"{path}.provider: required lowercase identifier matching {PROVIDER_RE.pattern}")

    label = raw.get("account_label", "default")
    if not isinstance(label, str) or not LABEL_RE.match(label):
        issues.append(
            f"{path}.account_label: must match {LABEL_RE.pattern} "
            "(an arbitrary label; e-mail addresses and auth identifiers are not accepted)"
        )

    declared_source = raw.get("source", source)
    if declared_source != source:
        issues.append(f"{path}.source: does not match the provider source {source!r}")

    status = raw.get("status")
    if status not in STATUSES:
        issues.append(f"{path}.status: required, one of {', '.join(STATUSES)}")

    observed = raw.get("observed_at")
    if observed is not None:
        observed = parse_timestamp(observed, f"{path}.observed_at", issues)
    elif "observed_at" not in raw:
        issues.append(f"{path}.observed_at: required (use null when unknown)")

    windows_raw = raw.get("windows")
    windows = []
    if not isinstance(windows_raw, list):
        issues.append(f"{path}.windows: required array (may be empty)")
    else:
        seen = set()
        for index, item in enumerate(windows_raw):
            window = _normalize_window(item, f"{path}.windows[{index}]", issues, version)
            if window is None:
                continue
            if window.key in seen:
                issues.append(f"{path}.windows[{index}].key: duplicate window {window.key!r}")
                continue
            seen.add(window.key)
            windows.append(window)

    if len(issues) != start:
        return None
    windows.sort(key=lambda w: w.key)
    return UsageSnapshot(
        provider=provider,
        account_label=label,
        source=source,
        observed_at=observed,
        status=status,
        windows=tuple(windows),
    )


def normalize_usage_document(doc, *, source, label="usage"):
    """Return snapshots sorted by (provider, account_label). Raises InputError."""
    version = check_schema_version(doc, label)
    issues = []
    _unknown_keys(doc, DOCUMENT_KEYS, label, issues)
    rows = doc.get("snapshots")
    if not isinstance(rows, list):
        raise InputError(f"{label}: 'snapshots' must be an array")
    snapshots = []
    seen = {}
    for index, raw in enumerate(rows):
        path = f"snapshots[{index}]"
        snapshot = normalize_snapshot(raw, path, source, issues, version)
        if snapshot is None:
            continue
        if snapshot.sort_key in seen:
            issues.append(
                f"{path}: duplicate snapshot for {snapshot.candidate_id} (first at snapshots[{seen[snapshot.sort_key]}])"
            )
            continue
        seen[snapshot.sort_key] = index
        snapshots.append(snapshot)
    if issues:
        raise InputError(f"{label}: invalid usage document", issues)
    snapshots.sort(key=lambda s: s.sort_key)
    return snapshots
