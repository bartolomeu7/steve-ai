"""Authenticode signature verification — deliberately NOT implemented in V1.5.

A real, working implementation was attempted here via `ctypes` + `WinVerifyTrust`
(wintrust.dll) — stdlib only, no new dependency. It was tested against real Windows
binaries before being trusted with anything: `C:\\Windows\\System32\\notepad.exe` and
`cmd.exe` (both definitely Microsoft-signed, catalog-signed system files) came back as
"unsigned", while an unrelated, unremarkable binary (this project's own venv
`python.exe`) came back as "signed" — the exact opposite of correct in both directions.
Whatever the underlying cause (catalog vs. embedded signatures need different handling,
a struct layout/flag mistake, or something else), the result was **not reliable enough
to trust**, and this project's own governing rule (see docs/ARCHITECTURE.md and every
prior V1.4.1 hardening report) is that an unreliable source must never be presented as
a real reading — a wrong "unsigned"/"signed" verdict here would be worse than useless,
it would actively mislead security/rules.py::unsigned_signal into flagging legitimate,
Microsoft-signed system binaries as suspicious.

Rather than ship a signature check that was measured to be wrong, this always returns
`None` ("not verified in this version"). security/rules.py's unsigned_signal() already
only fires on a definite `signed is False`, never on `None` — so this module returning
None everywhere simply means that one signal never contributes, not that anything
downstream misbehaves. A future version could revisit this with a corrected
implementation (or `pywin32`/a small trusted library once one is evaluated), tested the
same rigorous way (against known-signed AND known-unsigned real binaries) before being
trusted again.
"""
from __future__ import annotations


def is_authenticode_signed(path: str) -> bool | None:
    """Always None in this version — see module docstring. `path` is accepted (not
    just a no-arg stub) so a future real implementation is a drop-in replacement with
    no call-site changes needed anywhere in security/."""
    return None
