"""Variables, pré-vol, porte Framework, vérification post-déploiement, CLI.

Aucun appel réseau : FabricClient et le client Azure DevOps sont remplacés par
des doubles en mémoire. Ces tests prouvent la logique, pas le comportement du
tenant (à constater lors du pilote DEV).
"""

from __future__ import annotations

import json

import pytest
import yaml
from CICD.tests.conftest import SANDBOX_WS, FakeCredential, FakeFabric, base_dependencies, notebook_activity, pipeline

import deploy as cli
from fabcicd.ado_gate import check_framework_deployed
from fabcicd.items import discover_items
from fabcicd.preflight import run_preflight
from fabcicd.settings import load_repo
from fabcicd.variables import VariableSyntaxError, parse_variable, rule_value_for_env
from fabcicd.verify import run_verify

FW = "FR - DP - ITG - FAB - Framework - MANAGED"


# ---------------------------------------------------------------- variables
@pytest.mark.parametrize(
    "raw,kind,ws,item_type,item_name",
    [
        ("$workspace.$id", "target_workspace", None, None, None),
        ("$items.Notebook.nb_x.$id", "target_item", None, "Notebook", "nb_x"),
        (f"$workspace.{FW}.$id", "cross_workspace", FW, None, None),
        (f"$workspace.{FW}", "cross_workspace", FW, None, None),
        (f"$workspace.{FW}.$items.DataPipeline.pl_load_object.$id", "cross_item", FW, "DataPipeline", "pl_load_object"),
        ("$ENV:FABRIC_CONN_ID", "env", None, None, None),
        ("valeur", "literal", None, None, None),
    ],
)
def test_parse_variable(raw, kind, ws, item_type, item_name):
    v = parse_variable(raw)
    assert (v.kind, v.workspace_name, v.item_type, v.item_name) == (kind, ws, item_type, item_name)


@pytest.mark.parametrize("raw", ["$items.Notebook.$id", "$items.Notebook.nb.$foo", "$workspace.", "$autre.x"])
def test_parse_variable_errors(raw):
    with pytest.raises(VariableSyntaxError):
        parse_variable(raw)


def test_rule_value_for_env():
    assert rule_value_for_env({"replace_value": {"_ALL_": "x"}}, "PRD") == "x"
    assert rule_value_for_env({"replace_value": {"ITG": "y"}}, "PRD") is None
    assert rule_value_for_env({"replace_value": {"itg": "y"}}, "ITG") is None  # casse stricte, comme fabric-cicd


# ---------------------------------------------------------------- pré-vol
def consumer_repo(make_repo, connections=None):
    deps = base_dependencies(role="consumer")
    deps["framework"] = {
        "required": True,
        "workspace": {e: f"FR - DP - {e} - FAB - Framework - MANAGED" for e in ("DEV", "ITG", "PPR", "PRD")},
        "items": [{"type": "DataPipeline", "name": "pl_load_object"}],
    }
    if connections:
        deps["connections"] = connections
    root = make_repo(dependencies=deps)
    param = yaml.safe_load((root / "deployment" / "parameter.yml").read_text())
    param["key_value_replace"].append({
        "find_key": '$.properties.activities[?(@.name=="nb_get_ws_lh_ids")].typeProperties.notebookId',
        "replace_value": {e: f"$workspace.FR - DP - {e} - FAB - Framework - MANAGED.$items.DataPipeline.pl_load_object.$id"
                          for e in ("DEV", "ITG", "PPR", "PRD")},
        "item_type": "DataPipeline", "item_name": "pl_get_ws_lh_ids",
    })
    (root / "deployment" / "parameter.yml").write_text(yaml.safe_dump(param, allow_unicode=True))
    return root


def fake_tenant(extra_ws=None, fw_items=None):
    workspaces = [{"id": "ws-target", "displayName": FW.replace("Framework", "MDS")},
                  {"id": "ws-fw", "displayName": FW}] + (extra_ws or [])
    return workspaces, {"ws-fw": fw_items if fw_items is not None else [{"id": "pl-1", "type": "DataPipeline", "displayName": "pl_load_object"}]}


def test_preflight_resolves_framework_references(make_repo):
    root = consumer_repo(make_repo)
    config = yaml.safe_load((root / "deployment" / "config.yml").read_text())
    config["core"]["workspace"]["ITG"] = FW.replace("Framework", "MDS")
    (root / "deployment" / "config.yml").write_text(yaml.safe_dump(config, allow_unicode=True))
    workspaces, items = fake_tenant()
    result = run_preflight(load_repo(root, "ITG"), "ITG", FakeFabric(workspaces, items))
    assert result.findings.ok, [f.message for f in result.findings.errors]
    assert result.target_workspace_id == "ws-target"
    resolved = {r.variable: r.value for r in result.resolutions.values()}
    assert resolved[f"$workspace.{FW}.$items.DataPipeline.pl_load_object.$id"] == "pl-1"


