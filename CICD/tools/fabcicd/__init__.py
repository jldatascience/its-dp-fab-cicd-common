"""Outillage commun de la chaîne CI/CD Microsoft Fabric (MDP KPMG France).

Ce paquet encapsule fabric-cicd (outil de déploiement) et ajoute ce que fabric-cicd
ne fait pas lui-même :

* contrôles statiques bloquants (structure, JSON, config.yml, parameter.yml,
  GUID sandbox interdits, règles key_value_replace sans correspondance, secrets) ;
* pré-vol (workspace cible, capacité, connexions, dépendances Framework) ;
* résolution explicite des références dynamiques avant publication (absence ou
  doublon = échec explicite) ;
* vérification post-déploiement des références réellement déployées ;
* rapport de déploiement exploitable (JSON + Markdown).

Point d'entrée : ``python CICD/tools/deploy.py --help``.
"""

__version__ = "1.0.0"

ENVIRONMENTS = ("DEV", "ITG", "PPR", "PRD")
