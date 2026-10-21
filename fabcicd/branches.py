"""Règle de branches par environnement (v1.2, 20/10/2026).

* ITG : branche ``develop`` (DEV → ITG) ;
* PPR : branches ``release/*`` — une branche de release par lot de features, créée au besoin ;
* PRD : branche ``main``.

Un run de CD ne déploie qu'un seul environnement : celui qui correspond à la branche
source de l'artefact CI. DEV, s'il est déployé par la CD (``devMode``), suit ``develop``.
Les motifs utilisent la syntaxe ``fnmatch`` (``*`` couvre aussi ``/``).
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Mapping, Optional

DEFAULT_POLICY: dict[str, str] = {
    "ITG": "refs/heads/develop",
    "PPR": "refs/heads/release/*",
    "PRD": "refs/heads/main",
}
ORDER = ("ITG", "PPR", "PRD")


class BranchPolicyError(ValueError):
    """Règle de branches illisible ou incohérente."""


def parse_policy(text: Optional[str]) -> dict[str, str]:
    """``"ITG=refs/heads/develop,PPR=refs/heads/release/*,PRD=refs/heads/main"`` → dict."""
    if not text:
        return dict(DEFAULT_POLICY)
    policy: dict[str, str] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise BranchPolicyError(f"entrée invalide « {part} » (attendu ENV=refs/heads/…)")
        env, pattern = (p.strip() for p in part.split("=", 1))
        env = env.upper()
        if env not in ORDER:
            raise BranchPolicyError(f"environnement inconnu « {env} » (attendu ITG, PPR ou PRD)")
        if not pattern.startswith("refs/heads/"):
            raise BranchPolicyError(f"{env} : motif « {pattern} » doit commencer par refs/heads/")
        policy[env] = pattern
    missing = [e for e in ORDER if e not in policy]
    if missing:
        raise BranchPolicyError(f"environnements sans branche : {', '.join(missing)}")
    return policy


def matches(branch: str, pattern: str) -> bool:
    return bool(branch) and fnmatchcase(branch, pattern)


def target_environment(branch: str, policy: Mapping[str, str]) -> Optional[str]:
    """Environnement que la branche est autorisée à déployer (ou None)."""
    hits = [env for env in ORDER if matches(branch, policy[env])]
    if len(hits) > 1:
        raise BranchPolicyError(f"{branch} correspond à plusieurs environnements ({', '.join(hits)}) : règle ambiguë")
    return hits[0] if hits else None


def gate_branch(cfg_branch, env: str) -> str:
    """Branche (ou motif) des runs de la CD Framework à examiner par la porte pour ``env``.

    ``cfg_branch`` vient de ``dependencies.yml`` (``ado_gate.branch``) : chaîne unique
    (ancien format, appliquée à tous les environnements) ou dictionnaire par environnement.
    """
    if isinstance(cfg_branch, dict):
        value = cfg_branch.get(env)
        if value:
            return str(value)
    elif cfg_branch:
        return str(cfg_branch)
    return DEFAULT_POLICY.get(env, DEFAULT_POLICY["ITG"])
