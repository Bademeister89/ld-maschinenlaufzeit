"""Einmalige Bereinigung schon erfasster Läufe nach den Regeln von Version 1.11.1.

Grundlage ist der Feldtest an der DMU 105 (iTNC 530):
- Nach dem Programmende meldet die Steuerung gleich "inaktiv" statt "beendet". Läufe, die aus dem
  Programmlauf direkt auf "inaktiv" gingen, waren fertig, standen aber als "unterbrochen" drin.
- Version 1.11.0 hat gestoppte und dann abgebrochene Läufe kurz vor dem Ende als "fertig" gezählt.
- MDI-Sätze und Makros im Handbetrieb wurden als Läufe des angewählten Programms gebucht, ebenso
  Palettentabellen (.P), die ohne Auftragsprogramm liefen.
- Ein Satzvorlauf mitten ins Programm ergab einen eigenen, kurzen "fertigen" Lauf.

Die Zustandsabschnitte bleiben unverändert; verworfene Läufe werden zu Zeit ohne Lauf.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict

from .collector import MID_START_LINES, MID_START_SHARE
from .db import Database
from .state import MANUAL_MODES, MachineState, run_result

META_KEY = "runs_cleanup_1_11_1"


def cleanup_runs(db: Database) -> dict[str, int]:
    """Läufe bereinigen (einmal je Datenbank). Liefert die Anzahl je Änderung."""
    if db.get_meta(META_KEY) is not None:
        return {}
    counts = {"verworfen": 0, "fertig": 0, "unterbrochen": 0, "teillauf": 0}
    intervals: dict[str, list[dict]] = defaultdict(list)
    for iv in db.intervals(0, 1e12):
        intervals[iv["machine_id"]].append(iv)
    starts = {mid: [iv["start"] for iv in ivs] for mid, ivs in intervals.items()}
    blocks = db.program_blocks()
    with db.transaction():
        for run in db.runs(0, 1e12):
            ivs = intervals.get(run["machine_id"], [])
            own = [iv for iv in ivs if iv["run_id"] == run["id"]]
            program = run["program"] or ""
            if program.upper().endswith(".P") or (own and all(iv["exec_mode"] in MANUAL_MODES for iv in own)):
                db.discard_run(run["id"])
                counts["verworfen"] += 1
                continue
            if run["ended_at"] is None or not own:
                continue
            i = bisect_left(starts[run["machine_id"]], run["ended_at"] - 0.001)
            after = next((iv for iv in ivs[i:] if iv["run_id"] != run["id"]), None)
            result = run["result"]
            if after is not None and after["state"] == MachineState.READY.value and after["pgm_state"] == "IDLE":
                last = own[-1]
                ran_to_end = last["state"] == MachineState.RUNNING.value and last["exec_mode"] not in MANUAL_MODES
                result = run_result("FINISHED" if ran_to_end else "IDLE", bool(run["had_error"]))
                if result != run["result"]:
                    db.set_run_result(run["id"], result)
                    counts["fertig" if result == "finished" else "unterbrochen"] += 1
            if result == "finished" and run["start_observed"]:
                samples = db.run_progress(run["id"])
                total = blocks.get((run["machine_id"], program))
                margin = max(MID_START_LINES, total * MID_START_SHARE if total else 0)
                if samples and samples[0][1] == program and samples[0][2] > margin:
                    db.set_run_observed(run["id"], False)  # per Satzvorlauf mitten im Programm begonnen
                    counts["teillauf"] += 1
        db.set_meta(META_KEY, "done")
    return {k: v for k, v in counts.items() if v}
