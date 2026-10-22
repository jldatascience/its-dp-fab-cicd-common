"""Pré-vol et résolution des références, avant toute publication.

Tout ce qui est fait ici est en lecture seule. Le pré-vol échoue explicitement
(code d'erreur, message) dès qu'une dépendance est absente ou ambiguë : rien
n'est remplacé silencieusement.

Ordre :
1. Workspace cible : résolu par nom exact, unique, accessible, rattaché à une capacité.
2. Dépendances Framework (dépôts consommateurs) : workspace Framework de
   l'environnement, puis chaque item requis (type + nom exact), unique.
3. Connexions attendues : nom exact, unique, visible par l'identité de déploiement.
4. Références de parameter.yml : chaque variable inter-workspace est résolue
   par les mêmes règles que ci-dessus. Les variables du workspace cible
   ($items.<type>.<nom>) sont vérifiées après déploiement (module verify).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .fabric_api import FabricApiError, FabricClient, find_unique
from .findings import Findings
from .settings import RepoContext, is_placeholder
from .variables import VariableSyntaxError, parse_variable, rule_value_for_env

STEP = "preflight"


@dataclass
class Resolution:
    variable: str
    kind: str
    value: Optional[str]
    detail: str = ""


@dataclass
class PreflightResult:
    findings: Findings
    target_workspace_id: Optional[str] = None
    target_workspace_name: Optional[str] = None
    resolutions: dict[str, Resolution] = field(default_factory=dict)


class _WorkspaceCache:
    def __init__(self, client: FabricClient):
        self.client = client
        self._workspaces: Optional[list[dict]] = None
        self._items: dict[str, list[dict]] = {}

    def workspaces(self) -> list[dict]:
        if self._workspaces is None:
            self._workspaces = self.client.list_workspaces()
        return self._workspaces

    def items(self, workspace_id: str) -> list[dict]:
        if workspace_id not in self._items:
            self._items[workspace_id] = self.client.list_items(workspace_id)
        return self._items[workspace_id]

    def workspace_by_name(self, name: str) -> tuple[Optional[dict], Optional[str]]:
        return find_unique(self.workspaces(), label="workspace", displayName=name)

    def item(self, workspace_id: str, item_type: str, name: str) -> tuple[Optional[dict], Optional[str]]:
        return find_unique(self.items(workspace_id), label=f"item {item_type}", type=item_type, displayName=name)


def run_preflight(ctx: RepoContext, env: str, client: FabricClient) -> PreflightResult:
    findings = Findings(STEP)
    result = PreflightResult(findings)
    cache = _WorkspaceCache(client)

    # 0. Statut d'inventaire et valeurs à relever
    target_name = ctx.workspace_name(env)
    if not target_name:
        findings.error("PRE_TARGET_UNDEFINED", f"aucun workspace défini pour {env} dans config.yml")
        return result
    if is_placeholder(target_name):
        findings.error("PRE_TARGET_PLACEHOLDER", f"nom du workspace {env} à relever : {target_name}")
        return result

    # 1. Workspace cible
    try:
        workspace, reason = cache.workspace_by_name(target_name)
    except FabricApiError as exc:
        findings.error("PRE_API", f"liste des workspaces impossible : {exc}")
        return result
    if not workspace:
        findings.error("PRE_TARGET_WORKSPACE", reason or "workspace cible introuvable")
        return result
    result.target_workspace_id = workspace["id"]
    result.target_workspace_name = target_name
    findings.info("PRE_TARGET_OK", f"workspace cible « {target_name} » = {workspace['id']}")
    try:
        details = client.get_workspace(workspace["id"])
    except FabricApiError as exc:
        findings.error("PRE_TARGET_ACCESS", f"lecture du workspace cible refusée : {exc}")
        return result
    if not details.get("capacityId"):
        findings.error("PRE_TARGET_CAPACITY", "workspace cible sans capacité Fabric (critère A6)")

    # 2. Dépendance de déploiement unique : le Framework, pour les ADP et les BDP (v1.3, R1-R3).
    #    Le Framework et UCS n'ont aucun prérequis (R5). On distingue : présence du workspace
    #    (PRE_FRAMEWORK_WORKSPACE), présence des items utilisés (PRE_FRAMEWORK_ITEM) ; la
    #    disponibilité validée (dernier déploiement réussi) est contrôlée par la porte ADO.
    from .dependencies import needs_framework_gate
    if needs_framework_gate(ctx):
        fw_name = ctx.framework_workspace(env)
        fw_workspace, reason = (None, "nom non défini") if not fw_name else cache.workspace_by_name(fw_name)
        if not fw_workspace:
            findings.error("PRE_FRAMEWORK_WORKSPACE", f"workspace Framework {env} : {reason}")
        else:
            findings.info("PRE_FRAMEWORK_WORKSPACE_OK", f"Framework {env} « {fw_name} » = {fw_workspace['id']}")
            try:
                for required in ctx.framework_items:
                    item, reason = cache.item(fw_workspace["id"], required["type"], required["name"])
                    if item:
                        findings.info("PRE_FRAMEWORK_ITEM_OK", f"{required['type']} {required['name']} = {item['id']}")
                    else:
                        findings.error("PRE_FRAMEWORK_ITEM", f"Framework {env} : {reason}")
            except FabricApiError as exc:
                findings.error("PRE_FRAMEWORK_ACCESS", f"items du Framework illisibles (droit de lecture du SPN ?) : {exc}")

    # 3. Connexions
    expected_connections = ctx.connections(env)
    if expected_connections:
        try:
            connections = client.list_connections()
        except FabricApiError as exc:
            findings.error("PRE_CONNECTIONS_API", f"liste des connexions impossible : {exc}")
            connections = None
        if connections is not None:
            for name in expected_connections:
                connection, reason = find_unique(connections, label="connexion", displayName=name)
                if connection:
                    findings.info("PRE_CONNECTION_OK", f"connexion « {name} » = {connection['id']}")
                else:
                    findings.error("PRE_CONNECTION", f"{reason} (rôle User du SPN requis, critère D4)")

    # 4. Références inter-workspaces de parameter.yml
    for index, rule in enumerate(ctx.key_value_rules()):
        value = rule_value_for_env(rule, env)
        if value is None:
            findings.error("PRE_RULE_ENV_MISSING", f"key_value_replace[{index}] sans valeur pour {env}")
            continue
        try:
            variable = parse_variable(value)
        except VariableSyntaxError as exc:
            findings.error("PRE_RULE_SYNTAX", f"key_value_replace[{index}] : {exc}")
            continue
        if variable.raw in result.resolutions:
            continue
        if variable.kind == "target_workspace":
            result.resolutions[variable.raw] = Resolution(variable.raw, variable.kind, workspace["id"], target_name)
        elif variable.kind == "target_item":
            result.resolutions[variable.raw] = Resolution(variable.raw, variable.kind, None, "résolu après publication (verify)")
        elif variable.kind in ("cross_workspace", "cross_item"):
            other, reason = cache.workspace_by_name(variable.workspace_name)
            if not other:
                findings.error("PRE_REF_WORKSPACE", f"{variable.raw} : {reason}")
                continue
            if variable.kind == "cross_workspace":
                result.resolutions[variable.raw] = Resolution(variable.raw, variable.kind, other["id"], variable.workspace_name)
                continue
            if variable.attribute != "id":
                result.resolutions[variable.raw] = Resolution(variable.raw, variable.kind, None, f"attribut {variable.attribute} résolu par fabric-cicd")
                continue
            try:
                item, reason = cache.item(other["id"], variable.item_type, variable.item_name)
            except FabricApiError as exc:
                findings.error("PRE_REF_ACCESS", f"{variable.raw} : items illisibles : {exc}")
                continue
            if not item:
                findings.error("PRE_REF_ITEM", f"{variable.raw} : {reason}")
                continue
            result.resolutions[variable.raw] = Resolution(variable.raw, variable.kind, item["id"], f"{variable.workspace_name} / {variable.item_name}")
        elif variable.kind == "env":
            import os
            if variable.env_var not in os.environ:
                findings.error("PRE_REF_ENV", f"{variable.raw} : variable d'environnement {variable.env_var} absente")
            else:
                result.resolutions[variable.raw] = Resolution(variable.raw, variable.kind, "(valeur d'environnement)", variable.env_var)
    resolved = sum(1 for r in result.resolutions.values() if r.value)
    findings.info("PRE_REFS", f"{resolved}/{len(result.resolutions)} variables résolues avant publication")
    return result
