"""Simulierte iTNC 530 für Entwicklung und Tests ohne echte Maschine.

Die Simulation ist deterministisch (Seed) und zeitgesteuert: ``state_at(t)`` liefert den
Zustand zu einem Zeitpunkt. Dadurch dient sie sowohl als Live-Adapter als auch zum
Erzeugen von Demo-Historie (tools/seed_demo.py).
"""

from __future__ import annotations

import random
import time
import zlib
from collections.abc import Callable, Generator, Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..nc_program import ProgramFile
from ..tool_table import ToolTableFile, parse_tool_table
from .base import AdapterError, Snapshot

# Aufträge nach dem Namensschema JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM:
# (Auftrag, ((Aufspannung, Anzahl Programme), …))
ORDERS = (
    ("26-21055", ((1, 2), (2, 1))),
    ("26-21055V1", ((1, 1),)),  # Version (andere Ausführung) des Auftrags 21055
    ("26-21102", ((1, 1), (2, 1))),
    ("26-4711", ((1, 1),)),
    ("26-20988", ((1, 2), (2, 2))),
    ("26-5023", ((1, 1), (2, 1), (3, 1))),
    ("26-21230", ((1, 1), (2, 1))),
)
# Je Aufspannung die Programme in Reihenfolge: {Auftrag: [[Programme Aufspannung 1], …]}
ORDER_SETUPS = {
    order: [
        [f"TNC:\\AUFTRAG\\{order}-{setup:02d}-{number:02d}.H" for number in range(1, count + 1)]
        for setup, count in setups
    ]
    for order, setups in ORDERS
}
PROGRAMS = tuple(p for setups in ORDER_SETUPS.values() for programs in setups for p in programs)
# Werkzeugnummern im Betrieb: 1–1000
TOOLS = (
    (1, "NC-ANBOHRER"),
    (5, "BOHRER_D8.5"),
    (12, "FRAESER_D16"),
    (14, "SCHRUPPER_D50"),
    (21, "SCHLICHTER_D10"),
    (30, "GEWINDEFR_M10"),
    (104, "VHM-FRAESER_D6"),
    (118, "KUGELFRAESER_R3"),
    (215, "FASENFRAESER_90"),
    (342, "BOHRER_D10.2"),
    (407, "PLANFRAESER_D80"),
    (563, "ENTGRATER"),
    (872, "GEWINDEBOHRER_M8"),
)
ERRORS = (
    "Kühlmitteldruck zu gering",
    "Werkzeugbruch erkannt",
    "Schutztür während Bearbeitung geöffnet",
)

# (Ende des Abschnitts, Zustand); None bedeutet: Maschine aus bzw. nicht erreichbar.
Segment = tuple[float, Snapshot | None]


