"""Fixtures : construction de dépôts Fabric synthétiques pour les tests.

Les items synthétiques reproduisent la structure Git Integration de Fabric
(dossier ``<nom>.<Type>`` + ``.platform`` + fichier de définition) et les
références observées dans le MODOP (GUID sandbox 42463408-…).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

SANDBOX_WS = "42463408-0000-4000-8000-000000000001"
ENVS = ("DEV", "ITG", "PPR", "PRD")


def write_item(root: Path, folder: str, name: str, item_type: str, files: dict) -> Path:
    directory = root / folder / f"{name}.{item_type}" if folder else root / f"{name}.{item_type}"
    directory.mkdir(parents=True, exist_ok=True)
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": item_type, "displayName": name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }
    (directory / ".platform").write_text(json.dumps(platform), encoding="utf-8")
    for filename, content in files.items():
        text = content if isinstance(content, str) else json.dumps(content, indent=2)
        (directory / filename).write_text(text, encoding="utf-8")
    return directory


def pipeline(activities: list[dict]) -> dict:
    return {"properties": {"activities": activities}}


def notebook_activity(name: str, notebook_id: str, workspace_id: str) -> dict:
    return {"name": name, "type": "TridentNotebook", "typeProperties": {"notebookId": notebook_id, "workspaceId": workspace_id}}


def invoke_activity(name: str, pipeline_id: str, workspace_id: str) -> dict:
    return {
        "name": name,
        "type": "InvokePipeline",
        "typeProperties": {"operationType": "InvokeFabricPipeline", "pipelineId": pipeline_id, "workspaceId": workspace_id},
    }


def base_config(workspace_prefix: str = "FR - DP - {env} - FAB - Framework - MANAGED", exclude: str | None = "^/(Tests_|Flux/old)") -> dict:
    config = {
        "core": {
            "workspace": {env: workspace_prefix.format(env=env) for env in ENVS},
            # Relatif à CICD/deployment/config.yml : les items du dépôt de test sont à sa racine.
            "repository_directory": "../..",
            "item_types_in_scope": ["DataPipeline", "Notebook"],
            "parameter": "parameter.yml",
        },
        "publish": {"skip": {env: False for env in ENVS}},
        "unpublish": {"skip": {env: True for env in ENVS}},
    }
    if exclude:
        config["publish"]["folder_exclude_regex"] = exclude
        config["features"] = ["enable_experimental_features", "enable_exclude_folder"]
    return config


def base_dependencies(role: str = "framework", status: str = "validated", category: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "repository": "test-repo",
        "role": role,
        # v1.3 : catégorie obligatoire (framework | adp | bdp | ucs).
        "category": category or ("framework" if role == "framework" else "adp"),
        "inventory_status": status,
        "guid_policy": {"forbidden_prefixes": ["42463408-"]},
    }


@pytest.fixture
def make_repo(tmp_path):
    """Fabrique un dépôt de type Framework minimal, valide par défaut."""

    def _make(config=None, parameter=None, dependencies=None, extra=None) -> Path:
        root = tmp_path / "repo"
        root.mkdir()
        nb_id = "6eda81c3-0000-4000-8000-000000000002"
        write_item(root, "Flux", "nb_get_ws_lh_ids", "Notebook", {"notebook-content.py": "# Fabric notebook source\nprint('ok')\n"})
        write_item(root, "Flux", "pl_get_ws_lh_ids", "DataPipeline",
                   {"pipeline-content.json": pipeline([notebook_activity("nb_get_ws_lh_ids", nb_id, SANDBOX_WS)])})
        # Dossier exclu : contient un GUID sandbox qui ne doit PAS être signalé.
        write_item(root, "Flux/old", "pl_old_v1", "DataPipeline",
                   {"pipeline-content.json": pipeline([notebook_activity("x", nb_id, SANDBOX_WS)])})
        deployment = root / "CICD" / "deployment"
        deployment.mkdir(parents=True)
        (deployment / "config.yml").write_text(yaml.safe_dump(config or base_config(), allow_unicode=True), encoding="utf-8")
        default_param = {
            "key_value_replace": [
                {
                    "find_key": '$.properties.activities[?(@.name=="nb_get_ws_lh_ids")].typeProperties.workspaceId',
                    "replace_value": {"_ALL_": "$workspace.$id"},
                    "item_type": "DataPipeline",
                    "item_name": "pl_get_ws_lh_ids",
                },
                {
                    "find_key": '$.properties.activities[?(@.name=="nb_get_ws_lh_ids")].typeProperties.notebookId',
                    "replace_value": {"_ALL_": "$items.Notebook.nb_get_ws_lh_ids.$id"},
                    "item_type": "DataPipeline",
                    "item_name": "pl_get_ws_lh_ids",
                },
            ]
        }
        (deployment / "parameter.yml").write_text(yaml.safe_dump(parameter if parameter is not None else default_param), encoding="utf-8")
        (deployment / "dependencies.yml").write_text(yaml.safe_dump(dependencies or base_dependencies()), encoding="utf-8")
        for rel, content in (extra or {}).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    return _make


class FakeToken:
    token = "fake"
    expires_on = 9999999999


class FakeCredential:
    def get_token(self, *_):
        return FakeToken()


class FakeFabric:
    """Remplace FabricClient : données en mémoire, aucune requête réseau."""

    def __init__(self, workspaces=None, items=None, connections=None, definitions=None, variable_libraries=None):
        self.workspaces = workspaces or []
        self.items = items or {}
        self.connections = connections or []
        self.definitions = definitions or {}
        self.variable_libraries = variable_libraries or {}

    def list_workspaces(self):
        return self.workspaces

    def get_workspace(self, workspace_id):
        for ws in self.workspaces:
            if ws["id"] == workspace_id:
                return {**ws, "capacityId": ws.get("capacityId", "cap")}
        raise KeyError(workspace_id)

    def list_items(self, workspace_id):
        return self.items.get(workspace_id, [])

    def list_connections(self):
        return self.connections

    def get_item_definition(self, workspace_id, item_id):
        return self.definitions.get(item_id, {})  # item sans définition simulée = définition vide

    def get_variable_library(self, workspace_id, item_id):
        return self.variable_libraries[item_id]
