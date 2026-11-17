"""Secret redaction for anything that leaves the process: logs, events, errors, API responses."""
from __future__ import annotations

from typing import Any, Iterable

MASK = "********"


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets: list[str] = []
        for s in secrets:
            self.add(s)

    def add(self, secret: str | None) -> None:
        # Short secrets over-mask ordinary text; that is the safe direction.
        if secret and secret not in self._secrets:
            self._secrets.append(secret)
            self._secrets.sort(key=len, reverse=True)

    def __bool__(self) -> bool:
        return bool(self._secrets)

    def text(self, s: str) -> str:
        for secret in self._secrets:
            if secret in s:
                s = s.replace(secret, MASK)
        return s

    def obj(self, value: Any) -> Any:
        """Redact recursively inside dicts/lists/tuples."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.obj(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.obj(v) for v in value)
        return value


def mask_argv(argv: list[str], secret_flags: Iterable[str] = ("-p", "-dbap")) -> list[str]:
    """Mask the value following any of secret_flags (for display of oewizard/charbench commands)."""
    flags = set(secret_flags)
    out: list[str] = []
    mask_next = False
    for a in argv:
        out.append(MASK if mask_next else a)
        mask_next = a in flags
    return out