def _consumer_ctx(make_repo):
    root = consumer_repo(make_repo)
    config = yaml.safe_load((root / "deployment" / "config.yml").read_text())
    config["core"]["workspace"]["ITG"] = FW.replace("Framework", "MDS")
    (root / "deployment" / "config.yml").write_text(yaml.safe_dump(config, allow_unicode=True))
    return load_repo(root, "ITG")


def test_preflight_fails_when_framework_item_missing(make_repo):
    workspaces, items = fake_tenant(fw_items=[])
    result = run_preflight(_consumer_ctx(make_repo), "ITG", FakeFabric(workspaces, items))
    assert {"PRE_FRAMEWORK_ITEM", "PRE_REF_ITEM"} <= {f.code for f in result.findings.errors}


def test_preflight_fails_on_ambiguous_workspace(make_repo):
    workspaces, items = fake_tenant(extra_ws=[{"id": "ws-dup", "displayName": FW}])
    result = run_preflight(_consumer_ctx(make_repo), "ITG", FakeFabric(workspaces, items))
    assert any("ambigu" in f.message for f in result.findings.errors)


def test_preflight_fails_on_missing_connection(make_repo):
    root = consumer_repo(make_repo, connections=[{"name": {e: f"MDS_{e}_DP_FABRIC" for e in ("DEV", "ITG", "PPR", "PRD")}}])
    config = yaml.safe_load((root / "deployment" / "config.yml").read_text())
    config["core"]["workspace"]["ITG"] = FW.replace("Framework", "MDS")
    (root / "deployment" / "config.yml").write_text(yaml.safe_dump(config, allow_unicode=True))
    workspaces, items = fake_tenant()
    fabric = FakeFabric(workspaces, items, connections=[{"id": "c1", "displayName": "MDS_DEV_DP_FABRIC"}])
    result = run_preflight(load_repo(root, "ITG"), "ITG", fabric)
    assert "PRE_CONNECTION" in {f.code for f in result.findings.errors}


def test_preflight_refuses_placeholder(make_repo):
    root = make_repo()
    config = yaml.safe_load((root / "deployment" / "config.yml").read_text())
    config["core"]["workspace"]["ITG"] = "__A_RELEVER__ITG__xxx"
    (root / "deployment" / "config.yml").write_text(yaml.safe_dump(config))
    result = run_preflight(load_repo(root, "ITG"), "ITG", FakeFabric([]))
    assert "PRE_TARGET_PLACEHOLDER" in {f.code for f in result.findings.errors}


# ---------------------------------------------------------------- porte Framework (Azure DevOps)
class FakeAdo:
    def __init__(self, completed, in_progress=None):
        self.completed = completed
        self.in_progress = in_progress or []

    def get(self, path):
        if path.startswith("build/definitions"):
            return {"value": [{"id": 7}]}
        if "statusFilter=inProgress" in path:
            return {"value": [{"id": b["id"], "buildNumber": b["id"]} for b in self.in_progress]}
        if path.startswith("build/builds?"):
            return {"value": [{"id": b["id"], "buildNumber": f"1.0.{b['id']}", "sourceVersion": "abcdef123"} for b in self.completed]}
        build_id = int(path.split("/")[2])
        for b in self.completed + self.in_progress:
            if b["id"] == build_id:
                return {"records": [{"type": "Stage", "identifier": "Deploy_ITG", "state": b["state"], "result": b.get("result")}]}
        return {"records": []}


def test_gate_ok_when_last_framework_stage_succeeded():
    gate = check_framework_deployed(FakeAdo([{"id": 2, "state": "completed", "result": "succeeded"}]), "fw-cd", "ITG")
    assert gate.findings.ok and gate.framework_run_id == 2


def test_gate_skips_runs_without_the_stage_then_fails_on_failure():
    ado = FakeAdo([{"id": 3, "state": "completed", "result": "skipped"}, {"id": 2, "state": "completed", "result": "failed"}])
    gate = check_framework_deployed(ado, "fw-cd", "ITG")
    assert "GATE_LAST_FAILED" in {f.code for f in gate.findings.errors}


