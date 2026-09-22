"""ProcessesTab: a sortable table of real running processes. Monitoring only — no
kill/suspend/delete actions exist here or anywhere else in this version (see
docs/ARCHITECTURE.md); this tab is read-only by construction, it doesn't even know how
to act on a selected row.

Uses ttk.Treeview (stdlib, no new dependency) since CustomTkinter has no table widget of
its own — styled to approximate the active palette rather than left in ttk's default
look."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from system_monitor.models import ProcessSnapshot
from ui.desktop.dashboard.formatting import format_bytes, format_percent
from ui.desktop.styles import FONT_FAMILY, Palette

_COLUMNS = ("name", "pid", "cpu", "memory", "status")
_HEADINGS = {"name": "Processo", "pid": "PID", "cpu": "CPU", "memory": "RAM", "status": "Status"}
_MAX_DISPLAYED_ROWS = 60


class ProcessesTab(ctk.CTkFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.palette = palette
        self._sort_column = "cpu"
        self._sort_reverse = True
        self._latest_snapshot: ProcessSnapshot | None = None

        self._style_treeview(palette)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(container, columns=_COLUMNS, show="headings", style="SteveDashboard.Treeview")
        for column in _COLUMNS:
            self.tree.heading(column, text=_HEADINGS[column], command=lambda c=column: self._on_heading_click(c))
            self.tree.column(column, anchor="w" if column == "name" else "center", stretch=(column == "name"), width=140 if column == "name" else 80)

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

    @staticmethod
    def _style_treeview(palette: Palette) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")  # the only built-in ttk theme that reliably honors custom colors on Windows
        except tk.TclError:
            pass
        style.configure(
            "SteveDashboard.Treeview",
            background=palette.surface, foreground=palette.text, fieldbackground=palette.surface,
            bordercolor=palette.border, borderwidth=0, rowheight=24, font=(FONT_FAMILY, 11),
        )
        style.configure(
            "SteveDashboard.Treeview.Heading",
            background=palette.surface_alt, foreground=palette.text, relief="flat", font=(FONT_FAMILY, 11, "bold"),
        )
        style.map("SteveDashboard.Treeview", background=[("selected", palette.accent)], foreground=[("selected", palette.accent_text)])

    def _on_heading_click(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = column in ("cpu", "memory")
        self._render()

    def update_processes(self, snapshot: ProcessSnapshot) -> None:
        self._latest_snapshot = snapshot
        self._render()

    def _render(self) -> None:
        if self._latest_snapshot is None:
            return
        processes = list(self._latest_snapshot.processes)

        key_fns = {
            "name": lambda p: (p.name or "").lower(),
            "pid": lambda p: p.pid,
            "cpu": lambda p: p.cpu_percent if p.cpu_percent is not None else -1.0,
            "memory": lambda p: p.memory_percent if p.memory_percent is not None else -1.0,
            "status": lambda p: p.status or "",
        }
        processes.sort(key=key_fns[self._sort_column], reverse=self._sort_reverse)

        self.tree.delete(*self.tree.get_children())
        for process in processes[:_MAX_DISPLAYED_ROWS]:
            self.tree.insert(
                "", "end",
                values=(
                    process.name or "—",
                    process.pid,
                    format_percent(process.cpu_percent),
                    format_bytes(process.memory_bytes),
                    process.status or "—",
                ),
            )
