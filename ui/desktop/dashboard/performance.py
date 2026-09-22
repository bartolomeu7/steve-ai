"""PerformanceTab: CPU/GPU/RAM/Network history graphs fed straight from
SystemMonitorService's bounded deques (system_monitor/service.py) — this tab never
recomputes or stores history itself, it only renders whatever the service already
tracked, same "one collector, many viewers" principle as OverviewTab."""
from __future__ import annotations

from collections.abc import Sequence

import customtkinter as ctk

from ui.desktop.dashboard.graph import LineGraph
from ui.desktop.styles import Palette

_NARROW_WIDTH = 500


class PerformanceTab(ctk.CTkFrame):
    def __init__(self, master, palette: Palette, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.palette = palette

        self.cpu_graph = LineGraph(self, palette, "CPU (%)", value_max=100.0)
        self.gpu_graph = LineGraph(self, palette, "GPU (%)", value_max=100.0)
        self.ram_graph = LineGraph(self, palette, "RAM (%)", value_max=100.0)
        self.network_graph = LineGraph(self, palette, "Rede (download, B/s)", value_max=None)
        self._graphs = (self.cpu_graph, self.gpu_graph, self.ram_graph, self.network_graph)

        self._current_columns = -1
        self.bind("<Configure>", self._on_resize)
        self._relayout(2)

    def _on_resize(self, event) -> None:
        columns = 1 if event.width < _NARROW_WIDTH else 2
        if columns != self._current_columns:
            self._relayout(columns)

    def _relayout(self, columns: int) -> None:
        self._current_columns = columns
        for index, graph in enumerate(self._graphs):
            row, col = divmod(index, columns)
            graph.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
        for col in range(columns):
            self.grid_columnconfigure(col, weight=1)
        for row in range((len(self._graphs) + columns - 1) // columns):
            self.grid_rowconfigure(row, weight=1)

    def update_history(
        self,
        cpu_history: Sequence[float | None],
        gpu_history: Sequence[float | None],
        ram_history: Sequence[float],
        network_download_history: Sequence[float | None],
    ) -> None:
        self.cpu_graph.update_history(cpu_history)
        self.gpu_graph.update_history(gpu_history)
        self.ram_graph.update_history(ram_history)
        self.network_graph.update_history(network_download_history)
