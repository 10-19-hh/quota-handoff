import copy
import unittest

from quota_handoff.errors import InputError
from quota_handoff.jsonio import loads_strict
from quota_handoff.normalizer import normalize_usage_document

from tests.helpers import doc, normalize, snap


class NormalizerTests(unittest.TestCase):
    def assertRejected(self, document, fragment):
        with self.assertRaises(InputError) as ctx:
            normalize_usage_document(document, source="fixture")
        self.assertIn(fragment, str(ctx.exception))

    def test_two_providers_normalized_and_sorted(self):
        out = normalize(snap("codex", ("weekly", 65), ("5h", 42)), snap("claude", ("5h", 10)))
        self.assertEqual([s.provider for s in out], ["claude", "codex"])
        self.assertEqual([w.key for w in out[1].windows], ["5h", "weekly"])
        self.assertEqual(out[1].source, "fixture")

    def test_missing_and_unsupported_schema_version(self):
        self.assertRejected({"snapshots": []}, "schema_version")
        self.assertRejected({"schema_version": "9.9", "snapshots": []}, "unsupported schema_version")

    def test_out_of_range_percent_rejected_not_clamped(self):
        for bad in (-1, 100.01, 120):
            self.assertRejected(doc(snap("codex", ("5h", bad))), "outside [0, 100]")

    def test_boundary_percent_accepted(self):
        out = normalize(snap("codex", ("5h", 0), ("weekly", 100)))
        self.assertEqual([w.used_pct for w in out[0].windows], [0, 100])

    def test_non_numeric_percent_rejected(self):
        for bad in (True, "42", [42]):
            self.assertRejected(doc(snap("codex", ("5h", bad))), "expected number or null")

    def test_nan_rejected_at_parse(self):
        with self.assertRaises(InputError):
            loads_strict('{"used_pct": NaN}')

    def test_duplicate_json_key_rejected(self):
        with self.assertRaises(InputError):
            loads_strict('{"a": 1, "a": 2}')

    def test_null_percent_kept_as_unknown(self):
        out = normalize(snap("codex", ("5h", None), ("weekly", 50)))
        self.assertIsNone(out[0].windows[0].used_pct)

    def test_missing_used_pct_key_rejected(self):
        s = snap("codex", ("5h", 1))
        del s["windows"][0]["used_pct"]
        self.assertRejected(doc(s), "used_pct: required")

    def test_empty_windows_allowed(self):
        out = normalize(snap("codex"))
        self.assertEqual(out[0].windows, ())

    def test_duplicate_window_key_rejected(self):
        self.assertRejected(doc(snap("codex", ("5h", 1), ("5h", 2))), "duplicate window")

    def test_duplicate_snapshot_rejected(self):
        self.assertRejected(doc(snap("codex", ("5h", 1)), snap("codex", ("5h", 2))), "duplicate snapshot")

    def test_same_provider_different_labels_allowed(self):
        out = normalize(snap("codex", ("5h", 1), label="b"), snap("codex", ("5h", 2), label="a"))
        self.assertEqual([s.account_label for s in out], ["a", "b"])

    def test_bad_resets_at_rejected(self):
        s = snap("codex", ("5h", 1))
        s["windows"][0]["resets_at"] = "tomorrow"
        self.assertRejected(doc(s), "resets_at")

    def test_naive_timestamp_rejected(self):
        s = snap("codex", ("5h", 1))
        s["observed_at"] = "2026-11-18T09:00:00"
        self.assertRejected(doc(s), "UTC offset")

    def test_zulu_timestamp_accepted(self):
        s = snap("codex", ("5h", 1))
        s["observed_at"] = "2026-11-18T00:00:00Z"
        self.assertEqual(normalize_usage_document(doc(s), source="fixture")[0].observed_at.utcoffset().total_seconds(), 0)

    def test_invalid_status_rejected(self):
        self.assertRejected(doc(snap("codex", status="ok")), "status")

    def test_email_like_account_label_rejected(self):
        self.assertRejected(doc(snap("codex", label="someone@example.com")), "account_label")

    def test_unknown_field_rejected(self):
        s = snap("codex", ("5h", 1))
        s["windows"][0]["used_percent"] = 1
        self.assertRejected(doc(s), "unknown field 'used_percent'")

    def test_source_mismatch_rejected(self):
        s = snap("codex", ("5h", 1))
        s["source"] = "orca"
        self.assertRejected(doc(s), "does not match the provider source")

    def test_observed_at_must_be_present(self):
        s = snap("codex", ("5h", 1))
        del s["observed_at"]
        self.assertRejected(doc(s), "observed_at: required")

    def test_all_issues_reported_together(self):
        s = snap("codex", ("5h", 120), status="bogus")
        with self.assertRaises(InputError) as ctx:
            normalize_usage_document(doc(s), source="fixture")
        self.assertGreaterEqual(len(ctx.exception.issues), 2)

    def test_input_not_mutated(self):
        d = doc(snap("codex", ("weekly", 1), ("5h", 2)))
        before = copy.deepcopy(d)
        normalize_usage_document(d, source="fixture")
        self.assertEqual(d, before)


if __name__ == "__main__":
    unittest.main()
