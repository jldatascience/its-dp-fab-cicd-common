"""Contrôles statiques : cas nominal et cas d'erreur bloquants."""

from __future__ import annotations

import pytest
import yaml
from conftest import SANDBOX_WS, base_config, base_dependencies, invoke_activity, pipeline, write_item

from fabcicd.registry import Registry
from fabcicd.static_checks import run_static_checks


def codes(result):
    return {f.code for f in result.findings.errors}


def test_nominal_repository_passes(make_repo):
    result = run_static_checks(make_repo())
    assert result.findings.ok, [f.message for f in result.findings.errors]
    # Le GUID sandbox du dossier exclu Flux/old n'est pas inventorié.
    assert all("Flux/old" not in o.file for o in result.occurrences)
    covered = [o for o in result.occurrences if o.guid == SANDBOX_WS]
    assert covered and set(covered[0].covered_envs) == {"DEV", "ITG", "PPR", "PRD"}


def test_uncovered_sandbox_guid_fails(make_repo):
    root = make_repo(parameter={"key_value_replace": []})
    assert "GUID_HARDCODED" in codes(run_static_checks(root))


def test_rule_without_effect_fails(make_repo):
    parameter = {
        "key_value_replace": [
            {
                "find_key": '$.properties.activities[?(@.name=="nexiste_pas")].typeProperties.workspaceId',
                "replace_value": {"_ALL_": "$workspace.$id"},
                "item_type": "DataPipeline",
                "item_name": "pl_get_ws_lh_ids",
            }
        ]
    }
    assert "RULE_NO_EFFECT" in codes(run_static_checks(make_repo(parameter=parameter)))


def test_rule_missing_environment_fails(make_repo):
    parameter = {
        "key_value_replace": [
            {
                "find_key": '$.properties.activities[?(@.name=="nb_get_ws_lh_ids")].typeProperties.workspaceId',
                "replace_value": {"DEV": "$workspace.$id", "ITG": "$workspace.$id"},
                "item_type": "DataPipeline",
                "item_name": "pl_get_ws_lh_ids",
            }
        ]
    }
    result = run_static_checks(make_repo(parameter=parameter))
    assert "RULE_ENV_MISSING" in codes(result)
    assert "GUID_PARTIALLY_COVERED" in codes(result)


def test_rule_targeting_absent_item_fails(make_repo):
    parameter = {
        "key_value_replace": [
            {
                "find_key": "$.x",
                "replace_value": {"_ALL_": "$workspace.$id"},
                "item_type": "DataPipeline",
                "item_name": "pl_absent",
            }
        ]
    }
    assert "RULE_TARGET_ABSENT" in codes(run_static_checks(make_repo(parameter=parameter)))


def test_rule_target_item_not_in_repo_fails(make_repo):
    parameter = {
        "key_value_replace": [
            {
                "find_key": '$.properties.activities[?(@.name=="nb_get_ws_lh_ids")].typeProperties.notebookId',
                "replace_value": {"_ALL_": "$items.Notebook.nb_absent.$id"},
                "item_type": "DataPipeline",
                "item_name": "pl_get_ws_lh_ids",
            }
        ]
    }
    assert "RULE_TARGET_ITEM_NOT_IN_REPO" in codes(run_static_checks(make_repo(parameter=parameter)))


def test_lowercase_environment_keys_fail(make_repo):
    config = base_config()
    config["core"]["workspace"] = {env.lower(): name for env, name in config["core"]["workspace"].items()}
    result = run_static_checks(make_repo(config=config))
    assert {"CONFIG_ENV_UNKNOWN", "CONFIG_ENV_MISSING"} <= codes(result)


def test_exclude_regex_without_flags_fails(make_repo):
    config = base_config()
    config.pop("features")
    assert "CONFIG_EXCLUDE_WITHOUT_FLAGS" in codes(run_static_checks(make_repo(config=config)))


def test_unpublish_in_prd_fails(make_repo):
    config = base_config()
    config["unpublish"]["skip"]["PRD"] = False
    assert "CONFIG_UNPUBLISH_UPPER_ENV" in codes(run_static_checks(make_repo(config=config)))


def test_workspace_id_in_config_is_forbidden(make_repo):
    config = base_config()
    config["core"]["workspace_id"] = {"ITG": "bb764b8b-4e99-4c69-8026-e1cf252c192a"}
    assert "CONFIG_WORKSPACE_ID_FORBIDDEN" in codes(run_static_checks(make_repo(config=config)))


