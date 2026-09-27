"""Demo-Historie erzeugen: simuliert die konfigurierten Maschinen über die letzten Tage.

Die simulierten Zustände laufen durch denselben Collector wie im Live-Betrieb und landen
in der Demo-Datenbank (nie in der echten). Danach die App mit SIMULATE=1 starten.

    python tools/seed_demo.py --days 21
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ausgabe immer als UTF-8 (Windows nutzt bei Umleitung in Dateien/Pipes sonst cp1252)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.adapters.sim_adapter import SimulatedMachine  # noqa: E402
from app.collector import MachineCollector  # noqa: E402
from app.config import MachineConfig, default_data_dir, load_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.registry import MachineManager  # noqa: E402

POLL_MAX_S = 60


class _NoAdapter:
    def connect(self):
        raise RuntimeError("nur für process()")

    read = connect

    def close(self):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=21, help="Anzahl Tage bis heute (Standard 21)")
    parser.add_argument("--db", type=Path, default=default_data_dir() / "demo.db", help="Ziel-Datenbank")
    parser.add_argument("--force", action="store_true", help="vorhandene Demo-Datenbank ersetzen")
    args = parser.parse_args()

    settings = load_settings()
    if args.db.name == "data.db":
        sys.exit("Abbruch: Demo-Daten gehören nicht in die echte Datenbank data.db.")

    # Maschinen: die in der bisherigen Demo-Datenbank eingerichteten, sonst der Startbestand
    machines: list[MachineConfig] = list(settings.machines)
    if args.db.exists():
        if not args.force:
            sys.exit(f"{args.db} existiert bereits – mit --force ersetzen.")
        old = Database(args.db)
        machines = MachineManager(settings, old, lambda m: None).machines() or machines
        old.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{args.db}{suffix}").unlink(missing_ok=True)

    tz = ZoneInfo(settings.timezone)
    end = time.time()
    start = (datetime.fromtimestamp(end, tz) - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    t_start = start.timestamp()

    db = Database(args.db)
    for m in machines:
        db.insert_machine(m.id, m.name, m.host, m.port, m.note, m.sort_order, m.check_host)
        db.set_machine_image(m.id, m.image)
    db.set_meta("machines_seeded", "1")
    for index, machine in enumerate(machines):
        collector = MachineCollector(machine, _NoAdapter(), db)
        sim = SimulatedMachine(seed=100 + index, start=t_start, speed=1.0, shift=(6, 22), tz=settings.timezone)
        t = t_start
        steps = 0
        with db.transaction():
            while t < end:
                collector.process(sim.state_at(t), t, reason="Simulation: Maschine aus")
                t = min(sim.next_change, t + POLL_MAX_S)
                steps += 1
        print(f"{machine.name}: {steps} Abfragen simuliert")
    db.close()
    print(f"Demo-Daten ab {start:%d.%m.%Y} in {args.db}")
    print("Start der App mit Simulation:  start-simulation.cmd (portabel) bzw. start.cmd -Simulate")


if __name__ == "__main__":
    main()
