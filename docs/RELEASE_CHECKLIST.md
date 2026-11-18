# Release checklist

Steps for the first public release. Each item is done by the maintainer and checked only when confirmed.

## 1. CI evidence

Record the first GitHub Actions run of `.github/workflows/test.yml` for the release commit. Do not re-run CI just to fill this in; use the existing run.

| Field | Value |
|---|---|
| Run URL | _not recorded_ |
| Commit the run tested | _not recorded_ (must equal the release commit) |
| Confirmed by | _not recorded_ |
| Status | **Not confirmed.** No run URL or job results have been recorded yet. Independent verification has not been done. |

The repository was private at the time of review, so the run could not be read without the maintainer's GitHub login.

Expected jobs: 12, all `success`.

| OS | 3.11 | 3.12 | 3.13 | 3.14 |
|---|---|---|---|---|
| ubuntu-latest | _?_ | _?_ | _?_ | _?_ |
| macos-latest | _?_ | _?_ | _?_ | _?_ |
| windows-latest | _?_ | _?_ | _?_ | _?_ |

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