def test_secret_detection_masks_value(make_repo, capsys):
    fake_secret = "abc" + "8Q~" + "x" * 34  # forme d'un secret client Entra, valeur fictive
    root = make_repo(extra={"Flux/nb_get_ws_lh_ids.Notebook/notebook-content.py": f"s = '{fake_secret}'\n"})
    result = run_static_checks(root)
    assert "SECRET_ENTRA_CLIENT_SECRET" in codes(result)
    assert fake_secret not in capsys.readouterr().out


def test_legacy_deploy_script_with_ids_fails(make_repo):
    root = make_repo(extra={"deployment/deploy.py": 'WS = "bb764b8b-4e99-4c69-8026-e1cf252c192a"\n'})
    assert "STRUCT_LEGACY_DEPLOY_HARDCODED_IDS" in codes(run_static_checks(root))


def test_type_present_but_out_of_scope_fails(make_repo):
    root = make_repo()
    write_item(root, "", "sm_observability", "SemanticModel", {"definition.pbism": "{}"})
    assert "TYPES_SILENTLY_EXCLUDED" in codes(run_static_checks(root))


def test_type_intentionally_excluded_is_accepted(make_repo):
    deps = base_dependencies()
    deps["intentionally_excluded_item_types"] = ["SemanticModel"]
    root = make_repo(dependencies=deps)
    write_item(root, "", "sm_observability", "SemanticModel", {"definition.pbism": "{}"})
    assert "TYPES_SILENTLY_EXCLUDED" not in codes(run_static_checks(root))


def test_nested_activity_requires_recursive_path(make_repo):
    nested = pipeline([
        {"name": "if", "type": "IfCondition", "typeProperties": {"ifFalseActivities": [
            invoke_activity("log_ko", "11111111-0000-4000-8000-000000000003", SANDBOX_WS)]}}
    ])
    flat_rule = {
        "find_key": '$.properties.activities[?(@.name=="log_ko")].typeProperties.workspaceId',
        "replace_value": {"_ALL_": "$workspace.$id"}, "item_type": "DataPipeline", "item_name": "pl_nested",
    }
    root = make_repo(parameter={"key_value_replace": [flat_rule]})
    write_item(root, "Flux", "pl_nested", "DataPipeline", {"pipeline-content.json": nested})
    assert "RULE_NO_EFFECT" in codes(run_static_checks(root))


def test_nested_activity_with_recursive_path_passes(make_repo, tmp_path):
    nested = pipeline([
        {"name": "if", "type": "IfCondition", "typeProperties": {"ifFalseActivities": [
            invoke_activity("log_ko", "11111111-0000-4000-8000-000000000003", SANDBOX_WS)]}}
    ])
    base_rules = yaml.safe_load((make_repo() / "deployment" / "parameter.yml").read_text())["key_value_replace"]
    rules = base_rules + [
        {"find_key": '$..*[?(@.name=="log_ko")].typeProperties.workspaceId',
         "replace_value": {"_ALL_": "$workspace.$id"}, "item_type": "DataPipeline", "item_name": "pl_nested"},
        {"find_key": '$..*[?(@.name=="log_ko")].typeProperties.pipelineId',
         "replace_value": {"_ALL_": "$items.DataPipeline.pl_get_ws_lh_ids.$id"}, "item_type": "DataPipeline", "item_name": "pl_nested"},
    ]
    root = tmp_path / "repo"
    (root / "deployment" / "parameter.yml").write_text(yaml.safe_dump({"key_value_replace": rules}))
    write_item(root, "Flux", "pl_nested", "DataPipeline", {"pipeline-content.json": nested})
    result = run_static_checks(root)
    assert result.findings.ok, [f.message for f in result.findings.errors]


def test_registry_flags_hardcoded_environment_workspace(make_repo, tmp_path):
    itg_ws = "bb764b8b-4e99-4c69-8026-e1cf252c192a"
    root = make_repo()
    write_item(root, "", "pl_hard", "DataPipeline",
               {"pipeline-content.json": pipeline([invoke_activity("call", "22222222-0000-4000-8000-000000000004", itg_ws)])})
    registry_file = tmp_path / "reg.yml"
    registry_file.write_text(yaml.safe_dump({"guids": [{"value": itg_ws, "label": "ws MDS ITG", "category": "workspace"}]}))
    result = run_static_checks(root, Registry.load(registry_file))
    assert "GUID_HARDCODED" in codes(result)
    # Inventaire validé : le pipelineId inconnu est bloquant (correctif audit 20/10/2026).
    assert "GUID_UNKNOWN" in codes(result)


