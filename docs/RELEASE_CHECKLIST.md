# Release checklist

Steps for the first public release. Each item is done by the maintainer and checked only when confirmed.

## 1. CI evidence

Record the first GitHub Actions run of `.github/workflows/test.yml` for the release commit. Do not re-run CI just to fill this in; use the existing run.

| Field | Value |
|---|---|
| Run URL | <https://github.com/10-19-hh/quota-handoff/actions/runs/44336266947> |
| Commit the run tested | `b1789d0e79c47bfcfd97d32f22ceab1417e5aa3f` ("Fix release blockers and document security limitations") |
| Confirmed by | Final code review (T-011), which checked the run record and the job conclusions through the GitHub connection |
| Status | **success, 12/12 jobs** (Ubuntu 4/4, Windows 4/4, macOS 4/4) |

The release-preparation commit for `0.1.2` (version metadata and docs only, no code change) comes after `b1789d0`. It needs its own green run, which starts automatically when it is pushed.

| OS | 3.11 | 3.12 | 3.13 | 3.14 |
|---|---|---|---|---|
| ubuntu-latest | success | success | success | success |
| macos-latest | success | success | success | success |
| windows-latest | success | success | success | success |

Notes:

- A run of an earlier commit does not cover later code changes. The commit that is released needs its own green run.
- If the run is confirmed only by the maintainer and not by an independent reviewer, write **"maintainer-confirmed, independent verification not done"** in the Status row.
- After recording a run, update the CI sentence in `README.md` ("Development") to match.

## 2. Before making the repository public

- [ ] CI evidence above is filled in: 12/12 `success` on the release commit.
- [ ] `python -m unittest discover -s tests -t .` passes locally.
- [ ] Wheel and sdist build. The sdist contains `examples/` and `docs/`. The wheel installs in a clean venv, and `quota-handoff status --demo` and `plan --demo` work outside the checkout.
- [ ] The public-file scan for personal paths, e-mail addresses and secret patterns passes (`tests/test_hardening.py`).
- [ ] Commit author and committer use the GitHub `noreply` address.

## 3. Right after making the repository public

- [ ] Enable **Private vulnerability reporting** (repository **Settings** → **Security** / **Code security** → *Private vulnerability reporting* → **Enable**).
- [ ] Open `https://github.com/10-19-hh/quota-handoff/security/advisories/new` in a browser where you are **not** the repository owner (or signed out and then signed in as another account), and confirm the report form appears.
- [ ] Then change the "Current status" paragraph in `docs/SECURITY.md` to say that private reporting is enabled. Until this item is checked, the document must not claim it is.
