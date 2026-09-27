"""Verbindungstest für eine Heidenhain-Steuerung – rein lesend, verändert nichts.

Prüft Schritt für Schritt: TCP-Port erreichbar → LSV2-Anmeldung → DNC-Login (Option 18)
→ Statusabfragen über denselben Adapter, den auch die App verwendet. Denselben Test gibt es
im Konfigurations-Tab als Button "Verbindung testen".

    python tools/probe.py 192.168.0.10
    python tools/probe.py 192.168.0.10 --samples 20 --interval 2
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ausgabe immer als UTF-8 (Windows nutzt bei Umleitung in Dateien/Pipes sonst cp1252)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.adapters.base import AdapterError  # noqa: E402
from app.adapters.lsv2_adapter import Lsv2Adapter  # noqa: E402
from app.probe import run_probe  # noqa: E402
from app.state import EXEC_MODE_LABELS, PGM_STATE_LABELS, STATE_LABELS, classify  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="IP-Adresse oder Hostname der Steuerung")
    parser.add_argument("--port", type=int, default=19000, help="LSV2-Port (Standard 19000)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Timeout je Anfrage in Sekunden")
    parser.add_argument("--samples", type=int, default=5, help="Anzahl zusätzlicher Abfragen")
    parser.add_argument("--interval", type=float, default=2.0, help="Pause zwischen den Abfragen")
    parser.add_argument("--check", default="", help="Prüfadresse am Standort, z. B. 192.168.0.1 (Router vor Ort)")
    args = parser.parse_args()

    result = run_probe(args.host, args.port, args.timeout, args.check)
    for number, step in enumerate(result.steps, 1):
        print(f"\n[{number}] {step.title}")
        print(f"    {'OK     ' if step.ok else 'FEHLER ' if step.required else 'HINWEIS'}  {step.detail}")
        for hint in step.hints:
            print(f"             - {hint}")
    if not result.ok:
        # Exitcode: Nummer der ersten fehlgeschlagenen Pflichtprüfung (1 = Netzwerk …)
        required = [s for s in result.steps if s.required]
        return next(i for i, s in enumerate(required, 1) if not s.ok)

    print(f"\n[+] {args.samples} weitere Abfragen (alle {args.interval:g} s)")
    adapter = Lsv2Adapter(args.host, args.port, args.timeout)
    try:
        adapter.connect()
        for i in range(args.samples):
            t = time.perf_counter()
            snap = adapter.read()
            ms = (time.perf_counter() - t) * 1000
            override = f"F{snap.override_feed or 0:.0f}% S{snap.override_spindle or 0:.0f}%"
            print(
                f"    {i + 1:>3}  {STATE_LABELS[classify(snap.pgm_state)]:<9} | "
                f"{PGM_STATE_LABELS.get(snap.pgm_state, snap.pgm_state):<16} | "
                f"{EXEC_MODE_LABELS.get(snap.exec_mode, snap.exec_mode):<29} | {snap.program or '-'} "
                f"Satz {snap.line_no if snap.line_no is not None else '-'} | {snap.tool or '-'} | {override} | "
                f"{len(snap.errors)} Fehler | {ms:.0f} ms"
            )
            for text in snap.errors:
                print(f"           Meldung: {text}")
            if i + 1 < args.samples:
                time.sleep(args.interval)
    except AdapterError as exc:
        print(f"    FEHLER  {exc}")
        return 9
    finally:
        adapter.close()

    print(
        "\nErgebnis: Die Steuerung kann erfasst werden. Maschine im Konfigurations-Tab anlegen.\n"
        "Tipp: Einmal NC-Start/-Stopp an der Maschine auslösen und prüfen, ob der Zustand hier wechselt."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