def test_unknown_guid_is_only_a_warning_while_inventory_pending(make_repo, tmp_path):
    root = make_repo(dependencies=base_dependencies(status="pending"))
    write_item(root, "", "pl_hard", "DataPipeline",
               {"pipeline-content.json": pipeline([invoke_activity("call", "22222222-0000-4000-8000-000000000004", SANDBOX_WS)])})
    result = run_static_checks(root)
    assert "GUID_UNKNOWN" not in codes(result)
    assert any(f.code == "GUID_UNKNOWN" for f in result.findings.warnings)


def test_placeholder_workspace_is_blocking_once_validated(make_repo):
    config = base_config()
    config["core"]["workspace"]["PRD"] = "__A_RELEVER__PRD__"
    assert "CONFIG_WORKSPACE_PLACEHOLDER" in codes(run_static_checks(make_repo(config=config)))


def _add_find_replace(root, rule):
    path = root / "deployment" / "parameter.yml"
    data = yaml.safe_load(path.read_text())
    data["find_replace"] = [rule]
    path.write_text(yaml.safe_dump(data, allow_unicode=True))


def test_find_replace_covers_guid_in_notebook(make_repo):
    nb = f"# Fabric notebook source\nws = '{SANDBOX_WS}'\n"
    root = make_repo(extra={"Flux/nb_get_ws_lh_ids.Notebook/notebook-content.py": nb})
    assert "GUID_HARDCODED" in codes(run_static_checks(root))
    _add_find_replace(root, {"find_value": SANDBOX_WS, "replace_value": {"_ALL_": "$workspace.$id"}, "item_type": "Notebook"})
    result = run_static_checks(root)
    assert "GUID_HARDCODED" not in codes(result), [f.message for f in result.findings.errors]


def test_find_replace_without_effect_fails(make_repo):
    root = make_repo()
    _add_find_replace(root, {"find_value": "inexistant", "replace_value": {"_ALL_": "x"}, "item_type": "Notebook"})
    assert "FIND_REPLACE_NO_EFFECT" in codes(run_static_checks(root))


def test_notebook_syntax_error_fails(make_repo):
    root = make_repo(extra={"Flux/nb_get_ws_lh_ids.Notebook/notebook-content.py": "%pip install x\ndef f(:\n"})
    assert "NOTEBOOK_SYNTAX" in codes(run_static_checks(root))


def test_missing_dependencies_file_fails(make_repo):
    root = make_repo()
    (root / "deployment" / "dependencies.yml").unlink()
    assert "STRUCT_MISSING_FILE" in codes(run_static_checks(root))


def test_no_publishable_items_fails(make_repo):
    config = base_config()
    config["core"]["repository_directory"] = "../fabric"
    root = make_repo()
    (root / "fabric").mkdir()
    (root / "deployment" / "config.yml").write_text(yaml.safe_dump(config, allow_unicode=True))
    assert "STRUCT_NO_ITEMS" in codes(run_static_checks(root))


def test_legacy_ado_pipeline_left_in_github_workflows_is_blocking(make_repo):
    root = make_repo(extra={".github/workflows/CD.yml": "trigger: none\nvariables:\n- group: Fabric-CD-Secrets\nstages:\n- stage: Deploy_ITG\n"})
    assert "STRUCT_LEGACY_PIPELINE" in codes(run_static_checks(root))




# ---------------------------------------------------------------- matrice de dépendances v1.3
def _deps(category, required, **extra):
    deps = base_dependencies(role="framework" if category == "framework" else "consumer", category=category)
    fw = {"required": required}
    if required:
        fw.update({"workspace": {e: f"FR - DP - {e} - FAB - Framework - MANAGED" for e in ("DEV", "ITG", "PPR", "PRD")},
                   "items": [{"type": "DataPipeline", "name": "pl_load_object"}]})
    deps["framework"] = fw
    deps.update(extra)
    return deps