class SimulatedMachine:
    def __init__(
        self,
        seed: int,
        start: float,
        speed: float = 1.0,
        shift: tuple[int, int] | None = None,
        tz: str = "Europe/Berlin",
    ):
        """
        :param speed: Zeitraffer – 10 bedeutet, ein 10-Minuten-Zyklus dauert eine Minute.
        :param shift: (Beginn, Ende) als Stunde; außerhalb der Schicht und am Wochenende ist
                      die Maschine aus. ``None`` = durchgehend in Betrieb.
        """
        self._rng = random.Random(seed)
        self._speed = speed
        self._shift = shift
        self._tz = ZoneInfo(tz)
        # Programmeigenschaften hängen nur vom Programmnamen ab: Jede simulierte Maschine und
        # die Demo-Historie fahren dasselbe Programm gleich (wichtig für die Restlaufzeit).
        program_rngs = {p: random.Random(zlib.crc32(p.encode())) for p in PROGRAMS}
        self._cycle_min = {p: r.uniform(3, 12) for p, r in program_rngs.items()}
        self.program_blocks = {p: r.randint(400, 3000) for p, r in program_rngs.items()}
        self._plans = {p: self._plan(p, program_rngs[p]) for p in PROGRAMS}
        self._segments = self._generate(start)
        self._end = start
        self._current: Snapshot | None = None

    def state_at(self, t: float) -> Snapshot | None:
        """Zustand zum Zeitpunkt ``t`` (Zeit darf nur vorwärts laufen)."""
        while t >= self._end:
            self._end, self._current = next(self._segments)
        return self._current

    @property
    def next_change(self) -> float:
        return self._end

    def _minutes(self, lo: float, hi: float) -> float:
        return self._rng.uniform(lo, hi) * 60 / self._speed

    def _in_shift(self, t: float) -> bool:
        if self._shift is None:
            return True
        local = datetime.fromtimestamp(t, self._tz)
        return local.weekday() < 5 and self._shift[0] <= local.hour < self._shift[1]

    def _next_shift_start(self, t: float) -> float:
        assert self._shift is not None
        day = datetime.fromtimestamp(t, self._tz).date()
        while True:
            start = datetime(day.year, day.month, day.day, self._shift[0], tzinfo=self._tz)
            if start.timestamp() > t and start.weekday() < 5:
                return start.timestamp() + self._rng.uniform(0, 20 * 60)
            day += timedelta(days=1)

    def _generate(self, t: float) -> Iterator[Segment]:
        rng = self._rng
        last_program = None
        while True:
            if not self._in_shift(t):
                t = self._next_shift_start(t)
                yield t, None
                continue
            if rng.random() < 0.04:
                # Maschine kurz aus bzw. Netzwerk weg
                t += self._minutes(5, 30)
                yield t, None
                continue

            # Ein Los eines Auftrags: je Aufspannung rüsten, dann jedes Teil mit allen
            # Programmen dieser Aufspannung nacheinander bearbeiten
            order = rng.choice(ORDERS)[0]
            quantity = rng.randint(2, 6)
            for programs in ORDER_SETUPS[order]:
                if not self._in_shift(t):
                    break
                t += self._minutes(3, 12)
                yield t, Snapshot("IDLE", "MANUAL", program=last_program)
                for _ in range(quantity):
                    if not self._in_shift(t):
                        break
                    for program in programs:
                        t += self._minutes(0.2, 1)
                        yield t, Snapshot("IDLE", "AUTOMATIC", program=program, current_program=program)
                        last_program = program
                        t, ok = yield from self._part(t, program)
                        if not ok:
                            break  # Störung: Teil abgebrochen, weiter mit dem nächsten

    def _plan(self, program: str, rng: random.Random) -> list[tuple[str, float, int, int]]:
        """Fester Ablauf je Programm: (Werkzeug, Zeitanteil, erster Satz, letzter Satz).
        Zeit- und Satzanteile sind unabhängig – wie bei echten Programmen, in denen ein
        einzelner Satz sehr lange dauern kann."""
        tools = rng.sample(TOOLS, rng.randint(3, 6))
        time_w = [rng.uniform(0.3, 3.0) for _ in tools]
        block_w = [rng.uniform(0.2, 3.0) for _ in tools]
        total, blocks = sum(time_w), self.program_blocks[program]
        plan, first = [], 1
        for i, (number, name) in enumerate(tools):
            last = first + max(1, round(blocks * block_w[i] / sum(block_w))) - 1
            last = max(first, blocks - 1 if i == len(tools) - 1 else min(last, blocks - 1))
            # Wie die echte Steuerung: die Spindelabfrage liefert nur die Nummer, der Name steht in TOOL.T
            plan.append((f"T{number}", time_w[i] / total, first, last))
            first = last + 1
        return plan

    def _part(self, t: float, program: str) -> Generator[Segment, None, tuple[float, bool]]:
        rng = self._rng
        cycle_s = self._cycle_min[program] * rng.uniform(0.95, 1.05) * 60 / self._speed
        tool = None
        for tool, share, first, last in self._plans[program]:
            steps = 4
            line = first
            for k in range(steps):
                line = first + (last - first) * k // steps
                t += cycle_s * share / steps
                yield t, self._snap("STARTED", program, tool, line)
            r = rng.random()
            if r < 0.01:
                t += self._minutes(2, 8)
                yield t, self._snap("ERROR", program, tool, line, errors=(rng.choice(ERRORS),))
                t += self._minutes(0.5, 2)
                yield t, Snapshot("ERROR_CLEARED", "AUTOMATIC", program=program, tool=tool)
                return t, False
            if r < 0.12:
                t += self._minutes(0.5, 5)
                yield t, self._snap("STOPPED", program, tool, line)
        # Werkstückwechsel
        t += self._minutes(0.3, 1.5)
        blocks = self.program_blocks[program]
        yield t, Snapshot("FINISHED", "AUTOMATIC", program=program, current_program=program, line_no=blocks, tool=tool)
        return t, True

    def _snap(self, pgm_state: str, program: str, tool: str, line: int, errors: tuple[str, ...] = ()) -> Snapshot:
        return Snapshot(
            pgm_state=pgm_state,
            exec_mode="AUTOMATIC",
            program=program,
            current_program=program,
            line_no=line,
            tool=tool,
            override_feed=self._rng.choice((100.0, 100.0, 100.0, 80.0, 110.0)),
            override_spindle=100.0,
            override_rapid=50.0,
            errors=errors,
        )


