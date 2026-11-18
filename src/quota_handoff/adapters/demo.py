"""Synthetic usage for first-run demos. No files, processes or network.

Observations are stamped `now - 30s`, so the data is fresh at evaluation time and
`plan --demo` produces a normal recommendation without `--now`. Every snapshot uses
source "demo" and account_label "demo" so it cannot be mistaken for real usage.
"""

from datetime import timedelta

from quota_handoff.normalizer import normalize_usage_document

DEMO_OBSERVATION_LAG_SECONDS = 30

# provider -> [(window key, used_pct)]. Values are invented.
DEMO_USAGE = (
    ("claude", (("5h", 18), ("weekly", 41))),
    ("codex", (("5h", 35), ("weekly", 52))),
)


class DemoProvider:
    source = "demo"

    def __init__(self, now):
        self.now = now

    def document(self):
        observed = (self.now - timedelta(seconds=DEMO_OBSERVATION_LAG_SECONDS)).isoformat()
        return {
            "schema_version": "0.2",
            "description": "Synthetic demo data generated at run time; not real usage.",
            "snapshots": [
                {
                    "provider": provider,
                    "account_label": "demo",
                    "source": self.source,
                    "observed_at": observed,
                    "status": "available",
                    "windows": [{"key": k, "used_pct": v, "resets_at": None} for k, v in windows],
                }
                for provider, windows in DEMO_USAGE
            ],
        }

    def load(self):
        return normalize_usage_document(self.document(), source=self.source, label="demo")
