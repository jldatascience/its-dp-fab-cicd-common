"""Chargement des fichiers de configuration d'un dépôt Fabric.

Trois fichiers, tous dans ``<CICD_ROOT>/deployment/`` (``DEPLOYMENT_DIR``, par défaut
``CICD/deployment/``) :

* ``config.yml``      : format fabric-cicd (lu tel quel par ``deploy_with_config``) ;
* ``parameter.yml``   : format fabric-cicd (règles ``key_value_replace``…) ;
* ``dependencies.yml``: format propre à KPMG (ce paquet), qui décrit ce que
  fabric-cicd ne sait pas exprimer : rôle du dépôt, dépendances Framework,
  connexions attendues, politique GUID, Variable Libraries.

Les clés d'environnement sont en MAJUSCULES (DEV, ITG, PPR, PRD) dans les trois
fichiers. fabric-cicd compare ces clés à l'environnement passé à
``deploy_with_config`` de façon sensible à la casse (vérifié dans le code de la
version 1.4.0) : une clé ``itg`` ne serait jamais appliquée pour ``ITG``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import ENVIRONMENTS

PLACEHOLDER_MARK = "__A_RELEVER__"

# Dossier CI/CD du dépôt consommateur : paramètre de template ``cicdRoot``, transmis par la
# variable d'environnement CICD_ROOT (absente ou vide : ``CICD``). Le dossier du socle
# (``commonCicdRoot``) n'est pas lu ici : ``tools/`` et ``registry/`` sont trouvés via ``__file__``.
# CICD_ROOT est lue une seule fois, au chargement du module : elle doit être posée avant le
# lancement du processus (``env:`` de chaque étape deploy.py dans les templates) ; la modifier
# ensuite dans ``os.environ`` n'a aucun effet.
CICD_ROOT_DEFAULT = "CICD"
CICD_ROOT = os.environ.get("CICD_ROOT", "").strip() or CICD_ROOT_DEFAULT

# Dossier des trois fichiers de configuration, relatif à la racine du dépôt (ou de l'artefact).
DEPLOYMENT_DIR = Path(CICD_ROOT) / "deployment"


class SettingsError(Exception):
    """Fichier de configuration absent, illisible ou incohérent."""


def load_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SettingsError(f"Fichier introuvable : {path}") from exc
    except yaml.YAMLError as exc:
        raise SettingsError(f"YAML invalide dans {path} : {exc}") from exc


def env_value(value: Any, env: str) -> Any:
    """Valeur d'un champ qui peut être scalaire ou indexé par environnement."""
    if isinstance(value, dict):
        if env in value:
            return value[env]
        for key in value:
            if str(key).upper() == "_ALL_":
                return value[key]
        return None
    return value


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and PLACEHOLDER_MARK in value


@dataclass
class RepoContext:
    """Vue chargée d'un dépôt (racine = dossier qui contient ``DEPLOYMENT_DIR``)."""

    root: Path
    config: dict
    dependencies: dict
    parameter: dict
    config_path: Path
    parameter_path: Optional[Path]
    items_root: Path
    warnings: list[str] = field(default_factory=list)

    # ---- config.yml -----------------------------------------------------
    @property
    def environments(self) -> list[str]:
        workspace = self.config.get("core", {}).get("workspace", {})
        if isinstance(workspace, dict):
            return [env for env in ENVIRONMENTS if env in workspace]
        return list(ENVIRONMENTS)

    def workspace_name(self, env: str) -> Optional[str]:
        return env_value(self.config.get("core", {}).get("workspace"), env)

    def item_types_in_scope(self, env: str) -> list[str]:
        value = env_value(self.config.get("core", {}).get("item_types_in_scope"), env)
        return list(value or [])

    def folder_exclude_regex(self, env: str) -> Optional[str]:
        return env_value(self.config.get("publish", {}).get("folder_exclude_regex"), env)

    def features(self) -> list[str]:
        features = self.config.get("features") or []
        if isinstance(features, dict):  # forme par environnement non utilisée ici
            merged: list[str] = []
            for values in features.values():
                merged.extend(values or [])
            return merged
        return list(features)

    # ---- dependencies.yml ----------------------------------------------
    @property
    def repository(self) -> str:
        return str(self.dependencies.get("repository", self.root.name))

    @property
    def role(self) -> str:
        return str(self.dependencies.get("role", "consumer"))

    @property
    def inventory_status(self) -> str:
        return str(self.dependencies.get("inventory_status", "pending"))

    @property
    def framework(self) -> dict:
        return self.dependencies.get("framework") or {}

    @property
    def framework_required(self) -> Optional[bool]:
        value = self.framework.get("required")
        return value if isinstance(value, bool) else None

    def framework_workspace(self, env: str) -> Optional[str]:
        return env_value(self.framework.get("workspace"), env)

    @property
    def framework_items(self) -> list[dict]:
        return list(self.framework.get("items") or [])

    @property
    def category(self) -> Optional[str]:
        """framework | adp | bdp | ucs (v1.3) : détermine les dépendances de déploiement admises."""
        value = self.dependencies.get("category")
        return str(value).lower() if value else None

    @property
    def upstreams(self) -> list[dict]:
        """Ancien mécanisme (v1.1-v1.2) : interdit depuis la v1.3 (DEPS_UPSTREAM_FORBIDDEN)."""
        return list(self.dependencies.get("upstreams") or [])

    @property
    def data_dependencies(self) -> list:
        """Dépendances de données / techniques / d'exécution : documentaires, jamais bloquantes (R6)."""
        return list(self.dependencies.get("data_dependencies") or [])

    def smoke_tests(self, env: str) -> list[dict]:
        """Items Fabric à exécuter après déploiement (tests fonctionnels), par environnement.

        Format : smoke_tests: [{type: DataPipeline, name: pl_x, environments: [ITG, PPR], timeout_minutes: 30}]
        Par défaut ITG et PPR uniquement : une exécution en PRD a des effets sur les données
        et doit être déclarée explicitement.
        """
        tests = []
        for entry in self.dependencies.get("smoke_tests") or []:
            envs = entry.get("environments") or ["ITG", "PPR"]
            if env in envs:
                tests.append(entry)
        return tests

    def connections(self, env: str) -> list[str]:
        names = []
        for entry in self.dependencies.get("connections") or []:
            name = env_value(entry.get("name"), env)
            if name:
                names.append(str(name))
        return names

    @property
    def variable_libraries(self) -> list[dict]:
        return list(self.dependencies.get("variable_libraries") or [])

    @property
    def guid_policy(self) -> dict:
        return self.dependencies.get("guid_policy") or {}

    @property
    def intentionally_excluded_types(self) -> list[str]:
        return list(self.dependencies.get("intentionally_excluded_item_types") or [])

    # ---- parameter.yml -------------------------------------------------
    def key_value_rules(self) -> list[dict]:
        return list(self.parameter.get("key_value_replace") or [])


