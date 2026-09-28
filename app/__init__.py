"""LD Maschinenlaufzeit – Zustandserfassung für Heidenhain-Steuerungen."""

import os

# Beim Docker-Build von GitHub Actions gesetzt (Datum + Commit), sonst "dev".
# Wird in der Oberfläche angezeigt, damit man nach einem Update die laufende Version sieht.
__version__ = os.environ.get("LDM_VERSION") or "dev"
