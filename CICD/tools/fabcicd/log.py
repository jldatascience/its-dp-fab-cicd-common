"""Journalisation structurée, compatible Azure DevOps.

Chaque événement est écrit sur une ligne au format ``[NIVEAU] [étape] message``.
Sous Azure DevOps (variable TF_BUILD présente), les erreurs et avertissements
sont aussi émis avec les commandes de journalisation ``##vso[task.logissue]``
pour remonter dans le résumé du run.

Règle de sécurité : ce module ne journalise jamais de valeur de secret. Les
appelants passent des identifiants non secrets (noms, GUID d'objets Fabric).
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Iterator

_IN_ADO = bool(os.getenv("TF_BUILD"))


def _emit(level: str, step: str, message: str) -> None:
    line = f"[{level}] [{step}] {message}"
    print(line, flush=True)
    if _IN_ADO and level in ("ERROR", "WARNING"):
        kind = "error" if level == "ERROR" else "warning"
        # Les retours à la ligne cassent la commande de journalisation ADO.
        safe = message.replace("\r", " ").replace("\n", " ")
        print(f"##vso[task.logissue type={kind}]{step}: {safe}", flush=True)


def info(step: str, message: str) -> None:
    _emit("INFO", step, message)


def warning(step: str, message: str) -> None:
    _emit("WARNING", step, message)


def error(step: str, message: str) -> None:
    _emit("ERROR", step, message)


@contextmanager
def group(title: str) -> Iterator[None]:
    """Regroupe les lignes dans un bloc repliable sous Azure DevOps."""
    if _IN_ADO:
        print(f"##[group]{title}", flush=True)
    else:
        print(f"===== {title} =====", flush=True)
    try:
        yield
    finally:
        if _IN_ADO:
            print("##[endgroup]", flush=True)


def configure_stdout() -> None:
    """Sortie non bufferisée pour suivre le run en temps réel."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(line_buffering=True)
