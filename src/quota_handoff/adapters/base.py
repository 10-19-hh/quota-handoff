from typing import Protocol


class UsageProvider(Protocol):
    """Read-only source of usage snapshots.

    Implementations must not execute agents, read credential files or change OS state.
    """

    source: str

    def load(self):  # -> list[UsageSnapshot]
        ...
