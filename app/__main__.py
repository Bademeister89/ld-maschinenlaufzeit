"""Start der App: ``python -m app [--simulate] [--host H] [--port P] [--open]``.

Host und Port kommen aus der config.yaml (Abschnitt ``server``), sofern nicht angegeben.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Unter Windows verhindert SO_EXCLUSIVEADDRUSE, dass ein zweiter Prozess den Port "teilt"
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="LD Maschinenlaufzeit starten")
    parser.add_argument("--simulate", action="store_true", help="simulierte Maschinen (eigene Demo-Datenbank)")
    parser.add_argument("--host", help="Adresse, auf der die Oberfläche lauscht (Standard: config.yaml)")
    parser.add_argument("--port", type=int, help="Port der Oberfläche (Standard: config.yaml)")
    parser.add_argument("--open", action="store_true", help="Browser nach dem Start öffnen")
    args = parser.parse_args()

    if args.simulate:
        os.environ["SIMULATE"] = "1"
    # pythonw.exe (Hintergrundbetrieb) hat keine Konsole: Ausgaben verwerfen, Log-Datei nutzen
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    for stream in (sys.stdout, sys.stderr):
        # Umlaute auch bei Umleitung in Datei/Pipe (Windows nutzt dort sonst cp1252)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    import uvicorn

    from .config import load_settings

    settings = load_settings()
    host = args.host or settings.listen_host
    port = args.port or settings.port
    os.environ["LDM_LISTEN_HOST"] = host
    os.environ["LDM_PORT"] = str(port)
    url = f"http://localhost:{port}"

    if not _port_free(host, port):
        print(f"Port {port} ist belegt – läuft LD Maschinenlaufzeit bereits (z. B. über den Autostart)?")
        print(f"Oberfläche: {url}")
        if args.open:
            # Die Desktop-Verknüpfung startet start.cmd: Läuft die App schon, genügt das Öffnen
            # der Oberfläche – ohne Fehlercode, damit kein wartendes Konsolenfenster offen bleibt.
            webbrowser.open(url)
            sys.exit(0)
        sys.exit(1)

    if args.open:
        threading.Timer(2.0, webbrowser.open, (url,)).start()
    print(f"LD Maschinenlaufzeit{' (SIMULATION)' if settings.simulate else ''}")
    print(f"Oberfläche: http://localhost:{port}    Datenordner: {settings.data_dir}")
    print("Beenden mit Strg+C\n")
    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
