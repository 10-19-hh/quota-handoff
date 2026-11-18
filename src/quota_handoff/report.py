"""Serialize status / plan results to JSON-able dicts and plain text (PRD section 8)."""

from quota_handoff.models import SCHEMA_VERSION
from quota_handoff.policy import OBSERVATION_DISCLAIMER, _pct

DEMO_BANNER = "DEMO DATA: synthetic usage generated for this run. It is not your real usage."

_STATE_TEXT = {"unsupported": "unsupported (ignored)", "failed": "lookup failed", "not_reported": "not reported"}


def _ts(value):
    return value.isoformat() if value is not None else None


def _window_dict(window):
    return {"key": window.key, "state": window.state, "used_pct": window.used_pct,
            "resets_at": _ts(window.resets_at)}


def _assessment_dict(a):
    s = a.snapshot
    return {
        "candidate": s.candidate_id,
        "provider": s.provider,
        "account_label": s.account_label,
        "source": s.source,
        "observed_at": _ts(s.observed_at),
        "declared_status": s.status,
        "effective_status": a.effective_status,
        "freshness": a.freshness,
        "age_seconds": a.age_seconds,
        "windows": [_window_dict(w) for w in s.windows],
        "risk_pct": a.risk_pct,
        "risk_window": a.risk_window,
        "tier": a.tier,
        "confidence": a.confidence,
        "missing_windows": list(a.missing_windows),
        "unsupported_windows": list(a.unsupported_windows),
        "eligible": a.eligible,
        "manual_review": a.manual_review,
        "reason_codes": list(a.reason_codes),
    }


def _warnings(assessments, demo=False):
    warnings = [DEMO_BANNER] if demo else []
    for a in assessments:
        cid = a.snapshot.candidate_id
        if a.uncertain:
            warnings.append(f"{cid}: usage is {a.effective_status} ({', '.join(a.reason_codes)}); figures not used.")
        if a.partial:
            detail = ", ".join(f"{w.key} ({w.state})" for w in a.snapshot.windows if w.key in a.missing_windows)
            action = "not auto-selected" if a.manual_review else "excluded (a reported window is near the limit)"
            warnings.append(f"{cid}: partial usage (confidence=partial), missing {detail}; {action}.")
    warnings.append(OBSERVATION_DISCLAIMER)
    return warnings


def status_to_dict(assessments, now, policy, demo=False):
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "status",
        "demo": demo,
        "evaluated_at": _ts(now),
        "policy_version": policy.policy_version,
        "snapshots": [_assessment_dict(a) for a in assessments],
        "warnings": _warnings(assessments, demo),
    }


def plan_to_dict(result, now, task, demo=False):
    selected = result.selected
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "plan",
        "demo": demo,
        "evaluated_at": _ts(now),
        "policy_version": result.policy.policy_version,
        "task": task,
        "preferred_provider": result.preferred_provider,
        "preference_source": result.preference_source,
        "recommendation": {
            "decision": result.decision,
            "candidate": selected.snapshot.candidate_id if selected else None,
            "provider": selected.snapshot.provider if selected else None,
            "account_label": selected.snapshot.account_label if selected else None,
        },
        "reason_codes": list(result.reason_codes),
        "summary": result.summary,
        "candidate_evaluations": [_assessment_dict(a) for a in result.assessments],
        "warnings": _warnings(result.assessments, demo),
        "manual_approval_required": True,
    }


def _fmt_window(window, trusted):
    if window.state != "reported":
        value = _STATE_TEXT[window.state]
    elif trusted:
        value = _pct(window.used_pct)
    else:
        value = f"{_pct(window.used_pct)} (not trusted)"
    return f"{window.key} {value}"


def _fmt_age(age):
    if age is None:
        return "age unknown"
    if age < 0:
        return f"{int(-age)}s in the future"
    return f"{int(age)}s old"


def _candidate_lines(assessments):
    lines = []
    for a in assessments:
        s = a.snapshot
        trusted = a.available
        windows = "; ".join(_fmt_window(w, trusted) for w in s.windows) or "no windows"
        observed = _ts(s.observed_at) or "unknown time"
        lines.append(f"  {s.candidate_id}")
        lines.append(f"    status: {a.effective_status} (declared {s.status}), {a.freshness}, {_fmt_age(a.age_seconds)}")
        lines.append(f"    source: {s.source}, observed_at: {observed}")
        lines.append(f"    windows: {windows}")
        if trusted:
            lines.append(f"    highest reported window: {_pct(a.risk_pct)} ({a.risk_window}), tier: {a.tier}, "
                         f"confidence: {a.confidence}")
        lines.append(f"    reasons: {', '.join(a.reason_codes)}")
    return lines


def _wrap(lines, demo):
    if demo:
        banner = f"*** {DEMO_BANNER} ***"
        lines = [banner, ""] + lines + ["", banner]
    return "\n".join(lines) + "\n"


def status_to_text(assessments, now, policy, demo=False):
    lines = [f"Usage status (evaluated {now.isoformat()}, policy {policy.policy_version})", ""]
    lines += _candidate_lines(assessments) or ["  (no snapshots)"]
    lines += ["", "Warnings:"] + [f"  - {w}" for w in _warnings(assessments)]
    return _wrap(lines, demo)


def plan_to_text(result, now, task, demo=False):
    pref = f"{result.preferred_provider} (from {result.preference_source})" if result.preferred_provider else "none"
    lines = [
        f"Plan (evaluated {now.isoformat()}, policy {result.policy.policy_version})",
        f"  task: {task or '-'}    preference: {pref}",
        "",
        f"Decision: {result.decision}",
        f"  {result.summary}",
        f"  reason_codes: {', '.join(result.reason_codes)}",
        "  manual approval required: yes (this tool never starts an agent session)",
        "",
        "Candidates:",
    ]
    lines += _candidate_lines(result.assessments) or ["  (no snapshots)"]
    lines += ["", "Warnings:"] + [f"  - {w}" for w in _warnings(result.assessments)]
    return _wrap(lines, demo)
