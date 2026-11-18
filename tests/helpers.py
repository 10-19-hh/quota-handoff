from datetime import datetime, timedelta, timezone

from quota_handoff.normalizer import normalize_usage_document

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 18, 9, 0, 0, tzinfo=KST)


def snap(provider, *windows, label="local-primary", status="available", age=0, observed=True):
    """windows: (key, used_pct) pairs."""
    return {
        "provider": provider,
        "account_label": label,
        "source": "fixture",
        "observed_at": (NOW - timedelta(seconds=age)).isoformat() if observed else None,
        "status": status,
        "windows": [{"key": k, "used_pct": v, "resets_at": None} for k, v in windows],
    }


def doc(*snapshots):
    return {"schema_version": "0.2", "snapshots": list(snapshots)}


def normalize(*snapshots):
    return normalize_usage_document(doc(*snapshots), source="fixture")


def two(claude_5h, codex_5h, claude_weekly=None, codex_weekly=None, **kwargs):
    """Mirror of the earlier prototype quota(c, x, cw, xw) helper."""
    cw = claude_5h if claude_weekly is None else claude_weekly
    xw = codex_5h if codex_weekly is None else codex_weekly
    return normalize(
        snap("claude", ("5h", claude_5h), ("weekly", cw), **kwargs),
        snap("codex", ("5h", codex_5h), ("weekly", xw), **kwargs),
    )
