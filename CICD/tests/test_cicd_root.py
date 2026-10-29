"""Dossiers CI/CD : ``cicdRoot`` (consommateur, transmis au Python par ``CICD_ROOT``) et
``commonCicdRoot`` (socle, chemin de ``tools/`` dans les templates uniquement).

``DEPLOYMENT_DIR`` est calculé à l'import de ``fabcicd.settings`` : les tests rechargent le
module après avoir positionné la variable, puis restaurent son état d'origine (les autres
modules gardent ainsi les mêmes objets ``SettingsError``, ``RepoContext``…). ``static_checks``
importe ``DEPLOYMENT_DIR`` par valeur : la fixture lui propage la valeur rechargée.
"""

from __future__ import annotations

import importlib
import re
import shutil
from pathlib import Path

import pytest
import yaml

import deploy as cli
from conftest import FABRIC_DIR, base_config
from fabcicd import settings, static_checks

TEMPLATES = Path(__file__).resolve().parents[1] / "pipelines" / "templates"


@pytest.fixture
def reload_settings(monkeypatch):
    """Recharge ``fabcicd.settings`` avec ``CICD_ROOT`` = valeur donnée (None : absente)."""
    saved = dict(settings.__dict__)

    def _reload(value):
        if value is None:
            monkeypatch.delenv("CICD_ROOT", raising=False)
        else:
            monkeypatch.setenv("CICD_ROOT", value)
        module = importlib.reload(settings)
        monkeypatch.setattr(static_checks, "DEPLOYMENT_DIR", module.DEPLOYMENT_DIR)
        return module

    yield _reload
    settings.__dict__.clear()
    settings.__dict__.update(saved)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_deployment_dir_defaults_to_cicd(reload_settings, value):
    module = reload_settings(value)
    assert module.DEPLOYMENT_DIR == Path("CICD") / "deployment"
    assert isinstance(module.DEPLOYMENT_DIR, Path) and not module.DEPLOYMENT_DIR.is_absolute()


@pytest.mark.parametrize("value, expected", [("ci/cd", Path("ci/cd/deployment")), ("Pipelines", Path("Pipelines/deployment"))])
def test_deployment_dir_follows_cicd_root(reload_settings, value, expected):
    assert reload_settings(value).DEPLOYMENT_DIR == expected


def test_load_repo_reads_deployment_dir_under_cicd_root(make_repo, reload_settings):
    root = make_repo()
    (root / "CICD").rename(root / "Pipelines")
    module = reload_settings("Pipelines")
    ctx = module.load_repo(root)
    assert ctx.config_path == (root / "Pipelines" / "deployment" / "config.yml").resolve()


@pytest.mark.parametrize("command", [["validate"], ["scan", "--out", "inv.csv"], ["deploy", "--env", "ITG"], ["verify", "--env", "ITG"]])
def test_cli_stops_when_deployment_dir_missing(tmp_path, reload_settings, capsys, command):
    reload_settings(None)
    code = cli.main([command[0], "--repo", str(tmp_path), *command[1:]])
    out = capsys.readouterr().out
    assert code == cli.EXIT_USAGE
    assert f"Dossier de déploiement introuvable : {(tmp_path / 'CICD' / 'deployment').resolve()}" in out
    assert "CICD_ROOT non définie, défaut 'CICD'" in out
    assert "vérifier le paramètre cicdRoot / la variable CICD_ROOT" in out


def test_cli_stops_when_cicd_root_does_not_match_repo(make_repo, reload_settings, capsys):
    root = make_repo()  # contient CICD/deployment, pas Autre/deployment
    reload_settings("Autre")
    assert cli.main(["validate", "--repo", str(root)]) == cli.EXIT_USAGE
    out = capsys.readouterr().out
    assert f"Dossier de déploiement introuvable : {(root / 'Autre' / 'deployment').resolve()} (CICD_ROOT='Autre')" in out


