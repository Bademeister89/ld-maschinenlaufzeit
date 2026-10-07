"""LD-Machine-Viewer – Zustandserfassung für Heidenhain-Steuerungen."""

import os
from pathlib import Path

# Softwarestand nach dem Schema MAJOR.MINOR.PATCH (siehe README „Versionen“). Bei jeder
# Auslieferung erhöhen und in CHANGELOG.md beschreiben – ein Test prüft, dass beides zusammenpasst.
__version__ = "1.19.0"


def _build() -> str | None:
    """Build-Kennung (Datum + Commit): im Docker-Image über LDM_BUILD, in der portablen
    Version über die Datei app/BUILD. Beim Entwickeln aus dem Quellcode gibt es keine."""
    value = os.environ.get("LDM_BUILD", "")
    if not value:
        try:
            value = Path(__file__).with_name("BUILD").read_text(encoding="utf-8")
        except OSError:
            pass
    return value.strip() or None


BUILD = _build()
