# Release checklist

Steps for the first public release. Each item is done by the maintainer and checked only when confirmed.

## 1. CI evidence

Record the first GitHub Actions run of `.github/workflows/test.yml` for the release commit. Do not re-run CI just to fill this in; use the existing run.

| Field | Value |
|---|---|
| Run URL | <https://github.com/10-19-hh/quota-handoff/actions/runs/44352678205> |
| Commit the run tested | `45165ca9671153ef04d883c280282ef6d9787917` ("Prepare v0.1.2 release"), the release commit for `0.1.2` |
| Confirmed by | Maintainer, by reading the run record and each job conclusion with `gh run view 44352678205` (event `push`, status `completed`, conclusion `success`) |
| Status | **success, 12/12 jobs** (Ubuntu 4/4, Windows 4/4, macOS 4/4); maintainer-confirmed, independent verification not done |

Earlier run: <https://github.com/10-19-hh/quota-handoff/actions/runs/44336266947> passed 12/12 on `b1789d0` ("Fix release blockers and document security limitations"), confirmed by the final code review (T-011). The release commit `45165ca` changes version metadata and docs only, and has its own green run above.

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

These items were checked after the repository was already public, against the release commit `45165ca`.

- [x] CI evidence above is filled in: 12/12 `success` on the release commit (run 44352678205).
- [x] `python -m unittest discover -s tests -t .` passes locally (Windows, Python 3.14: 203 tests, OK, 2 skipped because symlinks are not permitted for this user).
- [x] Wheel and sdist build (`python -m build` in a temporary venv, from a clean export of `45165ca`). The sdist contains `examples/` (6 files) and `docs/` (6 files) and no `__pycache__` or `.pyc`. The wheel installs in a clean Python 3.11 venv, and `quota-handoff status --demo` and `plan --demo` exit 0 when run from a directory outside the checkout.
- [x] The public-file scan for personal paths, e-mail addresses and secret patterns passes (`tests/test_hardening.py`, `PublicContentTests`: 2/2 OK).
- [x] Commit author and committer use the GitHub `noreply` address (all 3 commits up to `45165ca`).

## 3. Right after making the repository public

- [ ] Enable **Private vulnerability reporting** (repository **Settings** → **Security** / **Code security** → *Private vulnerability reporting* → **Enable**).
- [ ] Open `https://github.com/10-19-hh/quota-handoff/security/advisories/new` in a browser where you are **not** the repository owner (or signed out and then signed in as another account), and confirm the report form appears.
- [ ] Then change the "Current status" paragraph in `docs/SECURITY.md` to say that private reporting is enabled. Until this item is checked, the document must not claim it is.
