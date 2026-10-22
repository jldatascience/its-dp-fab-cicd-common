"""Tests fonctionnels post-déploiement (smoke tests), optionnels, déclarés dans dependencies.yml.

Chaque entrée exécute un item Fabric du workspace cible (en pratique un pipeline de
contrôle, ex. pl_master_load sur un périmètre réduit) et exige le statut « Completed ».
Sert de contrôle automatisé « tests techniques et fonctionnels validés » avant la
promotion suivante (ITG -> PPR, PPR -> PRD). Les approbations manuelles restent
nécessaires pour ce qui n'est pas automatisable.
"""

from __future__ import annotations

from .fabric_api import FabricApiError, find_unique, run_item_job
from .findings import Findings
from .preflight import PreflightResult
from .settings import RepoContext

STEP = "smoke"
JOB_TYPES = {"DataPipeline": "Pipeline", "Notebook": "RunNotebook", "SparkJobDefinition": "sparkjob"}


def run_smoke_tests(ctx: RepoContext, env: str, client, preflight: PreflightResult, runner=run_item_job) -> Findings:
    findings = Findings(STEP)
    workspace_id = preflight.target_workspace_id
    items = client.list_items(workspace_id)
    for test in ctx.smoke_tests(env):
        item_type, name = test.get("type", "DataPipeline"), test.get("name")
        job_type = test.get("job_type") or JOB_TYPES.get(item_type)
        if not job_type:
            findings.error("SMOKE_JOB_TYPE", f"type {item_type} sans jobType connu pour {name}")
            continue
        item, reason = find_unique(items, label=f"item {item_type}", type=item_type, displayName=name)
        if not item:
            findings.error("SMOKE_ITEM", f"{name} : {reason}")
            continue
        try:
            status = runner(client, workspace_id, item["id"], job_type, timeout_seconds=int(test.get("timeout_minutes", 30)) * 60)
        except FabricApiError as exc:
            findings.error("SMOKE_API", f"{name} : lancement impossible : {exc}")
            continue
        if status == "Completed":
            findings.info("SMOKE_OK", f"{name} : Completed")
        else:
            findings.error("SMOKE_FAILED", f"{name} : statut {status} (attendu Completed)")
    return findings
