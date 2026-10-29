"""Dossier CI/CD (paramètre de template ``cicdRoot``) transmis au code Python par ``CICD_ROOT``.

``DEPLOYMENT_DIR`` est calculé à l'import de ``fabcicd.settings`` : les tests rechargent le
module après avoir positionné la variable, puis restaurent son état d'origine (les autres
modules gardent ainsi les mêmes objets ``SettingsError``, ``RepoContext``…).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

import deploy as cli
from fabcicd import settings

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
        return importlib.reload(settings)

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
    assert "Doit rester égal à DEPLOYMENT_DIR" not in text
