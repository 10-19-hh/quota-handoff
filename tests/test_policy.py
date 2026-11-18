import itertools
import unittest
from datetime import timedelta

from quota_handoff.errors import InputError
from quota_handoff.policy import (
    MANUAL,
    NO_ELIGIBLE,
    RECOMMENDED,
    TIER_NEAR_LIMIT,
    TIER_OK,
    TIER_WARNING,
    Policy,
    assess_snapshot,
    load_policy,
    plan,
)

from tests.helpers import NOW, normalize, snap, two


def one(pct, **kwargs):
    return normalize(snap("codex", ("5h", pct), **kwargs))[0]


class BoundaryTests(unittest.TestCase):
    """PRD F2 AC: 0/79/80/94/95/100."""

    def test_tiers(self):
        expected = {0: TIER_OK, 79: TIER_OK, 79.99: TIER_OK, 80: TIER_WARNING, 94: TIER_WARNING,
                    94.99: TIER_WARNING, 95: TIER_NEAR_LIMIT, 100: TIER_NEAR_LIMIT}
        for pct, tier in expected.items():
            with self.subTest(pct=pct):
                self.assertEqual(assess_snapshot(one(pct), NOW).tier, tier)

    def test_decisions_at_boundaries(self):
        for pct, decision in {0: RECOMMENDED, 79: RECOMMENDED, 80: RECOMMENDED, 94: RECOMMENDED,
                              95: NO_ELIGIBLE, 100: NO_ELIGIBLE}.items():
            with self.subTest(pct=pct):
                self.assertEqual(plan([one(pct)], NOW).decision, decision)

    def test_warning_tier_flagged(self):
        result = plan([one(80)], NOW)
        self.assertIn("all_eligible_at_or_above_warning", result.reason_codes)
        self.assertIn("warning threshold", result.summary)

    def test_custom_thresholds(self):
        policy = Policy(warning_pct=50, near_limit_pct=70)
        self.assertEqual(assess_snapshot(one(50), NOW, policy).tier, TIER_WARNING)
        self.assertEqual(assess_snapshot(one(70), NOW, policy).tier, TIER_NEAR_LIMIT)


class FreshnessTests(unittest.TestCase):
    def test_fresh_at_exactly_max_age(self):
        self.assertEqual(assess_snapshot(one(10, age=300), NOW).effective_status, "available")

    def test_stale_after_max_age_at_04(self):
        a = assess_snapshot(one(10, age=301), NOW)
        self.assertEqual((a.effective_status, a.freshness), ("stale", "stale"))
        self.assertIsNone(a.risk_pct)
        result = plan([one(10, age=301)], NOW)
        self.assertEqual(result.decision, MANUAL)
        self.assertIsNone(result.selected)

    def test_future_timestamp(self):
        self.assertEqual(assess_snapshot(one(10, age=-60), NOW).effective_status, "available")
        a = assess_snapshot(one(10, age=-61), NOW)
        self.assertEqual((a.effective_status, a.freshness), ("error", "future"))

    def test_missing_observed_at_is_unknown(self):
        a = assess_snapshot(one(10, observed=False), NOW)
        self.assertEqual(a.effective_status, "unknown")
        self.assertIn("observed_at_missing", a.reason_codes)

    def test_declared_status_respected(self):
        for status in ("unknown", "stale", "unavailable", "error"):
            with self.subTest(status=status):
                a = assess_snapshot(one(10, status=status), NOW)
                self.assertEqual(a.effective_status, status)
                self.assertFalse(a.eligible)


class UncertaintyTests(unittest.TestCase):
    def test_all_unknown_is_manual_without_numbers_at_03(self):
        snaps = normalize(snap("claude", status="unknown", observed=False), snap("codex", ("5h", None)))
        result = plan(snaps, NOW)
        self.assertEqual(result.decision, MANUAL)
        self.assertIsNone(result.selected)
        for a in result.assessments:
            self.assertIsNone(a.risk_pct)
            self.assertEqual(a.confidence, "none")
        self.assertNotIn("%", result.summary)

    def test_all_windows_null_is_unknown(self):
        a = assess_snapshot(normalize(snap("codex", ("5h", None), ("weekly", None)))[0], NOW)
        self.assertEqual(a.effective_status, "unknown")
        self.assertIn("no_reported_usage_window", a.reason_codes)

    def test_partial_windows_flagged(self):
        # T-007 (R2): partial-only data is no longer auto-recommended.
        snaps = normalize(snap("codex", ("5h", 10), ("weekly", None)))
        result = plan(snaps, NOW)
        self.assertEqual(result.decision, MANUAL)
        self.assertEqual(result.assessments[0].confidence, "partial")
        self.assertIn("only_partial_usage_available", result.reason_codes)
        self.assertIn("weekly", result.summary)

    def test_known_candidate_recommended_uncertain_marked(self):
        snaps = normalize(snap("claude", ("5h", 1), age=999), snap("codex", ("5h", 50)))
        result = plan(snaps, NOW)
        self.assertEqual(result.selected.snapshot.provider, "codex")
        self.assertIn("uncertain_candidates_need_manual_review", result.reason_codes)
        stale = [a for a in result.assessments if a.snapshot.provider == "claude"][0]
        self.assertTrue(stale.uncertain)

    def test_unknown_plus_near_limit_is_manual(self):
        snaps = normalize(snap("claude", status="unknown", observed=False), snap("codex", ("5h", 99)))
        self.assertEqual(plan(snaps, NOW).decision, MANUAL)

    def test_unavailable_only_is_no_eligible(self):
        snaps = normalize(snap("codex", status="unavailable"))
        result = plan(snaps, NOW)
        self.assertEqual(result.decision, NO_ELIGIBLE)
        self.assertIn("no_available_candidate", result.reason_codes)

    def test_empty_input(self):
        result = plan([], NOW)
        self.assertEqual((result.decision, result.reason_codes), (NO_ELIGIBLE, ("no_snapshots",)))


