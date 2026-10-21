"""Registre des GUID connus, utilisé pour classer les GUID trouvés dans les items.

Le registre est un fichier YAML du socle commun (``registry/known_guids.yml``).
Il recense les identifiants relevés lors de l'audit (workspaces par dépôt et par
environnement, sandbox personnels, capacités, identités). Un GUID qui figure au
registre et qui n'est couvert par aucune règle ``key_value_replace`` est un
identifiant d'environnement codé en dur : c'est une erreur bloquante.

Les entrées peuvent être des valeurs complètes ou des préfixes (``42463408-``)
lorsque la valeur complète n'est connue que tronquée dans la documentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class KnownGuid:
    value: str  # valeur complète ou préfixe, en minuscules
    label: str
    category: str  # sandbox | workspace | capacity | identity | tenant | connection | other
    forbidden_in_items: bool

    def matches(self, guid: str) -> bool:
        guid = guid.lower()
        return guid == self.value or (len(self.value) < 36 and guid.startswith(self.value))


class Registry:
    def __init__(self, entries: list[KnownGuid]):
        self.entries = entries

    @classmethod
    def load(cls, path: Optional[Path]) -> "Registry":
        if not path or not path.is_file():
            return cls([])
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = []
        for raw in data.get("guids") or []:
            entries.append(
                KnownGuid(
                    value=str(raw["value"]).lower(),
                    label=str(raw.get("label", "")),
                    category=str(raw.get("category", "other")),
                    forbidden_in_items=bool(raw.get("forbidden_in_items", True)),
                )
            )
        return cls(entries)

    def classify(self, guid: str) -> Optional[KnownGuid]:
        for entry in self.entries:
            if entry.matches(guid):
                return entry
        return None


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "registry" / "known_guids.yml"
