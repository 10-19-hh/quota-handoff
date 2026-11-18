"""Normalized data model (PRD F1 `UsageSnapshot`)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union

# Output documents are always written with SCHEMA_VERSION. Input accepts both versions;
# 0.1 is the original format (no window `state` field), 0.2 adds window `state`.
SCHEMA_VERSION = "0.2"
SUPPORTED_SCHEMA_VERSIONS = ("0.1", "0.2")

STATUSES = ("available", "unknown", "stale", "unavailable", "error")
UNCERTAIN_STATUSES = ("unknown", "stale", "error")

Number = Union[int, float]


# Window states. Only `reported` carries a number; the others always have used_pct=None.
#   reported      - usage value present
#   unsupported   - the provider does not offer this window; does not make a snapshot partial
#   failed        - the lookup for this window failed; makes the snapshot partial
#   not_reported  - value is null and no reason given (default for null); makes it partial
WINDOW_STATES = ("reported", "unsupported", "failed", "not_reported")
MISSING_WINDOW_STATES = ("failed", "not_reported")


@dataclass(frozen=True)
class UsageWindow:
    key: str
    used_pct: Optional[Number]  # None unless state == "reported"; never substituted with 0 or 100
    resets_at: Optional[datetime]
    state: str = "reported"


@dataclass(frozen=True)
class UsageSnapshot:
    provider: str
    account_label: str  # arbitrary display label, never an auth identifier
    source: str
    observed_at: Optional[datetime]
    status: str
    windows: tuple  # tuple[UsageWindow, ...]

    @property
    def candidate_id(self):
        return f"{self.provider}/{self.account_label}"

    @property
    def sort_key(self):
        return (self.provider, self.account_label)
