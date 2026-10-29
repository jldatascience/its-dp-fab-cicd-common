# its-dp-fab-cicd-common — socle commun CI/CD Microsoft Fabric

Dépôt **nouveau**, à créer dans l'organisation GitHub de la Data Platform. Il contient
tout ce qui est partagé par les 11 dépôts Fabric : les templates Azure DevOps, l'outil
de déploiement Python et ses tests. Les dépôts Fabric ne contiennent plus de script de
déploiement, seulement leur configuration (`CICD/deployment/*.yml`) et trois YAML minces
(`CICD/pipelines/CI.yml`, `CD.yml`, `PR-Validation.yml`).

Version courante : **v1.4.0** (détail des versions dans `CHANGELOG.md`).

```
its-dp-fab-cicd-common/
├── .yamllint.yml                          règles YAML (CI du socle et validation des PR)
├── CHANGELOG.md                           journal des versions
├── README.md
├── CICD/
│   ├── pytest.ini                         configuration pytest (à lancer depuis CICD/)
│   ├── pipelines/
│   │   ├── ci-common.yml                  CI de ce dépôt (tests, lint) — pipeline ADO à créer
│   │   └── templates/
│   │       ├── validate-pipeline.yml      validation de PR (étendu par PR-Validation.yml)
│   │       ├── cd-pipeline.yml            CD complète (étendu par CD.yml)
│   │       ├── stage-deploy.yml           un stage d'environnement
│   │       └── steps-python.yml           Python + dépendances épinglées
│   ├── registry/known_guids.yml           GUID relevés à l'audit (classement des références)
│   ├── tools/
│   │   ├── deploy.py                      point d'entrée unique (validate, deploy, verify, scan, lookup, manifest)
│   │   ├── requirements.txt               fabric-cicd==1.4.0 et dépendances épinglées
│   │   ├── requirements-dev.txt           dépendances de test
│   │   └── fabcicd/                       contrôles statiques, matrice de dépendances, pré-vol,
│   │                                      porte Framework (ADP/BDP), vérification, rapport
│   └── tests/                             tests unitaires (aucun appel réseau)
└── fabric/test.md
```

## Arborescence attendue dans les dépôts consommateurs

