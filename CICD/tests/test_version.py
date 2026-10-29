"""Version du paquet : ``fabcicd.__version__`` doit suivre la dernière version numérotée du
``CHANGELOG.md`` du socle (``## [X.Y.Z] - AAAA-MM-JJ`` ; ``[Unreleased]`` est ignoré).
"""

from __future__ import annotations

import re
from pathlib import Path

import fabcicd

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def test_version_matches_latest_changelog_release():
    releases = RELEASE_HEADING.findall(CHANGELOG.read_text(encoding="utf-8"))
    assert releases, f"aucune version numérotée dans {CHANGELOG}"
    assert fabcicd.__version__ == releases[0], (
        f"fabcicd.__version__ = {fabcicd.__version__!r}, dernière version du CHANGELOG = {releases[0]!r}"
    )