def test_gate_blocks_when_framework_deployment_in_progress():
    ado = FakeAdo([{"id": 2, "state": "completed", "result": "succeeded"}], in_progress=[{"id": 4, "state": "inProgress"}])
    assert "GATE_IN_PROGRESS" in {f.code for f in check_framework_deployed(ado, "fw-cd", "ITG").findings.errors}


def test_gate_uses_newest_running_cd_whose_stage_already_failed():
    ado = FakeAdo([{"id": 2, "state": "completed", "result": "succeeded"}],
                  in_progress=[{"id": 5, "state": "completed", "result": "failed"}])
    assert "GATE_LAST_FAILED" in {f.code for f in check_framework_deployed(ado, "fw-cd", "ITG").findings.errors}


def test_gate_uses_newest_running_cd_whose_stage_succeeded():
    ado = FakeAdo([{"id": 2, "state": "completed", "result": "failed"}],
                  in_progress=[{"id": 5, "state": "completed", "result": "succeeded"}])
    gate = check_framework_deployed(ado, "fw-cd", "ITG")
    assert gate.findings.ok and gate.framework_run_id == 5


def test_gate_fails_when_never_deployed():
    assert "GATE_NEVER_DEPLOYED" in {f.code for f in check_framework_deployed(FakeAdo([]), "fw-cd", "ITG").findings.errors}


# ---------------------------------------------------------------- vérification post-déploiement
def _deployed_definition(workspace_id, notebook_id):
    return {"pipeline-content.json": json.dumps(pipeline([notebook_activity("nb_get_ws_lh_ids", notebook_id, workspace_id)]))}


def _verify(make_repo, definition):
    root = make_repo()
    ctx = load_repo(root, "ITG")
    items = discover_items(ctx.items_root, ctx.folder_exclude_regex("ITG"))
    workspaces = [{"id": "ws-itg", "displayName": FW}]
    deployed = {"ws-itg": [{"id": "pl-9", "type": "DataPipeline", "displayName": "pl_get_ws_lh_ids"},
                           {"id": "nb-9", "type": "Notebook", "displayName": "nb_get_ws_lh_ids"}]}
    fabric = FakeFabric(workspaces, deployed, definitions={"pl-9": definition})
    pre = run_preflight(ctx, "ITG", fabric)
    assert pre.findings.ok
    return run_verify(ctx, "ITG", fabric, items, pre)


def test_verify_accepts_correct_references(make_repo):
    assert _verify(make_repo, _deployed_definition("ws-itg", "nb-9")).ok


def test_verify_detects_sandbox_reference_left_in_place(make_repo):
    found = _verify(make_repo, _deployed_definition(SANDBOX_WS, "nb-9"))
    assert {"VERIFY_REFERENCE_MISMATCH", "VERIFY_FORBIDDEN_GUID"} <= {f.code for f in found.errors}


# ---------------------------------------------------------------- CLI
def test_cli_validate_exit_codes(make_repo):
    assert cli.main(["validate", "--repo", str(make_repo())]) == cli.EXIT_OK


def test_cli_validate_fails_on_error(make_repo):
    assert cli.main(["validate", "--repo", str(make_repo(parameter={"key_value_replace": []}))]) == cli.EXIT_CHECKS


def test_cli_refuses_deploy_when_inventory_pending(make_repo, tmp_path):
    root = make_repo(dependencies=base_dependencies(status="pending"))
    code = cli.main(["deploy", "--repo", str(root), "--env", "ITG", "--auth", "secret", "--report-dir", str(tmp_path / "r")])
    assert code == cli.EXIT_POLICY
    report = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert report["status"] == "refused"


def test_cli_secret_auth_requires_variables(make_repo, tmp_path, monkeypatch):
    for name in ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    code = cli.main(["deploy", "--repo", str(make_repo()), "--env", "ITG", "--auth", "secret", "--mode", "validate",
                     "--report-dir", str(tmp_path / "r")])
    assert code == cli.EXIT_USAGE


# ---------------------------------------------------------------- intégrité de l'artefact et branche
def test_manifest_detects_modified_artifact(make_repo, tmp_path):
    root = make_repo()
    manifest = tmp_path / "m" / "deployment-manifest.json"
    assert cli.main(["manifest", "--path", str(root), "--out", str(manifest)]) == cli.EXIT_OK
    assert cli.main(["manifest", "--path", str(root), "--check", str(manifest)]) == cli.EXIT_OK
    (root / "deployment" / "config.yml").write_text("core: {}\n")
    assert cli.main(["manifest", "--path", str(root), "--check", str(manifest)]) == cli.EXIT_CHECKS