def deployment_dir_error(root: Path) -> Optional[str]:
    """Message d'erreur si ``<root>/DEPLOYMENT_DIR`` n'existe pas, ``None`` sinon."""
    deployment = (Path(root) / DEPLOYMENT_DIR).resolve()
    if deployment.is_dir():
        return None
    raw = os.environ.get("CICD_ROOT")
    origin = f"CICD_ROOT={raw!r}" if raw and raw.strip() else f"CICD_ROOT non définie, défaut {CICD_ROOT_DEFAULT!r}"
    return (
        f"Dossier de déploiement introuvable : {deployment} ({origin}) "
        "– vérifier le paramètre cicdRoot / la variable CICD_ROOT"
    )


def _resolve_relative(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def repository_directory_error(root: Path, env: str = "ITG") -> Optional[str]:
    """Message d'erreur si ``repository_directory`` (résolu pour ``env``) est absent ou sans item Fabric.

    ``None`` si le dossier contient au moins un item (fichier ``.platform``) ou si ``config.yml``
    est illisible : ce dernier cas est signalé par les contrôles statiques.
    """
    try:
        ctx = load_repo(root, env)
    except SettingsError:
        return None
    value = env_value(ctx.config.get("core", {}).get("repository_directory"), env)
    origin = f"repository_directory={value!r}" if value else "repository_directory absent, défaut '../../fabric'"
    hint = f"({origin}, relatif à {ctx.config_path.parent}) – vérifier repository_directory dans {ctx.config_path.name}"
    if not ctx.items_root.is_dir():
        return f"Dossier des items Fabric introuvable : {ctx.items_root} {hint}"
    if next(ctx.items_root.rglob(".platform"), None) is None:
        return f"Aucun item Fabric (fichier .platform) sous {ctx.items_root} {hint}"
    return None


def load_repo(root: Path, env_for_paths: str = "ITG") -> RepoContext:
    """Charge les trois fichiers d'un dépôt.

    ``repository_directory`` et ``parameter`` sont résolus relativement au
    fichier config.yml, comme le fait fabric-cicd 1.4.0.
    """
    root = root.resolve()
    deployment = root / DEPLOYMENT_DIR
    config_path = deployment / "config.yml"
    config = load_yaml(config_path) or {}
    if not isinstance(config, dict):
        raise SettingsError(f"{config_path} doit contenir un objet YAML")

    dependencies_path = deployment / "dependencies.yml"
    dependencies = load_yaml(dependencies_path) or {}

    core = config.get("core") or {}
    repo_dir_value = env_value(core.get("repository_directory"), env_for_paths) or "../../fabric"
    items_root = _resolve_relative(config_path.parent, str(repo_dir_value))

    parameter_value = env_value(core.get("parameter"), env_for_paths)
    parameter_path: Optional[Path] = None
    parameter: dict = {}
    if parameter_value:
        parameter_path = _resolve_relative(config_path.parent, str(parameter_value))
        parameter = load_yaml(parameter_path) or {}
        if not isinstance(parameter, dict):
            raise SettingsError(f"{parameter_path} doit contenir un objet YAML")

    return RepoContext(
        root=root,
        config=config,
        dependencies=dependencies,
        parameter=parameter,
        config_path=config_path,
        parameter_path=parameter_path,
        items_root=items_root,
    )


GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"
