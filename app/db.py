"""SQLite-Speicher. Alle Zeitstempel sind Unix-Sekunden (UTC).

Offene Intervalle/Läufe haben ``ended_at IS NULL``. ``last_seen`` wird bei jeder Abfrage
fortgeschrieben; als Ende eines offenen Intervalls gilt immer ``last_seen``. So bleibt die
Auswertung auch nach einem Absturz des Tools korrekt (keine erfundene Laufzeit).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS machines (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    host       TEXT NOT NULL,
    port       INTEGER NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    image      TEXT,
    removed    INTEGER NOT NULL DEFAULT 0,
    check_host TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS program_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id     TEXT NOT NULL REFERENCES machines(id),
    program        TEXT,
    started_at     REAL NOT NULL,
    ended_at       REAL,
    result         TEXT,
    had_error      INTEGER NOT NULL DEFAULT 0,
    start_observed INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_runs_machine_time ON program_runs(machine_id, started_at);
CREATE TABLE IF NOT EXISTS state_intervals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(id),
    state      TEXT NOT NULL,
    pgm_state  TEXT,
    exec_mode  TEXT,
    program    TEXT,
    run_id     INTEGER REFERENCES program_runs(id),
    started_at REAL NOT NULL,
    ended_at   REAL,
    last_seen  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_intervals_machine_time ON state_intervals(machine_id, started_at);
CREATE INDEX IF NOT EXISTS ix_intervals_run ON state_intervals(run_id);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(id),
    ts         REAL NOT NULL,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_events_machine_time ON events(machine_id, ts);
CREATE INDEX IF NOT EXISTS ix_runs_machine_program ON program_runs(machine_id, program, started_at);
CREATE TABLE IF NOT EXISTS run_progress (
    run_id  INTEGER NOT NULL REFERENCES program_runs(id),
    t_run   REAL NOT NULL,
    program TEXT,
    line_no INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_progress_run ON run_progress(run_id, t_run);
CREATE TABLE IF NOT EXISTS orders (
    key        TEXT PRIMARY KEY,
    year       INTEGER NOT NULL,
    number     TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL,
    closed_at  REAL
);
CREATE TABLE IF NOT EXISTS program_files (
    machine_id TEXT NOT NULL REFERENCES machines(id),
    path       TEXT NOT NULL,
    size       INTEGER,
    mtime      REAL,
    blocks     INTEGER,
    error      TEXT,
    checked_at REAL NOT NULL,
    PRIMARY KEY (machine_id, path)
);
CREATE TABLE IF NOT EXISTS tools (
    machine_id TEXT NOT NULL REFERENCES machines(id),
    number     INTEGER NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    limit_s    REAL,
    reset_at   REAL NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (machine_id, number)
);
CREATE TABLE IF NOT EXISTS tool_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(id),
    number     INTEGER NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tool_usage ON tool_usage(machine_id, number, ended_at);
CREATE TABLE IF NOT EXISTS tool_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(id),
    number     INTEGER NOT NULL,
    reset_at   REAL NOT NULL,
    used_s     REAL NOT NULL,
    limit_s    REAL
);
CREATE INDEX IF NOT EXISTS ix_tool_resets ON tool_resets(machine_id, number, reset_at);
"""

_END = "COALESCE(i.ended_at, i.last_seen)"


