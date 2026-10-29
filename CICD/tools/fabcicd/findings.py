"""Constats produits par les contrôles (statiques, pré-vol, post-déploiement)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from . import log

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    location: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Findings:
    step: str
    items: list[Finding] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str, location: Optional[str] = None) -> None:
        finding = Finding(severity, code, message, location)
        self.items.append(finding)
        where = f" ({location})" if location else ""
        text = f"{code} — {message}{where}"
        if severity == ERROR:
            log.error(self.step, text)
        elif severity == WARNING:
            log.warning(self.step, text)
        else:
            log.info(self.step, text)

    def error(self, code: str, message: str, location: Optional[str] = None) -> None:
        self.add(ERROR, code, message, location)

    def warning(self, code: str, message: str, location: Optional[str] = None) -> None:
        self.add(WARNING, code, message, location)

    def info(self, code: str, message: str, location: Optional[str] = None) -> None:
        self.add(INFO, code, message, location)

    def extend(self, other: "Findings") -> None:
        self.items.extend(other.items)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.items if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.items if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors
