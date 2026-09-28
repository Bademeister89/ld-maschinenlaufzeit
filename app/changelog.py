"""Änderungsprotokoll (CHANGELOG.md) für die Anzeige im Tab Konfiguration.

Erwartetes Format, neueste Version oben::

    ## [1.2.0] – 2026-09-28
    ### Neu
    - Eintrag, darf über mehrere
      eingerückte Zeilen gehen
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Liegt neben dem Ordner app/ – im Repo, im Docker-Image (/app) und in der portablen Version
CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

_RELEASE = re.compile(r"^##\s+\[?(\d+\.\d+\.\d+)\]?(?:\s*[–-]\s*(\d{4}-\d{2}-\d{2}))?")
_MARKUP = re.compile(r"\*\*|`")


def parse(text: str) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    section: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if match := _RELEASE.match(line):
            releases.append({"version": match[1], "date": match[2], "sections": []})
            section = None
        elif not releases:
            continue
        elif line.startswith("### "):
            section = {"title": line[4:].strip(), "items": []}
            releases[-1]["sections"].append(section)
        elif stripped.startswith("- "):
            if section is None:
                section = {"title": "", "items": []}
                releases[-1]["sections"].append(section)
            section["items"].append(_MARKUP.sub("", stripped[2:].strip()))
        elif stripped and line.startswith(" ") and section and section["items"]:
            section["items"][-1] += " " + _MARKUP.sub("", stripped)
    return releases


def load(path: Path = CHANGELOG) -> list[dict[str, Any]]:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError:
        return []
