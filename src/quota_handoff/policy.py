"""Deterministic, explainable recommendation policy (PRD F2). No LLM, no I/O.

Fail-closed basics (validated earlier in a private offline prototype):
- usage counts only if status is available, values are finite 0-100, and the
  observation is fresh (age within max age, not in the future);
- risk score = the highest reported used_pct over all windows;
- unknown/stale/error data is never auto-selected;
- ties are broken by a stable sort, so results never depend on input order.

Selection (policy v0.1.1, decisions R1/R2):
1. *Complete* candidate: status available, >= 1 reported window, and no window in
   state `failed` or `not_reported` (`unsupported` windows do not count as missing).
   *Partial* candidate: same, but some window is failed/not_reported. Partial
   candidates are shown with confidence=partial and are never auto-selected.
2. Eligible = complete and below `near_limit`. Safety tier is compared first:
   the pool is the eligible candidates in the best tier present (`ok` before `warning`).
3. An explicit preference (CLI `--prefer`, else policy `task_preferences[task]`)
   applies only inside that pool. A preferred candidate in a riskier tier, or one
   that is partial/uncertain/near limit, is ignored with an explicit reason code.
4. Otherwise lower risk wins; exact ties go to (provider, account_label) order.
   (Canonical order is used instead of raw input order so PRD AT-05 holds.)
5. No eligible candidate: `manual_decision_required` if any candidate is uncertain or
   partial below near_limit, else `no_eligible_provider`.
"""

from dataclasses import dataclass, field

from quota_handoff.errors import InputError
from quota_handoff.models import MISSING_WINDOW_STATES, UNCERTAIN_STATUSES
from quota_handoff.normalizer import PROVIDER_RE, check_schema_version, is_number, safe_name

# Upper bound for policy durations (10 years). Larger values are rejected, not clamped.
MAX_POLICY_SECONDS = 10 * 366 * 24 * 3600

RECOMMENDED = "recommended"
MANUAL = "manual_decision_required"
NO_ELIGIBLE = "no_eligible_provider"

TIER_OK = "ok"
TIER_WARNING = "warning"
TIER_NEAR_LIMIT = "near_limit"
TIER_ORDER = {TIER_OK: 0, TIER_WARNING: 1, TIER_NEAR_LIMIT: 2}

FRESH = "fresh"
STALE = "stale"
FUTURE = "future"
UNKNOWN = "unknown"

OBSERVATION_DISCLAIMER = (
    "Usage figures are observations from the named source at the stated time, "
    "not a guarantee of remaining quota."
)


# Reason-code registry. Every code the policy emits must be listed here and in
# docs/REASON_CODES.md (enforced by tests). Stability rules are in that document.
REASON_CODES = {
    # per-candidate: data quality
    "declared_status_unknown": "Source declared the usage status unknown.",
    "declared_status_stale": "Source declared the usage stale.",
    "declared_status_unavailable": "Source declared the provider unavailable.",
    "declared_status_error": "Source reported an error.",
    "observed_at_missing": "No observation time; usage treated as unknown.",
    "observed_at_in_future": "Observation time is beyond the future tolerance; treated as error.",
    "usage_stale": "Observation is older than the freshness limit.",
    "no_reported_usage_window": "No window carries a usage value.",
    "manual_review_required": "Usage is uncertain; the candidate needs a manual decision.",
    # per-candidate: tier and windows
    "below_warning": "Highest reported window is below the warning threshold.",
    "warning_threshold": "Highest reported window is at or above the warning threshold.",
    "near_limit": "Highest reported window is at or above the near-limit threshold; excluded.",
    "window_lookup_failed": "At least one window is in state failed.",
    "window_not_reported": "At least one window is in state not_reported.",
    "partial_usage_manual_review": "Partial usage below near-limit; not auto-selected.",
    "window_unsupported_ignored": "At least one window is unsupported and was ignored.",
    # plan level
    "no_snapshots": "No usage snapshots were provided.",
    "all_eligible_at_or_above_warning": "Every eligible candidate is in the warning tier.",
    "preference_applied": "The preferred provider was selected within the best tier.",
    "preference_ignored_provider_not_found": "The preferred provider has no snapshot.",
    "preference_ignored_riskier_tier": "The preferred provider is only eligible in a riskier tier.",
    "preference_ignored_not_eligible": "The preferred provider has no complete, eligible usage data.",
    "lowest_risk_selected": "The candidate with the lowest highest-window usage was selected.",
    "tie_broken_by_stable_order": "An exact usage tie was broken by (provider, account_label) order.",
    "partial_candidates_not_auto_selected": "Some candidates had partial data and were not selected.",
    "uncertain_candidates_need_manual_review": "Some candidates had uncertain usage and were not compared.",
    "only_partial_usage_available": "Only partial-data candidates remain; decide manually.",
    "no_candidate_with_complete_usage": "No candidate has fresh, complete usage.",
    "all_candidates_near_limit": "Every candidate is at or above the near-limit threshold.",
    "no_available_candidate": "No candidate is available (near limit or unavailable).",
}


