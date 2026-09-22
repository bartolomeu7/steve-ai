"""OverviewTab: CPU/GPU/RAM/Disk/Network cards fed by real SystemSnapshot data.

Responsive by re-gridding a fixed set of cards into 1/2/3 columns as the tab is resized
(never recreating the card widgets themselves), and by updating existing labels'
`.configure(text=...)` in place on every snapshot instead of rebuilding the UI — a
snapshot arrives roughly once a second, so rebuilding widgets on every update would be
wasteful and would fight the resize/re-grid logic.
"""
from __future__ import annotations

import customtkinter as ctk

from system_monitor.models import SystemSnapshot
from ui.desktop.dashboard.formatting import UNAVAILABLE_LABEL, format_bytes, format_celsius, format_mhz, format_percent
from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_BODY, FONT_SIZE_SMALL, Palette

_CARD_ORDER = ("cpu", "gpu", "memory", "disk", "network")
_CARD_TITLES = {"cpu": "CPU", "gpu": "GPU", "memory": "RAM", "disk": "DISCO", "network": "REDE"}

_NARROW_WIDTH = 420
_MEDIUM_WIDTH = 760


class OverviewTab(ctk.CTkScrollableFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.palette = palette
        self._bodies: dict[str, ctk.CTkLabel] = {}
        self._frames: dict[str, ctk.CTkFrame] = {}
        self._current_columns = -1

        for key in _CARD_ORDER:
            frame, body = self._build_card(_CARD_TITLES[key])
            self._frames[key] = frame
            self._bodies[key] = body

        self.bind("<Configure>", self._on_resize)
        self._relayout(2)

    def _build_card(self, title: str) -> tuple[ctk.CTkFrame, ctk.CTkLabel]:
        frame = ctk.CTkFrame(self, fg_color=self.palette.surface, corner_radius=10)
        ctk.CTkLabel(
            frame, text=title, text_color=self.palette.text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(10, 4))
        body = ctk.CTkLabel(
            frame, text="—", text_color=self.palette.text_muted, justify="left", anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        body.pack(anchor="w", padx=14, pady=(0, 12), fill="x")
        return frame, body

    def _on_resize(self, event) -> None:
        columns = 1 if event.width < _NARROW_WIDTH else (2 if event.width < _MEDIUM_WIDTH else 3)
        if columns != self._current_columns:
            self._relayout(columns)

    def _relayout(self, columns: int) -> None:
        self._current_columns = columns
        for index, key in enumerate(_CARD_ORDER):
            row, col = divmod(index, columns)
            self._frames[key].grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
        for col in range(columns):
            self.grid_columnconfigure(col, weight=1)

    def update_snapshot(self, snapshot: SystemSnapshot) -> None:
        cpu = snapshot.cpu
        self._bodies["cpu"].configure(
            text=(
                f"Uso: {format_percent(cpu.percent)}\n"
                f"Frequência: {format_mhz(cpu.frequency_mhz)} (máx: {format_mhz(cpu.frequency_max_mhz)})\n"
                f"Temperatura: {format_celsius(cpu.temperature_celsius)}\n"
                f"Núcleos: {cpu.core_count_physical or UNAVAILABLE_LABEL} físicos / {cpu.core_count_logical or UNAVAILABLE_LABEL} lógicos"
            )
        )

        gpu = snapshot.gpu
        if not gpu.available:
            self._bodies["gpu"].configure(text=gpu.unavailable_reason or UNAVAILABLE_LABEL)
        else:
            names = ", ".join(a.name for a in gpu.adapters) or UNAVAILABLE_LABEL
            self._bodies["gpu"].configure(
                text=(
                    f"Adaptador: {names}\n"
                    f"Uso: {format_percent(gpu.utilization_percent)}\n"
                    f"Temperatura: {format_celsius(gpu.temperature_celsius)}\n"
                    f"VRAM: {format_bytes(gpu.memory_used_bytes)} / {format_bytes(gpu.memory_total_bytes)}"
                )
            )

        memory = snapshot.memory
        self._bodies["memory"].configure(
            text=(
                f"Uso: {format_percent(memory.percent)}\n"
                f"Usada: {format_bytes(memory.used_bytes)}\n"
                f"Disponível: {format_bytes(memory.available_bytes)}\n"
                f"Total: {format_bytes(memory.total_bytes)}"
            )
        )

        disk = snapshot.disk
        self._bodies["disk"].configure(
            text=(
                f"Unidade: {disk.path}\n"
                f"Uso: {format_percent(disk.percent)}\n"
                f"Livre: {format_bytes(disk.free_bytes)} / {format_bytes(disk.total_bytes)}\n"
                f"Leitura/Escrita: {format_bytes(disk.read_bytes_per_sec, per_second=True)} / "
                f"{format_bytes(disk.write_bytes_per_sec, per_second=True)}"
            )
        )

        network = snapshot.network
        self._bodies["network"].configure(
            text=(
                f"Download: {format_bytes(network.download_bytes_per_sec, per_second=True)}\n"
                f"Upload: {format_bytes(network.upload_bytes_per_sec, per_second=True)}\n"
                f"Total recebido: {format_bytes(network.bytes_recv_total)}\n"
                f"Total enviado: {format_bytes(network.bytes_sent_total)}"
            )
        )
