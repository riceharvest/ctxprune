"""Opt-in, reversible-in-process redaction for common credential formats.

The mapping belongs to a Redactor instance and is never persisted. Callers should
keep one instance for a session if they need to restore placeholders later.
"""
from __future__ import annotations

import re


_RULES = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("JWT", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")),
    ("AWS_ACCESS_KEY", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    ("GH_TOKEN", re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})(?![A-Za-z0-9_])")),
    ("ANTHROPIC_KEY", re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("OPENAI_KEY", re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("SLACK_TOKEN", re.compile(r"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])")),
    ("GCP_API_KEY", re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])")),
    ("GENERIC_SECRET", re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)\b\s*[:=]\s*(['\"]?)([A-Za-z0-9_./+:-]{16,})\1")),
)


class Redactor:
    """Replace detected values with stable per-instance labels and support exact reversal."""

    def __init__(self) -> None:
        self._forward: dict[tuple[str, str], str] = {}
        self._reverse: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def redact(self, text: str) -> str:
        for kind, pattern in _RULES:
            def replace(match: re.Match, category: str = kind) -> str:
                value = match.group(2) if category == "GENERIC_SECRET" else match.group(0)
                key = (category, value)
                if key not in self._forward:
                    n = self._counts.get(category, 0) + 1
                    self._counts[category] = n
                    label = f"[REDACTED:{category}_{n}]"
                    self._forward[key] = label
                    self._reverse[label] = value
                label = self._forward[key]
                if category == "GENERIC_SECRET":
                    return match.group(0).replace(value, label, 1)
                return label
            text = pattern.sub(replace, text)
        return text

    def restore(self, text: str) -> str:
        for label, value in self._reverse.items():
            text = text.replace(label, value)
        return text

    @property
    def mapping_size(self) -> int:
        return len(self._reverse)