class SimAdapter:
    """Live-Adapter um eine SimulatedMachine."""

    CONTROL_INFO = {"control": "iTNC530 (Simulation)", "nc_sw": "340494 08 SP3", "plc": "SIM"}

    def __init__(self, machine: SimulatedMachine, clock: Callable[[], float] = time.time):
        self._machine = machine
        self._clock = clock

    def connect(self) -> dict[str, str]:
        if self._machine.state_at(self._clock()) is None:
            raise AdapterError("Simulierte Maschine ist ausgeschaltet")
        return dict(self.CONTROL_INFO)

    def read(self) -> Snapshot:
        snap = self._machine.state_at(self._clock())
        if snap is None:
            raise AdapterError("Simulierte Maschine nicht erreichbar")
        return snap

    def close(self) -> None:
        pass

    def site_reachable(self, address: str) -> bool:
        return True  # in der Simulation ist eine ausgeschaltete Maschine nie ein Netzausfall

    def fetch_tool_table(self, known: tuple[int, float] | None) -> ToolTableFile | None:
        text = tool_table_text()
        size, mtime = len(text), 1_750_000_000.0
        if known == (size, mtime):
            return None
        return ToolTableFile(size, mtime, parse_tool_table(text))

    def fetch_program(self, path: str, known: tuple[int, float] | None, max_bytes: int) -> ProgramFile | None:
        blocks = self._machine.program_blocks.get(path)
        if blocks is None:
            return ProgramFile(path, error="Programmdatei nicht gefunden")
        size, mtime = blocks * 28, 1_750_000_000.0
        if known == (size, mtime):
            return None
        return ProgramFile(path, size, mtime, blocks, calls=())


def tool_table_text() -> str:
    """TOOL.T der Simulation im Format der iTNC 530 (feste Spaltenbreiten, gekürzt)."""
    rows = [(0, "NULLWERKZEUG", 0.0, 0.0), *((n, name, 60 + n % 70, 1 + n % 25) for n, name in TOOLS)]
    lines = [
        "BEGIN TOOL     .T     MM",
        f"{'T':<5}{'NAME':<17}{'L':<12}{'R':<12}{'TL':<3}{'RT':<4}{'TIME1':<6}{'CUR.TIME':<9}DOC",
    ]
    for number, name, length, radius in rows:
        lines.append(f"{number:<5}{name:<17}{length:<+12.3f}{radius:<+12.3f}{'':<3}{'':<4}{0:<6}{0:<9}")
    lines.append("[END]")
    return "\n".join(lines) + "\n"
