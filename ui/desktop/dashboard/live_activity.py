"""LiveActivityTab ("Steve Core"): a stylized feed of Steve's own real activity.

Hard rule (see docs/ARCHITECTURE.md and the V1.4 spec): the semantics shown are always
real — every line rendered here traces back to an actual event on the shared EventBus
(core/orchestrator.py's TOOL_STARTED/TOOL_COMPLETED/AI_REQUEST_STARTED/
AI_RESPONSE_RECEIVED, voice/service.py's VOICE_STATE_CHANGED, core/lifecycle.py's
STEVE_READY/STEVE_DEGRADED, system_monitor/service.py's SYSTEM_MONITOR_*) — only the
*presentation* is stylized (cyberpunk-ish "> CORE MODULE / EXECUTING" lines instead of
the raw event name). Nothing here is a fake terminal or invented text: nothing is
displayed unless a real event caused it, and the feed returns to IDLE once an activity's
matching completion event arrives — it never "looks busy" on its own.

Every payload is small and pre-sanitized at the source (event publishers never put
message content, tool results, or file paths into these events — see
core/orchestrator.py's docstring) but `_sanitize` is still applied defensively here, so
this widget never trusts a payload blindly even if a future event forgets that rule.
"""
from __future__ import annotations

import re
import time

import customtkinter as ctk

from ui.desktop.styles import Palette

IDLE_LABEL = "IDLE"
_RETURN_TO_IDLE_MS = 2500
_MAX_LINES = 200

#: Any path-shaped substring ("C:\...", "/home/...") gets collapsed before display —
#: belt-and-suspenders alongside publishers never sending paths in the first place.
_PATH_RE = re.compile(r"([A-Za-z]:\\[^\s]+|/[\w./-]{3,})")


def _sanitize(text: str) -> str:
    return _PATH_RE.sub("[caminho ocultado]", text)


# event_name -> (state_label, feed_lines, is_completion)
_EVENT_PRESENTATION: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "lifecycle.steve_ready": ("READY", ("> STARTUP", "> READY"), True),
    "lifecycle.steve_degraded": ("DEGRADED", ("> STARTUP", "> DEGRADED"), True),
    "orchestrator.ai_request_started": ("PROCESSING", ("> AI CORE", "> PROCESSING"), False),
    "orchestrator.ai_response_received": ("COMPLETE", ("> AI CORE", "> RESPONSE READY"), True),
    "orchestrator.tool_started": ("EXECUTING", ("> CORE MODULE", "> EXECUTING"), False),
    "orchestrator.tool_completed": ("COMPLETE", ("> OPERATION", "> COMPLETE"), True),
    "voice.state_changed": ("LISTENING", ("> VOICE INTERFACE", "> ACTIVE"), False),
    "system_monitor.started": ("MONITORING", ("> SYSTEM MONITOR", "> ONLINE"), True),
    "system_monitor.stopped": ("IDLE", ("> SYSTEM MONITOR", "> OFFLINE"), True),
    "system_monitor.error": ("ERROR", ("> SYSTEM MONITOR", "> ERROR"), True),
    "security.engine_started": ("MONITORING", ("> SECURITY CENTER", "> ONLINE"), True),
    "security.engine_stopped": ("IDLE", ("> SECURITY CENTER", "> OFFLINE"), True),
    "security.engine_error": ("ERROR", ("> SECURITY CENTER", "> ERROR"), True),
    # A real, evidence-backed finding just appeared — see security/engine.py. The
    # process/file/reasons themselves are shown in the Security tab's own findings
    # table and details panel (opened from here), not duplicated into this feed, which
    # only ever shows short, stylized state lines for every event source.
    "security.finding_created": ("ALERT", ("> SECURITY CENTER", "> FINDING DETECTED"), True),
    "security.scan_started": ("SCANNING", ("> SECURITY SCAN", "> STARTED"), False),
    "security.scan_completed": ("COMPLETE", ("> SECURITY SCAN", "> COMPLETE"), True),
    "security.scan_cancelled": ("IDLE", ("> SECURITY SCAN", "> CANCELLED"), True),
}


class LiveActivityTab(ctk.CTkFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.palette = palette
        self._idle_after_id: str | None = None
        self._line_count = 0

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(
            header, text="STEVE CORE", text_color=self.palette.text,
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
        ).pack(side="left")
        self.state_label = ctk.CTkLabel(
            header, text=IDLE_LABEL, text_color=self.palette.text_muted,
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
        )
        self.state_label.pack(side="right")

        self.feed = ctk.CTkTextbox(
            self, fg_color=self.palette.surface, text_color=self.palette.accent,
            font=ctk.CTkFont(family="Consolas", size=12), wrap="word", activate_scrollbars=True,
        )
        self.feed.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.feed.configure(state="disabled")

    def handle_event(self, event_name: str, data: dict) -> None:
        """Called on the main thread only (the Dashboard window marshals every EventBus
        callback through BackgroundRunner before calling this — see window.py) — safe to
        touch widgets directly here."""
        presentation = _EVENT_PRESENTATION.get(event_name)
        if presentation is None:
            return
        state_label, lines, is_completion = presentation
        self._append_lines(lines)
        self._set_state(state_label)
        if is_completion:
            self._schedule_return_to_idle()
        elif self._idle_after_id is not None:
            self.after_cancel(self._idle_after_id)
            self._idle_after_id = None

    def _append_lines(self, lines: tuple[str, ...]) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.feed.configure(state="normal")
        for line in lines:
            self.feed.insert("end", f"[{timestamp}] {_sanitize(line)}\n")
            self._line_count += 1
        if self._line_count > _MAX_LINES:
            excess = self._line_count - _MAX_LINES
            self.feed.delete("1.0", f"{excess + 1}.0")
            self._line_count = _MAX_LINES
        self.feed.see("end")
        self.feed.configure(state="disabled")

    def _set_state(self, label: str) -> None:
        self.state_label.configure(text=label, text_color=self.palette.accent)

    def _schedule_return_to_idle(self) -> None:
        if self._idle_after_id is not None:
            self.after_cancel(self._idle_after_id)
        self._idle_after_id = self.after(_RETURN_TO_IDLE_MS, self._go_idle)

    def _go_idle(self) -> None:
        self._idle_after_id = None
        self.state_label.configure(text=IDLE_LABEL, text_color=self.palette.text_muted)

    def cancel_pending_callbacks(self) -> None:
        """Called right before the Dashboard window is destroyed — an `after()` callback
        firing against an already-destroyed widget is exactly the kind of leak the spec
        warns against."""
        if self._idle_after_id is not None:
            self.after_cancel(self._idle_after_id)
            self._idle_after_id = None
