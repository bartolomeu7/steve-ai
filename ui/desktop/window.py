"""MainWindow — dual-mode Steve UI (HUD Jarvis + Workspace).

Toggle: header button or Ctrl+Shift+U. Persists via on_ui_mode_change("hud"|"workspace").
Keeps a single Orb instance and re-packs it into the active mode host.
"""
from __future__ import annotations

import logging
from typing import Callable, Literal

import customtkinter as ctk

from ui.desktop.chat_view import ChatView
from ui.desktop.controls import InputBar
from ui.desktop.hud_chrome import HudClock, HudCornerLabel, HudMicCluster
from ui.desktop.orb.audio_reactive import normalized_rms
from ui.desktop.orb.orb import DEFAULT_SIZE as ORB_HERO_SIZE
from ui.desktop.orb.orb import Orb
from ui.desktop.orb.states import STATE_LABELS, OrbState
from ui.desktop.project_panel import ProjectPanel
from ui.desktop.status_bar import StatusBar
from ui.desktop.styles import (
    FONT_FAMILY,
    FONT_MONO,
    FONT_SIZE_SMALL,
    FONT_SIZE_TITLE,
    FONT_SIZE_TINY,
    Palette,
)
from ui.desktop.boot_splash import BootSplashOverlay
from ui.desktop.workspace_sidebar import WorkspaceSidebar

logger = logging.getLogger("steve.ui.desktop.window")

ERROR_AUTO_RECOVER_MS = 1400
UiMode = Literal["hud", "workspace"]