@pytest.mark.parametrize("category,required,code", [
    ("adp", False, "DEPS_FRAMEWORK_REQUIRED"),
    ("bdp", None, "DEPS_FRAMEWORK_REQUIRED"),
    ("ucs", True, "DEPS_UCS_INDEPENDENT"),
    ("framework", True, "DEPS_FRAMEWORK_SELF"),
    ("autre", False, "DEPS_CATEGORY"),
])
def test_dependency_matrix_violations(make_repo, category, required, code):
    assert code in codes(run_static_checks(make_repo(dependencies=_deps(category, required))))


@pytest.mark.parametrize("category,required", [("framework", False), ("adp", True), ("bdp", True), ("ucs", False)])
def test_dependency_matrix_reference_cases_pass(make_repo, category, required):
    found = codes(run_static_checks(make_repo(dependencies=_deps(category, required))))
    assert not {c for c in found if c.startswith("DEPS_")}, found


def test_missing_category_is_rejected(make_repo):
    deps = base_dependencies()
    deps.pop("category")
    assert "DEPS_CATEGORY" in codes(run_static_checks(make_repo(dependencies=deps)))


def test_category_must_match_role(make_repo):
    deps = _deps("adp", True)
    deps["role"] = "framework"
    assert "DEPS_CATEGORY_ROLE" in codes(run_static_checks(make_repo(dependencies=deps)))


def test_data_dependency_is_documented_but_never_blocking(make_repo):
    ok = _deps("ucs", False, data_dependencies=[{"repository": "its-dp-fab-adp-mds-managed", "kind": "data", "description": "tables MDS"}])
    assert not {c for c in codes(run_static_checks(make_repo(dependencies=ok))) if c.startswith("DEPS_")}


@pytest.mark.parametrize("bad", [
    {"repository": "its-dp-fab-adp-mds-managed", "kind": "deployment"},
    {"repository": "its-dp-fab-adp-mds-managed", "kind": "data", "blocking": True},
    {"kind": "data"},
])
def test_data_dependency_cannot_become_a_deployment_dependency(make_repo, bad):
    found = codes(run_static_checks(make_repo(dependencies=_deps("ucs", False, data_dependencies=[bad]))))
    assert found & {"DEPS_DATA_DEPENDENCY_FORMAT", "DEPS_DATA_DEPENDENCY_BLOCKING"}


def _cross_rule(workspace):
    return [{"find_key": '$.properties.activities[?(@.name=="nb_get_ws_lh_ids")].typeProperties.workspaceId',
             "replace_value": {e: f"$workspace.{workspace.format(env=e)}.$id" for e in ("DEV", "ITG", "PPR", "PRD")},
             "item_type": "DataPipeline", "item_name": "pl_get_ws_lh_ids"},
            {"find_key": '$.properties.activities[?(@.name=="nb_get_ws_lh_ids")].typeProperties.notebookId',
             "replace_value": {"_ALL_": "$items.Notebook.nb_get_ws_lh_ids.$id"}, "item_type": "DataPipeline", "item_name": "pl_get_ws_lh_ids"}]


def test_bdp_deploy_time_reference_to_an_adp_is_rejected(make_repo):
    root = make_repo(dependencies=_deps("bdp", True), parameter={"key_value_replace": _cross_rule("FR - DP - {env} - FAB - ADP - MDS - MANAGED")})
    assert "DEPS_CROSS_WORKSPACE_FORBIDDEN" in codes(run_static_checks(root))


def test_ucs_deploy_time_reference_to_framework_is_rejected(make_repo):
    root = make_repo(dependencies=_deps("ucs", False), parameter={"key_value_replace": _cross_rule("FR - DP - {env} - FAB - Framework - MANAGED")})
    assert "DEPS_CROSS_WORKSPACE_FORBIDDEN" in codes(run_static_checks(root))


def test_adp_reference_to_framework_is_allowed(make_repo):
    root = make_repo(dependencies=_deps("adp", True), parameter={"key_value_replace": _cross_rule("FR - DP - {env} - FAB - Framework - MANAGED")})
    assert "DEPS_CROSS_WORKSPACE_FORBIDDEN" not in codes(run_static_checks(root))


def test_framework_items_pending_is_a_warning_then_an_error(make_repo):
    pending = _deps("bdp", True)
    pending["framework"]["items"] = []
    pending["inventory_status"] = "pending"
    result = run_static_checks(make_repo(dependencies=pending))
    assert "DEPS_FRAMEWORK_ITEMS" not in codes(result) and any(f.code == "DEPS_FRAMEWORK_ITEMS" for f in result.findings.warnings)