def test_check_branch():
    assert cli.main(["check-branch", "--branch", "refs/heads/develop"]) == cli.EXIT_OK
    assert cli.main(["check-branch", "--branch", "refs/heads/feature/x"]) == cli.EXIT_POLICY


# ---------------------------------------------------------------- enchaînement complet (doubles)
def _full_flow(make_repo, tmp_path, monkeypatch, deployed_workspace_ref):
    import fabcicd.fabric_api as fabric_api
    import fabric_cicd

    root = make_repo()
    deployed = {"ws-itg": [{"id": "pl-9", "type": "DataPipeline", "displayName": "pl_get_ws_lh_ids"},
                           {"id": "nb-9", "type": "Notebook", "displayName": "nb_get_ws_lh_ids"}]}
    fake = FakeFabric([{"id": "ws-itg", "displayName": FW}], deployed,
                      definitions={"pl-9": _deployed_definition(deployed_workspace_ref, "nb-9")})
    calls = []

    class Result:
        message = "Deployment completed successfully"

    monkeypatch.setattr(fabric_api, "FabricClient", lambda credential: fake)
    monkeypatch.setattr(cli, "build_credential", lambda mode: FakeCredential())
    monkeypatch.setattr(fabric_cicd, "deploy_with_config", lambda path, **kw: calls.append((path, kw["environment"])) or Result())
    monkeypatch.setenv("FABRIC_TENANT_ID", "deff24bb-2089-4400-8c8e-f71e680378b2")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "bf2e1f9f-b18c-481f-9400-7eb8caee3d5f")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "valeur-de-test")
    code = cli.main(["deploy", "--repo", str(root), "--env", "ITG", "--auth", "secret", "--report-dir", str(tmp_path / "r")])
    report = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    return code, calls, report


def test_full_flow_succeeds_and_reports(make_repo, tmp_path, monkeypatch):
    code, calls, report = _full_flow(make_repo, tmp_path, monkeypatch, "ws-itg")
    assert code == cli.EXIT_OK
    assert calls and calls[0][1] == "ITG"  # environnement transmis en majuscules à fabric-cicd
    # v1.3 : étape « gate » informative (Framework : aucune dépendance de déploiement).
    assert [s["step"] for s in report["steps"]] == ["preflight", "gate", "publish", "verify"]
    assert "valeur-de-test" not in json.dumps(report)  # aucun secret dans le rapport


def test_full_flow_fails_when_deployed_reference_is_wrong(make_repo, tmp_path, monkeypatch):
    code, _, report = _full_flow(make_repo, tmp_path, monkeypatch, SANDBOX_WS)
    assert code == cli.EXIT_CHECKS and report["status"] == "failed"


def test_publish_skip_in_upper_env_is_rejected(make_repo):
    from CICD.tests.conftest import base_config
    from fabcicd.static_checks import run_static_checks
    config = base_config()
    config["publish"]["skip"]["ITG"] = True
    codes = {f.code for f in run_static_checks(make_repo(config=config)).findings.errors}
    assert "CONFIG_PUBLISH_SKIPPED" in codes


def test_authentication_failure_returns_usage_code_with_report(make_repo, tmp_path, monkeypatch):
    class BadCredential:
        def get_token(self, *_):
            raise RuntimeError("AADSTS7000215 (simulé)")
    monkeypatch.setattr(cli, "build_credential", lambda mode: BadCredential())
    code = cli.main(["deploy", "--repo", str(make_repo()), "--env", "ITG", "--report-dir", str(tmp_path / "r")])
    assert code == cli.EXIT_USAGE
    assert json.loads(next((tmp_path / "r").glob("*.json")).read_text())["status"] == "failed"


def test_preflight_exception_is_reported(make_repo, tmp_path, monkeypatch):
    import fabcicd.fabric_api as fabric_api

    class Broken(FakeFabric):
        def list_workspaces(self):
            raise TimeoutError("délai dépassé (simulé)")
    monkeypatch.setattr(cli, "build_credential", lambda mode: FakeCredential())
    monkeypatch.setattr(fabric_api, "FabricClient", lambda credential: Broken())
    code = cli.main(["deploy", "--repo", str(make_repo()), "--env", "ITG", "--report-dir", str(tmp_path / "r")])
    assert code == cli.EXIT_CHECKS
    report = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert report["steps"][-1]["step"] == "preflight" and report["status"] == "failed"