class SelectionTests(unittest.TestCase):
    def test_multi_window_conflict_uses_max_at_02(self):
        snaps = normalize(snap("claude", ("5h", 60), ("weekly", 97)), snap("codex", ("5h", 70), ("weekly", 10)))
        result = plan(snaps, NOW)
        self.assertEqual(result.selected.snapshot.provider, "codex")
        claude = result.assessments[0]
        self.assertEqual((claude.risk_pct, claude.risk_window, claude.tier), (97, "weekly", TIER_NEAR_LIMIT))

    def test_lone_97_weekly_never_recommended_at_02(self):
        result = plan(normalize(snap("claude", ("5h", 60), ("weekly", 97))), NOW)
        self.assertEqual(result.decision, NO_ELIGIBLE)

    def test_both_near_limit(self):
        result = plan(two(95, 100), NOW)
        self.assertEqual(result.decision, NO_ELIGIBLE)
        self.assertIn("all_candidates_near_limit", result.reason_codes)

    def test_lower_risk_wins_without_preference(self):
        self.assertEqual(plan(two(70, 20), NOW).selected.snapshot.provider, "codex")

    def test_preference_applies_among_safe(self):
        result = plan(two(10, 60), NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.provider, "codex")
        self.assertIn("preference_applied", result.reason_codes)

    def test_preference_does_not_override_safety(self):
        result = plan(two(10, 85), NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.provider, "claude")
        # T-007 (R1): reason code split into riskier-tier / not-eligible / not-found.
        self.assertIn("preference_ignored_riskier_tier", result.reason_codes)
        result = plan(two(10, 96), NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.provider, "claude")
        self.assertIn("preference_ignored_not_eligible", result.reason_codes)

    def test_preference_for_uncertain_provider_ignored(self):
        snaps = normalize(snap("claude", ("5h", 50)), snap("codex", ("5h", 1), age=400))
        result = plan(snaps, NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.provider, "claude")

    def test_preference_among_warning_tier(self):
        result = plan(two(82, 90), NOW, prefer="codex")
        self.assertEqual(result.selected.snapshot.provider, "codex")
        self.assertIn("all_eligible_at_or_above_warning", result.reason_codes)

    def test_policy_task_preference_and_cli_override(self):
        policy = Policy(task_preferences={"code": "codex"})
        self.assertEqual(plan(two(10, 60), NOW, task="code", policy=policy).preference_source, "policy")
        self.assertEqual(plan(two(10, 60), NOW, task="code", policy=policy).selected.snapshot.provider, "codex")
        result = plan(two(10, 60), NOW, task="code", prefer="claude", policy=policy)
        self.assertEqual((result.selected.snapshot.provider, result.preference_source), ("claude", "cli"))

    def test_tie_broken_by_stable_order(self):
        result = plan(two(40, 40), NOW)
        self.assertEqual(result.selected.snapshot.provider, "claude")
        self.assertIn("tie_broken_by_stable_order", result.reason_codes)

    def test_input_order_invariance_at_05(self):
        rows = [
            snap("codex", ("weekly", 33), ("5h", 20)),
            snap("claude", ("5h", 33), ("weekly", 1)),
            snap("codex", ("5h", 33), label="second"),
            snap("gemini", ("5h", 1), age=999),
        ]
        for prefer, expected in ((None, "claude/local-primary"), ("codex", "codex/local-primary")):
            outputs = {repr(plan(normalize(*p), NOW, prefer=prefer)) for p in itertools.permutations(rows)}
            self.assertEqual(len(outputs), 1, f"order-dependent output with prefer={prefer}")
            result = plan(normalize(*rows), NOW, prefer=prefer)
            self.assertEqual((result.decision, result.selected.snapshot.candidate_id), (RECOMMENDED, expected))

    def test_deterministic_repeat(self):
        self.assertEqual(repr(plan(two(30, 31), NOW)), repr(plan(two(30, 31), NOW)))

    def test_evaluation_time_explicit(self):
        later = NOW + timedelta(seconds=301)
        self.assertEqual(plan(two(1, 2), later).decision, MANUAL)


class PolicyFileTests(unittest.TestCase):
    def base(self, **over):
        d = {"schema_version": "0.1", "policy_version": "t1",
             "thresholds": {"warning": 80, "near_limit": 95},
             "freshness": {"max_age_seconds": 300, "future_tolerance_seconds": 60},
             "task_preferences": {"code": "codex"}}
        d.update(over)
        return d

    def test_valid(self):
        p = load_policy(self.base())
        self.assertEqual((p.policy_version, p.warning_pct, p.task_preferences), ("t1", 80, {"code": "codex"}))

    def test_invalid_threshold_order(self):
        with self.assertRaises(InputError):
            load_policy(self.base(thresholds={"warning": 95, "near_limit": 80}))

    def test_threshold_above_100(self):
        with self.assertRaises(InputError):
            load_policy(self.base(thresholds={"warning": 80, "near_limit": 101}))

    def test_unknown_keys(self):
        with self.assertRaises(InputError):
            load_policy(self.base(thresholds={"warning": 80, "near_limit": 95, "deny": 99}))
        with self.assertRaises(InputError):
            load_policy(self.base(live_execution=True))

    def test_bad_preference(self):
        with self.assertRaises(InputError):
            load_policy(self.base(task_preferences={"code": "Codex!"}))


if __name__ == "__main__":
    unittest.main()
