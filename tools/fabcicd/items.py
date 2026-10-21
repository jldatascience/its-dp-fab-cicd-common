"""Découverte des items Fabric versionnés dans un dépôt.

Reproduit la logique de fabric-cicd 1.4.0 (``FabricWorkspace._refresh_repository_items``
et ``_should_skip_by_folder``) :

* un item est un dossier qui contient un fichier ``.platform`` ;
* ``metadata.type`` et ``metadata.displayName`` sont obligatoires ;
* le chemin de dossier d'un item est le chemin de son dossier parent, relatif à
  ``repository_directory``, préfixé par ``/`` (ex. ``/Flux/old``) ;
* ``folder_exclude_regex`` est testé avec ``re.search`` sur ce chemin puis sur
  chacun de ses ancêtres ; un item à la racine n'est jamais exclu.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RepoItem:
    item_type: str
    name: str
    directory: Path
    folder_path: str  # "" pour un item à la racine
    logical_id: str
    excluded: bool = False

    @property
    def key(self) -> str:
        return f"{self.name}.{self.item_type}"


class ItemDiscoveryError(Exception):
    pass


def _is_excluded(folder_path: str, pattern: Optional[re.Pattern]) -> bool:
    if not pattern or not folder_path:
        return False
    path = folder_path
    while path:
        if pattern.search(path):
            return True
        if "/" in path:
            path = path.rsplit("/", 1)[0]
        else:
            break
    return False


def discover_items(items_root: Path, folder_exclude_regex: Optional[str] = None) -> list[RepoItem]:
    if not items_root.is_dir():
        raise ItemDiscoveryError(f"repository_directory introuvable : {items_root}")
    pattern = re.compile(folder_exclude_regex) if folder_exclude_regex else None
    items: list[RepoItem] = []
    for platform in sorted(items_root.rglob(".platform")):
        if ".git" in platform.parts:
            continue
        try:
            metadata = json.loads(platform.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ItemDiscoveryError(f"{platform} illisible : {exc}") from exc
        meta = metadata.get("metadata") or {}
        if "type" not in meta or "displayName" not in meta:
            raise ItemDiscoveryError(f"displayName et type obligatoires dans {platform}")
        directory = platform.parent
        relative = directory.relative_to(items_root).as_posix()
        folder_path = "/" + relative.rsplit("/", 1)[0] if "/" in relative else ""
        items.append(
            RepoItem(
                item_type=str(meta["type"]),
                name=str(meta["displayName"]),
                directory=directory,
                folder_path=folder_path,
                logical_id=str((metadata.get("config") or {}).get("logicalId", "")),
                excluded=_is_excluded(folder_path, pattern),
            )
        )
    return items


def item_files(item: RepoItem) -> list[Path]:
    """Fichiers de définition d'un item (hors ``.platform``)."""
    return sorted(p for p in item.directory.rglob("*") if p.is_file() and p.name != ".platform")
