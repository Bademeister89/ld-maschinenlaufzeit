"""Erreichbarkeit einer Adresse prüfen (TCP), z. B. des Routers am Maschinenstandort.

Damit lässt sich ein Ausfall der Verbindung zum Standort (VPN weg) von ausgeschalteten
Maschinen unterscheiden. ICMP-Ping bräuchte im Container Sonderrechte, daher TCP.
"""

from __future__ import annotations

import re
import socket

DEFAULT_PORT = 80
_ADDRESS = re.compile(r"^(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(?::(?P<port>\d{1,5}))?$")


def parse_address(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """``"192.168.0.1"`` oder ``"192.168.0.1:443"`` → (Host, Port)."""
    match = _ADDRESS.match(text.strip())
    if not match:
        raise ValueError(f"„{text}“ ist keine gültige Adresse (erwartet z. B. 192.168.0.1 oder 192.168.0.1:443)")
    port = int(match["port"] or default_port)
    if not 1 <= port <= 65535:
        raise ValueError(f"Port {port} liegt nicht zwischen 1 und 65535")
    return match["host"], port


def reachable(address: str, timeout: float = 3.0) -> bool:
    host, port = parse_address(address)
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except ConnectionRefusedError:
        return True  # das Gerät hat geantwortet (Port zu) – das Netz zum Standort steht
    except OSError:
        return False
