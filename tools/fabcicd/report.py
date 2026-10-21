"""Rapport de déploiement (JSON + Markdown), publié comme artefact du run."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .findings import Findings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Report:
    repository: str
    environment: str
    mode: str
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    status: str = "running"
    steps: list[dict] = field(default_factory=list)
    resolutions: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Traçabilité : identifiants non secrets du run et de l'artefact.
        for key, var in (
            ("ado_run", "BUILD_BUILDNUMBER"),
            ("ado_run_id", "BUILD_BUILDID"),
            ("ci_run_id", "CI_RUN_ID"),
            ("ci_run_name", "CI_RUN_NAME"),
            ("ci_commit", "CI_COMMIT"),
            ("artifact_sha256", "ARTIFACT_SHA256"),
            ("requested_by", "BUILD_REQUESTEDFOR"),
        ):
            if os.getenv(var):
                self.context[key] = os.getenv(var)

    def add_step(self, name: str, findings: Optional[Findings] = None, status: Optional[str] = None, extra: Optional[dict] = None) -> None:
        entry = {
            "step": name,
            "status": status or ("succeeded" if findings is None or findings.ok else "failed"),
            "at": _now(),
            "errors": [f.as_dict() for f in findings.errors] if findings else [],
            "warnings": [f.as_dict() for f in findings.warnings] if findings else [],
        }
        if extra:
            entry.update(extra)
        self.steps.append(entry)

    def finish(self, status: str) -> None:
        self.status = status
        self.finished_at = _now()

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"deployment-report-{self.repository}-{self.environment}"
        json_path = directory / f"{stem}.json"
        json_path.write_text(json.dumps(self.__dict__, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path = directory / f"{stem}.md"
        lines = [
            f"## {self.repository} — {self.environment} ({self.mode})",
            "",
            f"**Statut : {self.status}** — début {self.started_at}, fin {self.finished_at}",
            "",
            "| Étape | Statut | Erreurs | Avertissements |",
            "|---|---|---|---|",
        ]
        for step in self.steps:
            lines.append(f"| {step['step']} | {step['status']} | {len(step['errors'])} | {len(step['warnings'])} |")
        if self.resolutions:
            lines += ["", "| Variable | Résolue en | Détail |", "|---|---|---|"]
            for r in self.resolutions:
                lines.append(f"| `{r['variable']}` | {r.get('value') or '—'} | {r.get('detail', '')} |")
        errors = [e for s in self.steps for e in s["errors"]]
        if errors:
            lines += ["", "### Erreurs", ""]
            lines += [f"- `{e['code']}` {e['message']}" + (f" ({e['location']})" if e.get("location") else "") for e in errors[:50]]
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if os.getenv("TF_BUILD"):
            print(f"##vso[task.uploadsummary]{md_path}", flush=True)
        return json_path
