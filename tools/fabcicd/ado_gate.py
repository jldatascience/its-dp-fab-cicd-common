"""Porte « Framework disponible » fondée sur l'historique Azure DevOps (ADP et BDP seulement, v1.3).

Question posée avant de déployer un Data Product sur l'environnement X :
« le dernier déploiement du Framework sur X s'est-il terminé avec succès, et
aucun déploiement du Framework sur X n'est-il en cours ? »

Le stage ``Deploy_<ENV>`` de la CD Framework inclut la vérification
post-déploiement : un stage réussi signifie donc « déployé ET validé ».

Mise en œuvre : API REST Build d'Azure DevOps, avec le jeton du run
(``System.AccessToken``, à mapper explicitement dans ``env`` du step). Le
service de build du projet doit avoir le droit « View builds » sur la
définition de la CD Framework.

Ce contrôle complète, sans le remplacer, le contrôle d'existence des items
Framework dans Fabric (preflight) : l'un prouve que le déploiement a été validé,
l'autre que les items sont réellement présents.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests

from .findings import Findings

STEP = "framework-gate"
API = "api-version=7.1"


@dataclass
class GateResult:
    findings: Findings
    framework_run_id: Optional[int] = None
    framework_commit: Optional[str] = None
    framework_build_number: Optional[str] = None


class AdoClient:
    def __init__(self, collection_uri: str, project: str, token: str, session: Optional[requests.Session] = None):
        self.base = f"{collection_uri.rstrip('/')}/{quote(project)}/_apis"
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    @classmethod
    def from_environment(cls) -> "AdoClient":
        missing = [v for v in ("SYSTEM_COLLECTIONURI", "SYSTEM_TEAMPROJECT", "SYSTEM_ACCESSTOKEN") if not os.getenv(v)]
        if missing:
            raise RuntimeError(f"variables Azure DevOps absentes : {', '.join(missing)} (mapper System.AccessToken dans env)")
        return cls(os.environ["SYSTEM_COLLECTIONURI"], os.environ["SYSTEM_TEAMPROJECT"], os.environ["SYSTEM_ACCESSTOKEN"])

    def get(self, path: str) -> dict:
        separator = "&" if "?" in path else "?"
        response = self.session.get(f"{self.base}/{path}{separator}{API}", timeout=60)
        response.raise_for_status()
        return response.json()


def _stage_record(timeline: dict, stage: str) -> Optional[dict]:
    for record in timeline.get("records") or []:
        if record.get("type") == "Stage" and stage in (record.get("identifier"), record.get("name")):
            return record
    return None


def _branch_query(branch: str) -> str:
    """Filtre ``branchName`` de l'API (exact) ; un motif (release/*) est filtré côté client."""
    return "" if any(c in branch for c in "*?[") else f"&branchName={quote(branch)}"


def _on_branch(build: dict, branch: str) -> bool:
    if not any(c in branch for c in "*?["):
        return True  # déjà filtré par l'API
    return fnmatchcase(str(build.get("sourceBranch") or ""), branch)


def check_framework_deployed(client: AdoClient, pipeline_name: str, env: str, branch: str = "refs/heads/develop",
                             stage_prefix: str = "Deploy_", depth: int = 20) -> GateResult:
    """Porte Framework : dernier déploiement ``env`` de la CD Framework réussi, sur sa branche.

    v1.3 : appelée uniquement pour les ADP et les BDP (``needs_framework_gate``). Elle
    vérifie la DISPONIBILITÉ du Framework (dernier stage ``Deploy_<ENV>`` réussi, aucun
    en cours) et n'exige jamais un nouveau run : un Framework déployé il y a longtemps
    reste valable. v1.2 : ``branch`` dépend de l'environnement (develop → ITG,
    release/* → PPR, main → PRD) et peut être un motif.
    """
    findings = Findings(STEP)
    result = GateResult(findings)
    stage = f"{stage_prefix}{env}"

    definitions = client.get(f"build/definitions?name={quote(pipeline_name)}").get("value") or []
    if len(definitions) != 1:
        findings.error("GATE_PIPELINE", f"pipeline Framework « {pipeline_name} » : {len(definitions)} définition(s) trouvée(s), 1 attendue")
        return result
    definition_id = definitions[0]["id"]

    in_progress = client.get(
        f"build/builds?definitions={definition_id}{_branch_query(branch)}&statusFilter=inProgress,notStarted"
    ).get("value") or []
    in_progress = [b for b in in_progress if _on_branch(b, branch)]
    # Runs encore actifs dont le stage de cet environnement est déjà terminé (ex. ITG fini,
    # PPR en attente d'approbation) : ce sont les plus récents, ils passent en premier
    # (correctif audit 20/10/2026 — auparavant ignorés au profit d'un run terminé plus ancien).
    recent: list[tuple[dict, dict]] = []
    for build in in_progress:
        record = _stage_record(client.get(f"build/builds/{build['id']}/timeline"), stage)
        if record and record.get("state") in ("inProgress", "pending"):
            findings.error("GATE_IN_PROGRESS", f"déploiement du Framework sur {env} en cours (run {build.get('buildNumber')}) : relancer après sa fin")
            return result
        if record and record.get("state") == "completed" and record.get("result") != "skipped":
            recent.append((build, record))
    recent.sort(key=lambda pair: pair[0]["id"], reverse=True)

    builds = client.get(
        f"build/builds?definitions={definition_id}{_branch_query(branch)}&statusFilter=completed"
        f"&queryOrder=finishTimeDescending&$top={depth * (5 if not _branch_query(branch) else 1)}"
    ).get("value") or []
    builds = [b for b in builds if _on_branch(b, branch)][:depth]
    candidates = recent + [(b, None) for b in builds]
    for build, record in candidates:
        if record is None:
            record = _stage_record(client.get(f"build/builds/{build['id']}/timeline"), stage)
        if not record or record.get("state") != "completed" or record.get("result") == "skipped":
            continue  # ce run n'a pas exécuté le stage : on remonte l'historique
        if record.get("result") == "succeeded":
            result.framework_run_id = build["id"]
            result.framework_commit = build.get("sourceVersion")
            result.framework_build_number = build.get("buildNumber")
            findings.info("GATE_OK", f"Framework validé sur {env} : run {build.get('buildNumber')} (commit {str(build.get('sourceVersion'))[:8]})")
        else:
            findings.error("GATE_LAST_FAILED", f"dernier déploiement du Framework sur {env} en échec ({record.get('result')}, run {build.get('buildNumber')})")
        return result
    findings.error("GATE_NEVER_DEPLOYED", f"aucun déploiement réussi du Framework sur {env} dans les {depth} derniers runs")
    return result
