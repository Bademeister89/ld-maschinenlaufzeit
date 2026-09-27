"""Adapter liefern Momentaufnahmen einer Steuerung (echt oder simuliert)."""

from .base import AdapterError, MachineAdapter, Snapshot

__all__ = ["AdapterError", "MachineAdapter", "Snapshot"]
