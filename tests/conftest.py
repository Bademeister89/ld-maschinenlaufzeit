from __future__ import annotations

import pytest

from app.adapters.base import Snapshot
from app.collector import MachineCollector
from app.config import MachineConfig
from app.db import Database

MACHINE = MachineConfig(id="m1", name="Maschine 1", host="127.0.0.1")


class NullAdapter:
    def connect(self) -> dict[str, str]:
        return {}

    def read(self) -> Snapshot:
        raise AssertionError("in Tests wird process() direkt aufgerufen")

    def close(self) -> None:
        pass


def snap(pgm_state: str, program: str | None = "P1", exec_mode: str = "AUTOMATIC", **kw) -> Snapshot:
    return Snapshot(pgm_state=pgm_state, exec_mode=exec_mode, program=program, **kw)


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def make_collector(db):
    def factory(machine: MachineConfig = MACHINE) -> MachineCollector:
        return MachineCollector(machine, NullAdapter(), db, poll_interval_s=2)

    return factory


def feed(collector: MachineCollector, *steps: tuple[float, Snapshot | None]) -> None:
    for t, s in steps:
        collector.process(s, t)
