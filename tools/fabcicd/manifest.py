"""Empreinte de l'artefact CI promu entre environnements.

La CD calcule une seule fois (étape Validate_Artifact) l'empreinte SHA-256 de
l'artefact téléchargé et la publie dans ``deployment-manifest.json``. Chaque
stage d'environnement recalcule l'empreinte de l'artefact qu'il s'apprête à
déployer et refuse de continuer si elle diffère : c'est la preuve que le même
artefact, inchangé, est promu de DEV à PRD.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def tree_sha256(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big") + rel)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    return digest.hexdigest(), count


def write_manifest(root: Path, out: Path, repository: str) -> dict:
    sha, count = tree_sha256(root)
    manifest = {
        "repository": repository,
        "artifact_sha256": sha,
        "file_count": count,
        "ci_run_id": os.getenv("CI_RUN_ID"),
        "ci_run_name": os.getenv("CI_RUN_NAME"),
        "ci_commit": os.getenv("CI_COMMIT"),
        "ci_source_branch": os.getenv("CI_SOURCE_BRANCH"),
        "cd_run": os.getenv("BUILD_BUILDNUMBER"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def check_manifest(root: Path, manifest_path: Path) -> tuple[bool, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sha, count = tree_sha256(root)
    if sha != manifest["artifact_sha256"]:
        return False, f"empreinte {sha} différente de celle validée ({manifest['artifact_sha256']}, {manifest['file_count']} fichiers) — artefact modifié"
    return True, f"artefact intègre ({count} fichiers, sha256 {sha})"
