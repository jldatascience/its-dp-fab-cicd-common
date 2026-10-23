"""Contrôles structurels des templates Azure DevOps du socle (v1.4.0, décision du 22/10/2026).

Coffre unique frfrkvdevdp0001, une service connection ARM distincte par environnement
(fédération d'identité), tâche AzureKeyVault@2 dans le template imposé.

Ces tests lisent les YAML comme du texte et comme des documents ; ils ne compilent pas le
pipeline (la compilation des expressions ${{ }} n'est possible que dans Azure DevOps,
par exemple avec « Run pipeline → Preview » ou l'API pipelines/preview).
"""

from __future__ import annotations

from pathlib import Path

import yaml

TEMPLATES = Path(__file__).resolve().parents[1] / "pipelines" / "templates"


def _text(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _params(name: str) -> dict:
    return {p["name"]: p for p in yaml.safe_load(_text(name))["parameters"]}


def test_cd_template_single_vault_and_arm_connection_per_environment():
    params = _params("cd-pipeline.yml")
    assert params["keyVaultName"]["default"] == "frfrkvdevdp0001"  # coffre unique
    assert "keyVaultNames" not in params  # pas de coffre par environnement
    assert params["secretSource"]["values"] == ["keyVault"]  # pas de groupe de variables
    assert params["authMode"]["default"] == "secret"
    assert params["devMode"]["default"] == "skip"  # pas de CD sur DEV par défaut
    scs = params["serviceConnections"]["default"]
    assert scs["DEV"] == "" and all(scs[e] == f"__A_CONFIRMER_SERVICE_CONNECTION_ARM_{e}__" for e in ("ITG", "PPR", "PRD"))
    assert "KEYVAULT_" not in _text("cd-pipeline.yml")


def test_stage_reads_only_the_three_secrets_of_its_environment():
    stage = _text("stage-deploy.yml")
    assert "${{ if eq(parameters.secretSource, 'keyVault') }}:" in stage  # lu quel que soit authMode
    assert "KeyVaultName: ${{ parameters.keyVaultName }}" in stage
    assert "azureSubscription: ${{ parameters.serviceConnections[parameters.env] }}" in stage
    assert "SecretsFilter: ${{ format('{0}-AZURE-CLIENT-ID,{0}-AZURE-CLIENT-SECRET,{0}-AZURE-TENANT-ID', parameters.env) }}" in stage
    assert "variableGroup" not in stage and "group:" not in stage


def test_stage_compile_time_guards_on_service_connections():
    stage = _text("stage-deploy.yml")
    assert "contains(parameters.serviceConnections[parameters.env], '__A_CONFIRMER')" in stage
    assert "service connection ARM à confirmer" in stage
    assert "eq(sc.value, parameters.serviceConnections[parameters.env])" in stage  # une SC distincte par environnement


def test_secret_mode_maps_vault_secrets_to_deploy_script():
    stage = _text("stage-deploy.yml")
    block = stage[stage.index("${{ if eq(parameters.authMode, 'secret') }}:"):stage.index("${{ if eq(parameters.authMode, 'cli') }}:")]
    assert "--auth secret" in block
    for var, kind in (("FABRIC_TENANT_ID", "TENANT-ID"), ("FABRIC_CLIENT_ID", "CLIENT-ID"), ("FABRIC_CLIENT_SECRET", "CLIENT-SECRET")):
        assert f"{var}: $(${{{{ format('{{0}}-AZURE-{kind}', parameters.env) }}}})" in block
