# Security boundary

## What the tool does

- Reads only the JSON files you name on the command line (`--fixture`, `--policy`, `--input`). Each file is capped at 1 MiB.
- `--demo` reads no files. It builds synthetic data in memory, labeled `source: "demo"`.
- Writes only the files you name (`--output`, `--json-output`). Before writing anything, it checks every target:
  - The parent directory must exist (it never creates directories), and the target must not be a directory.
  - No target may be the input file (`--input`), even with `--force` (exit 4).
  - The two outputs must not be the same file, even with `--force` (exit 2).
  - "Same file" is decided before writing, on every OS:
    - Paths are compared as absolute, `..`- and symlink-resolved, Unicode-NFC-normalized and **casefolded**. Paths that differ only in case are treated as the same target, even on case-sensitive file systems.
    - Existing files are additionally compared with `os.path.samefile`, which detects hard links, symlinks and case-insensitive file systems.
  - Without `--force`, no target may exist yet, including a dangling symlink. Otherwise the command exits with code 4.
  - **Conflicts are refused before anything is written or deleted.** Existing files are left byte-for-byte unchanged.
- How files are written:
  - Without `--force`, each file is created exclusively. Existing files are never touched.
  - With `--force`, each new content is first written to a temporary file `.quota-handoff-*.tmp` in the target's directory, and only then is each target replaced with `os.replace`. A symlink at the target is replaced, not followed.
- If a write itself fails (an I/O error such as permission denied or a full disk), files this run newly created are removed, staged temporary files are deleted, and the command exits with code 2. **Pre-existing files are never deleted.**
- **Not a transaction across files.** With `--force`, if replacing the second file fails after the first was replaced, the first file keeps its new content. The error message names the file that was already replaced. Staging both contents first makes this unlikely, but it is not prevented.
- Prints results to stdout and diagnostics to stderr.

## What error messages may contain

- **String values from input files are never echoed.** This includes `schema_version`, timestamps and `source`. Messages name the field path (for example `snapshots[0].observed_at`) and, for a rejected `schema_version`, only its type or length.
- Numeric range errors show the rejected number (for example `used_pct: 120 is outside [0, 100]`). A number longer than 24 characters is shown only as its length.
- Abnormal input (deeply nested JSON, numbers too large to compare, policy durations above 10 years, timestamps within 2 days of the `datetime` limits, unreadable input files) ends with a short error and exit 2. A last-resort guard turns any remaining `RecursionError`, `OverflowError` or `OSError` into exit 2 and prints only the exception type, never its text.
- Input-controlled field names (unknown or duplicate JSON keys) are masked with the secret patterns below and truncated to 40 characters.
- Paths you passed on the command line are shown as given.
- Rendered handoff documents contain your checkpoint text, after masking. That is the purpose of the tool, not an error message.

## What it does not do

- Start, stop or message any agent, orchestrator worker or terminal session.
- Call model APIs or any network endpoint.
- Read, request, store or forward credentials, OAuth tokens, cookies or auth files. It does not read environment variables or the home directory.
- Run Git commands or read repository contents, diffs or arbitrary files.
- Change OS settings, services, ACLs, the registry or sandbox configuration.
- Run as a background service or keep state between runs.

## How this is tested, and the limits of those tests

| Check | What it covers | What it does not cover |
|---|---|---|
| Static AST scan of `src/` (`test_cli.py`, `test_hardening.py`) | No imports of process, network, registry or dynamic-import modules. No calls to `eval`/`exec`/`__import__`. No `os.system`/`popen`/`startfile`. File-writing calls only in `jsonio.py`. No reads of the environment or home directory. | Indirect or obfuscated access (for example via `getattr` strings). Behavior of the Python standard library itself. |
| Runtime patching (`forbid_side_effects`) | Every CLI command runs with `subprocess.*`, `os.system`/`popen`/`exec*`/`spawn*`/`startfile` and `socket.socket`/`create_connection` replaced by functions that fail. | Native code paths and entry points that were not patched. It is a regression signal, not a sandbox. |
| Output tests (`test_release_fixes.py`, `test_output_conflicts.py`) | Pre-checks, rollback after I/O failure, input protection, and output conflicts (case-only, relative/absolute, `..`, hard link, symlink, Unicode form). In each conflict case, file writes and deletions are patched to fail, and existing files are compared byte for byte. | Platforms outside the CI matrix (CI passed on Windows, Ubuntu and macOS with Python 3.11–3.14 for commit `b1789d0`; see `docs/RELEASE_CHECKLIST.md`), concurrent writers, network file systems. Symlink tests are skipped where the user may not create symlinks (default Windows). |

## What it does not guarantee

- **Secret masking is not a guarantee.** It is pattern-based and conservative. It catches common API-key, GitHub, Slack, AWS and JWT formats, bearer tokens, `name=value` secrets that look like secrets, URL credentials, private-key blocks and home-directory user names. It will miss other secrets and may occasionally mask harmless text. Review every handoff file before sharing it.
- **Usage numbers are observations**, not guarantees of remaining quota. A recommendation is advice that needs manual approval. It never triggers an action.
- The tool runs with your normal user permissions. It does not add isolation or "harden" anything.

## Handoff documents are untrusted input

A handoff document is built from a checkpoint file that another agent may have written. Treat its content as **untrusted**.

- `objective` and `expected_outcome` are copied as written, including line breaks. Other fields are put on one line but are otherwise unchanged.
- The document can therefore contain Markdown headings (for example a second `## Do not` section that contradicts the real one), raw HTML, HTML comments that are hidden when rendered, and text that looks like commands or instructions to an agent.
- The tool does **not** detect, neutralize or remove prompt injection. Secret masking (above) is unrelated and does not make the content safe.
- Review the generated document yourself and pass it to the next agent only after you have approved it.
- This version does not run agents. It never starts a session, sends the document anywhere or acts on its content. Automatic agent execution is not supported.

## Live adapters (M1+)

The conditions for adding any live, read-only adapter are listed in `docs/REVIEW-M0.1.md` ("M1 entry conditions"). Executing agents is out of scope for v0.1.

## Reporting a vulnerability

Please do not report security issues in public issues, discussions or pull requests.

Report them privately through GitHub Security Advisories:

1. Open <https://github.com/10-19-hh/quota-handoff/security/advisories/new> (or the repository's **Security** tab → **Report a vulnerability**).
2. Describe the issue, the affected version or commit, and the steps to reproduce it. Use synthetic data only. Never include real tokens, credentials or private usage data.
3. The maintainer replies in the advisory. A fix and an advisory are published together, after the fix is available.

**Current status:** the repository is not public yet, and private vulnerability reporting is **not enabled yet**, so the link above does not work for outside reporters. Enabling it and testing the link are release steps in `docs/RELEASE_CHECKLIST.md`.
