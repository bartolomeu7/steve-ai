"""Unit tests for security/network_analysis.py — psutil mocked so this never depends on
real network activity being present on the test machine."""
from __future__ import annotations

import types

import pytest

from security.network_analysis import sample_external_connections


def _conn(pid, raddr, status="ESTABLISHED"):
    return types.SimpleNamespace(pid=pid, raddr=raddr, status=status)


def test_sample_external_connections_includes_established_remote(monkeypatch):
    raddr = types.SimpleNamespace(ip="93.184.216.34", port=443)
    monkeypatch.setattr(
        "security.network_analysis.psutil.net_connections", lambda kind="inet": [_conn(1234, raddr)]
    )
    monkeypatch.setattr(
        "security.network_analysis.psutil.Process",
        lambda pid: types.SimpleNamespace(name=lambda: "browser.exe"),
    )

    evidence = sample_external_connections()

    assert len(evidence) == 1
    assert evidence[0].pid == 1234
    assert evidence[0].process_name == "browser.exe"
    assert evidence[0].remote_address == "93.184.216.34"
    assert evidence[0].remote_port == 443


def test_sample_external_connections_excludes_listening_sockets(monkeypatch):
    # raddr is an empty tuple for a LISTEN socket, per psutil's own convention
    monkeypatch.setattr(
        "security.network_analysis.psutil.net_connections", lambda kind="inet": [_conn(1, (), status="LISTEN")]
    )
    assert sample_external_connections() == ()


def test_sample_external_connections_excludes_loopback(monkeypatch):
    raddr = types.SimpleNamespace(ip="127.0.0.1", port=8080)
    monkeypatch.setattr("security.network_analysis.psutil.net_connections", lambda kind="inet": [_conn(1, raddr)])
    assert sample_external_connections() == ()


def test_sample_external_connections_survives_missing_process(monkeypatch):
    """The process behind a connection can exit between the connection snapshot and
    the name lookup -- must not raise, process_name just comes back None."""
    import psutil as real_psutil

    raddr = types.SimpleNamespace(ip="1.2.3.4", port=80)
    monkeypatch.setattr("security.network_analysis.psutil.net_connections", lambda kind="inet": [_conn(999, raddr)])

    def _raise(pid):
        raise real_psutil.NoSuchProcess(pid)

    monkeypatch.setattr("security.network_analysis.psutil.Process", _raise)

    evidence = sample_external_connections()
    assert len(evidence) == 1
    assert evidence[0].process_name is None


def test_sample_external_connections_survives_access_denied(monkeypatch):
    import psutil as real_psutil

    def _raise(kind="inet"):
        raise real_psutil.AccessDenied()

    monkeypatch.setattr("security.network_analysis.psutil.net_connections", _raise)
    assert sample_external_connections() == ()


def test_sample_external_connections_never_opens_a_connection(monkeypatch):
    """Structural guarantee: this module must only ever call psutil's read-only
    connection-listing APIs, never anything that could itself open a socket."""
    import security.network_analysis as network_analysis_module
    import inspect

    source = inspect.getsource(network_analysis_module)
    for forbidden in ("socket.socket", "requests.", "urllib.request", "http.client"):
        assert forbidden not in source
