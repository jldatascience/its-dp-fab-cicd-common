"""Registre des GUID du socle (``CICD/registry/known_guids.yml``) : format et couverture.

Le registre classe les GUID trouvés dans les items : un GUID ``forbidden_in_items`` non couvert
par une règle ``parameter.yml`` fait échouer la CI (``GUID_HARDCODED``). Ces tests protègent le
fichier contre les erreurs de saisie (doublon, majuscules, GUID mal formé) et contre l'oubli d'un
dépôt consommateur.
"""

from __future__ import annotations

import re
from collections import defaultdict

import yaml

from fabcicd import ENVIRONMENTS
from fabcicd.registry import Registry, default_registry_path

FULL_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PREFIX = re.compile(r"^[0-9a-f]{8}-$")
CATEGORIES = {"sandbox", "workspace", "capacity", "identity", "tenant", "connection", "other"}
WORKSPACE_LABEL = re.compile(r"^workspace (its-dp-fab-[a-z0-9-]+-managed) ((?:DEV|ITG|PPR|PRD)(?:/(?:DEV|ITG|PPR|PRD))*)\b")

# Dépôts du périmètre (20 ; ADP Alexandrie retiré le 11/11/2026) et environnements dont l'ID de
# workspace est connu.
ALL_ENVS = set(ENVIRONMENTS)
EXPECTED_REPOSITORIES = {
    "its-dp-fab-item-framework-managed": ALL_ENVS,
    "its-dp-fab-adp-gds-managed": ALL_ENVS,
    "its-dp-fab-adp-ibs-managed": ALL_ENVS,
    "its-dp-fab-adp-mds-managed": ALL_ENVS,
    "its-dp-fab-adp-referentiel-managed": ALL_ENVS,
    "its-dp-fab-adp-sirene-managed": ALL_ENVS,
    "its-dp-fab-bdp-campaign-managed": ALL_ENVS,
    "its-dp-fab-bdp-centre-profit-managed": ALL_ENVS,
    "its-dp-fab-bdp-collaborateur-managed": ALL_ENVS,
    "its-dp-fab-bdp-compte-managed": ALL_ENVS,
    "its-dp-fab-bdp-contact-managed": ALL_ENVS,
    "its-dp-fab-bdp-engagement-managed": ALL_ENVS,
    "its-dp-fab-bdp-facture-client-managed": ALL_ENVS,
    "its-dp-fab-bdp-lead-managed": ALL_ENVS,
    "its-dp-fab-bdp-opportunite-managed": ALL_ENVS,
    "its-dp-fab-bdp-performance-engagement-managed": ALL_ENVS,
    "its-dp-fab-ucs-hubspot-managed": ALL_ENVS,
    "its-dp-fab-ucs-mybsc-snow-managed": ALL_ENVS,
    "its-dp-fab-ucs-referentiel-managed": ALL_ENVS,
    "its-dp-fab-ucs-reporting-fees-et-wip-managed": ALL_ENVS,
}


def _raw_entries() -> list[dict]:
    return yaml.safe_load(default_registry_path().read_text(encoding="utf-8"))["guids"]


def test_registry_entries_are_well_formed():
    for entry in _raw_entries():
        value = entry["value"]
        assert value == value.lower(), f"{value} : valeur en minuscules attendue"
        assert FULL_GUID.match(value) or PREFIX.match(value), f"{value} : GUID complet ou préfixe « xxxxxxxx- » attendu"
        assert entry.get("label"), f"{value} : libellé manquant"
        assert entry.get("category") in CATEGORIES, f"{value} : catégorie inconnue {entry.get('category')!r}"
        assert isinstance(entry.get("forbidden_in_items"), bool), f"{value} : forbidden_in_items booléen attendu"


def test_registry_values_are_unique_and_not_shadowed_by_a_prefix():
    values = [entry["value"] for entry in _raw_entries()]
    assert len(values) == len(set(values)), "valeur en double dans le registre"
    prefixes = [v for v in values if len(v) < 36]
    for value in values:
        for prefix in prefixes:
            assert value == prefix or not value.startswith(prefix), f"{value} masqué par le préfixe {prefix}"


def test_registry_covers_every_consumer_workspace():
    covered: dict[str, set[str]] = defaultdict(set)
    for entry in _raw_entries():
        if entry["category"] != "workspace":
            continue
        match = WORKSPACE_LABEL.match(entry["label"])
        if not match:  # préfixe documentaire (MODOP) sans dépôt
            continue
        covered[match.group(1)].update(match.group(2).split("/"))
    assert set(covered) == set(EXPECTED_REPOSITORIES), (
        f"dépôts en trop : {sorted(set(covered) - set(EXPECTED_REPOSITORIES))} ; "
        f"manquants : {sorted(set(EXPECTED_REPOSITORIES) - set(covered))}"
    )
    for repository, envs in EXPECTED_REPOSITORIES.items():
        assert covered[repository] == envs, f"{repository} : environnements {sorted(covered[repository])}, attendu {sorted(envs)}"


def test_workspaces_and_capacities_are_forbidden_in_items():
    for entry in _raw_entries():
        if entry["category"] in ("workspace", "capacity", "sandbox"):
            assert entry["forbidden_in_items"] is True, f"{entry['value']} ({entry['label']}) doit être interdit dans les items"


def test_capacity_correction_of_2026_11_11():
    registry = Registry.load(default_registry_path())
    capacity = registry.classify("DC14404E-ABDA-4191-8A07-99980BEC2D41")
    assert capacity and capacity.category == "capacity" and capacity.forbidden_in_items
    assert registry.classify("29cdc503-aa27-48e1-a41e-4d6af4472f00") is None, "ancienne capacité erronée encore présente"
    prd = registry.classify("8B3B9E06-C303-40FC-87B5-7FA949901720")
    assert prd and prd.category == "capacity"