class Database:
    def __init__(self, path: Path | str):
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._tx_depth = 0
        with self._lock:
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA synchronous=NORMAL")
            self._con.execute("PRAGMA foreign_keys=ON")
            self._con.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        # v1 → v2: Maschinen werden in der Datenbank gepflegt (Notiz, Reihenfolge, Bild, entfernt)
        # v3 → v4: Prüfadresse am Standort
        columns = {row["name"] for row in self._con.execute("PRAGMA table_info(machines)")}
        for name, ddl in (
            ("note", "TEXT NOT NULL DEFAULT ''"),
            ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
            ("image", "TEXT"),
            ("removed", "INTEGER NOT NULL DEFAULT 0"),
            ("check_host", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                self._con.execute(f"ALTER TABLE machines ADD COLUMN {name} {ddl}")
        # v4 → v5: Auftragsnummer (aus dem Programmnamen) an Zustandsabschnitten und Läufen
        for table in ("state_intervals", "program_runs"):
            columns = {row["name"] for row in self._con.execute(f"PRAGMA table_info({table})")}
            if "order_key" not in columns:
                self._con.execute(f"ALTER TABLE {table} ADD COLUMN order_key TEXT")
        self._con.execute("CREATE INDEX IF NOT EXISTS ix_intervals_order ON state_intervals(order_key)")
        self._con.execute("CREATE INDEX IF NOT EXISTS ix_runs_order ON program_runs(order_key)")
        self.set_meta("schema_version", str(SCHEMA_VERSION))

    def get_meta(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def close(self) -> None:
        with self._lock:
            self._con.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Transaktion; verschachtelte Aufrufe laufen in der äußeren mit."""
        with self._lock:
            if self._tx_depth:
                self._tx_depth += 1
                try:
                    yield
                finally:
                    self._tx_depth -= 1
                return
            self._con.execute("BEGIN")
            self._tx_depth = 1
            try:
                yield
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            else:
                self._con.execute("COMMIT")
            finally:
                self._tx_depth = 0

    def _execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._con.execute(sql, params)

    def _query(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._con.execute(sql, params).fetchall()]

    # --- Maschinen -----------------------------------------------------------------

    _MACHINE_COLUMNS = "id, name, host, port, note, sort_order, image, removed, check_host"

    def ensure_machine(self, machine_id: str, name: str, host: str, port: int) -> None:
        """Maschine anlegen, falls es sie noch nicht gibt (bestehende Einträge bleiben unverändert)."""
        self._execute(
            "INSERT INTO machines(id, name, host, port) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
            (machine_id, name, host, port),
        )

    def machines(self, include_removed: bool = False) -> list[dict[str, Any]]:
        where = "" if include_removed else "WHERE removed = 0 "
        return self._query(f"SELECT {self._MACHINE_COLUMNS} FROM machines {where}ORDER BY sort_order, name, id")

    def machine(self, machine_id: str) -> dict[str, Any] | None:
        rows = self._query(f"SELECT {self._MACHINE_COLUMNS} FROM machines WHERE id = ?", (machine_id,))
        return rows[0] if rows else None

    def insert_machine(
        self,
        machine_id: str,
        name: str,
        host: str,
        port: int,
        note: str = "",
        sort_order: int = 0,
        check_host: str = "",
    ) -> None:
        self._execute(
            "INSERT INTO machines(id, name, host, port, note, sort_order, check_host) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (machine_id, name, host, port, note, sort_order, check_host),
        )

    def update_machine(
        self, machine_id: str, name: str, host: str, port: int, note: str, check_host: str = ""
    ) -> None:
        self._execute(
            "UPDATE machines SET name = ?, host = ?, port = ?, note = ?, check_host = ? WHERE id = ?",
            (name, host, port, note, check_host, machine_id),
        )

    def set_machine_order(self, machine_ids: list[str]) -> None:
        with self.transaction():
            for index, machine_id in enumerate(machine_ids):
                self._execute("UPDATE machines SET sort_order = ? WHERE id = ?", (index * 10, machine_id))

    def set_machine_image(self, machine_id: str, image: str | None) -> None:
        self._execute("UPDATE machines SET image = ? WHERE id = ?", (image, machine_id))

    def remove_machine(self, machine_id: str) -> None:
        """Aus der Konfiguration entfernen; erfasste Daten bleiben erhalten."""
        self._execute("UPDATE machines SET removed = 1, image = NULL WHERE id = ?", (machine_id,))

    # --- Zustandsintervalle --------------------------------------------------------

    def open_interval(
        self,
        machine_id: str,
        state: str,
        pgm_state: str | None,
        exec_mode: str | None,
        program: str | None,
        run_id: int | None,
        started_at: float,
        last_seen: float,
        order_key: str | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO state_intervals(machine_id, state, pgm_state, exec_mode, program, run_id, started_at, "
            "last_seen, order_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (machine_id, state, pgm_state, exec_mode, program, run_id, started_at, last_seen, order_key),
        )
        return int(cur.lastrowid)

    def touch_interval(self, interval_id: int, t: float) -> None:
        self._execute("UPDATE state_intervals SET last_seen = ? WHERE id = ?", (t, interval_id))

    def close_interval(self, interval_id: int, t: float) -> None:
        self._execute("UPDATE state_intervals SET ended_at = ?, last_seen = ? WHERE id = ?", (t, t, interval_id))

    def close_stale_intervals(self, machine_id: str) -> int:
        """Nach Neustart/Absturz: offene Intervalle am letzten Lebenszeichen schließen."""
        cur = self._execute(
            "UPDATE state_intervals SET ended_at = last_seen WHERE machine_id = ? AND ended_at IS NULL", (machine_id,)
        )
        return cur.rowcount

    def intervals(self, t0: float, t1: float, machine_id: str | None = None) -> list[dict[str, Any]]:
        """Alle Intervalle, die [t0, t1) überlappen; ``end`` ist das effektive Ende."""
        sql = (
            f"SELECT i.id, i.machine_id, i.state, i.pgm_state, i.exec_mode, i.program, i.run_id, "
            f"i.started_at AS start, {_END} AS end, i.ended_at IS NULL AS open "
            f"FROM state_intervals i WHERE i.started_at < ? AND {_END} > ?"
        )
        params: list[Any] = [t1, t0]
        if machine_id:
            sql += " AND i.machine_id = ?"
            params.append(machine_id)
        return self._query(sql + " ORDER BY i.machine_id, i.started_at", params)

    # --- Programmdurchläufe --------------------------------------------------------

    def start_run(
        self, machine_id: str, program: str | None, t: float, start_observed: bool, order_key: str | None = None
    ) -> int:
        cur = self._execute(
            "INSERT INTO program_runs(machine_id, program, started_at, start_observed, order_key) VALUES (?, ?, ?, ?, ?)",
            (machine_id, program, t, int(start_observed), order_key),
        )
        return int(cur.lastrowid)

    def end_run(self, run_id: int, t: float, result: str) -> None:
        self._execute("UPDATE program_runs SET ended_at = ?, result = ? WHERE id = ?", (t, result, run_id))

    def mark_run_error(self, run_id: int) -> None:
        self._execute("UPDATE program_runs SET had_error = 1 WHERE id = ?", (run_id,))

    _RUN_SELECT = (
        "SELECT r.id, r.machine_id, r.program, r.started_at, r.ended_at, r.result, r.had_error, r.start_observed, "
        f"COALESCE(SUM(CASE WHEN i.state = 'RUNNING' THEN {_END} - i.started_at END), 0) AS run_s, "
        f"COALESCE(SUM(CASE WHEN i.state IN ('STOPPED', 'ERROR') THEN {_END} - i.started_at END), 0) AS stop_s, "
        f"COALESCE(MAX({_END}), r.started_at) AS last_active "
        "FROM program_runs r LEFT JOIN state_intervals i ON i.run_id = r.id "
    )

    def open_run(self, machine_id: str) -> dict[str, Any] | None:
        rows = self._query(
            self._RUN_SELECT + "WHERE r.machine_id = ? AND r.ended_at IS NULL GROUP BY r.id ORDER BY r.id DESC LIMIT 1",
            (machine_id,),
        )
        return rows[0] if rows else None

    def runs(self, t0: float, t1: float, machine_id: str | None = None) -> list[dict[str, Any]]:
        """Läufe, die [t0, t1) überlappen, inkl. reiner Lauf- und Stoppzeit."""
        sql = self._RUN_SELECT + "WHERE r.started_at < ? AND COALESCE(r.ended_at, ?) > ?"
        params: list[Any] = [t1, t1, t0]
        if machine_id:
            sql += " AND r.machine_id = ?"
            params.append(machine_id)
        return self._query(sql + " GROUP BY r.id ORDER BY r.started_at", params)

    def reference_runs(self, machine_id: str, program: str, limit: int) -> list[dict[str, Any]]:
        """Die letzten vollständig beobachteten, fertigen Läufe eines Programms (neueste zuerst)."""
        return self._query(
            self._RUN_SELECT
            + "WHERE r.machine_id = ? AND r.program = ? AND r.result = 'finished' AND r.start_observed = 1 "
            "GROUP BY r.id ORDER BY r.started_at DESC LIMIT ?",
            (machine_id, program, limit),
        )

    # --- Aufträge ------------------------------------------------------------------------

    def ensure_order(self, key: str, year: int, number: str, t: float, reopen: bool = False) -> str | None:
        """Auftrag anlegen, falls neu. Liefert "created", "reopened" oder None."""
        cur = self._execute(
            "INSERT INTO orders(key, year, number, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO NOTHING",
            (key, year, number, t),
        )
        if cur.rowcount:
            return "created"
        if reopen:
            cur = self._execute(
                "UPDATE orders SET status = 'open', closed_at = NULL WHERE key = ? AND status = 'closed'", (key,)
            )
            if cur.rowcount:
                return "reopened"
        return None

    _ORDER_COLUMNS = "key, year, number, title, status, created_at, closed_at"

    def orders(self, status: str = "all") -> list[dict[str, Any]]:
        where, params = ("", ()) if status == "all" else ("WHERE status = ? ", (status,))
        return self._query(f"SELECT {self._ORDER_COLUMNS} FROM orders {where}ORDER BY created_at DESC", params)

    def order(self, key: str) -> dict[str, Any] | None:
        rows = self._query(f"SELECT {self._ORDER_COLUMNS} FROM orders WHERE key = ?", (key,))
        return rows[0] if rows else None

    def update_order(self, key: str, title: str, status: str, t: float) -> None:
        self._execute(
            "UPDATE orders SET title = ?, status = ?, "
            "closed_at = CASE WHEN ? = 'closed' THEN COALESCE(closed_at, ?) ELSE NULL END WHERE key = ?",
            (title, status, status, t, key),
        )

    def programs_without_order(self) -> list[str]:
        with self._lock:
            rows = self._con.execute(
                "SELECT DISTINCT program FROM state_intervals WHERE order_key IS NULL AND program IS NOT NULL "
                "UNION SELECT DISTINCT program FROM program_runs WHERE order_key IS NULL AND program IS NOT NULL"
            ).fetchall()
        return [row[0] for row in rows]

    def assign_order(self, program: str, key: str) -> float:
        """Auftragsnummer für alle Daten eines Programms nachtragen; liefert den ersten Zeitpunkt."""
        with self.transaction():
            self._execute(
                "UPDATE state_intervals SET order_key = ? WHERE program = ? AND order_key IS NULL", (key, program)
            )
            self._execute("UPDATE program_runs SET order_key = ? WHERE program = ? AND order_key IS NULL", (key, program))
            rows = self._query("SELECT MIN(started_at) AS t FROM state_intervals WHERE program = ?", (program,))
        return rows[0]["t"] or 0.0

    def order_state_totals(self, states: tuple[str, ...]) -> list[dict[str, Any]]:
        marks = ", ".join("?" * len(states))
        return self._query(
            f"SELECT i.order_key, i.state, SUM({_END} - i.started_at) AS seconds, MIN(i.started_at) AS first, "
            f"MAX({_END}) AS last FROM state_intervals i WHERE i.order_key IS NOT NULL AND i.run_id IS NOT NULL "
            f"AND i.state IN ({marks}) GROUP BY i.order_key, i.state",
            states,
        )

    def order_run_counts(self) -> list[dict[str, Any]]:
        return self._query(
            "SELECT order_key, COUNT(*) AS runs, SUM(result = 'finished') AS finished FROM program_runs "
            "WHERE order_key IS NOT NULL GROUP BY order_key"
        )

    def order_programs(self) -> list[dict[str, Any]]:
        return self._query(
            "SELECT order_key, machine_id, program FROM program_runs WHERE order_key IS NOT NULL "
            "GROUP BY order_key, machine_id, program"
        )

    def order_program_totals(self, key: str, states: tuple[str, ...]) -> list[dict[str, Any]]:
        marks = ", ".join("?" * len(states))
        return self._query(
            f"SELECT i.program, i.machine_id, i.state, SUM({_END} - i.started_at) AS seconds FROM state_intervals i "
            f"WHERE i.order_key = ? AND i.run_id IS NOT NULL AND i.state IN ({marks}) "
            "GROUP BY i.program, i.machine_id, i.state",
            (key, *states),
        )

    def order_intervals(self, key: str, states: tuple[str, ...]) -> list[dict[str, Any]]:
        marks = ", ".join("?" * len(states))
        return self._query(
            f"SELECT i.machine_id, i.state, i.program, i.started_at AS start, {_END} AS end FROM state_intervals i "
            f"WHERE i.order_key = ? AND i.run_id IS NOT NULL AND i.state IN ({marks}) ORDER BY i.started_at",
            (key, *states),
        )

    def order_runs(self, key: str) -> list[dict[str, Any]]:
        return self._query(self._RUN_SELECT + "WHERE r.order_key = ? GROUP BY r.id ORDER BY r.started_at", (key,))

    # --- Werkzeuge -------------------------------------------------------------------------
    # Je Maschine und T-Nummer. tool_usage hält die Abschnitte, in denen das Werkzeug bei laufendem
    # Programm in der Spindel war (ended_at wird bei jeder Abfrage fortgeschrieben). Die Einsatzzeit
    # ist die Summe dieser Abschnitte seit reset_at (Anlage bzw. letztes Zurücksetzen).

    _TOOL_SELECT = (
        "SELECT t.machine_id, t.number, t.name, t.note, t.limit_s, t.reset_at, t.created_at, "
        "COALESCE((SELECT SUM(u.ended_at - MAX(u.started_at, t.reset_at)) FROM tool_usage u "
        "WHERE u.machine_id = t.machine_id AND u.number = t.number AND u.ended_at > t.reset_at), 0) AS used_s, "
        "(SELECT MAX(u.ended_at) FROM tool_usage u WHERE u.machine_id = t.machine_id AND u.number = t.number) "
        "AS last_used_at FROM tools t "
    )

    def ensure_tool(self, machine_id: str, number: int, name: str, t: float) -> bool:
        """Werkzeug anlegen, falls neu (True); sonst nur den Namen aus der Steuerung nachführen."""
        cur = self._execute(
            "INSERT INTO tools(machine_id, number, name, reset_at, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(machine_id, number) DO NOTHING",
            (machine_id, number, name, t, t),
        )
        if cur.rowcount:
            return True
        if name:
            self._execute(
                "UPDATE tools SET name = ? WHERE machine_id = ? AND number = ? AND name != ?",
                (name, machine_id, number, name),
            )
        return False

    def set_tool_names(self, machine_id: str, names: dict[int, str]) -> int:
        """Namen aus der Werkzeugtabelle der Steuerung übernehmen; liefert die Zahl der Änderungen."""
        changed = 0
        with self.transaction():
            for number, name in names.items():
                cur = self._execute(
                    "UPDATE tools SET name = ? WHERE machine_id = ? AND number = ? AND name != ?",
                    (name, machine_id, number, name),
                )
                changed += cur.rowcount
        return changed

    def insert_tool(self, machine_id: str, number: int, note: str, limit_s: float | None, t: float) -> bool:
        cur = self._execute(
            "INSERT INTO tools(machine_id, number, note, limit_s, reset_at, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(machine_id, number) DO NOTHING",
            (machine_id, number, note, limit_s, t, t),
        )
        return bool(cur.rowcount)

    def tools(self, machine_id: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE t.machine_id = ? ", (machine_id,)) if machine_id else ("", ())
        return self._query(self._TOOL_SELECT + where + "ORDER BY t.machine_id, t.number", params)

    def tool(self, machine_id: str, number: int) -> dict[str, Any] | None:
        rows = self._query(self._TOOL_SELECT + "WHERE t.machine_id = ? AND t.number = ?", (machine_id, number))
        return rows[0] if rows else None

    def update_tool(self, machine_id: str, number: int, note: str, limit_s: float | None) -> None:
        self._execute(
            "UPDATE tools SET note = ?, limit_s = ? WHERE machine_id = ? AND number = ?",
            (note, limit_s, machine_id, number),
        )

    def reset_tool(self, machine_id: str, number: int, t: float) -> float:
        """Einsatzzeit auf 0 setzen; der bisherige Stand kommt in die Historie. Liefert ihn zurück."""
        with self.transaction():
            tool = self.tool(machine_id, number)
            used = tool["used_s"] if tool else 0.0
            self._execute(
                "INSERT INTO tool_resets(machine_id, number, reset_at, used_s, limit_s) VALUES (?, ?, ?, ?, ?)",
                (machine_id, number, t, used, tool["limit_s"] if tool else None),
            )
            self._execute("UPDATE tools SET reset_at = ? WHERE machine_id = ? AND number = ?", (t, machine_id, number))
        return used

    def delete_tool(self, machine_id: str, number: int) -> None:
        """Eintrag und Historie entfernen; die Einsatzabschnitte bleiben (Grundlage der Auswertung)."""
        with self.transaction():
            self._execute("DELETE FROM tool_resets WHERE machine_id = ? AND number = ?", (machine_id, number))
            self._execute("DELETE FROM tools WHERE machine_id = ? AND number = ?", (machine_id, number))

    def tool_resets(self, machine_id: str, number: int) -> list[dict[str, Any]]:
        return self._query(
            "SELECT reset_at, used_s, limit_s FROM tool_resets WHERE machine_id = ? AND number = ? "
            "ORDER BY reset_at DESC",
            (machine_id, number),
        )

    def open_tool_usage(self, machine_id: str, number: int, t: float) -> int:
        cur = self._execute(
            "INSERT INTO tool_usage(machine_id, number, started_at, ended_at) VALUES (?, ?, ?, ?)",
            (machine_id, number, t, t),
        )
        return int(cur.lastrowid)

    def touch_tool_usage(self, usage_id: int, t: float) -> None:
        self._execute("UPDATE tool_usage SET ended_at = ? WHERE id = ?", (t, usage_id))

    # --- Satzverlauf (Grundlage der Restlaufzeit-Prognose) ------------------------------

    def add_progress(self, run_id: int, t_run: float, program: str | None, line_no: int) -> None:
        self._execute(
            "INSERT INTO run_progress(run_id, t_run, program, line_no) VALUES (?, ?, ?, ?)",
            (run_id, t_run, program, line_no),
        )

    def run_progress(self, run_id: int) -> list[tuple[float, str | None, int]]:
        with self._lock:
            rows = self._con.execute(
                "SELECT t_run, program, line_no FROM run_progress WHERE run_id = ? ORDER BY t_run", (run_id,)
            ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def prune_progress(self, machine_id: str, program: str | None, keep: int) -> None:
        """Satzverlauf nur für die letzten ``keep`` Referenzläufe eines Programms aufheben."""
        self._execute(
            "DELETE FROM run_progress WHERE run_id IN ("
            " SELECT id FROM program_runs WHERE machine_id = ? AND program IS ? AND ended_at IS NOT NULL"
            " AND id NOT IN (SELECT id FROM program_runs WHERE machine_id = ? AND program IS ?"
            "  AND result = 'finished' AND start_observed = 1 ORDER BY started_at DESC LIMIT ?))",
            (machine_id, program, machine_id, program, keep),
        )

    # --- Programmdateien -------------------------------------------------------------

    def program_file(self, machine_id: str, path: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT path, size, mtime, blocks, error, checked_at FROM program_files WHERE machine_id = ? AND path = ?",
            (machine_id, path),
        )
        return rows[0] if rows else None

    def save_program_file(
        self, machine_id: str, path: str, size: int | None, mtime: float | None, blocks: int | None,
        error: str | None, checked_at: float,
    ) -> None:
        self._execute(
            "INSERT INTO program_files(machine_id, path, size, mtime, blocks, error, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(machine_id, path) DO UPDATE SET "
            "size = excluded.size, mtime = excluded.mtime, blocks = excluded.blocks, "
            "error = excluded.error, checked_at = excluded.checked_at",
            (machine_id, path, size, mtime, blocks, error, checked_at),
        )

    def touch_program_file(self, machine_id: str, path: str, checked_at: float) -> None:
        self._execute(
            "UPDATE program_files SET checked_at = ? WHERE machine_id = ? AND path = ?", (checked_at, machine_id, path)
        )

    def program_blocks(self) -> dict[tuple[str, str], int]:
        rows = self._query("SELECT machine_id, path, blocks FROM program_files WHERE blocks IS NOT NULL")
        return {(r["machine_id"], r["path"]): r["blocks"] for r in rows}

    # --- Ereignisse ----------------------------------------------------------------

    def add_event(self, machine_id: str, t: float, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self._execute(
            "INSERT INTO events(machine_id, ts, type, payload) VALUES (?, ?, ?, ?)",
            (machine_id, t, event_type, json.dumps(payload or {}, ensure_ascii=False)),
        )

    def events(self, t0: float, t1: float, machine_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        sql = "SELECT id, machine_id, ts, type, payload FROM events WHERE ts >= ? AND ts < ?"
        params: list[Any] = [t0, t1]
        if machine_id:
            sql += " AND machine_id = ?"
            params.append(machine_id)
        rows = self._query(sql + " ORDER BY ts DESC LIMIT ?", [*params, limit])
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows
