# Reason codes

`plan` and `status` output carry machine-readable `reason_codes`. This file is the registry. The code-level registry `quota_handoff.policy.REASON_CODES` must match this table exactly, and tests enforce it.

## Stability rules (from the first public release)

1. **Adding** a code is a minor change. Consumers must ignore codes they do not know.
2. **Renaming or removing** a code requires a new output `schema_version` and at least one release in which the old code is marked deprecated here.
3. The meaning of an existing code does not change. A changed meaning gets a new code.
4. Human-readable `summary` and `warnings` text is not covered by these rules and may change at any time.

Codes released before the first public release (output schema 0.1, 0.2 pre-release) carry no stability promise.

## Per-candidate codes (`candidate_evaluations[].reason_codes`)

| Code | Meaning |
|---|---|
| `declared_status_unknown` | Source declared the usage status unknown. |
| `declared_status_stale` | Source declared the usage stale. |
| `declared_status_unavailable` | Source declared the provider unavailable. |
| `declared_status_error` | Source reported an error. |
| `observed_at_missing` | No observation time; usage treated as unknown. |
| `observed_at_in_future` | Observation time is beyond the future tolerance; treated as error. |
| `usage_stale` | Observation is older than the freshness limit. |
| `no_reported_usage_window` | No window carries a usage value. |
| `manual_review_required` | Usage is uncertain; the candidate needs a manual decision. |
| `below_warning` | Highest reported window is below the warning threshold. |
| `warning_threshold` | Highest reported window is at or above the warning threshold. |
| `near_limit` | Highest reported window is at or above the near-limit threshold; excluded. |
| `window_lookup_failed` | At least one window is in state `failed`. |
| `window_not_reported` | At least one window is in state `not_reported`. |
| `partial_usage_manual_review` | Partial usage below near-limit; not auto-selected. |
| `window_unsupported_ignored` | At least one window is `unsupported` and was ignored. |

## Plan-level codes (`reason_codes`)

| Code | Meaning |
|---|---|
| `no_snapshots` | No usage snapshots were provided. |
| `all_eligible_at_or_above_warning` | Every eligible candidate is in the warning tier. |
| `preference_applied` | The preferred provider was selected within the best tier. |
| `preference_ignored_provider_not_found` | The preferred provider has no snapshot. |
| `preference_ignored_riskier_tier` | The preferred provider is only eligible in a riskier tier. |
| `preference_ignored_not_eligible` | The preferred provider has no complete, eligible usage data. |
| `lowest_risk_selected` | The candidate with the lowest highest-window usage was selected. |
| `tie_broken_by_stable_order` | An exact usage tie was broken by (provider, account_label) order. |
| `partial_candidates_not_auto_selected` | Some candidates had partial data and were not selected. |
| `uncertain_candidates_need_manual_review` | Some candidates had uncertain usage and were not compared. |
| `only_partial_usage_available` | Only partial-data candidates remain; decide manually. |
| `no_candidate_with_complete_usage` | No candidate has fresh, complete usage. |
| `all_candidates_near_limit` | Every candidate is at or above the near-limit threshold. |
| `no_available_candidate` | No candidate is available (near limit or unavailable). |