# ---------------------------------------------------------------- correctifs de l'audit du 20/10/2026
def test_unresolved_ado_macro_is_reported_as_missing_secret(make_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIC_TENANT_ID", "deff24bb-2089-4400-8c8e-f71e680378b2")
    monkeypatch.setenv("FABRIC_CLIENT_ID", "bf2e1f9f-b18c-481f-9400-7eb8caee3d5f")
    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "$(ITG-AZURE-CLIENT-SECRET)")  # secret non lu dans le coffre
    with pytest.raises(RuntimeError, match="non résolue"):
        cli.build_credential("secret")
    code = cli.main(["deploy", "--repo", str(make_repo()), "--env", "ITG", "--auth", "secret", "--report-dir", str(tmp_path / "r")])
    assert code == cli.EXIT_USAGE


def test_role_mismatch_between_cd_and_dependencies_is_blocking(make_repo, tmp_path):
    code = cli.main(["deploy", "--repo", str(make_repo()), "--env", "ITG", "--expected-role", "consumer",
                     "--report-dir", str(tmp_path / "r")])
    assert code == cli.EXIT_CHECKS


def test_ambiguous_framework_item_blocks_preflight(make_repo):
    workspaces, _ = fake_tenant()
    dup = [{"id": "pl-1", "type": "DataPipeline", "displayName": "pl_load_object"},
           {"id": "pl-2", "type": "DataPipeline", "displayName": "pl_load_object"}]
    result = run_preflight(_consumer_ctx(make_repo), "ITG", FakeFabric(workspaces, {"ws-fw": dup}))
    assert any("ambigu" in f.message for f in result.findings.errors)


def test_verify_scans_all_published_pipelines_for_sandbox_guid(make_repo):
    from CICD.tests.conftest import invoke_activity, write_item
    root = make_repo()
    write_item(root, "Flux", "pl_sans_regle", "DataPipeline", {"pipeline-content.json": pipeline([])})
    ctx = load_repo(root, "ITG")
    items = discover_items(ctx.items_root, ctx.folder_exclude_regex("ITG"))
    deployed = {"ws-itg": [{"id": "pl-9", "type": "DataPipeline", "displayName": "pl_get_ws_lh_ids"},
                           {"id": "nb-9", "type": "Notebook", "displayName": "nb_get_ws_lh_ids"},
                           {"id": "pl-8", "type": "DataPipeline", "displayName": "pl_sans_regle"}]}
    leaked = {"pipeline-content.json": json.dumps(pipeline([invoke_activity("x", "p", SANDBOX_WS)]))}
    fabric = FakeFabric([{"id": "ws-itg", "displayName": FW}], deployed,
                        definitions={"pl-9": _deployed_definition("ws-itg", "nb-9"), "pl-8": leaked})
    pre = run_preflight(ctx, "ITG", fabric)
    found = run_verify(ctx, "ITG", fabric, items, pre)
    assert "VERIFY_FORBIDDEN_GUID" in {f.code for f in found.errors}


def _smoke_ctx(make_repo):
    deps = base_dependencies()
    deps["smoke_tests"] = [{"type": "DataPipeline", "name": "pl_get_ws_lh_ids", "environments": ["ITG"], "timeout_minutes": 1}]
    return load_repo(make_repo(dependencies=deps), "ITG")


def test_smoke_test_failure_is_blocking(make_repo):
    from fabcicd.preflight import PreflightResult
    from fabcicd.smoke import run_smoke_tests
    from fabcicd.findings import Findings
    ctx = _smoke_ctx(make_repo)
    fabric = FakeFabric([], {"ws-itg": [{"id": "pl-9", "type": "DataPipeline", "displayName": "pl_get_ws_lh_ids"}]})
    pre = PreflightResult(Findings("p"), target_workspace_id="ws-itg")
    assert run_smoke_tests(ctx, "ITG", fabric, pre, runner=lambda *a, **k: "Completed").ok
    failed = run_smoke_tests(ctx, "ITG", fabric, pre, runner=lambda *a, **k: "Failed")
    assert "SMOKE_FAILED" in {f.code for f in failed.errors}
    assert run_smoke_tests(ctx, "PRD", fabric, pre, runner=lambda *a, **k: "Failed").ok  # non déclaré pour PRD


# ---------------------------------------------------------------- règle de branches v1.2
def test_route_maps_each_branch_to_its_environment(capsys):
    cases = {"refs/heads/develop": "ITG", "refs/heads/release/2026-11-paie": "PPR",
             "refs/heads/release/feat-a+feat-b": "PPR", "refs/heads/main": "PRD"}
    for branch, env in cases.items():
        assert cli.main(["route", "--branch", branch]) == cli.EXIT_OK
        assert f"variable=targetEnv;isOutput=true]{env}" in capsys.readouterr().out


