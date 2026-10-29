# Changelog

Toutes les évolutions notables du socle `its-dp-fab-cicd-common` sont consignées ici.

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; versions : [SemVer](https://semver.org/lang/fr/),
publiées par tag `vX.Y.Z` (un tag publié n'est jamais déplacé). Les versions antérieures
(`v1.3.0`, `v1.3.1`, `v1.4.0`) sont décrites dans le README, section « Versionnement ».

## [Unreleased]

### Fixed

- `fabcicd.__version__` aligné sur la version publiée : `"1.0.0"` → `"1.5.0"`. Le tag `v1.5.0`,
  déjà publié, contient encore `"1.0.0"` et n'est pas déplacé : la correction sera livrée en
  `v1.5.1` (`__version__` à passer à `"1.5.1"` avec la section `[1.5.1]`).

### Added

- Test `tests/test_version.py` : `fabcicd.__version__` doit être égal à la dernière version
  numérotée de ce fichier.
- README, section « Versionnement » : convention `CHANGELOG.md` dans chaque dépôt (versions
  numérotées pour le socle, entrées datées pour les consommateurs, dont l'ancien `CHANGES.md`
  est renommé).

## [1.5.0] - 2026-10-29

### Changed

- `cicdRoot` devient la seule source de vérité du dossier CI/CD : les templates le transmettent à
  `deploy.py` par la variable `CICD_ROOT` (défaut `CICD`), dont `fabcicd/settings.py` dérive
  `DEPLOYMENT_DIR` ; `deploy.py` s'arrête (code 4) avec un message explicite si
  `<repo>/<CICD_ROOT>/deployment` est introuvable.
- Templates : `cicdRoot` ne désigne plus que le dossier CI/CD **du dépôt consommateur**
  (`deployment/`, `pipelines/`, variable `CICD_ROOT`). Le dossier CI/CD **du socle** (`tools/`,
  `requirements.txt`) devient le paramètre distinct `commonCicdRoot` (défaut `CICD`, relatif au
  checkout `s/cicd_common`), propagé à `stage-deploy.yml` et `steps-python.yml`. Valeurs par
  défaut : chemins identiques à la v1.4.1.
- **Changement de comportement** – `validate` (et `scan`, `deploy`, `verify`) sur un dépôt sans
  `<CICD_ROOT>/deployment` sort avec le code 4 (erreur de configuration) au lieu du code 1
  (`STRUCT_MISSING_FILE`).
- **Changement de comportement** – garde `repository_directory` : sur les quatre commandes qui
  prennent `--repo` (`validate`, `scan`, `deploy`, `verify`), un `repository_directory` absent ou
  sans item Fabric (aucun `.platform`) provoque un arrêt avec le code 4 et le chemin résolu, avant
  les contrôles statiques, au lieu du code 1 (`STRUCT_ITEMS` / `STRUCT_NO_ITEMS`).
- **Changement de comportement** – `STRUCT_NO_ITEMS` (code 1) n'est plus levé que lorsque des
  items existent mais sont tous exclus par `folder_exclude_regex`.
- **Changement de comportement** – `steps-python.yml` : paramètre `cicdRoot` renommé
  `commonCicdRoot` (défaut `CICD`). Seuls les dépôts qui appelleraient `steps-python.yml`
  directement doivent adapter leur appel ; ceux qui étendent `cd-pipeline.yml` ou
  `validate-pipeline.yml` ne sont pas concernés.
- README : `repository_directory` explicite obligatoire dès que `cicdRoot` n'est pas un dossier à
  un seul niveau (exemple `cicdRoot: a/b` → `../../../fabric`) ; `CICD_ROOT` lue au chargement du
  module, donc posée avant le lancement du processus ; codes de sortie mis à jour.

## [1.4.1] - 2026-10-29

### Fixed

- Paquet `fabcicd` : fichier d'initialisation renommé `___init___.py` → `__init__.py`.
  `fabcicd` était importé comme paquet namespace, sans `ENVIRONMENTS` ni `log` :
  `deploy.py` échouait dès l'import.
- Imports relatifs invalides `from ....tools.fabcicd import …` dans `fabcicd/settings.py` et
  `fabcicd/findings.py`, remplacés par `from . import …`.
- Tests : imports `from CICD.tests.conftest import …` remplacés par `from conftest import …`
  (`pythonpath = tools tests` de `pytest.ini`) ; la collecte des tests échouait.
- Fixture des tests (`make_repo`) alignée sur la structure des dépôts consommateurs : items
  générés sous `fabric/` (constante `FABRIC_DIR`), `repository_directory` par défaut
  `../../fabric`.
- Templates Azure DevOps alignés sur l'arborescence réelle du socle : `tools/` →
  `CICD/tools/` (`deploy.py`, `requirements.txt`).
- Contrôles du dépôt consommateur alignés sur son arborescence réelle : `deployment/` →
  `CICD/deployment/` (`settings.load_repo`, `static_checks.check_structure`) et yamllint de la
  validation de PR sur `CICD/deployment` et `CICD/pipelines` au lieu de `deployment ./*.yml`.
- `ci-common.yml` : exécution depuis `CICD/` (où se trouvent `pytest.ini`, `tools/`,
  `pipelines/`, `registry/`), configuration yamllint lue à la racine (`../.yamllint.yml`).

### Added

- Paramètre de template `cicdRoot` (défaut `CICD`) dans `validate-pipeline.yml`,
  `cd-pipeline.yml`, `stage-deploy.yml` et `steps-python.yml` : chemin du dossier CICD,
  centralisé au lieu d'être répété dans chaque template.
- Constante `DEPLOYMENT_DIR = CICD/deployment` dans `fabcicd/settings.py`, utilisée par le
  chargement de la configuration et par les contrôles de structure.
- `.yamllint.yml` à la racine (fichier référencé par les templates mais absent du dépôt ;
  règles reprises de l'ancien `CICD/yamllint.yml`, supprimé dans `1a01dd4`).
- `CHANGELOG.md` (ce fichier).

### Changed

- Valeur par défaut de `repository_directory` (si absente de `config.yml`) : `".."` →
  `"../../fabric"`, résolue depuis `CICD/deployment/`, comme fabric-cicd 1.4.0.
- `STRUCT_LEGACY_DEPLOY_HARDCODED_IDS` détecte l'ancien `deploy.py` à la racine
  (`deployment/deploy.py`) et sous `CICD/deployment/`.
- Les dépôts consommateurs étendent désormais `CICD/pipelines/templates/<template>.yml@cicd_common`
  (au lieu de `pipelines/templates/…`) : changement incompatible, à livrer avec un nouveau tag.
- README : arborescence, commandes locales et chemins alignés sur `CICD/`.