def _deploy_steps(node):
    """Étapes (dict) dont le script exécute deploy.py, quelle que soit l'imbrication ${{ if }}."""
    if isinstance(node, dict):
        script = node.get("script") or (node.get("inputs") or {}).get("inlineScript") or ""
        if "deploy.py" in script:
            yield node
        for value in node.values():
            yield from _deploy_steps(value)
    elif isinstance(node, list):
        for value in node:
            yield from _deploy_steps(value)


@pytest.mark.parametrize("name, expected", [("cd-pipeline.yml", 3), ("stage-deploy.yml", 4), ("validate-pipeline.yml", 1)])
def test_every_deploy_step_receives_cicd_root(name, expected):
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    steps = list(_deploy_steps(yaml.safe_load(text)))
    assert len(steps) == expected
    for step in steps:
        assert (step.get("env") or {}).get("CICD_ROOT") == "${{ parameters.cicdRoot }}", step.get("displayName")
        assert "COMMON_CICD_ROOT" not in (step.get("env") or {})  # le Python ne lit pas le dossier du socle
    assert "Doit rester égal à DEPLOYMENT_DIR" not in text


# ---------------------------------------------------------------- cicdRoot / commonCicdRoot
TEMPLATE_NAMES = ("cd-pipeline.yml", "stage-deploy.yml", "steps-python.yml", "validate-pipeline.yml")
# Chemins du socle avant la séparation des paramètres (cicdRoot = CICD) : inchangés par défaut.
LEGACY_TOOLS = "$(Pipeline.Workspace)/s/cicd_common/CICD/tools"


def _params(name):
    return {p["name"]: p for p in yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))["parameters"]}


