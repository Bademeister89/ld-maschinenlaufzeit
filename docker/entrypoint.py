"""Container-Start: Datenordner vorbereiten und die App ohne Root-Rechte starten.

Läuft der Container als root (Standard), bekommt ``$LDM_DATA_DIR`` den Besitzer
``PUID:PGID`` und der Prozess gibt seine Root-Rechte ab. Läuft er bereits als anderer
Benutzer (``--user``), wird nur gestartet.
"""

import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Aufruf: entrypoint.py <Befehl> [Argumente ...]")

    if os.getuid() == 0:
        uid = int(os.environ.get("PUID", "99"))
        gid = int(os.environ.get("PGID", "100"))
        data = Path(os.environ.get("LDM_DATA_DIR", "/data"))
        data.mkdir(parents=True, exist_ok=True)
        for path in (data, *data.rglob("*")):
            os.lchown(path, uid, gid)
        if uid != 0:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)

    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