def _rc(code):
    """Return `code` after checking it is registered (unregistered codes are a bug)."""
    if code not in REASON_CODES:
        raise ValueError(f"unregistered reason code: {code}")
    return code


@dataclass(frozen=True)
class Policy:
    policy_version: str = "default-0.1.1"
    warning_pct: float = 80
    near_limit_pct: float = 95
    max_age_seconds: float = 300
    future_tolerance_seconds: float = 60
    task_preferences: dict = field(default_factory=dict)


DEFAULT_POLICY = Policy()

POLICY_KEYS = {"schema_version", "policy_version", "thresholds", "freshness", "task_preferences", "description"}


def load_policy(doc, label="policy"):
    check_schema_version(doc, label)
    issues = []
    for key in sorted(set(doc) - POLICY_KEYS):
        issues.append(f"{label}: unknown field {safe_name(key)}")
    version = doc.get("policy_version")
    if not isinstance(version, str) or not version.strip():
        issues.append(f"{label}.policy_version: required non-empty string")

    thresholds = doc.get("thresholds", {})
    freshness = doc.get("freshness", {})
    prefs = doc.get("task_preferences", {})
    values = {}
    for section, name, source_key, default in (
        ("thresholds", "warning_pct", "warning", DEFAULT_POLICY.warning_pct),
        ("thresholds", "near_limit_pct", "near_limit", DEFAULT_POLICY.near_limit_pct),
        ("freshness", "max_age_seconds", "max_age_seconds", DEFAULT_POLICY.max_age_seconds),
        ("freshness", "future_tolerance_seconds", "future_tolerance_seconds", DEFAULT_POLICY.future_tolerance_seconds),
    ):
        container = thresholds if section == "thresholds" else freshness
        if not isinstance(container, dict):
            issues.append(f"{label}.{section}: expected object")
            values[name] = default
            continue
        value = container.get(source_key, default)
        if not is_number(value) or value < 0:
            issues.append(f"{label}.{section}.{source_key}: expected non-negative number")
            value = default
        elif section == "freshness" and value > MAX_POLICY_SECONDS:
            issues.append(f"{label}.{section}.{source_key}: must be at most {MAX_POLICY_SECONDS} seconds")
            value = default
        values[name] = value
    for section, allowed in (("thresholds", {"warning", "near_limit"}),
                             ("freshness", {"max_age_seconds", "future_tolerance_seconds"})):
        container = doc.get(section, {})
        if isinstance(container, dict):
            for key in sorted(set(container) - allowed):
                issues.append(f"{label}.{section}: unknown field {safe_name(key)}")
    if not 0 < values["warning_pct"] < values["near_limit_pct"] <= 100:
        issues.append(f"{label}.thresholds: require 0 < warning < near_limit <= 100")
    if values["max_age_seconds"] <= 0:
        issues.append(f"{label}.freshness.max_age_seconds: must be > 0")

    if not isinstance(prefs, dict):
        issues.append(f"{label}.task_preferences: expected object of task -> provider")
        prefs = {}
    for task, provider in prefs.items():
        if not isinstance(provider, str) or not PROVIDER_RE.match(provider):
            issues.append(f"{label}.task_preferences.{safe_name(task)}: expected provider identifier")
    if issues:
        raise InputError(f"{label}: invalid policy", issues)
    return Policy(policy_version=version, task_preferences=dict(sorted(prefs.items())), **values)