def test_route_refuses_other_branches_and_scope_beyond_last_env(capsys):
    for branch in ("refs/heads/feature/x", "refs/heads/release", "refs/heads/hotfix/y", "refs/heads/Main"):
        assert cli.main(["route", "--branch", branch]) == cli.EXIT_POLICY
    assert cli.main(["route", "--branch", "refs/heads/main", "--last-env", "ITG"]) == cli.EXIT_POLICY
    assert "targetEnv" not in capsys.readouterr().out


def test_route_rejects_ambiguous_or_incomplete_policy():
    assert cli.main(["route", "--branch", "refs/heads/develop",
                     "--policy", "ITG=refs/heads/*,PPR=refs/heads/develop,PRD=refs/heads/main"]) == cli.EXIT_USAGE
    assert cli.main(["route", "--branch", "refs/heads/develop", "--policy", "ITG=refs/heads/develop"]) == cli.EXIT_USAGE


def test_check_branch_per_environment():
    ok = [("ITG", "refs/heads/develop"), ("PPR", "refs/heads/release/r1"), ("PRD", "refs/heads/main")]
    ko = [("ITG", "refs/heads/main"), ("PPR", "refs/heads/develop"), ("PRD", "refs/heads/release/r1")]
    for env, branch in ok:
        assert cli.main(["check-branch", "--branch", branch, "--env", env]) == cli.EXIT_OK
    for env, branch in ko:
        assert cli.main(["check-branch", "--branch", branch, "--env", env]) == cli.EXIT_POLICY


def test_gate_branch_defaults_follow_environment():
    from fabcicd.branches import gate_branch
    assert gate_branch(None, "ITG") == "refs/heads/develop"
    assert gate_branch(None, "PPR") == "refs/heads/release/*"
    assert gate_branch(None, "PRD") == "refs/heads/main"
    assert gate_branch({"PPR": "refs/heads/release/*"}, "PPR") == "refs/heads/release/*"
    assert gate_branch("refs/heads/develop", "PRD") == "refs/heads/develop"  # ancien format conservé


class FakeAdoBranches:
    """Historique ADO avec branche source : un run release/* et un run develop."""

    def __init__(self, runs):
        self.runs = runs  # (id, sourceBranch, stage, result)
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path.startswith("build/definitions"):
            return {"value": [{"id": 7}]}
        if "statusFilter=inProgress" in path:
            return {"value": []}
        if path.startswith("build/builds?"):
            return {"value": [{"id": r[0], "buildNumber": str(r[0]), "sourceBranch": r[1], "sourceVersion": "c0ffee"}
                              for r in sorted(self.runs, reverse=True)]}
        build_id = int(path.split("/")[2])
        run = next(r for r in self.runs if r[0] == build_id)
        return {"records": [{"type": "Stage", "identifier": run[2], "state": "completed", "result": run[3]}]}


def test_gate_ppr_reads_release_runs_only():
    ado = FakeAdoBranches([(9, "refs/heads/develop", "Deploy_PPR", "failed"),
                           (5, "refs/heads/release/r1", "Deploy_PPR", "succeeded")])
    gate = check_framework_deployed(ado, "fw-cd", "PPR", branch="refs/heads/release/*")
    assert gate.findings.ok and gate.framework_run_id == 5
    assert all("branchName=" not in p for p in ado.paths if p.startswith("build/builds?"))


def test_gate_ppr_fails_when_last_release_run_failed():
    ado = FakeAdoBranches([(6, "refs/heads/release/r2", "Deploy_PPR", "failed"),
                           (5, "refs/heads/release/r1", "Deploy_PPR", "succeeded")])
    gate = check_framework_deployed(ado, "fw-cd", "PPR", branch="refs/heads/release/*")
    assert "GATE_LAST_FAILED" in {f.code for f in gate.findings.errors}


# ---------------------------------------------------------------- matrice de dépendances v1.3 (R1-R6)
FW_PIPELINE = "its-dp-fab-item-framework-managed-cd"
LABELS = {"framework": "Framework", "adp": "ADP - MDS", "bdp": "BDP - Compte", "ucs": "UCS - Reporting Fees et WIP"}


def ws_name(category, env):
    return f"FR - DP - {env} - FAB - {LABELS[category]} - MANAGED"


