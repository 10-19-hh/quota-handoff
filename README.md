# quota-handoff

A local-first, **read-only** CLI that:

1. normalizes usage snapshots from coding agents (e.g. Claude Code, Codex) into one shape,
2. gives a **deterministic, explainable** suggestion of which agent to use next, and
3. renders a **handoff document** from a checkpoint you write, so you can continue the work in a session you open yourself.

It never starts agents, calls model APIs, reads credential files, touches Git, or changes OS settings.
Status: `0.1.2.dev0` (pre-release, fixture and demo data only). MIT licensed. Not affiliated with any agent vendor.

## Install

Requires Python 3.11+. No runtime dependencies.

The package is not published on PyPI yet. Install it from a repository checkout, or from a wheel or sdist built from one:

```console
python -m venv .venv
.venv/bin/python -m pip install .          # Windows: .venv\Scripts\python -m pip install .
```

Or run straight from a checkout without installing:

```console
PYTHONPATH=src python -m quota_handoff --help          # PowerShell: $env:PYTHONPATH="src"
```

Uninstall: `python -m pip uninstall quota-handoff` (or delete the `.venv` folder). The tool keeps no state files.

## Try it (no login, no network, no real usage)

### After installing: works in any directory

These commands need nothing but the installed package:

```console
quota-handoff status --demo
quota-handoff plan --demo --task code --prefer codex
```

`--demo` uses built-in **synthetic** usage observed 30 seconds before the current time, so `plan` gives a normal recommendation on the first run. Every demo run is labeled `DEMO DATA` and uses `source: "demo"` (`"demo": true` in `--json` output). Nothing is read from your accounts.

To try `handoff create`, write a minimal checkpoint file `task.json` yourself:

```json
{
  "schema_version": "0.2",
  "objective": "Describe what the next session should achieve.",
  "done": ["What is already finished"],
  "next_steps": ["The first thing the next session should do"]
}
```

```console
quota-handoff handoff create --input task.json --output handoff.md
```

### From a repository checkout: uses the files in `examples/`

The `examples/` folder is part of the repository and the sdist, but not of the installed wheel. Run these from the repository root:

```console
quota-handoff handoff create --input examples/task-sample.json --output ./handoff.md
quota-handoff plan --fixture examples/usage-sample.json --now 2026-11-18T09:02:00+09:00
```

The fixtures have fixed timestamps (`2026-11-18T09:00+09:00`), so `--now` replays them reproducibly. Without `--now` the real clock is used. Observations older than 5 minutes are `stale`, and observations more than 60 s in the future are `error`. In both cases `plan` asks for a manual decision (exit 3). Other fixtures:

| Fixture | Shows |
|---|---|
| `usage-uncertain.json` | stale + unknown → `manual_decision_required` |
| `usage-near-limit.json` | 5h 60% but weekly 97% → never recommended |
| `usage-partial.json` | a failed weekly lookup → `confidence=partial`, not auto-selected; an unsupported window is ignored |

Add `--json` for machine-readable output, and `--policy examples/policy-sample.json` to use a policy file.

## How the suggestion is made (policy v0.1.1)

1. **Risk** = the highest reported `used_pct` across a candidate's windows. **Tiers** are policy defaults, not predictions of real limits: `< 80` ok, `80` to below `95` warning, `≥ 95` near_limit (excluded).
2. Only fresh data counts: `available` status and age ≤ 300 s by default. `unknown`, `stale` and `error` candidates are listed for manual review and never selected automatically.
3. **Complete vs partial.** A window whose lookup `failed` or that is `not_reported` makes the candidate *partial* (`confidence=partial`, with a warning). Partial candidates are never auto-selected; complete candidates win. An `unsupported` window (the provider has no such window) is ignored and does not make a candidate partial.
4. **Safety tier first, then preference.** Among eligible complete candidates, the best tier present forms the pool. `--prefer` (or `task_preferences[--task]` in a policy file) applies only inside that pool. A preferred provider in a riskier tier is ignored, with reason code `preference_ignored_riskier_tier`.
5. Otherwise lower usage wins. Exact ties go to `(provider, account_label)` order, so input order never changes the result.
6. If no candidate is eligible, the decision is `manual_decision_required` when anything is uncertain or partial. Otherwise it is `no_eligible_provider`. Missing values stay `null`; the tool never fills in 0% or 100%.