@dataclass(frozen=True)
class Assessment:
    snapshot: object
    effective_status: str
    freshness: str
    age_seconds: object  # float | None
    risk_pct: object  # highest reported value when status is available, else None
    risk_window: object
    reported_windows: tuple
    missing_windows: tuple  # keys in state failed / not_reported
    unsupported_windows: tuple
    tier: object  # str | None
    reason_codes: tuple

    @property
    def available(self):
        return self.effective_status == "available"

    @property
    def complete(self):
        return self.available and not self.missing_windows

    @property
    def eligible(self):
        return self.complete and self.tier in (TIER_OK, TIER_WARNING)

    @property
    def uncertain(self):
        return self.effective_status in UNCERTAIN_STATUSES

    @property
    def partial(self):
        return self.available and bool(self.missing_windows)

    @property
    def manual_review(self):
        """Not auto-selectable, but not known to be over the limit either."""
        return self.uncertain or (self.partial and self.tier != TIER_NEAR_LIMIT)

    @property
    def confidence(self):
        if not self.available:
            return "none"
        return "partial" if self.missing_windows else "complete"


def assess_snapshot(snapshot, now, policy=DEFAULT_POLICY):
    reasons = []
    age = None
    if snapshot.observed_at is None:
        freshness = UNKNOWN
    else:
        age = (now - snapshot.observed_at).total_seconds()
        if age < -policy.future_tolerance_seconds:
            freshness = FUTURE
        elif age > policy.max_age_seconds:
            freshness = STALE
        else:
            freshness = FRESH

    reported = tuple(w for w in snapshot.windows if w.state == "reported")
    missing = tuple(w.key for w in snapshot.windows if w.state in MISSING_WINDOW_STATES)
    unsupported = tuple(w.key for w in snapshot.windows if w.state == "unsupported")
    risk = risk_window = None
    if reported:
        top = max(reported, key=lambda w: (w.used_pct, w.key))
        risk, risk_window = top.used_pct, top.key

    status = snapshot.status
    if status != "available":
        reasons.append(_rc(f"declared_status_{status}"))
    elif freshness == UNKNOWN:
        status = "unknown"
        reasons.append(_rc("observed_at_missing"))
    elif freshness == FUTURE:
        status = "error"
        reasons.append(_rc("observed_at_in_future"))
    elif freshness == STALE:
        status = "stale"
        reasons.append(_rc("usage_stale"))
    elif not reported:
        status = "unknown"
        reasons.append(_rc("no_reported_usage_window"))

    tier = None
    if status == "available":
        if risk >= policy.near_limit_pct:
            tier = TIER_NEAR_LIMIT
            reasons.append(_rc("near_limit"))
        elif risk >= policy.warning_pct:
            tier = TIER_WARNING
            reasons.append(_rc("warning_threshold"))
        else:
            tier = TIER_OK
            reasons.append(_rc("below_warning"))
        if any(w.state == "failed" for w in snapshot.windows):
            reasons.append(_rc("window_lookup_failed"))
        if any(w.state == "not_reported" for w in snapshot.windows):
            reasons.append(_rc("window_not_reported"))
        if missing and tier != TIER_NEAR_LIMIT:
            reasons.append(_rc("partial_usage_manual_review"))
        if unsupported:
            reasons.append(_rc("window_unsupported_ignored"))
    elif status in UNCERTAIN_STATUSES:
        reasons.append(_rc("manual_review_required"))

    return Assessment(
        snapshot=snapshot,
        effective_status=status,
        freshness=freshness,
        age_seconds=age,
        risk_pct=risk if status == "available" else None,
        risk_window=risk_window if status == "available" else None,
        reported_windows=reported,
        missing_windows=missing,
        unsupported_windows=unsupported,
        tier=tier,
        reason_codes=tuple(reasons),
    )


@dataclass(frozen=True)
class Plan:
    decision: str
    selected: object  # Assessment | None
    preferred_provider: object
    preference_source: object
    reason_codes: tuple
    summary: str
    assessments: tuple
    policy: Policy


def _rank(a):
    return (a.risk_pct, a.snapshot.provider, a.snapshot.account_label)


def resolve_preference(task, prefer, policy):
    if prefer:
        return prefer, "cli"
    if task and task in policy.task_preferences:
        return policy.task_preferences[task], "policy"
    return None, None