def dp_deps(category, **extra):
    deps = base_dependencies(role="framework" if category == "framework" else "consumer", category=category)
    if category in ("adp", "bdp"):
        deps["framework"] = {"required": True,
                             "workspace": {e: ws_name("framework", e) for e in ("DEV", "ITG", "PPR", "PRD")},
                             "items": [],
                             "ado_gate": {"pipeline_name": FW_PIPELINE}}
    else:
        deps["framework"] = {"required": False}
    deps.update(extra)
    return deps


def dp_repo(make_repo, category, **extra):
    from CICD.tests.conftest import base_config
    return make_repo(config=base_config(workspace_prefix=ws_name(category, "{env}")), dependencies=dp_deps(category, **extra))


def tenant(*workspaces):
    """workspaces : (catégorie, environnement) présents sur le tenant simulé."""
    return FakeFabric([{"id": f"ws-{c}-{e}", "displayName": ws_name(c, e)} for c, e in workspaces])


def run_deploy(monkeypatch, tmp_path, root, env, fabric, framework_deployed_envs=("ITG", "PPR", "PRD")):
    """Déploiement en mode validate (s'arrête après les portes) ; renvoie (code, pipelines interrogés)."""
    import fabcicd.ado_gate as ado_gate
    import fabcicd.fabric_api as fabric_api
    from fabcicd.ado_gate import GateResult
    from fabcicd.findings import Findings

    calls = []

    def gate(client, pipeline, gate_env, **kw):
        calls.append((pipeline, gate_env, kw.get("branch")))
        result = GateResult(Findings("gate"))
        if gate_env not in framework_deployed_envs:
            result.findings.error("GATE_NEVER_DEPLOYED", f"Framework jamais déployé sur {gate_env} (simulé)")
        return result

    monkeypatch.setattr(ado_gate, "check_framework_deployed", gate)
    monkeypatch.setattr(ado_gate.AdoClient, "from_environment", classmethod(lambda cls: object()))
    monkeypatch.setattr(fabric_api, "FabricClient", lambda credential: fabric)
    monkeypatch.setattr(cli, "build_credential", lambda mode: FakeCredential())
    code = cli.main(["deploy", "--repo", str(root), "--env", env, "--mode", "validate", "--report-dir", str(tmp_path / f"r-{env}")])
    return code, calls


def test_r1_framework_deploys_without_any_other_repository(make_repo, tmp_path, monkeypatch):
    code, calls = run_deploy(monkeypatch, tmp_path, dp_repo(make_repo, "framework"), "ITG", tenant(("framework", "ITG")))
    assert code == cli.EXIT_OK and calls == []


@pytest.mark.parametrize("category", ["adp", "bdp"])
def test_r1_blocked_without_framework_workspace(make_repo, tmp_path, monkeypatch, category):
    code, _ = run_deploy(monkeypatch, tmp_path, dp_repo(make_repo, category), "ITG", tenant((category, "ITG")))
    assert code == cli.EXIT_CHECKS
    report = json.loads(next((tmp_path / "r-ITG").glob("*.json")).read_text())
    assert "PRE_FRAMEWORK_WORKSPACE" in json.dumps(report)


@pytest.mark.parametrize("category", ["adp", "bdp"])
def test_r1_blocked_when_framework_not_validated(make_repo, tmp_path, monkeypatch, category):
    fabric = tenant((category, "ITG"), ("framework", "ITG"))
    code, calls = run_deploy(monkeypatch, tmp_path, dp_repo(make_repo, category), "ITG", fabric, framework_deployed_envs=())
    assert code == cli.EXIT_CHECKS and [c[0] for c in calls] == [FW_PIPELINE]


@pytest.mark.parametrize("category", ["adp", "bdp"])
def test_r1_allowed_with_framework_available_without_redeploying_it(make_repo, tmp_path, monkeypatch, category):
    fabric = tenant((category, "ITG"), ("framework", "ITG"))
    code, calls = run_deploy(monkeypatch, tmp_path, dp_repo(make_repo, category), "ITG", fabric)
    # Seule la porte Framework est interrogée ; aucun redéploiement du Framework n'est demandé.
    assert code == cli.EXIT_OK and calls == [(FW_PIPELINE, "ITG", "refs/heads/develop")]


@pytest.mark.parametrize("category,absent", [("bdp", "adp"), ("adp", "bdp")])
def test_r2_r3_adp_and_bdp_never_wait_for_each_other(make_repo, tmp_path, monkeypatch, category, absent):
    # BDP avant ADP, puis ADP avant BDP : l'autre famille est absente du tenant.
    fabric = tenant((category, "ITG"), ("framework", "ITG"))
    assert all(absent.upper() not in w["displayName"] for w in fabric.workspaces)
    code, calls = run_deploy(monkeypatch, tmp_path, dp_repo(make_repo, category), "ITG", fabric)
    assert code == cli.EXIT_OK and {c[0] for c in calls} == {FW_PIPELINE}


