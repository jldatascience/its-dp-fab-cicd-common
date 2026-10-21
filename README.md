# its-dp-fab-cicd-common — socle commun CI/CD Microsoft Fabric

Dépôt **nouveau**, à créer dans l'organisation GitHub de la Data Platform. Il contient
tout ce qui est partagé par les 11 dépôts Fabric : les templates Azure DevOps, l'outil
de déploiement Python et ses tests. Les dépôts Fabric ne contiennent plus de script de
déploiement, seulement leur configuration (`deployment/*.yml`) et trois YAML minces.

```
its-dp-fab-cicd-common/
├── .yamllint.yml                      règles YAML (CI du socle et validation des PR)
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
│   └── fabcicd/                       contrôles statiques, matrice de dépendances (v1.3), pré-vol,
│                                      porte Framework (ADP/BDP), vérification, rapport
└── tests/                             tests unitaires (aucun appel réseau)
```

## Utilisation locale

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r tools/requirements-dev.txt
python -m pytest                                   # 110 tests, sans réseau
python tools/deploy.py validate --repo ../its-dp-fab-adp-mds-managed
python tools/deploy.py scan --repo ../its-dp-fab-bdp-compte-managed --out inventaire.csv
# Lecture seule sur le tenant (identité az login ou variables FABRIC_*):
python tools/deploy.py deploy --repo ../its-dp-fab-adp-mds-managed --env ITG --mode validate --auth cli --framework-gate off
```

## Versionnement

Les 11 dépôts référencent ce socle par **tag** (`ref: refs/tags/v1.3.0`). Toute évolution :
PR sur `main` → CI verte → tag `v1.x.y` → PR de montée de version dans les dépôts
consommateurs (pilote d'abord). Ne jamais référencer `main` depuis une CD.

## Codes de sortie de deploy.py

| Code | Signification |
|---|---|
| 0 | succès |
| 1 | contrôle bloquant en échec (statique, pré-vol, porte Framework, vérification) |
| 2 | échec de publication fabric-cicd |
| 3 | refus de politique (inventaire `pending`, branche non autorisée) |
| 4 | erreur d'usage ou d'authentification |
