"""Vérification post-déploiement (lecture seule).

Après la publication par fabric-cicd, on relit ce qui a réellement été déployé :

1. chaque item du périmètre publié existe dans le workspace cible (garde-fou
   contre le « succès » sans item publié, MODOP section 4 point 14) ;
2. pour chaque règle key_value_replace, la clé visée dans la définition
   déployée contient la valeur attendue (workspace cible, item du workspace
   cible, ou référence inter-workspace résolue au pré-vol) — critère E3 ;
3. aucun GUID interdit (sandbox personnel…) ne subsiste dans les définitions
   relues ;
4. les Variable Libraries déclarées ont le value set actif attendu (critère E6).
"""

from __future__ import annotations

import json
from typing import Optional

from jsonpath_ng.ext import parse as jsonpath_parse

from .fabric_api import FabricApiError, FabricClient
from .findings import Findings
from .items import RepoItem
from .preflight import PreflightResult
from .settings import GUID_RE, RepoContext
from .static_checks import _rule_targets
from .variables import VariableSyntaxError, parse_variable, rule_value_for_env

STEP = "verify"


def run_verify(ctx: RepoContext, env: str, client: FabricClient, items: list[RepoItem],
               preflight: PreflightResult) -> Findings:
    findings = Findings(STEP)
    workspace_id = preflight.target_workspace_id
    if not workspace_id:
        findings.error("VERIFY_NO_TARGET", "workspace cible non résolu")
        return findings

    scope = set(ctx.item_types_in_scope(env))
    expected_items = [i for i in items if not i.excluded and i.item_type in scope]
    try:
        deployed = client.list_items(workspace_id)
    except FabricApiError as exc:
        findings.error("VERIFY_API", f"liste des items déployés impossible : {exc}")
        return findings
    deployed_ids = {(d["type"], d["displayName"]): d["id"] for d in deployed}

    missing = [i.key for i in expected_items if (i.item_type, i.name) not in deployed_ids]
    if missing:
        findings.error("VERIFY_MISSING_ITEMS", f"{len(missing)} item(s) attendus absents après publication : {', '.join(missing[:20])}")
    else:
        findings.info("VERIFY_ITEMS_OK", f"{len(expected_items)} item(s) du périmètre présents dans le workspace cible")

    definitions: dict[str, dict[str, str]] = {}

    def definition(item_type: str, name: str) -> Optional[dict[str, str]]:
        item_id = deployed_ids.get((item_type, name))
        if not item_id:
            return None
        if item_id not in definitions:
            definitions[item_id] = client.get_item_definition(workspace_id, item_id)
        return definitions[item_id]

    forbidden = [str(p).lower() for p in ctx.guid_policy.get("forbidden_prefixes") or []]

    for index, rule in enumerate(ctx.key_value_rules()):
        value = rule_value_for_env(rule, env)
        try:
            variable = parse_variable(value) if value is not None else None
        except VariableSyntaxError:
            variable = None
        if variable is None:
            continue
        if variable.kind == "target_workspace" and variable.attribute == "id":
            expected = workspace_id
        elif variable.kind == "target_item" and variable.attribute == "id":
            expected = deployed_ids.get((variable.item_type, variable.item_name))
            if not expected:
                findings.error("VERIFY_TARGET_ITEM", f"key_value_replace[{index}] : {variable.raw} introuvable après publication")
                continue
        elif variable.kind in ("cross_workspace", "cross_item"):
            resolution = preflight.resolutions.get(variable.raw)
            expected = resolution.value if resolution else None
            if not expected:
                continue  # attribut non-id : contrôlé par fabric-cicd lui-même
        else:
            continue

        expression = jsonpath_parse(rule["find_key"])
        for target in _rule_targets(rule, items, ctx.items_root):
            if target.item_type not in scope:
                continue  # type hors périmètre : non publié, rien à vérifier
            try:
                parts = definition(target.item_type, target.name)
            except FabricApiError as exc:
                findings.error("VERIFY_DEFINITION", f"définition de {target.key} illisible : {exc}")
                continue
            if parts is None:
                continue
            found = 0
            for path, content in parts.items():
                if not path.endswith(".json"):
                    continue
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    continue
                for match in expression.find(data):
                    found += 1
                    if str(match.value).lower() != str(expected).lower():
                        findings.error(
                            "VERIFY_REFERENCE_MISMATCH",
                            f"{target.key} {match.full_path} = {match.value}, attendu {expected} ({variable.raw})",
                        )
            if found == 0:
                findings.error("VERIFY_RULE_NOT_FOUND", f"key_value_replace[{index}] : clé absente de la définition déployée de {target.key}")

    # Relecture complète : pipelines, notebooks, lakehouses (raccourcis) et SJD publiés du périmètre, pas seulement les cibles
    # des règles (une référence sandbox non inventoriée serait sinon invisible).
    for item in expected_items:
        if item.item_type in ("DataPipeline", "Notebook", "Lakehouse", "SparkJobDefinition"):
            try:
                definition(item.item_type, item.name)
            except FabricApiError as exc:
                findings.error("VERIFY_DEFINITION", f"définition de {item.key} illisible : {exc}")

    for item_id, parts in definitions.items():
        for path, content in parts.items():
            for guid in GUID_RE.findall(content):
                if any(guid.lower().startswith(p) for p in forbidden):
                    findings.error("VERIFY_FORBIDDEN_GUID", f"GUID interdit {guid} encore présent après déploiement ({path}, item {item_id})")

    for library in ctx.variable_libraries:
        expected_set = (library.get("expected_active_value_set") or {}).get(env)
        item_id = deployed_ids.get(("VariableLibrary", library.get("name")))
        if not expected_set:
            continue
        if not item_id:
            findings.error("VERIFY_VL_MISSING", f"Variable Library {library.get('name')} absente du workspace cible")
            continue
        try:
            props = client.get_variable_library(workspace_id, item_id).get("properties") or {}
        except FabricApiError as exc:
            findings.error("VERIFY_VL_API", f"lecture de {library.get('name')} impossible ({exc}) : contrôle E6 non réalisé")
            continue
        active = props.get("activeValueSetName")
        if active != expected_set:
            findings.error(
                "VERIFY_VL_ACTIVE_SET",
                f"{library.get('name')} : value set actif « {active} », attendu « {expected_set} » (risque de lecture croisée d'environnement)",
            )
        else:
            findings.info("VERIFY_VL_OK", f"{library.get('name')} : value set actif {active}")

    if findings.ok:
        findings.info("VERIFY_OK", f"{len(definitions)} définition(s) relue(s), références conformes")
    return findings