def test_r4_adp_and_bdp_simultaneously(make_repo, tmp_path, monkeypatch):
    fabric = tenant(("adp", "ITG"), ("bdp", "ITG"), ("framework", "ITG"))
    import shutil
    adp = dp_repo(make_repo, "adp")
    adp_copy = tmp_path / "adp"
    shutil.copytree(adp, adp_copy)
    shutil.rmtree(adp)
    bdp = dp_repo(make_repo, "bdp")
    code_adp, calls_adp = run_deploy(monkeypatch, tmp_path / "a", adp_copy, "ITG", fabric)
    code_bdp, calls_bdp = run_deploy(monkeypatch, tmp_path / "b", bdp, "ITG", fabric)
    assert code_adp == code_bdp == cli.EXIT_OK
    assert {c[0] for c in calls_adp + calls_bdp} == {FW_PIPELINE}  # aucune porte entre ADP et BDP


@pytest.mark.parametrize("present", [[("ucs", "ITG")], [("ucs", "ITG"), ("framework", "ITG")]])
def test_r5_ucs_deploys_without_framework_adp_or_bdp(make_repo, tmp_path, monkeypatch, present):
    code, calls = run_deploy(monkeypatch, tmp_path, dp_repo(make_repo, "ucs"), "ITG", tenant(*present), framework_deployed_envs=())
    assert code == cli.EXIT_OK and calls == []


def test_r5_ucs_data_consumption_is_not_a_deployment_gate(make_repo, tmp_path, monkeypatch):
    data = [{"repository": "its-dp-fab-bdp-compte-managed", "kind": "data", "description": "lecture des tables du BDP Compte"}]
    root = dp_repo(make_repo, "ucs", data_dependencies=data)
    from fabcicd.static_checks import run_static_checks
    assert run_static_checks(root).findings.ok
    code, calls = run_deploy(monkeypatch, tmp_path, root, "ITG", tenant(("ucs", "ITG")), framework_deployed_envs=())
    assert code == cli.EXIT_OK and calls == []


@pytest.mark.parametrize("category", ["adp", "bdp"])
def test_framework_available_in_itg_but_absent_in_ppr_blocks_ppr(make_repo, tmp_path, monkeypatch, category):
    fabric = tenant((category, "ITG"), (category, "PPR"), ("framework", "ITG"))
    root = dp_repo(make_repo, category)
    code_itg, _ = run_deploy(monkeypatch, tmp_path, root, "ITG", fabric, framework_deployed_envs=("ITG",))
    code_ppr, _ = run_deploy(monkeypatch, tmp_path, root, "PPR", fabric, framework_deployed_envs=("ITG",))
    assert code_itg == cli.EXIT_OK and code_ppr == cli.EXIT_CHECKS


def test_framework_workspace_present_but_never_deployed_in_ppr_blocks_ppr(make_repo, tmp_path, monkeypatch):
    fabric = tenant(("bdp", "PPR"), ("framework", "PPR"))
    code, calls = run_deploy(monkeypatch, tmp_path, dp_repo(make_repo, "bdp"), "PPR", fabric, framework_deployed_envs=("ITG",))
    assert code == cli.EXIT_CHECKS and calls == [(FW_PIPELINE, "PPR", "refs/heads/release/*")]


@pytest.mark.parametrize("category,upstream", [
    ("bdp", "its-dp-fab-adp-mds-managed"),             # ancienne dépendance ADP → BDP
    ("ucs", "its-dp-fab-bdp-compte-managed"),          # ancienne dépendance BDP → UCS
])
def test_old_inter_data_product_dependencies_are_rejected(make_repo, tmp_path, monkeypatch, category, upstream):
    root = dp_repo(make_repo, category, upstreams=[{"repository": upstream, "ado_gate": {"pipeline_name": upstream + "-cd"}}])
    from fabcicd.static_checks import run_static_checks
    assert "DEPS_UPSTREAM_FORBIDDEN" in {f.code for f in run_static_checks(root).findings.errors}
    fabric = tenant((category, "ITG"), ("framework", "ITG"))
    code, calls = run_deploy(monkeypatch, tmp_path, root, "ITG", fabric)
    assert code == cli.EXIT_CHECKS and calls == []  # refus avant toute porte
