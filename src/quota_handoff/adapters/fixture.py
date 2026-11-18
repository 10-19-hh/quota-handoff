from pathlib import Path

from quota_handoff.jsonio import read_json_file
from quota_handoff.normalizer import normalize_usage_document


class FixtureProvider:
    """Reads a hand-written or replayed usage JSON file. No process or network access."""

    source = "fixture"

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        doc = read_json_file(self.path, "fixture")
        return normalize_usage_document(doc, source=self.source, label="fixture")
