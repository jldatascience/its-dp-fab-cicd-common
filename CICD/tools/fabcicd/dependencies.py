"""Règles de dépendances entre dépôts (v1.3, 20/10/2026).

Matrice de référence (normative) :

==========================  ============================  ==============================
Catégorie (``category``)    Prérequis de déploiement      Dépendances vers ADP / BDP
==========================  ============================  ==============================
framework                   aucun                         aucune
adp (MDS, Alexandrie, IBS)  Framework disponible          aucune (indépendant des BDP)
bdp (6 dépôts)              Framework disponible          aucune (indépendant des ADP)
ucs (Reporting Fees & WIP)  aucun                         consommation de données possible
==========================  ============================  ==============================

R1  Le Framework est l'unique prérequis de déploiement des ADP et des BDP ; il n'a pas
    besoin d'être redéployé s'il est déjà disponible et validé sur l'environnement cible.
R2  Un ADP n'est jamais bloqué par un BDP.     R3  Un BDP n'est jamais bloqué par un ADP.
R4  Une fois le Framework disponible, ADP et BDP se déploient dans n'importe quel ordre.
R5  UCS n'a aucune dépendance de déploiement.
R6  Les dépendances de données, techniques ou d'exécution (``data_dependencies``) sont
    documentaires : elles ne créent JAMAIS de porte ni d'ordre de déploiement.

Conséquences techniques :
* ``upstreams`` (dépendance de déploiement entre Data Products, v1.1-v1.2) est interdit ;
* une référence ``$workspace.<autre>…`` dans parameter.yml, résolue AU DÉPLOIEMENT, créerait un
  ordre implicite : seule la référence au workspace Framework est admise (ADP et BDP).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .settings import RepoContext

CATEGORIES = ("framework", "adp", "bdp", "ucs")
NEEDS_FRAMEWORK = ("adp", "bdp")
DATA_KINDS = ("data", "technical", "execution")


def needs_framework_gate(ctx: "RepoContext") -> bool:
    """Porte Framework (pré-vol + historique ADO) : ADP et BDP uniquement (R1-R3, R5)."""
    return ctx.category in NEEDS_FRAMEWORK and ctx.framework_required is True


def rule_violations(ctx: "RepoContext") -> list[tuple[str, str]]:
    """Écarts à la matrice R1-R6, communs aux contrôles statiques et au déploiement."""
    errors: list[tuple[str, str]] = []
    category = ctx.category
    if category not in CATEGORIES:
        errors.append(("DEPS_CATEGORY", f"category doit valoir {', '.join(CATEGORIES)} (trouvé : {category})"))
        return errors
    if (ctx.role == "framework") != (category == "framework"):
        errors.append(("DEPS_CATEGORY_ROLE", f"role « {ctx.role} » incohérent avec category « {category} »"))
    if ctx.upstreams:
        names = ", ".join(str(u.get("repository", "?")) for u in ctx.upstreams)
        errors.append(("DEPS_UPSTREAM_FORBIDDEN",
                       f"upstreams interdit ({names}) : aucune dépendance de déploiement entre Data Products (R2-R5) ; "
                       "déclarer une consommation de données dans data_dependencies"))
    required = ctx.framework_required
    if category == "framework" and required is True:
        errors.append(("DEPS_FRAMEWORK_SELF", "le Framework ne dépend d'aucun dépôt : framework.required doit valoir false"))
    if category in NEEDS_FRAMEWORK and required is not True:
        errors.append(("DEPS_FRAMEWORK_REQUIRED",
                       f"{category.upper()} : le Framework est l'unique prérequis de déploiement (R1) : framework.required doit valoir true"))
    if category == "ucs" and (required is True or ctx.framework.get("ado_gate")):
        errors.append(("DEPS_UCS_INDEPENDENT",
                       "UCS n'a aucune dépendance de déploiement (R5) : framework.required false et aucun ado_gate"))
    for index, dep in enumerate(ctx.data_dependencies):
        where = f"data_dependencies[{index}]"
        if not isinstance(dep, dict) or not dep.get("repository"):
            errors.append(("DEPS_DATA_DEPENDENCY_FORMAT", f"{where} : objet avec repository, kind et description attendu"))
            continue
        if dep.get("kind") not in DATA_KINDS:
            errors.append(("DEPS_DATA_DEPENDENCY_FORMAT",
                           f"{where} : kind doit valoir {', '.join(DATA_KINDS)} (une dépendance de déploiement n'est pas admise, R6)"))
        if dep.get("blocking"):
            errors.append(("DEPS_DATA_DEPENDENCY_BLOCKING", f"{where} : une dépendance de données n'est jamais bloquante (R6)"))
    return errors