def _preference_outcome(preferred, assessments, pool):
    """Reason code explaining why a preference could not be applied."""
    mine = [a for a in assessments if a.snapshot.provider == preferred]
    if not mine:
        return _rc("preference_ignored_provider_not_found")
    pool_tier = TIER_ORDER[pool[0].tier]
    if any(a.eligible and TIER_ORDER[a.tier] > pool_tier for a in mine):
        return _rc("preference_ignored_riskier_tier")
    return _rc("preference_ignored_not_eligible")


def plan(snapshots, now, *, task=None, prefer=None, policy=DEFAULT_POLICY):
    assessments = tuple(
        assess_snapshot(s, now, policy) for s in sorted(snapshots, key=lambda s: s.sort_key)
    )
    preferred, pref_source = resolve_preference(task, prefer, policy)
    reasons = []
    eligible = [a for a in assessments if a.eligible]
    uncertain = [a for a in assessments if a.uncertain]
    partial = [a for a in assessments if a.partial and a.manual_review]

    if not assessments:
        return Plan(NO_ELIGIBLE, None, preferred, pref_source, (_rc("no_snapshots"),),
                    "No usage snapshots were provided; nothing can be recommended.", assessments, policy)

    if eligible:
        best_tier = min(TIER_ORDER[a.tier] for a in eligible)
        pool = [a for a in eligible if TIER_ORDER[a.tier] == best_tier]
        if pool[0].tier == TIER_WARNING:
            reasons.append(_rc("all_eligible_at_or_above_warning"))
        contenders = pool
        if preferred:
            preferred_pool = [a for a in pool if a.snapshot.provider == preferred]
            if preferred_pool:
                contenders = preferred_pool
                reasons.append(_rc("preference_applied"))
            else:
                reasons.append(_preference_outcome(preferred, assessments, pool))
        selected = min(contenders, key=_rank)
        if contenders is pool:
            reasons.append(_rc("lowest_risk_selected"))
        if sum(1 for a in contenders if a.risk_pct == selected.risk_pct) > 1:
            reasons.append(_rc("tie_broken_by_stable_order"))
        if partial:
            reasons.append(_rc("partial_candidates_not_auto_selected"))
        if uncertain:
            reasons.append(_rc("uncertain_candidates_need_manual_review"))

        summary = (
            f"Suggest {selected.snapshot.candidate_id}: highest observed usage window is "
            f"{_pct(selected.risk_pct)} ({selected.risk_window}) as of "
            f"{selected.snapshot.observed_at.isoformat()} from source '{selected.snapshot.source}'."
        )
        if selected.tier == TIER_WARNING:
            summary += f" It is at or above the {_pct(policy.warning_pct)} warning threshold."
        if "preference_ignored_riskier_tier" in reasons:
            summary += f" Preferred provider '{preferred}' was ignored: it is in a riskier usage tier."
        elif any(r.startswith("preference_ignored") for r in reasons):
            summary += f" Preferred provider '{preferred}' was ignored: it has no complete, eligible usage data."
        if partial:
            summary += " Partial data, not auto-selected: " + ", ".join(a.snapshot.candidate_id for a in partial) + "."
        if uncertain:
            summary += " Not compared (usage uncertain): " + ", ".join(a.snapshot.candidate_id for a in uncertain) + "."
        return Plan(RECOMMENDED, selected, preferred, pref_source, tuple(reasons), summary, assessments, policy)

    if uncertain or partial:
        if partial:
            reasons.append(_rc("only_partial_usage_available"))
        reasons.append(_rc("no_candidate_with_complete_usage"))
        parts = [f"{a.snapshot.candidate_id} ({a.effective_status})" for a in uncertain]
        parts += [f"{a.snapshot.candidate_id} (partial: missing {', '.join(a.missing_windows)})" for a in partial]
        summary = "No candidate has fresh, complete usage; decide manually. Needs review: " + ", ".join(parts) + "."
        return Plan(MANUAL, None, preferred, pref_source, tuple(reasons), summary, assessments, policy)

    if all(a.tier == TIER_NEAR_LIMIT for a in assessments):
        reasons.append(_rc("all_candidates_near_limit"))
    else:
        reasons.append(_rc("no_available_candidate"))
    summary = "No provider is eligible: every candidate is near its limit or reported unavailable."
    return Plan(NO_ELIGIBLE, None, preferred, pref_source, tuple(reasons), summary, assessments, policy)


def _pct(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}%"