class MainWindow(ctk.CTk):
    """Assign `self.controller` before interaction (see ui/desktop/app.py)."""

    def __init__(
        self,
        app_status: dict,
        voice_available: bool,
        appearance_mode: str = "cyber",
        orb_enabled: bool = True,
        ui_mode: str = "hud",
        on_close: Callable[[], bool | None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        on_open_dashboard: Callable[[], None] | None = None,
        on_ui_mode_change: Callable[[str], None] | None = None,
    ):
        super().__init__()
        self.controller = None
        self._voice_available = voice_available
        self._orb_enabled = orb_enabled
        self._on_close_cb = on_close
        self._on_open_settings_cb = on_open_settings
        self._on_reply_cb = on_reply
        self._on_quit_cb = on_quit
        self._on_open_dashboard_cb = on_open_dashboard
        self._on_ui_mode_change_cb = on_ui_mode_change
        self._on_before_mic_cb = None
        self._on_after_voice_cb = None
        self._mode: UiMode = "workspace" if str(ui_mode).lower() == "workspace" else "hud"
        self._app_status = dict(app_status or {})
        self._hud_history_open = False

        ctk.set_appearance_mode(
            "dark"
            if appearance_mode.lower() in ("dark", "cyber", "thomas", "dark_cyber", "jarvis")
            else "light"
        )
        self.palette = palette_for(appearance_mode)
        self.title("STEVE")
        self.minsize(720, 640)
        self.configure(fg_color=self.palette.bg)

        # Parking lot for shared widgets between rebuilds
        self._park = ctk.CTkFrame(self, width=1, height=1, fg_color=self.palette.bg)

        self._orb_host = ctk.CTkFrame(self._park, fg_color="transparent")
        self.orb = Orb(self._orb_host, palette=self.palette, size=max(ORB_HERO_SIZE, 280))
        self.orb.pack(anchor="center")
        self.orb.bind("<Button-1>", self._handle_orb_click)

        self._chat_host = ctk.CTkFrame(self._park, fg_color=self.palette.bg)
        self.chat_view = ChatView(self._chat_host, palette=self.palette, fg_color=self.palette.bg)
        self.chat_view.pack(fill="both", expand=True)

        self._input_host = ctk.CTkFrame(self._park, fg_color=self.palette.bg)
        self.input_bar = InputBar(
            self._input_host,
            palette=self.palette,
            on_send=self._handle_send,
            on_mic=self._handle_mic,
            voice_available=voice_available,
            voice_only=(self._mode == "hud"),
        )
        self.input_bar.pack(fill="x")

        self.status_bar = StatusBar(self._park, palette=self.palette)

        self._shell = ctk.CTkFrame(self, fg_color=self.palette.bg, corner_radius=0)
        self._shell.pack(fill="both", expand=True)
        self._mode_root: ctk.CTkFrame | None = None
        self._hud_clock: HudClock | None = None
        self._hud_mic: HudMicCluster | None = None
        self._hud_status: ctk.CTkLabel | None = None
        self._hud_corner_model: HudCornerLabel | None = None
        self._hud_corner_mic: HudCornerLabel | None = None
        self._hud_history: ctk.CTkFrame | None = None
        self._orb_state_chip: ctk.CTkFrame | None = None
        self.orb_label: ctk.CTkLabel | None = None
        self.orb_status_detail: ctk.CTkLabel | None = None
        self.connection_label: ctk.CTkLabel | None = None
        self._connection_pill: ctk.CTkFrame | None = None

        self.set_ui_mode(self._mode, persist=False, initial=True)
        self.apply_status(self._app_status)
        if self._orb_enabled:
            self.orb.start()

        self.bind("<Control-Shift-U>", lambda _e: self.toggle_ui_mode())
        self.bind("<Control-Shift-u>", lambda _e: self.toggle_ui_mode())
        self.bind("<Escape>", lambda _e: self._handle_close())
        self.bind("<FocusIn>", lambda _e: self._on_window_focus(True))
        self.bind("<FocusOut>", lambda _e: self._on_window_focus(False))
        self.protocol("WM_DELETE_WINDOW", self._handle_close)
        self._borderless_fs = False
        self.after(50, self._boot_borderless_fullscreen)

    # ----- mode switching -------------------------------------------------------
    def set_ui_mode(self, mode: str, persist: bool = True, initial: bool = False) -> None:
        mode_n: UiMode = "workspace" if str(mode).lower() == "workspace" else "hud"
        if not initial and mode_n == self._mode and self._mode_root is not None:
            return
        self._mode = mode_n
        self._park_shared()
        if self._hud_clock is not None:
            self._hud_clock.stop()
            self._hud_clock = None
        if self._mode_root is not None:
            self._mode_root.destroy()
            self._mode_root = None
        self._hud_mic = None
        self._hud_history = None
        self._hud_history_open = False
        self._hud_status = None
        self._hud_corner_model = None
        self._hud_corner_mic = None
        self.connection_label = None
        self._connection_pill = None

        if mode_n == "hud":
            if not getattr(self, "_borderless_fs", False):
                self.geometry("960x820")
            self._build_hud()
            self.input_bar.set_voice_only(True)
        else:
            if not getattr(self, "_borderless_fs", False):
                self.geometry("1200x820")
            self._build_workspace()
            self.input_bar.set_voice_only(False)

        self.apply_status(self._app_status)
        if persist and self._on_ui_mode_change_cb:
            try:
                self._on_ui_mode_change_cb(mode_n)
            except Exception:
                logger.exception("on_ui_mode_change failed")

    def toggle_ui_mode(self) -> None:
        self.set_ui_mode("workspace" if self._mode == "hud" else "hud")

    def _park_shared(self) -> None:
        """Hide shared hosts; widgets are recreated under the active mode parent."""
        for w in (getattr(self, "_orb_host", None), getattr(self, "_chat_host", None),
                  getattr(self, "_input_host", None), getattr(self, "status_bar", None)):
            if w is None:
                continue
            try:
                w.pack_forget()
            except Exception:
                pass
            try:
                w.place_forget()
            except Exception:
                pass

    def _remake_orb(self, parent, *, size: int | None = None) -> None:
        """Recreate Orb under parent (Tk cannot reparent; do not edit orb.py)."""
        old = getattr(self, "orb", None)
        running = bool(old is not None and getattr(old, "_running", False))
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
            try:
                old.destroy()
            except Exception:
                pass
        self._orb_host = ctk.CTkFrame(parent, fg_color="transparent")
        sz = int(size or max(ORB_HERO_SIZE, 280))
        self.orb = Orb(self._orb_host, palette=self.palette, size=sz)
        self.orb.pack(anchor="center")
        self.orb.bind("<Button-1>", self._handle_orb_click)
        if self._orb_enabled and (running or True):
            try:
                self.orb.start()
            except Exception:
                logger.debug("orb start failed", exc_info=True)

    def _remake_chat(self, parent) -> None:
        old = getattr(self, "chat_view", None)
        history = []
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        self._chat_host = ctk.CTkFrame(parent, fg_color=self.palette.bg)
        self.chat_view = ChatView(self._chat_host, palette=self.palette, fg_color=self.palette.bg)
        self.chat_view.pack(fill="both", expand=True)

    def _remake_input(self, parent, *, voice_only: bool) -> None:
        old = getattr(self, "input_bar", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        self._input_host = ctk.CTkFrame(parent, fg_color=self.palette.bg)
        self.input_bar = InputBar(
            self._input_host,
            palette=self.palette,
            on_send=self._handle_send,
            on_mic=self._handle_mic,
            voice_available=self._voice_available,
            voice_only=voice_only,
        )
        self.input_bar.pack(fill="x")

    def _remake_status(self, parent) -> None:
        old = getattr(self, "status_bar", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        self.status_bar = StatusBar(parent, palette=self.palette)

    # ----- HUD ------------------------------------------------------------------
    def _build_hud(self) -> None:
        p = self.palette
        root = ctk.CTkFrame(self._shell, fg_color=p.bg, corner_radius=0)
        root.pack(fill="both", expand=True)
        self._mode_root = root

        top = ctk.CTkFrame(root, fg_color=p.bg, height=72, corner_radius=0)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)

        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", padx=24, pady=12)
        ctk.CTkLabel(
            left, text="STEVE", text_color=p.accent,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_TITLE + 4, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            left, text="ASSISTENTE PESSOAL INTELIGENTE", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_TINY),
        ).pack(anchor="w")

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right", padx=20, pady=8)
        self._hud_clock = HudClock(right, palette=p)
        self._hud_clock.pack(side="right")

        mid = ctk.CTkFrame(top, fg_color="transparent")
        mid.pack(side="right", padx=8)
        ctk.CTkButton(
            mid, text="Workspace", width=110, height=32, fg_color="transparent",
            text_color=p.accent, hover_color=p.hover, border_width=1, border_color=p.border,
            corner_radius=14, font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
            command=lambda: self.set_ui_mode("workspace"),
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            mid, text="⚙", width=36, height=32, fg_color="transparent",
            text_color=p.text_muted, hover_color=p.hover, command=self._handle_open_settings,
        ).pack(side="right", padx=2)

        corners = ctk.CTkFrame(root, fg_color="transparent", height=22)
        corners.pack(fill="x", padx=24)
        self._hud_corner_model = HudCornerLabel(corners, palette=p, text="")
        self._hud_corner_model.pack(side="left")
        self._hud_corner_mic = HudCornerLabel(corners, palette=p, text="")
        self._hud_corner_mic.pack(side="right")

        stage = ctk.CTkFrame(root, fg_color=p.bg)
        stage.pack(fill="both", expand=True)
        center = ctk.CTkFrame(stage, fg_color="transparent")
        center.place(relx=0.5, rely=0.42, anchor="center")

        self._remake_orb(center, size=max(ORB_HERO_SIZE, 280))
        self._orb_host.pack(anchor="center")
        self._orb_state_chip = ctk.CTkFrame(center, fg_color=p.tag_bg, corner_radius=14)
        self._orb_state_chip.pack(anchor="center", pady=(14, 0))
        self.orb_label = ctk.CTkLabel(
            self._orb_state_chip, text=STATE_LABELS[OrbState.IDLE], text_color=p.tag_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL, weight="bold"),
        )
        self.orb_label.pack(side="left", padx=(12, 6), pady=5)
        self.orb_status_detail = ctk.CTkLabel(
            self._orb_state_chip, text="Pronto para conversar", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self.orb_status_detail.pack(side="left", padx=(0, 12), pady=5)
        self._hud_status = ctk.CTkLabel(
            center, text="Aguardando comando de voz…", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_MONO, size=FONT_SIZE_TINY),
        )
        self._hud_status.pack(anchor="center", pady=(8, 0))

        self._hud_history = ctk.CTkFrame(root, fg_color=p.surface, height=220, corner_radius=0)
        self._remake_chat(self._hud_history)
        self._chat_host.pack(fill="both", expand=True, padx=8, pady=8)

        bottom = ctk.CTkFrame(root, fg_color=p.bg, height=120, corner_radius=0)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        self._hud_mic = HudMicCluster(
            bottom, palette=p, on_mic=self._handle_mic,
            on_history=self._toggle_hud_history,
            on_settings=self._handle_open_settings,
            voice_available=self._voice_available,
        )
        self._hud_mic.pack(anchor="center", pady=16)

    def _toggle_hud_history(self) -> None:
        if self._hud_history is None or self._hud_mic is None:
            return
        if self._hud_history_open:
            self._hud_history.pack_forget()
            self._hud_history_open = False
        else:
            self._hud_history.pack(fill="x", side="bottom", before=self._hud_mic.master)
            self._hud_history_open = True

    # ----- Workspace ------------------------------------------------------------
    def _build_workspace(self) -> None:
        p = self.palette
        root = ctk.CTkFrame(self._shell, fg_color=p.bg, corner_radius=0)
        root.pack(fill="both", expand=True)
        self._mode_root = root

        WorkspaceSidebar(
            root, palette=p,
            on_chat=lambda: None, on_search=lambda: None,
            on_settings=self._handle_open_settings,
            on_toggle_mode=lambda: self.set_ui_mode("hud"),
        ).pack(side="left", fill="y")

        center = ctk.CTkFrame(root, fg_color=p.bg, corner_radius=0)
        center.pack(side="left", fill="both", expand=True)

        header = ctk.CTkFrame(center, fg_color=p.bg, height=52, corner_radius=0)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", padx=16, pady=10)
        ctk.CTkLabel(
            left, text="STEVE", text_color=p.text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_TITLE, weight="bold"),
        ).pack(side="left")
        self._connection_pill = ctk.CTkFrame(left, fg_color=p.tag_bg, corner_radius=12)
        self._connection_pill.pack(side="left", padx=(12, 0))
        self.connection_label = ctk.CTkLabel(
            self._connection_pill, text="● Verificando...", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self.connection_label.pack(side="left", padx=10, pady=3)

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", padx=12)
        ctk.CTkButton(
            right, text="HUD", width=72, height=32, fg_color="transparent",
            text_color=p.accent, hover_color=p.hover, border_width=1, border_color=p.border,
            corner_radius=14, font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
            command=lambda: self.set_ui_mode("hud"),
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            right, text="📊", width=36, height=32, fg_color="transparent",
            text_color=p.text_muted, hover_color=p.hover, command=self._handle_open_dashboard,
        ).pack(side="right")
        ctk.CTkButton(
            right, text="⚙", width=36, height=32, fg_color="transparent",
            text_color=p.text_muted, hover_color=p.hover, command=self._handle_open_settings,
        ).pack(side="right")

        orb_row = ctk.CTkFrame(center, fg_color=p.bg, height=ORB_HERO_SIZE + 56)
        orb_row.pack(fill="x", side="top")
        orb_row.pack_propagate(False)
        orb_wrap = ctk.CTkFrame(orb_row, fg_color="transparent")
        orb_wrap.pack(expand=True)
        self._remake_orb(orb_wrap, size=max(160, ORB_HERO_SIZE // 2))
        self._orb_host.pack(anchor="center")
        self._orb_state_chip = ctk.CTkFrame(orb_wrap, fg_color=p.tag_bg, corner_radius=14)
        self._orb_state_chip.pack(anchor="center", pady=(6, 0))
        self.orb_label = ctk.CTkLabel(
            self._orb_state_chip, text=STATE_LABELS[OrbState.IDLE], text_color=p.tag_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL, weight="bold"),
        )
        self.orb_label.pack(side="left", padx=(12, 6), pady=4)
        self.orb_status_detail = ctk.CTkLabel(
            self._orb_state_chip, text="Pronto para conversar", text_color=p.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self.orb_status_detail.pack(side="left", padx=(0, 12), pady=4)

        self._remake_chat(center)
        self._chat_host.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        self._remake_input(center, voice_only=False)
        self._input_host.pack(fill="x", padx=16, pady=(8, 8))
        self._remake_status(center)
        self.status_bar.pack(fill="x", side="bottom")
        ProjectPanel(root, palette=p).pack(side="right", fill="y")

    # ----- window chrome --------------------------------------------------------
    def hide(self) -> None:
        self.withdraw()

    def show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def set_voice_available(self, available: bool) -> None:
        self._voice_available = available
        self.input_bar.set_voice_available(available)
        if self._hud_mic is not None:
            self._hud_mic.set_voice_available(available)

    def apply_status(self, app_status: dict) -> None:
        self._app_status = dict(app_status or {})
        p = self.palette
        model = app_status.get("model", "—")
        online = app_status.get("ollama_online")
        mic_ok = app_status.get("mic_available")
        voice_ok = app_status.get("voice_available")

        if self._mode == "hud":
            if self._hud_corner_model is not None:
                self._hud_corner_model.configure(text=f"MODEL  {model}")
            if self._hud_corner_mic is not None:
                self._hud_corner_mic.configure(text="MIC  ON" if mic_ok else "MIC  OFF")
            return

        self.status_bar.set("model", model)
        if online:
            self.status_bar.set("ollama", "● Ollama", p.success)
            if self.connection_label is not None and self._connection_pill is not None:
                self.connection_label.configure(text="● Online", text_color=p.success)
                self._connection_pill.configure(fg_color=p.tag_bg)
        else:
            self.status_bar.set("ollama", "● Ollama", p.danger)
            if self.connection_label is not None and self._connection_pill is not None:
                self.connection_label.configure(text="● Offline", text_color=p.danger)
                self._connection_pill.configure(fg_color=p.surface_alt)
        self.status_bar.set(
            "mic", "● Microfone" if mic_ok else "○ Microfone",
            p.success if mic_ok else p.text_muted,
        )
        self.status_bar.set(
            "voice", "● Voz" if voice_ok else "○ Voz",
            p.success if voice_ok else p.text_muted,
        )
        self.status_bar.set("tools", f"🔧 {app_status.get('tools_count', 0)}")

    # ----- handlers -------------------------------------------------------------
    def _handle_send(self, text: str) -> None:
        if self.controller is None:
            return
        self.chat_view.add_user_message(text)
        self.input_bar.set_busy(True)
        started = self.controller.send_text(
            text, on_status=self._on_status, on_reply=self._on_reply,
            on_error=self._on_error, on_orb_state=self._on_orb_state,
        )
        if not started:
            self.input_bar.set_busy(False)

    def _handle_mic(self) -> None:
        if self._on_before_mic_cb:
            self._on_before_mic_cb()
        if self.controller is None:
            return
        self.input_bar.set_busy(True)
        if self._hud_mic is not None:
            self._hud_mic.set_busy(True)
        started = self.controller.send_voice(
            on_status=self._on_status, on_result=self._on_voice_result,
            on_error=self._on_error, on_orb_state=self._on_orb_state,
            on_mic_amplitude=self._on_mic_amplitude,
        )
        if not started:
            self.input_bar.set_busy(False)
            if self._hud_mic is not None:
                self._hud_mic.set_busy(False)

    def _handle_orb_click(self, _event=None) -> None:
        if self._voice_available and self.controller is not None and not getattr(
            self.controller, "busy", False
        ):
            self._handle_mic()

    def _handle_open_settings(self) -> None:
        if self._on_open_settings_cb:
            self._on_open_settings_cb()

    def _handle_open_dashboard(self) -> None:
        if self._on_open_dashboard_cb:
            self._on_open_dashboard_cb()

    def _handle_close(self) -> None:
        should_quit = self._on_close_cb() if self._on_close_cb else True
        if should_quit is False:
            return
        if self._hud_clock is not None:
            self._hud_clock.stop()
        self.orb.stop()
        self.destroy()

    def _handle_quit(self) -> None:
        if self._on_quit_cb:
            self._on_quit_cb()
        if self._hud_clock is not None:
            self._hud_clock.stop()
        self.orb.stop()
        self.destroy()

    def _on_status(self, text: str) -> None:
        if self._mode == "workspace":
            self.status_bar.set("activity", text)
        if self._hud_status is not None and self._mode == "hud":
            self._hud_status.configure(text=text)

    def _on_orb_state(self, state: OrbState) -> None:
        if not self._orb_enabled:
            return
        self.orb.set_state(state)
        if self.orb_label is not None:
            self.orb_label.configure(text=STATE_LABELS[state])
        details = {
            OrbState.IDLE: "Pronto para conversar",
            OrbState.LISTENING: "Ouvindo sua voz",
            OrbState.THINKING: "Processando sua mensagem",
            OrbState.TOOL_EXECUTION: "Executando uma ferramenta",
            OrbState.SPEAKING: "Gerando resposta por voz",
            OrbState.ERROR: "Ocorreu um erro",
        }
        if self.orb_status_detail is not None:
            self.orb_status_detail.configure(text=details.get(state, ""))
        self.input_bar.set_listening(state is OrbState.LISTENING)
        if self._hud_mic is not None:
            self._hud_mic.set_listening(state is OrbState.LISTENING)
        chip = {
            OrbState.IDLE: self.palette.tag_bg,
            OrbState.LISTENING: self.palette.selected,
            OrbState.THINKING: self.palette.selected,
            OrbState.TOOL_EXECUTION: self.palette.surface_alt,
            OrbState.SPEAKING: self.palette.selected,
            OrbState.ERROR: self.palette.surface_alt,
        }.get(state, self.palette.tag_bg)
        if self._orb_state_chip is not None:
            self._orb_state_chip.configure(fg_color=chip)
        if self._mode == "workspace":
            self.status_bar.set("activity", STATE_LABELS[state], self.palette.tag_text)
        if self._hud_status is not None and self._mode == "hud":
            self._hud_status.configure(text=details.get(state, ""))
        if state is OrbState.ERROR:
            self.after(ERROR_AUTO_RECOVER_MS, lambda: self._on_orb_state(OrbState.IDLE))

    def _on_mic_amplitude(self, raw_rms: float) -> None:
        if self._orb_enabled:
            self.orb.set_amplitude(normalized_rms(raw_rms))

    def _on_reply(self, reply: str) -> None:
        self.chat_view.add_steve_message(reply)
        self.status_bar.set_ready()
        self.input_bar.set_busy(False)
        if self._hud_mic is not None:
            self._hud_mic.set_busy(False)
        if self._on_reply_cb:
            self._on_reply_cb(reply)

    def _on_error(self, message: str) -> None:
        self.chat_view.add_system_message(message)
        self.status_bar.set_ready()
        self.input_bar.set_busy(False)
        if self._hud_mic is not None:
            self._hud_mic.set_busy(False)

        if self._on_after_voice_cb:
            try:
                self._on_after_voice_cb()
            except Exception:
                logger.debug("after_voice_cb failed", exc_info=True)

    def _on_voice_result(self, result) -> None:
        if result.transcript:
            self.chat_view.add_user_message(f"🎙️ {result.transcript}")
        if result.success:
            self.chat_view.add_steve_message(result.reply)
        else:
            self.chat_view.add_system_message(result.error)
        self.status_bar.set_ready()
        self.input_bar.set_busy(False)
        if self._hud_mic is not None:
            self._hud_mic.set_busy(False)
        if self._on_after_voice_cb:
            try:
                self._on_after_voice_cb()
            except Exception:
                logger.debug("after_voice_cb failed", exc_info=True)



    def _on_window_focus(self, focused: bool) -> None:
        try:
            if self.orb is not None:
                self.orb.set_window_focused(focused)
        except Exception:
            pass

    def _boot_borderless_fullscreen(self) -> None:
        """Open edge-to-edge without OS chrome, then play boot splash (no Orb changes)."""
        if getattr(self, "_boot_done", False):
            return
        self._boot_done = True
        try:
            # Always surface the window on boot (overrides start_minimized tray hide).
            try:
                self.deiconify()
            except Exception:
                pass
            self.update_idletasks()
            self._enter_borderless_fullscreen()
        except Exception:
            logger.exception("borderless fullscreen failed")
        try:
            BootSplashOverlay(
                self,
                palette=self.palette,
                on_done=None,
                duration_ms=2600,
            ).start()
        except Exception:
            logger.exception("boot splash failed")

    def _enter_borderless_fullscreen(self) -> None:
        self._borderless_fs = True
        try:
            self.deiconify()
        except Exception:
            pass
        self.update_idletasks()
        # Prefer OS fullscreen attribute first (covers DPI / multi-monitor better).
        try:
            self.attributes("-fullscreen", True)
        except Exception:
            logger.exception("fullscreen attr failed")
        sw = int(self.winfo_screenwidth())
        sh = int(self.winfo_screenheight())
        try:
            self.overrideredirect(True)
        except Exception:
            logger.exception("overrideredirect failed")
        try:
            self.state("zoomed")
        except Exception:
            pass
        try:
            self.geometry(f"{sw}x{sh}+0+0")
        except Exception:
            logger.exception("geometry fullscreen failed")
        # Stay on top for the whole splash (~3s), then drop.
        try:
            self.attributes("-topmost", True)
            self.after(3200, lambda: self.attributes("-topmost", False))
        except Exception:
            pass
        self.lift()
        try:
            self.focus_force()
        except Exception:
            pass
        self.update_idletasks()
        self.update()
        try:
            aw = int(self.winfo_width())
            ah = int(self.winfo_height())
        except Exception:
            aw, ah = sw, sh
        logger.info("borderless fullscreen %sx%s (actual %sx%s)", sw, sh, aw, ah)



def palette_for(mode: str):
    from ui.desktop.styles import palette_for as _pf
    return _pf(mode)
