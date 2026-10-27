"""
Netzwerksperre für den Sidecar (Phase 3).

Ein Python-Audit-Hook (PEP 578) verweigert jede ausgehende Verbindung und
jede Namensauflösung zu einem Ziel außerhalb dieses Rechners (127.0.0.1,
::1, localhost). Damit kann weder eine Bibliothek (z. B. huggingface_hub)
unbeabsichtigt ein Modell nachladen noch irgendein Dokumentinhalt das
Gerät verlassen. Ein einmal installierter Audit-Hook lässt sich in Python
nicht wieder entfernen.

Grenze: Der Hook sieht nur Netzwerkzugriffe, die über Pythons socket-Modul
laufen. Native Bibliotheken (llama.cpp, PyTorch) bauen selbst keine
Verbindungen auf; eine Firewall-Regel wäre die zusätzliche Absicherung
auf Betriebssystemebene.
"""

import ipaddress
import logging
import sys
import threading

_LOCAL_NAMES = {"localhost", "localhost.localdomain", ""}
_installed = False
_lock = threading.Lock()
blocked: list[str] = []  # verweigerte Ziele (für Log und Tests)


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]").split("%")[0].lower()
    if host in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _deny(target: str):
    blocked.append(target)
    logging.getLogger("redaction").warning(f"[Offline] Netzwerkzugriff verweigert: {target}")
    raise PermissionError(f"Netzwerkzugriff blockiert (Offline-Betrieb): {target}")


def _hook(event: str, args):
    if event in ("socket.connect", "socket.sendto"):
        address = args[1] if len(args) > 1 else None
        if isinstance(address, tuple) and address and not _is_local(address[0]):
            _deny(f"{address[0]}:{address[1] if len(address) > 1 else ''}")
    elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"):
        host = args[0] if args else None
        if not _is_local(host):
            _deny(str(host))


def install() -> None:
    """Sperre aktivieren (idempotent)."""
    global _installed
    with _lock:
        if not _installed:
            sys.addaudithook(_hook)
            _installed = True


def is_installed() -> bool:
    return _installed
