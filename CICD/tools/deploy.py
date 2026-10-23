#!/usr/bin/env python3
"""Point d'entrée unique de la chaîne CI/CD Fabric (socle commun).

Remplace les 11 copies de ``deployment/deploy.py`` qui contenaient chacune les
IDs de workspace, de capacité et les client IDs en dur.

Sous-commandes
--------------
validate  Contrôles statiques bloquants (CI, PR, première étape de la CD).
deploy    Pré-vol -> porte Framework -> publication fabric-cicd -> vérification.
          ``--mode validate`` s'arrête avant la publication (aucune écriture).
verify    Vérification post-déploiement seule (lecture seule).
manifest  Empreinte SHA-256 de l'artefact promu (création / contrôle).
route     Détermine l'environnement cible d'un run selon la branche source (v1.2) :
          develop → ITG, release/* → PPR, main → PRD.
check-branch  Refuse le déploiement d'un artefact construit depuis une branche non autorisée.
scan      Inventaire CSV des GUID trouvés dans les items (audit des dépôts).
lookup    Retrouve le nom exact de workspaces à partir de leurs IDs (lecture seule).

Codes de sortie
---------------
0 succès · 1 contrôle bloquant en échec · 2 échec de publication · 3 refus de politique
(inventaire non validé, valeur à relever) · 4 erreur d'usage ou d'authentification.

fabric-cicd n'offre pas de mode « dry-run ». ``--mode validate`` exécute tout ce
qui est possible sans écrire : contrôles statiques, authentification, pré-vol et
résolution de toutes les références inter-workspaces dans l'environnement cible.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from CICD.tools.fabcicd import log
from fabcicd import ENVIRONMENTS  # noqa: E402
from CICD.tools.fabcicd.findings import Findings  # noqa: E402
from CICD.tools.fabcicd.items import discover_items  # noqa: E402
from CICD.tools.fabcicd.registry import Registry, default_registry_path  # noqa: E402
from CICD.tools.fabcicd.report import Report  # noqa: E402
from CICD.tools.fabcicd.settings import SettingsError, load_repo  # noqa: E402
from CICD.tools.fabcicd.static_checks import run_static_checks  # noqa: E402

EXIT_OK, EXIT_CHECKS, EXIT_DEPLOY, EXIT_POLICY, EXIT_USAGE = 0, 1, 2, 3, 4


def build_credential(mode: str):
    """Identité de déploiement.

    cli    : AzureCliCredential, à utiliser dans une tâche AzureCLI@2 dont la
             service connection utilise la fédération d'identité (aucun secret).
    secret : ClientSecretCredential à partir de FABRIC_TENANT_ID, FABRIC_CLIENT_ID,
             FABRIC_CLIENT_SECRET (transitoire, secret lu dans Key Vault).
    """
    if mode == "cli":
        from azure.identity import AzureCliCredential
        return AzureCliCredential()
    if mode == "secret":
        from azure.identity import ClientSecretCredential
        values = {}
        for name in ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET"):
            value = os.getenv(name, "").strip()
            if not value:
                raise RuntimeError(f"variable {name} absente ou vide")
            if value.startswith("$(") and value.endswith(")"):
                # Azure DevOps laisse la macro telle quelle quand la variable n'existe pas :
                # secret absent du coffre, non lu par AzureKeyVault@2 ou mal nommé.
                raise RuntimeError(f"variable {name} non résolue ({value}) : secret absent ou non lu dans le coffre")
            values[name] = value
        for name in ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID"):
            uuid.UUID(values[name])
        log.info("auth", f"Service Principal {values['FABRIC_CLIENT_ID']} (secret lu depuis l'environnement, non affiché)")
        return ClientSecretCredential(values["FABRIC_TENANT_ID"], values["FABRIC_CLIENT_ID"], values["FABRIC_CLIENT_SECRET"])
    raise RuntimeError(f"mode d'authentification inconnu : {mode}")


def _registry(args) -> Registry:
    path = Path(args.registry) if getattr(args, "registry", None) else default_registry_path()
    return Registry.load(path)


def cmd_validate(args) -> int:
    root = Path(args.repo)
    with log.group(f"Contrôles statiques — {root.name}"):
        result = run_static_checks(root, _registry(args))
    if args.inventory_csv:
        _write_inventory(result.occurrences, Path(args.inventory_csv), root.name)
    findings = result.findings
    log.info("static", f"{len(findings.errors)} erreur(s), {len(findings.warnings)} avertissement(s)")
    return EXIT_OK if findings.ok else EXIT_CHECKS


def _write_inventory(occurrences, path: Path, repository: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["repository", "item", "item_type", "file", "location", "guid", "classification", "covered_envs"])
        for o in occurrences:
            writer.writerow([repository, o.item, o.item_type, o.file, o.location, o.guid, o.classification, ",".join(o.covered_envs)])
    log.info("scan", f"inventaire écrit : {path} ({len(occurrences)} occurrence(s))")


def cmd_scan(args) -> int:
    root = Path(args.repo)
    result = run_static_checks(root, _registry(args))
    _write_inventory(result.occurrences, Path(args.out), root.name)
    return EXIT_OK


def cmd_deploy(args, verify_only: bool = False) -> int:
    from CICD.tools.fabcicd.fabric_api import FabricClient
    from CICD.tools.fabcicd.preflight import run_preflight
    from CICD.tools.fabcicd.verify import run_verify

    env = args.env.upper()
    if env not in ENVIRONMENTS:
        log.error("usage", f"environnement inconnu : {args.env}")
        return EXIT_USAGE
    root = Path(args.repo)
    try:
        ctx = load_repo(root, env)
        items = discover_items(ctx.items_root, ctx.folder_exclude_regex(env))
    except (SettingsError, Exception) as exc:
        log.error("load", str(exc))
        return EXIT_CHECKS

    mode = "verify" if verify_only else args.mode
    expected_role = getattr(args, "expected_role", None)
    if expected_role and expected_role != ctx.role:
        log.error("load", f"rôle incohérent : CD.yml déclare « {expected_role} », dependencies.yml « {ctx.role} »")
        return EXIT_CHECKS
    report = Report(ctx.repository, env, mode)
    report_dir = Path(args.report_dir)

    from CICD.tools.fabcicd.dependencies import rule_violations
    violations = rule_violations(ctx)

    def finish(status: str, code: int) -> int:
        report.finish(status)
        report.write(report_dir)
        return code

    # Matrice de dépendances v1.3 (R1-R6) : rejouée ici, même si la CI l'a déjà contrôlée.
    if violations:
        rules = Findings("dependencies")
        for code, message in violations:
            rules.error(code, message)
        report.add_step("dependencies", rules)
        return finish("refused", EXIT_CHECKS)

    # Politique : pas de publication tant que l'inventaire du dépôt n'est pas validé.
    if mode == "deploy" and ctx.inventory_status != "validated":
        policy = Findings("policy")
        policy.error("POLICY_INVENTORY_PENDING", "inventory_status = pending dans dependencies.yml : publication refusée (mode validate uniquement)")
        report.add_step("policy", policy)
        return finish("refused", EXIT_POLICY)

    skip = (ctx.config.get("publish") or {}).get("skip")
    skip_env = skip.get(env) if isinstance(skip, dict) else skip
    if mode == "deploy" and skip_env is True:
        policy = Findings("policy")
        policy.error("POLICY_PUBLISH_SKIPPED", f"publish.skip.{env} = true : la publication serait ignorée, le stage ne peut pas réussir")
        report.add_step("policy", policy)
        return finish("refused", EXIT_POLICY)

    try:
        credential = build_credential(args.auth)
        from CICD.tools.fabcicd.fabric_api import FABRIC_SCOPE
        credential.get_token(FABRIC_SCOPE)  # échec d'authentification détecté ici (code 4), pas au pré-vol
        client = FabricClient(credential)
    except Exception as exc:
        log.error("auth", f"authentification impossible : {exc}")
        report.add_step("auth", status="failed", extra={"error": str(exc)})
        return finish("failed", EXIT_USAGE)

    with log.group(f"Pré-vol {ctx.repository} → {env}"):
        try:
            pre = run_preflight(ctx, env, client)
        except Exception as exc:  # réseau, délai, erreur inattendue : échec explicite avec rapport
            log.error("preflight", f"pré-vol interrompu : {type(exc).__name__}: {exc}")
            report.add_step("preflight", status="failed", extra={"error": f"{type(exc).__name__}: {exc}"[:2000]})
            return finish("failed", EXIT_CHECKS)
    report.add_step("preflight", pre.findings)
    report.resolutions = [r.__dict__ for r in pre.resolutions.values()]
    if not pre.findings.ok:
        return finish("failed", EXIT_CHECKS)

    if not verify_only and args.framework_gate == "ado":
        from CICD.tools.fabcicd.ado_gate import AdoClient, check_framework_deployed
        from CICD.tools.fabcicd.branches import gate_branch
        from CICD.tools.fabcicd.dependencies import needs_framework_gate
        # v1.3 : une seule porte, le Framework, pour les ADP et les BDP (R1-R4). Aucune porte
        # pour le Framework ni pour UCS (R5) ; jamais de porte entre Data Products (R2, R3).
        # La porte vérifie la DISPONIBILITÉ (dernier déploiement réussi sur l'environnement) :
        # un Framework déjà disponible n'a pas besoin d'être redéployé.
        gates = []
        if needs_framework_gate(ctx):
            cfg = ctx.framework.get("ado_gate") or {}
            gates.append(("Framework", args.framework_pipeline or cfg.get("pipeline_name"), cfg))
        else:
            info = Findings("gate")
            info.info("GATE_NOT_APPLICABLE", f"catégorie {ctx.category} : aucune dépendance de déploiement (matrice v1.3)")
            report.add_step("gate", info)
        for label, pipeline, cfg in gates:
            step = f"gate-{label}"
            with log.group(f"Porte {label} {env}"):
                try:
                    gate = check_framework_deployed(
                        AdoClient.from_environment(), pipeline, env,
                        branch=gate_branch(cfg.get("branch"), env),
                        stage_prefix=cfg.get("stage_prefix", "Deploy_"),
                    )
                except Exception as exc:
                    gate_findings = Findings(step)
                    gate_findings.error("GATE_API", f"{label} : historique Azure DevOps illisible : {exc}")
                    report.add_step(step, gate_findings)
                    return finish("failed", EXIT_CHECKS)
            report.add_step(step, gate.findings, extra={"pipeline": pipeline, "framework_run": gate.framework_build_number, "framework_commit": gate.framework_commit})
            if not gate.findings.ok:
                return finish("failed", EXIT_CHECKS)

    if mode == "validate":
        log.info("deploy", "mode validate : arrêt avant publication (aucune écriture effectuée)")
        return finish("validated", EXIT_OK)

    if not verify_only:
        from fabric_cicd import deploy_with_config
        with log.group(f"Publication fabric-cicd {ctx.repository} → {env}"):
            try:
                result = deploy_with_config(str(ctx.config_path), token_credential=credential, environment=env)
                report.add_step("publish", status="succeeded", extra={"message": result.message})
            except Exception as exc:
                log.error("publish", f"échec de publication : {exc}")
                report.add_step("publish", status="failed", extra={"error": str(exc)[:2000]})
                return finish("failed", EXIT_DEPLOY)

    with log.group(f"Vérification post-déploiement {env}"):
        try:
            verify = run_verify(ctx, env, client, items, pre)
        except Exception as exc:
            log.error("verify", f"vérification interrompue : {type(exc).__name__}: {exc}")
            report.add_step("verify", status="failed", extra={"error": f"{type(exc).__name__}: {exc}"[:2000]})
            return finish("failed", EXIT_CHECKS)
    report.add_step("verify", verify)
    if not verify.ok:
        return finish("failed", EXIT_CHECKS)

    if not verify_only and ctx.smoke_tests(env):
        from CICD.tools.fabcicd.smoke import run_smoke_tests
        with log.group(f"Tests fonctionnels {env}"):
            try:
                smoke = run_smoke_tests(ctx, env, client, pre)
            except Exception as exc:
                log.error("smoke", f"tests fonctionnels interrompus : {type(exc).__name__}: {exc}")
                report.add_step("smoke", status="failed", extra={"error": f"{type(exc).__name__}: {exc}"[:2000]})
                return finish("failed", EXIT_CHECKS)
        report.add_step("smoke", smoke)
        if not smoke.ok:
            return finish("failed", EXIT_CHECKS)
    return finish("succeeded", EXIT_OK)


def cmd_lookup(args) -> int:
    from CICD.tools.fabcicd.fabric_api import FabricApiError, FabricClient
    client = FabricClient(build_credential(args.auth))
    code = EXIT_OK
    for workspace_id in args.ids:
        try:
            print(f"{workspace_id}\t{client.get_workspace(workspace_id).get('displayName')}")
        except FabricApiError as exc:
            print(f"{workspace_id}\tERREUR {exc.status}")
            code = EXIT_CHECKS
    return code


def cmd_manifest(args) -> int:
    from CICD.tools.fabcicd.manifest import check_manifest, write_manifest
    root = Path(args.path)
    if args.check:
        ok, message = check_manifest(root, Path(args.check))
        (log.info if ok else log.error)("manifest", message)
        if ok and os.getenv("TF_BUILD"):
            import json
            sha = json.loads(Path(args.check).read_text(encoding="utf-8"))["artifact_sha256"]
            print(f"##vso[task.setvariable variable=ARTIFACT_SHA256]{sha}", flush=True)
        return EXIT_OK if ok else EXIT_CHECKS
    manifest = write_manifest(root, Path(args.out), args.repository or root.name)
    log.info("manifest", f"empreinte {manifest['artifact_sha256']} ({manifest['file_count']} fichiers)")
    return EXIT_OK


def cmd_check_branch(args) -> int:
    from CICD.tools.fabcicd.branches import BranchPolicyError, matches, parse_policy

    if args.env:
        try:
            pattern = parse_policy(args.policy)[args.env.upper()]
        except (BranchPolicyError, KeyError) as exc:
            log.error("branch", f"règle de branches invalide : {exc}")
            return EXIT_USAGE
        allowed = [pattern]
    else:
        allowed = [b.strip() for b in args.allowed.split(",") if b.strip()]
    if any(matches(args.branch, pattern) for pattern in allowed):
        log.info("branch", f"artefact construit depuis {args.branch} : autorisé")
        return EXIT_OK
    where = f" pour {args.env.upper()}" if args.env else ""
    log.error("branch", f"artefact construit depuis {args.branch} : seules {', '.join(allowed)} peuvent être déployées{where}")
    return EXIT_POLICY


def cmd_route(args) -> int:
    """Environnement cible du run : publié en variable de sortie ``targetEnv`` (ADO)."""
    from CICD.tools.fabcicd.branches import ORDER, BranchPolicyError, parse_policy, target_environment

    try:
        policy = parse_policy(args.policy)
        env = target_environment(args.branch, policy)
    except BranchPolicyError as exc:
        log.error("route", str(exc))
        return EXIT_USAGE
    if env is None:
        rules = ", ".join(f"{e} ← {policy[e]}" for e in ORDER)
        log.error("route", f"branche {args.branch} : aucun environnement autorisé ({rules})")
        return EXIT_POLICY
    last = (args.last_env or "PRD").upper()
    if last in ORDER and ORDER.index(env) > ORDER.index(last):
        log.error("route", f"branche {args.branch} → {env}, mais la CD s'arrête à {last} (lastEnvironment)")
        return EXIT_POLICY
    log.info("route", f"branche {args.branch} → déploiement {env}")
    print(f"##vso[task.setvariable variable=targetEnv;isOutput=true]{env}", flush=True)
    return EXIT_OK


def main(argv=None) -> int:
    log.configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="contrôles statiques")
    p.add_argument("--repo", required=True)
    p.add_argument("--registry")
    p.add_argument("--inventory-csv")

    for name in ("deploy", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        p.add_argument("--env", required=True, choices=[*ENVIRONMENTS, *(e.lower() for e in ENVIRONMENTS)])
        p.add_argument("--auth", choices=["cli", "secret"], default="cli")
        p.add_argument("--report-dir", default="deployment-reports")
        if name == "deploy":
            p.add_argument("--mode", choices=["validate", "deploy"], default="deploy")
            p.add_argument("--framework-gate", choices=["ado", "off"], default="ado")
            p.add_argument("--framework-pipeline")
            p.add_argument("--expected-role", choices=["framework", "consumer"],
                           help="rôle déclaré dans CD.yml, comparé à dependencies.yml")

    p = sub.add_parser("scan", help="inventaire CSV des GUID")
    p.add_argument("--repo", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--registry")

    p = sub.add_parser("lookup", help="nom exact de workspaces à partir de leurs IDs")
    p.add_argument("--auth", choices=["cli", "secret"], default="cli")
    p.add_argument("ids", nargs="+")

    p = sub.add_parser("manifest", help="empreinte de l'artefact (création ou contrôle)")
    p.add_argument("--path", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--out")
    group.add_argument("--check")
    p.add_argument("--repository")

    p = sub.add_parser("check-branch", help="branche source autorisée pour un environnement")
    p.add_argument("--branch", required=True)
    p.add_argument("--allowed", default="refs/heads/develop", help="motifs autorisés (sans --env)")
    p.add_argument("--env", choices=[*ENVIRONMENTS, *(e.lower() for e in ENVIRONMENTS)],
                   help="contrôle selon la règle de branches de cet environnement")
    p.add_argument("--policy", help="ITG=refs/heads/develop,PPR=refs/heads/release/*,PRD=refs/heads/main")

    p = sub.add_parser("route", help="environnement cible selon la branche source")
    p.add_argument("--branch", required=True)
    p.add_argument("--policy")
    p.add_argument("--last-env", choices=["DEV", "ITG", "PPR", "PRD"], default="PRD")

    args = parser.parse_args(argv)
    if args.command == "manifest":
        return cmd_manifest(args)
    if args.command == "check-branch":
        return cmd_check_branch(args)
    if args.command == "route":
        return cmd_route(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "deploy":
        return cmd_deploy(args)
    if args.command == "verify":
        return cmd_deploy(args, verify_only=True)
    if args.command == "lookup":
        return cmd_lookup(args)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
