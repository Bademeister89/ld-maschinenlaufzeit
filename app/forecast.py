"""Restlaufzeit-Prognose für den laufenden Programmdurchlauf.

Verfahren in dieser Reihenfolge (das erste, das greift, wird verwendet):

1. ``profile``: Satzverlauf früherer Läufe desselben Programms. Aus der aktuellen Satznummer
   ergibt sich, welcher Anteil der Laufzeit bei früheren Läufen an dieser Stelle schon
   vergangen war. Das berücksichtigt, dass einzelne Sätze sehr lange dauern können. Die
   Prognose wird mit dem Tempo des aktuellen Laufs (z. B. anderer Override) nachgeführt.
2. ``history``: typische Laufzeit früherer Läufe minus bisherige Laufzeit.
3. ``plan``: erster Lauf eines Programms mit CAM-Planzeit (Tebis-Doku oder von Hand): Planzeit
   minus bisherige Laufzeit.
4. ``blocks``: erster Lauf ohne Planzeit – lineare Hochrechnung aus Satznummer und Satzanzahl
   (grob, weil Sätze unterschiedlich lange dauern).

Mit Planzeit zählen frühere Läufe unter einem Viertel davon nicht als üblich: Das sind Testanläufe
mit Abbruch, die die iTNC wie ein Programmende meldet.

Alle Zeiten sind reine Laufzeit (Zustand LÄUFT); künftige Stopps kann niemand vorhersehen.

Ein Programm ist sein Name, nicht sein Speicherort: Läufe einer Kopie in einem anderen Ordner
(``…\21053 …\26-21051-02-01.h``) zählen wie Läufe des Originals.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from .db import Database
from .nc_program import call_name

REFERENCE_RUNS = 5  # so viele frühere Läufe gehen in die typische Laufzeit ein
MIN_BLOCK_PROGRESS = 0.05  # darunter ist die Hochrechnung aus Satznummern zu unsicher
PACE_MIN_PROGRESS = 0.1  # ab hier wird das Tempo des aktuellen Laufs berücksichtigt
PLAN_MIN_SHARE = 0.25  # mit Planzeit: kürzere Läufe sind Testanläufe und zählen nicht
PACE_LIMITS = (0.5, 2.0)

Sample = tuple[float, str | None, int]  # (reine Laufzeit seit Start, Programm, Satz)


@dataclass
class _Reference:
    typical_run_s: float
    basis_runs: int
    samples: list[Sample]
    profile_run_s: float


def locate(samples: list[Sample], program: str | None, line_no: int, start: int) -> int | None:
    """Erste Stelle ab ``start``, an der der Referenzlauf dieses Programm an oder nach
    ``line_no`` war. Die Suche läuft nur vorwärts, damit Wiederholungen (LBL-Aufrufe)
    nicht zurückspringen."""
    for index in range(start, len(samples)):
        _, sample_program, sample_line = samples[index]
        if sample_program == program and sample_line >= line_no:
            return index
    return None


class Forecaster:
    def __init__(self, db: Database, machine_id: str):
        self._db = db
        self._machine_id = machine_id
        self._refs: dict[str | None, _Reference | None] = {}
        self._plans: dict[str, float | None] = {}  # Programmname → CAM-Planzeit
        self._run_id: int | None = None
        self._position = 0
        self._fraction: float | None = None
        # Erster beobachteter Punkt des Laufs (Fortschritt, reine Laufzeit). Das Tempo wird nur
        # aus dem selbst beobachteten Abschnitt berechnet – auch wenn die Erfassung mitten in
        # einen Lauf eingestiegen ist.
        self._anchor: tuple[float, float] | None = None
        self._block_anchor: tuple[float, float] | None = None

    def invalidate(self, program: str | None) -> None:
        """Nach einem abgeschlossenen Lauf neu berechnen – auch für Kopien gleichen Namens."""
        name = call_name(program) if program else None
        for key in [k for k in self._refs if k == program or (name and k and call_name(k) == name)]:
            self._refs.pop(key, None)

    def plan_s(self, program: str | None) -> float | None:
        """CAM-Planzeit des Programms (über den Namen, gleich in welchem Ordner); None = keine."""
        if not program:
            return None
        name = call_name(program)
        if name not in self._plans:
            self._plans[name] = self._db.plan_s(name)
        return self._plans[name]

    def forget_plans(self) -> None:
        """Planzeiten geändert (Import oder von Hand): neu lesen, Referenzläufe neu filtern."""
        self._plans.clear()
        self._refs.clear()

    def typical_run_s(self, program: str | None) -> float | None:
        """Übliche reine Laufzeit eines Programms (Median der letzten fertigen Läufe); None = unbekannt."""
        ref = self._reference(program)
        return ref.typical_run_s if ref else None

    def _reference(self, program: str | None) -> _Reference | None:
        if program not in self._refs:
            self._refs[program] = self._load_reference(program)
        return self._refs[program]

    def _load_reference(self, program: str | None) -> _Reference | None:
        if program is None:
            return None
        # Läufe dieses Programms in jedem Ordner (gleicher Name)
        name = call_name(program)
        paths = {program, *(p for p in self._db.run_programs(self._machine_id) if call_name(p) == name)}
        plan = self.plan_s(program)
        runs = [
            r for r in self._db.reference_runs(self._machine_id, sorted(paths), REFERENCE_RUNS)
            if r["run_s"] > 0 and (plan is None or r["run_s"] >= PLAN_MIN_SHARE * plan)
        ]
        if not runs:
            return None
        samples: list[Sample] = []
        profile_run_s = 0.0
        for run in runs:  # neuester Lauf mit aufgezeichnetem Satzverlauf
            samples = self._db.run_progress(run["id"])
            if samples:
                if run["program"] != program:
                    # Satzverlauf einer Kopie: ihre Sätze gelten für dieses Programm
                    samples = [(t, program if p == run["program"] else p, line) for t, p, line in samples]
                profile_run_s = run["run_s"]
                break
        return _Reference(median(r["run_s"] for r in runs), len(runs), samples, profile_run_s)

    def update(
        self,
        run_id: int,
        program: str | None,
        run_s: float,
        current_program: str | None,
        line_no: int | None,
        total_blocks: int | None,
        start_observed: bool = True,
    ) -> dict[str, Any] | None:
        if run_id != self._run_id:
            self._run_id, self._position, self._fraction = run_id, 0, None
            self._anchor = self._block_anchor = None

        ref = self._reference(program)
        if ref is not None:
            if ref.samples and line_no is not None:
                index = locate(ref.samples, current_program or program, line_no, self._position)
                if index is not None:
                    self._position = index
                    self._fraction = min(ref.samples[index][0] / ref.profile_run_s, 0.999)
                    if self._anchor is None:
                        self._anchor = (0.0, 0.0) if start_observed else (self._fraction, run_s)
            if self._fraction is not None:
                return self._profile_forecast(ref, run_s)
            # Ohne bekannten Start sagt die bisherige Laufzeit nichts über den Rest aus
            return self._history_forecast(ref, run_s) if start_observed else None

        plan = self.plan_s(program)
        if plan:
            if start_observed:
                return self._history_forecast(_Reference(plan, 0, [], 0.0), run_s, "plan")
            if total_blocks and line_no is not None and (current_program or program) == program:
                # Mitten im Lauf eingestiegen: Fortschritt aus der Satznummer, Gesamtzeit aus dem Plan
                progress = min(line_no / total_blocks, 0.999)
                return {
                    "method": "plan", "remaining_s": plan * (1 - progress), "progress": progress,
                    "basis_runs": 0, "typical_run_s": plan, "overdue_s": None,
                }
            return None

        if total_blocks and line_no is not None and (current_program or program) == program:
            progress = min(line_no / total_blocks, 1.0)
            if self._block_anchor is None:
                self._block_anchor = (0.0, 0.0) if start_observed else (progress, run_s)
            p0, t0 = self._block_anchor
            if progress - p0 >= MIN_BLOCK_PROGRESS and run_s > t0:
                return {
                    "method": "blocks",
                    "remaining_s": (run_s - t0) * (1 - progress) / (progress - p0),
                    "progress": progress,
                    "basis_runs": 0,
                    "typical_run_s": None,
                    "overdue_s": None,
                }
        return None

    def _profile_forecast(self, ref: _Reference, run_s: float) -> dict[str, Any]:
        fraction = self._fraction or 0.0
        f0, t0 = self._anchor or (0.0, 0.0)
        pace = 1.0
        if fraction - f0 >= PACE_MIN_PROGRESS and run_s > t0:
            # >1: dieser Lauf ist schneller als üblich, <1: langsamer (z. B. Override < 100 %)
            pace = (fraction - f0) * ref.typical_run_s / (run_s - t0)
            pace = min(max(pace, PACE_LIMITS[0]), PACE_LIMITS[1])
        return {
            "method": "profile",
            "remaining_s": ref.typical_run_s * (1 - fraction) / pace,
            "progress": fraction,
            "basis_runs": ref.basis_runs,
            "typical_run_s": ref.typical_run_s,
            "overdue_s": None,
        }

    @staticmethod
    def _history_forecast(ref: _Reference, run_s: float, method: str = "history") -> dict[str, Any]:
        remaining = ref.typical_run_s - run_s
        return {
            "method": method,
            "remaining_s": max(remaining, 0.0),
            "progress": min(run_s / ref.typical_run_s, 0.999),
            "basis_runs": ref.basis_runs,
            "typical_run_s": ref.typical_run_s,
            "overdue_s": -remaining if remaining < 0 else None,
        }
