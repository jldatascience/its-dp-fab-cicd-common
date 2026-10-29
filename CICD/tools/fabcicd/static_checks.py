"""Contrôles statiques bloquants (CI et première étape de la CD).

Ces contrôles n'appellent aucune API : ils s'exécutent sur le dépôt (validation
de PR) ou sur l'artefact téléchargé (étape « Validate_Artifact » de la CD).

Codes d'erreur principaux
-------------------------
STRUCT_*    structure du dépôt / fichiers manquants
CONFIG_*    config.yml (validation fabric-cicd + règles KPMG)
PARAM_*     parameter.yml (validation fabric-cicd + règles KPMG)
RULE_*      règles key_value_replace (environnement, cible, effet réel) ; un GUID littéral
            dans replace_value est bloquant (RULE_LITERAL_GUID), sauf si dependencies.yml
            déclare guid_policy.allow_literal_ids: true (v1.3.1, constat informatif)
JSON_*      fichiers JSON d'items illisibles
TYPES_*     cohérence item_types_in_scope / items présents
GUID_*      GUID sandbox ou d'environnement codés en dur hors règle
SECRET_*    secret probable dans un fichier versionné
DEPS_*      dependencies.yml
FIND_REPLACE_*  règles find_replace (notebooks, SQL…) : effet réel, environnements
NOTEBOOK_*  notebooks non compilables
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from jsonpath_ng.ext import parse as jsonpath_parse

from . import ENVIRONMENTS
from .findings import Findings
from .items import ItemDiscoveryError, RepoItem, discover_items, item_files
from .registry import Registry
from .settings import DEPLOYMENT_DIR, GUID_RE, ZERO_GUID, RepoContext, SettingsError, is_placeholder, load_repo
from .variables import VariableSyntaxError, parse_variable, rule_value_for_env

STEP = "static"

TEXT_SUFFIXES = {".json", ".py", ".yml", ".yaml", ".tmdl", ".sql", ".kql", ".txt", ".md", ".ps1", ".sh", ".ipynb", ".pq", ".m"}

SECRET_PATTERNS = [
    ("SECRET_ENTRA_CLIENT_SECRET", re.compile(r"(?<![A-Za-z0-9_~.-])[A-Za-z0-9_~.-]{3}\dQ~[A-Za-z0-9_~.-]{31,34}(?![A-Za-z0-9_~.-])")),
    ("SECRET_GITHUB_TOKEN", re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("SECRET_PRIVATE_KEY", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("SECRET_STORAGE_KEY", re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}")),
    ("SECRET_ASSIGNMENT", re.compile(r"(?i)(client_secret|password|pwd|secret)\s*[:=]\s*[\"'](?![\s\"'$({<])[^\"'\s]{12,}[\"']")),
]


@dataclass
class GuidOccurrence:
    item: str
    item_type: str
    file: str
    location: str
    guid: str
    classification: str
    covered_envs: list[str] = field(default_factory=list)


@dataclass
class StaticResult:
    findings: Findings
    occurrences: list[GuidOccurrence]
    rule_matches: dict  # (env, index) -> nombre de correspondances


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _as_list(value) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _rule_targets(rule: dict, items: list[RepoItem], items_root: Path) -> list[RepoItem]:
    types = _as_list(rule.get("item_type"))
    names = _as_list(rule.get("item_name"))
    paths = _as_list(rule.get("file_path"))
    selected = []
    for item in items:
        if item.excluded:
            continue
        if types is not None and item.item_type not in types:
            continue
        if names is not None and item.name not in names:
            continue
        if paths is not None:
            files = {f.relative_to(items_root).as_posix() for f in item_files(item)}
            if not any(any(Path(f).match(p.lstrip("/")) or f == p.lstrip("/") for f in files) for p in paths):
                continue
        selected.append(item)
    return selected


def _load_structured(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    return None


def _iter_text_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in (".platform",):
            yield path


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------

def check_structure(root: Path, findings: Findings) -> bool:
    ok = True
    for rel in ((DEPLOYMENT_DIR / "config.yml").as_posix(), (DEPLOYMENT_DIR / "dependencies.yml").as_posix()):
        if not (root / rel).is_file():
            findings.error("STRUCT_MISSING_FILE", f"Fichier obligatoire absent : {rel}")
            ok = False
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for path in sorted(workflows.glob("*.y*ml")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?m)^\s*(stages|extends|resources)\s*:", text) or "Fabric-CD-Secrets" in text:
                findings.error(
                    "STRUCT_LEGACY_PIPELINE",
                    "ancien pipeline Azure DevOps encore présent dans .github/workflows : une seconde CD pourrait "
                    "être déclenchée par la même CI (supprimer le fichier et repointer la définition ADO vers CD.yml)",
                    path.relative_to(root).as_posix(),
                )
    # Ancien script : à la racine (disposition d'origine) ou sous CICD/deployment/.
    for legacy in (root / "deployment" / "deploy.py", root / DEPLOYMENT_DIR / "deploy.py"):
        if not legacy.is_file():
            continue
        rel = legacy.relative_to(root).as_posix()
        text = legacy.read_text(encoding="utf-8", errors="replace")
        if GUID_RE.search(text):
            findings.error(
                "STRUCT_LEGACY_DEPLOY_HARDCODED_IDS",
                f"{rel} contient des GUID en dur (ancien script) : le supprimer, "
                "le déploiement passe par le socle commun et config.yml",
                rel,
            )
    return ok


def check_config(ctx: RepoContext, findings: Findings) -> None:
    from fabric_cicd._common._config_utils import load_config_file  # import tardif (lourd)

    core = ctx.config.get("core") or {}
    workspace = core.get("workspace")
    if "workspace_id" in core:
        findings.error(
            "CONFIG_WORKSPACE_ID_FORBIDDEN",
            "core.workspace_id interdit : désigner les workspaces par leur nom exact (core.workspace)",
            str(ctx.config_path),
        )
    if not isinstance(workspace, dict):
        findings.error("CONFIG_WORKSPACE_MAPPING", "core.workspace doit être un dictionnaire DEV/ITG/PPR/PRD", str(ctx.config_path))
        return
    missing = [env for env in ENVIRONMENTS if env not in workspace]
    unknown = [key for key in workspace if key not in ENVIRONMENTS]
    if missing:
        findings.error("CONFIG_ENV_MISSING", f"Environnements absents de core.workspace : {', '.join(missing)}", str(ctx.config_path))
    if unknown:
        findings.error(
            "CONFIG_ENV_UNKNOWN",
            f"Clés d'environnement non reconnues (casse comprise) : {', '.join(map(str, unknown))} — attendu DEV/ITG/PPR/PRD",
            str(ctx.config_path),
        )
    for env in ENVIRONMENTS:
        name = workspace.get(env)
        if is_placeholder(name):
            # Audit 20/10/2026 : bloquant dès que l'inventaire est déclaré validé (avant : avertissement seul).
            report = findings.error if ctx.inventory_status == "validated" else findings.warning
            report("CONFIG_WORKSPACE_PLACEHOLDER", f"{env} : nom de workspace à relever ({name}) — le déploiement sera refusé", str(ctx.config_path))
    if any(ctx.folder_exclude_regex(env) for env in ENVIRONMENTS):
        flags = set(ctx.features())
        required = {"enable_experimental_features", "enable_exclude_folder"}
        if not required.issubset(flags):
            findings.error(
                "CONFIG_EXCLUDE_WITHOUT_FLAGS",
                "folder_exclude_regex est ignoré sans les feature flags enable_experimental_features et enable_exclude_folder",
                str(ctx.config_path),
            )
    publish_skip = (ctx.config.get("publish") or {}).get("skip")
    for env in ("ITG", "PPR", "PRD"):
        value = publish_skip.get(env) if isinstance(publish_skip, dict) else publish_skip
        if value is True:
            findings.error(
                "CONFIG_PUBLISH_SKIPPED",
                f"publish.skip.{env} = true : le stage {env} serait vert sans rien publier",
                str(ctx.config_path),
            )
    unpublish = (ctx.config.get("unpublish") or {}).get("skip")
    for env in ("PPR", "PRD"):
        value = unpublish.get(env) if isinstance(unpublish, dict) else unpublish
        if value is not True:
            findings.error(
                "CONFIG_UNPUBLISH_UPPER_ENV",
                f"unpublish.skip.{env} doit valoir true : aucune suppression automatique d'items en {env}",
                str(ctx.config_path),
            )
    for env in ENVIRONMENTS:
        if env not in workspace:
            continue
        try:
            load_config_file(str(ctx.config_path), env)
        except Exception as exc:  # ConfigValidationError, InputError…
            findings.error("CONFIG_FABRIC_CICD_INVALID", f"{env} : {exc}", str(ctx.config_path))


def check_parameter_fabric(ctx: RepoContext, items_root: Path, findings: Findings) -> None:
    if not ctx.parameter_path:
        return
    from fabric_cicd._parameter._parameter import Parameter

    for env in ENVIRONMENTS:
        try:
            parameter = Parameter(
                repository_directory=items_root,
                item_type_in_scope=ctx.item_types_in_scope(env),
                environment=env,
                parameter_file_path=str(ctx.parameter_path),
            )
            valid = parameter._validate_parameter_file()  # API interne, version épinglée
        except Exception as exc:
            findings.error("PARAM_FABRIC_CICD_ERROR", f"{env} : {exc}", str(ctx.parameter_path))
            continue
        if not valid:
            findings.error("PARAM_FABRIC_CICD_INVALID", f"{env} : parameter.yml refusé par fabric-cicd", str(ctx.parameter_path))


def check_json_files(items: list[RepoItem], items_root: Path, findings: Findings) -> None:
    for item in items:
        if item.excluded:
            continue
        for path in item_files(item):
            if path.suffix == ".json":
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    findings.error("JSON_INVALID", f"JSON invalide : {exc}", path.relative_to(items_root).as_posix())


def check_item_types(ctx: RepoContext, items: list[RepoItem], findings: Findings) -> None:
    present = sorted({i.item_type for i in items if not i.excluded})
    excluded_types = set(ctx.intentionally_excluded_types)
    for env in ENVIRONMENTS:
        scope = set(ctx.item_types_in_scope(env))
        if not scope:
            findings.error("TYPES_SCOPE_EMPTY", f"{env} : item_types_in_scope vide ou absent")
            continue
        for item_type in present:
            if item_type not in scope and item_type not in excluded_types:
                findings.error(
                    "TYPES_SILENTLY_EXCLUDED",
                    f"{env} : le type {item_type} est présent dans le dépôt mais absent de item_types_in_scope "
                    "(il serait exclu sans message). L'ajouter, ou le déclarer dans intentionally_excluded_item_types",
                )
        for item_type in sorted(scope - set(present)):
            findings.warning("TYPES_DECLARED_ABSENT", f"{env} : {item_type} déclaré dans item_types_in_scope mais aucun item de ce type")


def check_rules(
    ctx: RepoContext, items: list[RepoItem], items_root: Path, findings: Findings
) -> tuple[dict, dict]:
    """Contrôle chaque règle key_value_replace pour chaque environnement.

    Retourne (couverture, compte) :
    couverture[(fichier, chemin_json)] = ensemble des environnements couverts
    compte[(env, index)] = nombre de clés réellement atteintes par la règle
    """
    coverage: dict[tuple[str, str], set[str]] = {}
    counts: dict[tuple[str, int], int] = {}
    repo_items = {(i.item_type, i.name) for i in items if not i.excluded}
    env_flag = "enable_environment_variable_replacement" in set(ctx.features())

    for index, rule in enumerate(ctx.key_value_rules()):
        where = f"parameter.yml key_value_replace[{index}]"
        find_key = rule.get("find_key")
        try:
            expression = jsonpath_parse(find_key)
        except Exception as exc:
            findings.error("RULE_FIND_KEY_INVALID", f"find_key invalide ({find_key}) : {exc}", where)
            continue
        if rule.get("item_type") is None and rule.get("item_name") is None and rule.get("file_path") is None:
            findings.warning("RULE_NO_FILTER", "règle sans filtre item_type/item_name/file_path : appliquée à tous les fichiers", where)

        targets = _rule_targets(rule, items, items_root)
        if not targets:
            findings.error(
                "RULE_TARGET_ABSENT",
                f"aucun item publié ne correspond aux filtres (item_type={rule.get('item_type')}, item_name={rule.get('item_name')})",
                where,
            )

        matched_paths: list[tuple[str, str]] = []
        matched_items: set[str] = set()
        for item in targets:
            for path in item_files(item):
                if path.suffix != ".json":
                    continue
                try:
                    data = _load_structured(path)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                rel = path.relative_to(items_root).as_posix()
                for match in expression.find(data):
                    matched_paths.append((rel, str(match.full_path)))
                    matched_items.add(item.key)

        for env in ENVIRONMENTS:
            value = rule_value_for_env(rule, env)
            if value is None:
                findings.error(
                    "RULE_ENV_MISSING",
                    f"{env} : aucune valeur de remplacement (ni {env}, ni _ALL_ seul) — la référence ne serait pas remplacée",
                    where,
                )
                continue
            counts[(env, index)] = len(matched_paths)
            for key in matched_paths:
                coverage.setdefault(key, set()).add(env)
            try:
                variable = parse_variable(value)
            except VariableSyntaxError as exc:
                findings.error("RULE_VARIABLE_SYNTAX", f"{env} : {exc}", where)
                continue
            if variable.kind == "target_item" and (variable.item_type, variable.item_name) not in repo_items:
                findings.error(
                    "RULE_TARGET_ITEM_NOT_IN_REPO",
                    f"{env} : {value} vise un item absent du périmètre publié de ce dépôt",
                    where,
                )
            if variable.kind == "env" and not env_flag:
                findings.error("RULE_ENV_VAR_WITHOUT_FLAG", f"{env} : {value} exige le feature flag enable_environment_variable_replacement", where)
            if variable.kind in ("cross_workspace", "cross_item") and is_placeholder(variable.workspace_name):
                findings.warning("RULE_PLACEHOLDER", f"{env} : nom de workspace à relever dans {value}", where)
            if variable.kind == "literal" and GUID_RE.fullmatch(str(value) or ""):
                if ctx.guid_policy.get("allow_literal_ids") is True:
                    # Décision du 22/10/2026 : IDs en clair admis dans parameter.yml (solution temporaire).
                    findings.info("RULE_LITERAL_GUID_ALLOWED", f"{env} : ID littéral admis par guid_policy.allow_literal_ids ({value})", where)
                else:
                    findings.error("RULE_LITERAL_GUID", f"{env} : GUID littéral interdit dans replace_value ({value})", where)

        if targets and not matched_paths:
            findings.error(
                "RULE_NO_EFFECT",
                f"find_key ne correspond à aucune clé dans les items ciblés : la règle ne ferait rien, sans message ({find_key}). "
                "Si l'activité est imbriquée (IfCondition, ForEach, Until…), utiliser $..*[?(@.name==\"…\")]",
                where,
            )
        elif len(targets) > 1 and len(matched_items) < len(targets):
            unmatched = sorted(i.key for i in targets if i.key not in matched_items)
            findings.warning(
                "RULE_PARTIAL_EFFECT",
                f"la règle n'atteint aucune clé dans {len(unmatched)} des {len(targets)} items ciblés : {', '.join(unmatched[:5])}",
                where,
            )
    check_find_replace(ctx, items, items_root, findings, coverage)
    return coverage, counts


def _find_replace_matcher(rule: dict):
    """Fonction texte -> liste des sous-chaînes remplacées par fabric-cicd (groupe 1 si regex)."""
    find_value = str(rule.get("find_value") or "")
    if str(rule.get("is_regex", "")).lower() == "true":
        pattern = re.compile(find_value)
        return lambda text: [m.group(1) if m.groups() else m.group(0) for m in pattern.finditer(text)]
    return lambda text: [find_value] * text.count(find_value) if find_value else []


def check_find_replace(ctx: RepoContext, items: list[RepoItem], items_root: Path, findings: Findings, coverage: dict) -> None:
    """Règles find_replace (notebooks, SQL, TMDL…) : effet réel et couverture des GUID remplacés.

    Couverture enregistrée par (fichier, emplacement|GUID) pour les fichiers texte et
    (fichier, chemin JSON) pour les JSON, lue par scan_guids.
    """
    for index, rule in enumerate(ctx.parameter.get("find_replace") or []):
        where = f"parameter.yml find_replace[{index}]"
        try:
            matcher = _find_replace_matcher(rule)
        except re.error as exc:
            findings.error("FIND_REPLACE_REGEX", f"find_value invalide : {exc}", where)
            continue
        targets = _rule_targets(rule, items, items_root)
        if not targets:
            findings.error("FIND_REPLACE_TARGET_ABSENT", "aucun item publié ne correspond aux filtres", where)
            continue
        envs = []
        for env in ENVIRONMENTS:
            if rule_value_for_env(rule, env) is None:
                findings.error("FIND_REPLACE_ENV_MISSING", f"{env} : aucune valeur de remplacement (ni {env}, ni _ALL_ seul)", where)
            else:
                envs.append(env)
        hits = 0
        for item in targets:
            for path in item_files(item):
                if path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                rel = path.relative_to(items_root).as_posix()
                if path.suffix == ".json":
                    try:
                        data = _load_structured(path)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    for match in jsonpath_parse("$..*").find(data):
                        if isinstance(match.value, str) and any(GUID_RE.search(s or "") for s in matcher(match.value)):
                            hits += 1
                            coverage.setdefault((rel, str(match.full_path)), set()).update(envs)
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    for replaced in matcher(line):
                        hits += 1
                        for guid in GUID_RE.findall(replaced or ""):
                            coverage.setdefault((rel, f"ligne {number}|{guid.lower()}"), set()).update(envs)
        if not hits:
            findings.error("FIND_REPLACE_NO_EFFECT", "find_value ne correspond à rien dans les items ciblés : règle sans effet", where)


def check_notebooks(items: list[RepoItem], items_root: Path, findings: Findings) -> None:
    """Compilation Python des notebooks (cellules magiques %… et !… neutralisées)."""
    for item in items:
        if item.excluded or item.item_type != "Notebook":
            continue
        for path in item_files(item):
            if path.name != "notebook-content.py":
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            lines = ["" if re.match(r"\s*[%!]", line) else line for line in source.splitlines()]
            try:
                compile("\n".join(lines), str(path), "exec")
            except SyntaxError as exc:
                findings.error("NOTEBOOK_SYNTAX", f"erreur de syntaxe Python ligne {exc.lineno} : {exc.msg}",
                               path.relative_to(items_root).as_posix())


def scan_guids(
    ctx: RepoContext,
    items: list[RepoItem],
    items_root: Path,
    coverage: dict,
    registry: Registry,
    findings: Findings,
) -> list[GuidOccurrence]:
    policy = ctx.guid_policy
    forbidden_prefixes = [str(p).lower() for p in policy.get("forbidden_prefixes") or []]
    allowed = [(str(a.get("value", "")).lower(), str(a.get("reason", ""))) for a in policy.get("allowed") or []]
    # Audit 20/10/2026 : un GUID inconnu devient bloquant dès que l'inventaire est validé.
    strict = bool(policy.get("strict_unknown", False)) or ctx.inventory_status == "validated"
    occurrences: list[GuidOccurrence] = []

    def classify(guid: str) -> tuple[str, str]:
        low = guid.lower()
        if low == ZERO_GUID:
            return "zero", ""
        for prefix in forbidden_prefixes:
            if low.startswith(prefix):
                return "forbidden", f"préfixe interdit {prefix}"
        for value, reason in allowed:
            if value and (low == value or low.startswith(value)):
                return "allowed", reason
        known = registry.classify(low)
        if known:
            return ("forbidden" if known.forbidden_in_items else "allowed"), f"{known.category}: {known.label}"
        return "unknown", ""

    for item in items:
        if item.excluded:
            continue
        for path in item_files(item):
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = path.relative_to(items_root).as_posix()
            hits: list[tuple[str, str]] = []
            if path.suffix == ".json":
                try:
                    data = _load_structured(path)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                for match in jsonpath_parse("$..*").find(data):
                    if isinstance(match.value, str):
                        for guid in GUID_RE.findall(match.value):
                            hits.append((str(match.full_path), guid))
            else:
                for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    for guid in GUID_RE.findall(line):
                        hits.append((f"ligne {number}", guid))
            for location, guid in hits:
                label, reason = classify(guid)
                if label == "zero":
                    continue
                covered = sorted(coverage.get((rel, location), set()) | coverage.get((rel, f"{location}|{guid.lower()}"), set()))
                fully_covered = set(covered) == set(ENVIRONMENTS)
                occurrence = GuidOccurrence(item.key, item.item_type, rel, location, guid.lower(), label, covered)
                occurrences.append(occurrence)
                if fully_covered:
                    continue
                if covered:
                    findings.error(
                        "GUID_PARTIALLY_COVERED",
                        f"{guid} remplacé seulement pour {', '.join(covered)}",
                        f"{rel} :: {location}",
                    )
                elif label == "forbidden":
                    findings.error("GUID_HARDCODED", f"{guid} en dur hors règle parameter.yml ({reason})", f"{rel} :: {location}")
                elif label == "unknown":
                    message = f"{guid} non couvert par parameter.yml et absent du registre : à qualifier"
                    if strict:
                        findings.error("GUID_UNKNOWN", message, f"{rel} :: {location}")
                    else:
                        findings.warning("GUID_UNKNOWN", message, f"{rel} :: {location}")
    return occurrences


def scan_secrets(root: Path, findings: Findings) -> None:
    for path in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            for code, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    # La valeur n'est jamais recopiée dans le journal.
                    findings.error(code, "secret probable dans un fichier versionné (valeur masquée)", f"{rel}:{number}")


def check_dependencies(ctx: RepoContext, findings: Findings) -> None:
    deps = ctx.dependencies
    if deps.get("schema_version") != 1:
        findings.error("DEPS_SCHEMA", "dependencies.yml : schema_version: 1 attendu")
    if ctx.role not in ("framework", "consumer"):
        findings.error("DEPS_ROLE", f"role doit valoir framework ou consumer (trouvé : {ctx.role})")
    if ctx.inventory_status not in ("validated", "pending"):
        findings.error("DEPS_STATUS", f"inventory_status doit valoir validated ou pending (trouvé : {ctx.inventory_status})")
    if ctx.inventory_status == "pending":
        findings.warning("DEPS_PENDING", "inventaire non validé : la CD refusera tout déploiement (mode validation seul)")
    from .dependencies import NEEDS_FRAMEWORK, rule_violations
    for code, message in rule_violations(ctx):
        findings.error(code, message)
    if ctx.category in NEEDS_FRAMEWORK and ctx.framework_required is True:
        for env in ENVIRONMENTS:
            if not ctx.framework_workspace(env):
                findings.error("DEPS_FRAMEWORK_WORKSPACE", f"framework.workspace.{env} manquant")
        if not ctx.framework_items:
            # Les items du Framework réellement utilisés se connaissent à l'inventaire.
            report = findings.error if ctx.inventory_status == "validated" else findings.warning
            report("DEPS_FRAMEWORK_ITEMS", "framework.items vide : préciser les items du Framework utilisés (vérifiés au pré-vol)")
    # Cohérence : chaque item Framework visé par parameter.yml doit être déclaré ; aucune
    # référence résolue au déploiement vers un autre workspace que le Framework (R2-R5).
    declared = {(i.get("type"), i.get("name")) for i in ctx.framework_items}
    for env in ENVIRONMENTS:
        fw_name = ctx.framework_workspace(env) if ctx.category in NEEDS_FRAMEWORK else None
        for rule in ctx.key_value_rules():
            try:
                variable = parse_variable(rule_value_for_env(rule, env) or "")
            except VariableSyntaxError:
                continue
            if variable.kind in ("cross_workspace", "cross_item") and variable.workspace_name != fw_name:
                findings.error(
                    "DEPS_CROSS_WORKSPACE_FORBIDDEN",
                    f"{env} : parameter.yml référence le workspace « {variable.workspace_name} » au déploiement : "
                    + ("seul le workspace Framework est admis (R1-R3)" if ctx.category in NEEDS_FRAMEWORK
                       else f"aucune référence inter-workspace n'est admise pour la catégorie {ctx.category} (R5)")
                    + " ; une consommation de données se résout à l'exécution et se déclare dans data_dependencies (R6)",
                )
        for rule in ctx.key_value_rules():
            value = rule_value_for_env(rule, env)
            try:
                variable = parse_variable(value) if value else None
            except VariableSyntaxError:
                continue
            if variable and variable.kind == "cross_item" and variable.workspace_name == fw_name:
                if (variable.item_type, variable.item_name) not in declared:
                    findings.error(
                        "DEPS_FRAMEWORK_ITEM_UNDECLARED",
                        f"{env} : parameter.yml référence {variable.item_type} {variable.item_name} du Framework, "
                        "absent de framework.items (le pré-vol ne le vérifierait pas)",
                    )


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def run_static_checks(root: Path, registry: Optional[Registry] = None) -> StaticResult:
    findings = Findings(STEP)
    registry = registry or Registry([])
    if not check_structure(root, findings):
        return StaticResult(findings, [], {})
    try:
        ctx = load_repo(root)
    except SettingsError as exc:
        findings.error("STRUCT_UNREADABLE", str(exc))
        return StaticResult(findings, [], {})

    check_config(ctx, findings)
    check_dependencies(ctx, findings)
    scan_secrets(root, findings)

    exclude = ctx.folder_exclude_regex("ITG")
    for env in ENVIRONMENTS:
        if ctx.folder_exclude_regex(env) != exclude:
            findings.error("CONFIG_EXCLUDE_PER_ENV", "folder_exclude_regex doit être identique pour les 4 environnements (même artefact)")
            break
    try:
        items = discover_items(ctx.items_root, exclude)
    except ItemDiscoveryError as exc:
        findings.error("STRUCT_ITEMS", str(exc))
        return StaticResult(findings, [], {})
    if not [i for i in items if not i.excluded]:
        findings.error(
            "STRUCT_NO_ITEMS",
            f"aucun item publiable sous {ctx.items_root} : repository_directory incorrect ou artefact incomplet "
            "(fabric-cicd « réussirait » sans rien publier)",
        )
    names = {}
    for item in items:
        if item.excluded:
            continue
        key = (item.item_type, item.name)
        if key in names:
            findings.error("STRUCT_DUPLICATE_ITEM", f"deux items {item.item_type} nommés {item.name}", item.directory.name)
        names[key] = item

    check_item_types(ctx, items, findings)
    # Un item d'un type hors périmètre n'est pas publié : il sort des contrôles
    # suivants (le contrôle ci-dessus garantit que cette exclusion est voulue).
    in_scope = set()
    for env in ENVIRONMENTS:
        in_scope |= set(ctx.item_types_in_scope(env))
    for item in items:
        if item.item_type not in in_scope:
            item.excluded = True
    check_json_files(items, ctx.items_root, findings)
    check_notebooks(items, ctx.items_root, findings)
    check_parameter_fabric(ctx, ctx.items_root, findings)
    coverage, counts = check_rules(ctx, items, ctx.items_root, findings)
    occurrences = scan_guids(ctx, items, ctx.items_root, coverage, registry, findings)
    return StaticResult(findings, occurrences, counts)