Every plan includes `decision`, `reason_codes`, a one-line summary with source and observation time, per-candidate `confidence`, and `manual_approval_required: true`. Reason codes and their stability rules are listed in `docs/REASON_CODES.md`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK (`plan`: a candidate was recommended) |
| 2 | Input, schema or output error (invalid JSON, unsupported `schema_version`, out-of-range value, bad flag, both outputs naming the same file, a write that failed) |
| 3 | `plan` could not recommend (`manual_decision_required` or `no_eligible_provider`) |
| 4 | Safety gate refused: an output file exists and `--force` was not given, or an output would overwrite the `--input` file |

## Input formats

Input files may use `schema_version` `"0.2"` (current) or `"0.1"` (original format, no window `state` field). All JSON output is written as `"0.2"`. Rejected `schema_version` values are never echoed in error messages.

**Usage fixture**: `snapshots[]` of `{provider, account_label, source, observed_at, status, windows[{key, used_pct, resets_at, state?}]}`.

- `status` ∈ `available|unknown|stale|unavailable|error`.
- `used_pct` ∈ `[0, 100]` or `null`.
- `state` ∈ `reported|unsupported|failed|not_reported` (schema 0.2 only). It defaults to `reported` for numbers and `not_reported` for `null`.
- Timestamps are ISO 8601 **with an offset**.
- `account_label` is any short label. E-mail addresses are rejected.
- Unknown fields, duplicates and out-of-range values are errors (exit 2), never silently fixed. Error messages name the field but do not echo its value.

**Task / checkpoint**:

- `objective` (required).
- Optional: `title`, `from_agent`, `to_agent`, `created_at`, `done[]`, `remaining[]`, `blockers[]`, `read_first[]`, `tests[{name, command?, status: pass|fail|not_run|unknown, notes?}]`, `next_steps[]`, `do_not[]`, `expected_outcome`, `notes[]`.

See `examples/task-sample.json`.

## Handoff output

- Markdown goes to stdout, or to `--output FILE`. `--json-output FILE` adds a JSON copy.
- All targets are checked before anything is written. Without `--force`, if any target exists, nothing is written. Missing directories are not created.
- The `--input` file can never be an output, even with `--force`. This covers relative/absolute paths, `..`, case differences, symlinks and hard links.
- If a write fails, files created by that run are removed. With `--force`, the two files are replaced one after the other; this is not a multi-file transaction (see `docs/SECURITY.md`).
- Output is a fixed template. Identical input gives byte-identical files (no generated timestamps, LF line endings).
- **Secret masking is not a guarantee.** Strings matching common secret patterns (API keys, GitHub/Slack/AWS tokens, JWTs, bearer tokens, `password=…`-style values, URL credentials, private keys) and home-directory user names are masked, and a warning is printed. Other secrets will get through. Review the file before sharing it.

## Limits

- Fixture and demo input only. No live usage source is connected.
- No local history (`history`/`clear`) yet.
- Usage figures are observations from a source at a time, not guarantees of remaining quota.

## Development

```console
python -m unittest discover -s tests -t .
```

Run the tests from a repository checkout or an unpacked sdist; the wheel does not include `tests/`.

CI (`.github/workflows/test.yml`) runs on Windows, Linux and macOS with Python 3.11–3.14. It runs the tests, builds the wheel and sdist, and runs the installed CLI. **This workflow has not been executed yet.** So far, results come only from local Windows runs with Python 3.11, 3.13 and 3.14.

See `docs/SECURITY.md` for the security boundary and what the tests do and do not verify. Review records are in `docs/REVIEW-M0.1.md` and `docs/REVIEW-M0.2-INDEPENDENT.md`.

## License

MIT. See `LICENSE`.