def _render(name, **values):
    """Texte du template, paramètres remplacés (valeur donnée, sinon défaut du template)."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for param, spec in _params(name).items():
        if isinstance(spec.get("default"), str):
            text = text.replace(f"${{{{ parameters.{param} }}}}", values.get(param, spec["default"]))
    return text


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_cicd_root_parameters_default_to_cicd(name):
    params = _params(name)
    assert params["commonCicdRoot"]["default"] == "CICD"
    if name != "steps-python.yml":  # steps-python n'installe que les dépendances du socle
        assert params["cicdRoot"]["default"] == "CICD"
    else:
        assert "cicdRoot" not in params


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_no_template_reaches_tools_through_cicd_root(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    assert not re.search(r"parameters\.cicdRoot \}\}/(tools|registry|pipelines/templates)", text)
    assert "/tools" not in text or "${{ parameters.commonCicdRoot }}/tools" in text


@pytest.mark.parametrize("name", ["cd-pipeline.yml", "stage-deploy.yml", "validate-pipeline.yml"])
def test_defaults_keep_previous_paths(name):
    text = _render(name)
    tools = re.findall(r"\S*/tools\b", text)
    assert tools and all(LEGACY_TOOLS in path for path in tools)
    assert "CICD_ROOT: CICD" in text


def test_steps_python_default_keeps_previous_requirements_path():
    assert f'-r "{LEGACY_TOOLS}/requirements.txt"' in _render("steps-python.yml")


def test_both_parameters_propagate_to_nested_templates():
    cd = (TEMPLATES / "cd-pipeline.yml").read_text(encoding="utf-8")
    assert cd.count("template: stage-deploy.yml") == 4
    assert cd.count("          cicdRoot: ${{ parameters.cicdRoot }}\n          commonCicdRoot: ${{ parameters.commonCicdRoot }}") == 4
    for name in ("cd-pipeline.yml", "stage-deploy.yml", "validate-pipeline.yml"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        block = text[text.index("template: steps-python.yml"):]
        assert "commonCicdRoot: ${{ parameters.commonCicdRoot }}" in block.split("- script")[0], name


@pytest.mark.parametrize("name", ["cd-pipeline.yml", "stage-deploy.yml", "validate-pipeline.yml"])
def test_custom_consumer_cicd_root_leaves_common_paths_unchanged(name):
    text = _render(name, cicdRoot="a/b")
    assert all(LEGACY_TOOLS in path for path in re.findall(r"\S*/tools\b", text))
    assert "CICD_ROOT: a/b" in text and "CICD_ROOT: CICD" not in text
    if name == "validate-pipeline.yml":
        assert '"a/b/deployment" "a/b/pipelines"' in text


def _move_cicd(root, target="a/b"):
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / "CICD").rename(root / target)
    return root / target / "deployment" / "config.yml"


def test_custom_cicd_root_with_explicit_repository_directory_passes(make_repo, reload_settings):
    config = base_config()
    config["core"]["repository_directory"] = f"../../../{FABRIC_DIR}"  # a/b/deployment → racine
    root = make_repo(config=config)
    _move_cicd(root)
    reload_settings("a/b")
    assert cli.main(["validate", "--repo", str(root)]) == cli.EXIT_OK


def test_custom_cicd_root_with_implicit_repository_directory_stops(make_repo, reload_settings, capsys):
    root = make_repo()  # repository_directory ../../fabric : faux depuis a/b/deployment
    _move_cicd(root)
    reload_settings("a/b")
    assert cli.main(["validate", "--repo", str(root)]) == cli.EXIT_USAGE
    out = capsys.readouterr().out
    assert f"Dossier des items Fabric introuvable : {(root / 'a' / FABRIC_DIR).resolve()}" in out
    assert "repository_directory='../../fabric'" in out


# ---------------------------------------------------------------- repository_directory
@pytest.mark.parametrize("command", [["validate"], ["scan", "--out", "inv.csv"], ["deploy", "--env", "ITG"], ["verify", "--env", "PRD"]])
def test_cli_stops_when_repository_directory_missing(make_repo, reload_settings, capsys, command):
    reload_settings(None)
    root = make_repo()
    shutil.rmtree(root / FABRIC_DIR)
    assert cli.main([command[0], "--repo", str(root), *command[1:]]) == cli.EXIT_USAGE
    out = capsys.readouterr().out
    assert f"Dossier des items Fabric introuvable : {(root / FABRIC_DIR).resolve()}" in out
    assert "vérifier repository_directory dans config.yml" in out


@pytest.mark.parametrize("command", [["validate"], ["scan", "--out", "inv.csv"], ["deploy", "--env", "ITG"], ["verify", "--env", "PRD"]])
def test_cli_stops_when_repository_directory_has_no_fabric_item(make_repo, reload_settings, capsys, command):
    reload_settings(None)
    root = make_repo()
    shutil.rmtree(root / FABRIC_DIR)
    (root / FABRIC_DIR).mkdir()
    (root / FABRIC_DIR / "test.md").write_text("placeholder", encoding="utf-8")
    assert cli.main([command[0], "--repo", str(root), *command[1:]]) == cli.EXIT_USAGE
    assert f"Aucun item Fabric (fichier .platform) sous {(root / FABRIC_DIR).resolve()}" in capsys.readouterr().out


def test_all_items_excluded_raises_struct_no_items(make_repo, reload_settings, capsys):
    # Items présents mais tous exclus par folder_exclude_regex : la garde laisse passer (code 4
    # réservé au dossier absent ou vide), le contrôle statique STRUCT_NO_ITEMS bloque (code 1).
    reload_settings(None)
    root = make_repo(config=base_config(exclude="^/Flux"))
    assert "STRUCT_NO_ITEMS" in {f.code for f in static_checks.run_static_checks(root).findings.errors}
    assert cli.main(["validate", "--repo", str(root)]) == cli.EXIT_CHECKS
    out = capsys.readouterr().out
    assert "STRUCT_NO_ITEMS" in out
    assert "Aucun item Fabric" not in out and "introuvable" not in out


def test_repository_directory_is_resolved_for_the_deploy_environment(make_repo, reload_settings, capsys):
    reload_settings(None)
    config = base_config()
    config["core"]["repository_directory"] = {"_ALL_": f"../../{FABRIC_DIR}", "PRD": "../../absent"}
    root = make_repo(config=config)
    assert cli.main(["verify", "--repo", str(root), "--env", "PRD"]) == cli.EXIT_USAGE
    assert f"introuvable : {(root / 'absent').resolve()}" in capsys.readouterr().out
