# Changelog

Toutes les évolutions notables du socle `its-dp-fab-cicd-common` sont consignées ici.

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; versions : [SemVer](https://semver.org/lang/fr/),
publiées par tag `vX.Y.Z` (un tag publié n'est jamais déplacé). Les versions antérieures
(`v1.3.0`, `v1.3.1`, `v1.4.0`) sont décrites dans le README, section « Versionnement ».

## [Unreleased]

## [1.5.1] - 2026-11-11

Version corrective, sans changement de comportement des templates ni de `deploy.py` pour un dépôt
conforme. Elle prend en compte les 20 dépôts Fabric du périmètre, tous sur `refs/tags/v1.5.0` :
10 des 11 dépôts initiaux (ADP Alexandrie est retiré du périmètre) et 10 dépôts initialisés du
05/11 au 11/11/2026 (ADP GDS, Referentiel et Sirene ; BDP Campaign, Contact, Lead et
Opportunite ; UCS Hubspot, MyBSC / SNOW et Referentiel).

### Fixed

- `fabcicd.__version__` aligné sur la version publiée : `"1.5.1"`. Le tag `v1.5.0`, déjà
  publié, contient encore `"1.0.0"` et n'est pas déplacé (`main` portait `"1.5.0"` depuis
  `703253c`).
- Registre `CICD/registry/known_guids.yml` : capacité Fabric DEV/ITG/PPR corrigée,
  `29cdc503-…` (erronée) remplacée par `dc14404e-abda-4191-8a07-99980bec2d41`
  (`frfrfcdevdp0001`) ; libellé de la capacité PRD complété
  (`gomfcaprodeuwfabricdataplatformfr0001`).
- Registre : workspaces du BDP Performance Engagement libellés avec le nom du dépôt réel,
  `its-dp-fab-bdp-performance-engagement-managed` (au lieu de
  `its-dp-fab-bdp-engagement-performance-managed`).

### Removed

- ADP Alexandrie (`its-dp-fab-adp-alexandrie-managed`) retiré du périmètre du socle : ses 3
  workspaces (ITG `206e2eea-…`, PPR `f139506e-…`, PRD `e447de01-…`) sont supprimés du registre et
  le dépôt de la matrice de dépendances, du README et des tests.

### Changed

- Registre complété à partir de la liste des workspaces par environnement : 80 workspaces de
  20 dépôts (34 auparavant, ITG/PPR/PRD des 11 dépôts initiaux). Ajouts : les workspaces DEV de
  tous les dépôts (le préfixe MDS DEV `a699158a-` devient la valeur complète) et les 4
  environnements des 10 nouveaux dépôts. **Effet** : un ID de ces workspaces
  laissé en dur dans un item, hors règle `parameter.yml`, lève désormais `GUID_HARDCODED`
  (vérifié sans régression sur les 20 dépôts : aucun n'en contient). Les préfixes
  déclarés en compensation dans `guid_policy.forbidden_prefixes` des nouveaux dépôts deviennent
  redondants, mais restent valides.
- UCS MyBSC / SNOW : `d7578bcf-…` est l'ID du workspace DEV ; l'ID PPR, relevé dans Fabric le
  11/11/2026, est `8f97d25d-495c-4b93-9699-b2c4947033e1` (la liste des workspaces donnait à tort
  l'ID DEV pour PPR, et l'ancien `deploy.py` du dépôt visait donc DEV pour PPR).
- Matrice de dépendances (`fabcicd/dependencies.py`) : liste des dépôts par catégorie mise à
  jour (framework 1, ADP 5, BDP 10, UCS 4). Règles R1 à R6 inchangées.
- README : périmètre de 20 dépôts, ligne `v1.5.1` du versionnement, arborescence.
- Commentaires « 11 dépôts » de `ci-common.yml`, `cd-pipeline.yml` et `.yamllint.yml`
  remplacés par « dépôts consommateurs ».
- `fabric/test.md` (contenu « test », référencé nulle part) remplacé par `fabric/.gitkeep`,
  comme dans les dépôts consommateurs.

### Added

- Test `tests/test_version.py` : `fabcicd.__version__` doit être égal à la dernière version
  numérotée de ce fichier.
- Tests `tests/test_registry.py` : format du registre (GUID complet ou préfixe, minuscules,
  catégorie, booléen), absence de doublon et de préfixe masquant, couverture des 20 dépôts par
  environnement, correction de capacité.
- README, section « Versionnement » : convention `CHANGELOG.md` dans chaque dépôt (versions
  numérotées pour le socle, entrées datées pour les consommateurs).

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
