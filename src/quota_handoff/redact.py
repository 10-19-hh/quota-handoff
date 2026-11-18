"""Conservative pattern-based masking. NOT a guarantee that no secret remains.

Only the values matched below are replaced. Users must review output before sharing.
"""

import re

_VALUE = r"[A-Za-z0-9_\-./+=]"

# (kind, pattern, replacement). Order matters: specific tokens before generic assignments.
_SECRET_PATTERNS = [
    ("private_key_block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)", re.S), None),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), None),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), None),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), None),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), None),
    ("bearer_token", re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]{16,}=*"), r"\1"),
    ("url_credentials", re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s/:@]*:)(?!\[REDACTED)[^\s/@]+(@)"), r"\1"),
    (
        # `name = value` where the value looks like a secret: 8+ chars containing both a letter and
        # a digit, or any 16+ char token. Keeps prose such as "the token= field" or
        # "max_tokens: 4096" and references such as "token=$GITHUB_TOKEN" unmasked.
        "secret_assignment",
        re.compile(
            r"(?i)(\b[\w.-]*(?:api[_-]?key|secret|token|password|passwd|pwd|credential)s?[\"']?\s*[:=]\s*[\"']?)"
            r"(?!\[REDACTED)"
            r"(?:(?=" + _VALUE + r"*[A-Za-z])(?=" + _VALUE + r"*\d)" + _VALUE + r"{8,}|" + _VALUE + r"{16,})"
        ),
        r"\1",
    ),
]

_HOME_PATTERNS = [
    re.compile(r"(?i)\b([A-Z]:[\\/]+Users[\\/]+)(?!<user>)([^\\/\s\"'`]+)"),
    re.compile(r"((?:^|[\s\"'`(=])/(?:home|Users)/)(?!<user>)([^/\s\"'`]+)"),
]


MAX_NAME_ECHO = 40


def safe_name(value):
    """Render an input-controlled name (e.g. a JSON key) for an error message:
    masked with `redact`, then truncated to MAX_NAME_ECHO characters."""
    text, _ = redact(str(value))
    return repr(text if len(text) <= MAX_NAME_ECHO else text[:MAX_NAME_ECHO] + "...")


def redact(text):
    """Return (masked_text, kinds) where kinds is a sorted list of matched kinds."""
    kinds = set()
    for kind, pattern, keep in _SECRET_PATTERNS:
        marker = f"[REDACTED:{kind}]"

        def _sub(match, keep=keep, marker=marker, kind=kind):
            kinds.add(kind)
            return (match.expand(keep) if keep else "") + marker + (match.group(2) if kind == "url_credentials" else "")

        text = pattern.sub(_sub, text)
    for pattern in _HOME_PATTERNS:
        def _home(match):
            kinds.add("home_path_username")
            return match.group(1) + "<user>"

        text = pattern.sub(_home, text)
    return text, sorted(kinds)
