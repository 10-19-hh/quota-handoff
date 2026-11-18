"""Regression cases carried over from an earlier private offline prototype.

Only the usage-policy checks below remain valid for the generic product; names match the
prototype's case names. Prototype checks for project-specific environment gates, approval
flags, size/kind gates (replaced by the 80/95 tiers), QA routing and worker/checkpoint state
were not carried over.
"""

import unittest

from quota_handoff.policy import MANUAL, NO_ELIGIBLE, RECOMMENDED, plan

from tests.helpers import NOW, normalize, two


class PrototypeRegression(unittest.TestCase):
    def provider(self, result):
        return result.selected.snapshot.provider if result.selected else None

    def test_A_claude_95_codex_30(self):
        result = plan(two(95, 30), NOW)
        self.assertEqual(self.provider(result), "codex")
        self.assertIn("near_limit", result.assessments[0].reason_codes)

    def test_B_claude_25_codex_95(self):
        self.assertEqual(self.provider(plan(two(25, 95), NOW)), "claude")

    def test_C_both_95_held(self):
        result = plan(two(95, 95), NOW)
        self.assertEqual((result.decision, result.selected), (NO_ELIGIBLE, None))

    def test_unknown_no_usage_held(self):
        # The prototype passed {} (no providers). The product reports this explicitly.
        result = plan(normalize(), NOW)
        self.assertEqual((result.decision, result.selected), (NO_ELIGIBLE, None))

    def test_weekly_cap(self):
        result = plan(two(0, 0, 95, 95), NOW)
        self.assertEqual((result.decision, result.selected), (NO_ELIGIBLE, None))

    def test_stale(self):
        result = plan(two(0, 0, age=301), NOW)
        self.assertEqual((result.decision, result.selected), (MANUAL, None))

    def test_lowest_worst_window_then_preference_breaks_ties(self):
        # Prototype ordering key: (worst, agent != preferred, agent). Product: preference applies
        # within the safe pool, so it may pick a higher-usage safe candidate (documented change).
        self.assertEqual(self.provider(plan(two(10, 30), NOW)), "claude")
        self.assertEqual(self.provider(plan(two(30, 30), NOW, prefer="codex")), "codex")
        self.assertEqual(plan(two(30, 30), NOW).decision, RECOMMENDED)


if __name__ == "__main__":
    unittest.main()
