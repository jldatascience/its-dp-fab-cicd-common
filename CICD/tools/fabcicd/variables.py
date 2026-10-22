"""Analyse des variables dynamiques de parameter.yml (syntaxe fabric-cicd 1.4.0).

Formes reconnues (vérifiées dans ``fabric_cicd/_parameter/_utils.py``) :

``$workspace.$id`` / ``$workspace.id``           -> id du workspace cible
``$workspace.$name`` / ``$workspace.$name_encoded``
``$workspace.<nom>`` / ``$workspace.<nom>.$id``   -> id d'un autre workspace, par nom exact
``$workspace.<nom>.$items.<type>.<nom>.$<attr>``  -> attribut d'un item d'un autre workspace
``$items.<type>.<nom>.$<attr>``                   -> attribut d'un item du workspace cible
``$ENV:<VAR>``                                     -> variable d'environnement (feature flag)

Attributs d'item supportés : id, sqlendpoint, sqlendpointid, queryserviceuri.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

ITEM_ATTRIBUTES = ("id", "sqlendpoint", "sqlendpointid", "queryserviceuri")
_ENV_RE = re.compile(r"\$ENV:(\w+)")


class VariableSyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class DynamicVariable:
    raw: str
    kind: str  # target_workspace | cross_workspace | cross_item | target_item | env | literal
    workspace_name: Optional[str] = None
    item_type: Optional[str] = None
    item_name: Optional[str] = None
    attribute: Optional[str] = None
    env_var: Optional[str] = None


def _split_item(rest: str, raw: str) -> tuple[str, str, str]:
    # rest = "<type>.<nom>.$<attr>" ; le nom peut contenir des points.
    if ".$" not in rest:
        raise VariableSyntaxError(f"Attribut manquant (attendu .$id) : {raw}")
    head, attr = rest.rsplit(".$", 1)
    if "." not in head:
        raise VariableSyntaxError(f"Type ou nom d'item manquant : {raw}")
    item_type, item_name = head.split(".", 1)
    attr = attr.lower()
    if attr not in ITEM_ATTRIBUTES:
        raise VariableSyntaxError(f"Attribut '{attr}' non supporté par fabric-cicd 1.4.0 : {raw}")
    if not item_type or not item_name:
        raise VariableSyntaxError(f"Type ou nom d'item vide : {raw}")
    return item_type, item_name, attr


def parse_variable(value: str) -> DynamicVariable:
    if not isinstance(value, str) or not value.startswith("$"):
        return DynamicVariable(raw=str(value), kind="literal")

    env_match = _ENV_RE.fullmatch(value)
    if env_match:
        return DynamicVariable(raw=value, kind="env", env_var=env_match.group(1))

    if value.startswith("$items."):
        item_type, item_name, attr = _split_item(value[len("$items."):], value)
        return DynamicVariable(value, "target_item", item_type=item_type, item_name=item_name, attribute=attr)

    if value.startswith("$workspace."):
        rest = value[len("$workspace."):]
        if rest in ("$id", "id"):
            return DynamicVariable(value, "target_workspace", attribute="id")
        if rest in ("$name", "$name_encoded"):
            return DynamicVariable(value, "target_workspace", attribute=rest[1:])
        if ".$items." in rest:
            workspace_name, item_part = rest.split(".$items.", 1)
            if not workspace_name:
                raise VariableSyntaxError(f"Nom de workspace vide : {value}")
            item_type, item_name, attr = _split_item(item_part, value)
            return DynamicVariable(
                value, "cross_item", workspace_name=workspace_name, item_type=item_type, item_name=item_name, attribute=attr
            )
        workspace_name = rest[: -len(".$id")] if rest.endswith(".$id") else rest
        if not workspace_name or workspace_name.startswith("$"):
            raise VariableSyntaxError(f"Variable $workspace non reconnue : {value}")
        return DynamicVariable(value, "cross_workspace", workspace_name=workspace_name, attribute="id")

    raise VariableSyntaxError(f"Variable dynamique non reconnue : {value}")


def rule_value_for_env(rule: dict, env: str) -> Optional[str]:
    """Valeur de remplacement d'une règle pour un environnement (ou None)."""
    replace_value = rule.get("replace_value") or {}
    if not isinstance(replace_value, dict):
        return None
    if env in replace_value:
        return replace_value[env]
    for key, value in replace_value.items():
        if str(key).upper() == "_ALL_" and len(replace_value) == 1:
            return value
    return None
