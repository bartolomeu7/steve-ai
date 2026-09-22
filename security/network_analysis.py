"""Process<->remote-endpoint correlation — NEVER packet content, NEVER a proxy/MITM,
NEVER TLS termination or certificate installation. Everything here is metadata already
exposed by the OS itself (the same information `netstat -ano` shows): which process
owns which socket, and which remote address/port it's talking to. This is genuinely new
data SystemMonitorService doesn't already collect (it only tracks aggregate
upload/download bytes/sec, never per-connection/per-process detail) — justified per the
V1.5 spec's "se precisar de uma coleta específica para segurança: justificar".
"""
from __future__ import annotations

import logging

import psutil

from security.models import NetworkEvidence

logger = logging.getLogger("steve.security.network_analysis")

_LOOPBACK_ADDRESSES = ("127.0.0.1", "::1")


def sample_external_connections() -> tuple[NetworkEvidence, ...]:
    """One point-in-time snapshot of every process with an established connection to a
    non-loopback remote address. Never opens a new connection, never reads socket
    content — `psutil.net_connections()` only exposes what the OS's own connection
    table already has (equivalent to `netstat -ano`)."""
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        logger.debug("Falha ao listar conexões de rede (sem permissão ou indisponível).", exc_info=True)
        return ()

    evidence: list[NetworkEvidence] = []
    for conn in connections:
        if not conn.raddr:
            continue  # a listening socket or one with no remote peer -- not a "conexão externa"
        if conn.raddr.ip in _LOOPBACK_ADDRESSES:
            continue

        process_name = None
        if conn.pid is not None:
            try:
                process_name = psutil.Process(conn.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                process_name = None  # process exited between the connection snapshot and this lookup

        evidence.append(
            NetworkEvidence(
                pid=conn.pid,
                process_name=process_name,
                remote_address=conn.raddr.ip,
                remote_port=conn.raddr.port,
                status=conn.status,
            )
        )
    return tuple(evidence)