Les templates et `deploy.py` lisent, à partir de la racine du dépôt (ou de l'artefact CI) :

| Chemin | Lu par |
|---|---|
| `CICD/pipelines/CI.yml`, `CD.yml`, `PR-Validation.yml` | Azure DevOps ; yamllint (validation de PR) |
| `CICD/deployment/config.yml`, `parameter.yml`, `dependencies.yml` | `deploy.py` (`DEPLOYMENT_DIR` = `<CICD_ROOT>/deployment`, cf. ci-dessous) ; yamllint |
| `fabric/` | fabric-cicd, via `repository_directory: "../../fabric"` dans `config.yml` |

`repository_directory` et `parameter` sont résolus **relativement au dossier de `config.yml`**
(`CICD/deployment/`), par fabric-cicd 1.4.0 comme par `fabcicd`, et non depuis le dossier
d'exécution du pipeline.

Les templates distinguent deux dossiers CI/CD, par deux paramètres indépendants :

| Paramètre | Désigne | Défaut | Usages |
|---|---|---|---|
| `cicdRoot` | dossier CI/CD **du dépôt consommateur**, relatif à sa racine | `CICD` | `<cicdRoot>/deployment` (`deploy.py`, yamllint), `<cicdRoot>/pipelines` (yamllint) |
| `commonCicdRoot` | dossier CI/CD **du socle**, relatif à son checkout `$(Pipeline.Workspace)/s/cicd_common` | `CICD` | `<commonCicdRoot>/tools/deploy.py`, `<commonCicdRoot>/tools/requirements.txt` |

Un consommateur peut donc changer `cicdRoot` sans toucher au chemin du socle. Les consommateurs
actuels ne passent aucun des deux paramètres : les chemins sont identiques à ceux de la v1.4.1.

`cicdRoot` est la seule source de vérité du dossier consommateur : chaque étape qui exécute
`deploy.py` le transmet par la variable d'environnement `CICD_ROOT`
(`env: CICD_ROOT: ${{ parameters.cicdRoot }}`), dont `fabcicd/settings.py` dérive
`DEPLOYMENT_DIR = <CICD_ROOT>/deployment`, relatif à la racine passée en `--repo`. Variable absente
ou vide (exécution locale, tests) : `CICD`, soit `CICD/deployment`. `CICD_ROOT` est lue une seule
fois, au chargement du module : elle doit donc être posée avant le lancement du processus, ce que
font les templates (`env:` de l'étape). `commonCicdRoot` n'est pas transmis au Python : `deploy.py`
trouve `fabcicd/` et `registry/known_guids.yml` à partir de son propre emplacement.

Si `<--repo>/<CICD_ROOT>/deployment` n'existe pas, `validate`, `scan`, `deploy` et `verify`
s'arrêtent (code 4) avec le chemin cherché et la valeur de `CICD_ROOT`. Même arrêt (code 4) si le
dossier pointé par `repository_directory` (résolu pour `--env`, ITG pour `validate` et `scan`)
n'existe pas ou ne contient aucun item Fabric (fichier `.platform`) : le message donne le chemin
résolu et la valeur lue dans `config.yml`.

**`repository_directory` doit être explicite** dans `config.yml` dès que `cicdRoot` n'est pas un
dossier à un seul niveau sous la racine : le défaut `../../fabric` suppose
`<racine>/<cicdRoot>/deployment/config.yml`. Exemple pour `cicdRoot: a/b` (config dans
`a/b/deployment/`) :

```yaml
core:
  repository_directory: "../../../fabric"   # a/b/deployment → racine du dépôt → fabric/
```

Les dépôts l'étendent ainsi :

```yaml
extends:
  template: CICD/pipelines/templates/cd-pipeline.yml@cicd_common        # CD.yml
  # template: CICD/pipelines/templates/validate-pipeline.yml@cicd_common # PR-Validation.yml
```

## Utilisation locale

Depuis la racine du dépôt :

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r CICD/tools/requirements-dev.txt
(cd CICD && python -m pytest)                      # 114 tests, sans réseau (pytest.ini est dans CICD/)
yamllint -c .yamllint.yml CICD/pipelines CICD/registry
python CICD/tools/deploy.py validate --repo ../its-dp-fab-adp-mds-managed
python CICD/tools/deploy.py scan --repo ../its-dp-fab-bdp-compte-managed --out inventaire.csv
# Lecture seule sur le tenant (identité az login ou variables FABRIC_*) :
python CICD/tools/deploy.py deploy --repo ../its-dp-fab-adp-mds-managed --env ITG --mode validate --auth cli --framework-gate off
```

## Versionnement

Les 11 dépôts référencent ce socle par **tag**, jamais par `main` depuis une CD :

| Tag | À utiliser par |
|---|---|
| `refs/tags/v1.4.0` | cible des 11 dépôts : service connections ARM par environnement (marqueurs clarifiés) |
| `refs/tags/v1.3.1` | retour arrière : ID en clair admis |
| `refs/tags/v1.3.0` | retour arrière : comportement d'origine |

Un tag n'existe qu'une fois créé et poussé dans GitHub : tant que `v1.4.0` n'y figure pas, les
dépôts restent sur leur tag actuel.

Toute évolution : PR sur `main` → CI verte → tag `v1.x.y` → PR de montée de version dans les
dépôts consommateurs (pilote d'abord). Un tag publié n'est jamais déplacé.

## ID en clair dans parameter.yml (depuis v1.3.1)

Par défaut, un GUID littéral dans `replace_value` est bloquant (`RULE_LITERAL_GUID`). Un dépôt qui
écrit en clair les ID des items Framework et des connexions, une valeur par environnement
(scénario B), l'autorise explicitement dans `CICD/deployment/dependencies.yml` :

```yaml
guid_policy:
  allow_literal_ids: true
```

Le constat devient informatif (`RULE_LITERAL_GUID_ALLOWED`). Les autres contrôles sont inchangés ;
`config.yml` continue de désigner le workspace cible par son nom (`CONFIG_WORKSPACE_ID_FORBIDDEN`).

## Authentification, Key Vault et service connections (v1.4.0)

Deux mécanismes distincts, à ne pas confondre :

- **service connection Azure DevOps** (type Azure Resource Manager) : authentifie le pipeline
  auprès d'Azure. Une **distincte par environnement** (ITG, PPR, PRD), créée par Ops en
  **fédération d'identité**. Déclarée dans `CICD/pipelines/CD.yml` → `serviceConnections`.
- **connexion Fabric** (`FABPIPELINES_<ENV>_DP_FABRIC`, `MDS_<ENV>_DP_FABRIC`…) : utilisée par les
  items Fabric à l'exécution. Gérée dans Fabric et `parameter.yml`, jamais dans Key Vault.

Chaque stage lit dans le **coffre unique** `frfrkvdevdp0001`, avec la service connection de son
environnement, les 3 secrets `<ENV>-AZURE-CLIENT-ID / -CLIENT-SECRET / -TENANT-ID`
(`AzureKeyVault@2`). Cette tâche est dans le template imposé (Required template) : un dépôt ne peut
ni la retirer ni la remplacer par un groupe de variables. Le SPN de l'environnement publie
ensuite dans Fabric (`authMode: secret`).

```yaml
# CICD/pipelines/CD.yml d'un dépôt
authMode: secret
secretSource: keyVault
keyVaultName: frfrkvdevdp0001   # coffre unique ITG, PPR, PRD
serviceConnections:             # une service connection ARM distincte par environnement
  DEV: ""
  ITG: __A_CONFIRMER_SERVICE_CONNECTION_ARM_ITG__
  PPR: __A_CONFIRMER_SERVICE_CONNECTION_ARM_PPR__
  PRD: __A_CONFIRMER_SERVICE_CONNECTION_ARM_PRD__
```

Gardes de compilation : service connection vide, marquée `__A_CONFIRMER…` ou partagée entre deux
environnements. Les ID des items Fabric ne passent jamais par Key Vault ni par `$ENV:`.

## Codes de sortie de deploy.py

| Code | Signification |
|---|---|
| 0 | succès |
| 1 | contrôle bloquant en échec (statique, pré-vol, porte Framework, vérification) |
| 2 | échec de publication fabric-cicd |
| 3 | refus de politique (inventaire `pending`, branche non autorisée) |
| 4 | erreur d'usage, d'authentification ou de configuration (`<CICD_ROOT>/deployment` absent, `repository_directory` absent ou sans item Fabric) |
