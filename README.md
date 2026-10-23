# its-dp-fab-cicd-common — socle commun CI/CD Microsoft Fabric

Dépôt **nouveau**, à créer dans l'organisation GitHub de la Data Platform. Il contient
tout ce qui est partagé par les 11 dépôts Fabric : les templates Azure DevOps, l'outil
de déploiement Python et ses tests. Les dépôts Fabric ne contiennent plus de script de
déploiement, seulement leur configuration (`deployment/*.yml`) et trois YAML minces.

Version courante : **v1.4.0** (détail des versions dans `CHANGELOG.md`).

```
its-dp-fab-cicd-common/
├── .yamllint.yml                      règles YAML (CI du socle et validation des PR)
├── CHANGELOG.md                       journal des versions
├── pytest.ini
├── pipelines/
│   ├── ci-common.yml                  CI de ce dépôt (tests, lint) — pipeline ADO à créer
│   └── templates/
│       ├── validate-pipeline.yml      validation de PR (étendu par PR-Validation.yml)
│       ├── cd-pipeline.yml            CD complète (étendu par CD.yml)
│       ├── stage-deploy.yml           un stage d'environnement
│       └── steps-python.yml           Python + dépendances épinglées
├── registry/known_guids.yml           GUID relevés à l'audit (classement des références)
├── tools/
│   ├── deploy.py                      point d'entrée unique (validate, deploy, verify, scan, lookup, manifest)
│   ├── requirements.txt               fabric-cicd==1.4.0 et dépendances épinglées
│   ├── requirements-dev.txt           dépendances de test
│   └── fabcicd/                       contrôles statiques, matrice de dépendances, pré-vol,
│                                      porte Framework (ADP/BDP), vérification, rapport
└── tests/                             tests unitaires (aucun appel réseau)
```

## Utilisation locale

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r tools/requirements-dev.txt
python -m pytest                                   # 116 tests, sans réseau
python tools/deploy.py validate --repo ../its-dp-fab-adp-mds-managed
python tools/deploy.py scan --repo ../its-dp-fab-bdp-compte-managed --out inventaire.csv
# Lecture seule sur le tenant (identité az login ou variables FABRIC_*) :
python tools/deploy.py deploy --repo ../its-dp-fab-adp-mds-managed --env ITG --mode validate --auth cli --framework-gate off
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
(scénario B), l'autorise explicitement dans `deployment/dependencies.yml` :

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
  **fédération d'identité**. Déclarée dans `CD.yml` → `serviceConnections`.
- **connexion Fabric** (`FABPIPELINES_<ENV>_DP_FABRIC`, `MDS_<ENV>_DP_FABRIC`…) : utilisée par les
  items Fabric à l'exécution. Gérée dans Fabric et `parameter.yml`, jamais dans Key Vault.

Chaque stage lit dans le **coffre unique** `frfrkvdevdp0001`, avec la service connection de son
environnement, les 3 secrets `<ENV>-AZURE-CLIENT-ID / -CLIENT-SECRET / -TENANT-ID`
(`AzureKeyVault@2`). Cette tâche est dans le template imposé (Required template) : un dépôt ne peut
ni la retirer ni la remplacer par un groupe de variables. Le SPN de l'environnement publie
ensuite dans Fabric (`authMode: secret`).

```yaml
# CD.yml d'un dépôt
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
| 4 | erreur d'usage ou d'authentification |
