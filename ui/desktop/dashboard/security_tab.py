"""SecurityTab: the Security Center's view inside the existing Dashboard window — not a
second application, not a second window (see security/engine.py's module docstring and
the V1.5 spec's explicit "não criar uma segunda aplicação"). Purely a view: like
OverviewTab/ProcessesTab, it never subscribes to the EventBus itself — DashboardWindow
owns every subscription and calls `apply_*`/`add_*` methods here, same pattern as the
other tabs. The one exception is triggering a scan (Quick/File/Folder), which needs
`security_engine`/`runner` directly, same as this window already needs `system_monitor`
for the idempotent `start()` call.

Monitoring/reviewing only: no button here can execute, move, delete, or quarantine
anything — see security/scanner.py and security/file_analysis.py.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk

from security.engine import SecurityEngine
from security.models import FindingStatus, ProtectionState, ScanKind, SecurityFinding, Severity
from ui.desktop.async_bridge import BackgroundRunner
from ui.desktop.dashboard.formatting import UNAVAILABLE_LABEL
from ui.desktop.styles import FONT_FAMILY, FONT_SIZE_BODY, FONT_SIZE_SMALL, Palette

_COLUMNS = ("severity", "title", "category", "status")
_HEADINGS = {"severity": "Severidade", "title": "Achado", "category": "Categoria", "status": "Estado"}
_MAX_DISPLAYED_FINDINGS = 100

_STATE_LABELS = {
    ProtectionState.PROTECTED: "PROTEGIDO",
    ProtectionState.MONITORING: "MONITORANDO",
    ProtectionState.DEGRADED: "DEGRADADO",
    ProtectionState.DISABLED: "DESATIVADO",
}
_STATUS_LABELS = {
    FindingStatus.DETECTED: "Detectado",
    FindingStatus.REVIEWED: "Revisado",
    FindingStatus.DISMISSED: "Dispensado",
}


class SecurityTab(ctk.CTkFrame):
    def __init__(self, master, palette: Palette, security_engine: SecurityEngine, runner: BackgroundRunner, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.palette = palette
        self._engine = security_engine
        self._runner = runner
        self._findings: list[SecurityFinding] = []

        self._build_header()
        self._build_scan_controls()
        self._build_findings_table()
        self._build_details_panel()

        self._style_treeview(palette)
        self.apply_state(ProtectionState.DISABLED)

    # --- layout ----------------------------------------------------------------
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=self.palette.surface, corner_radius=10)
        header.pack(fill="x", padx=8, pady=(8, 4))
        self._status_label = ctk.CTkLabel(
            header, text="● —", text_color=self.palette.text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_BODY, weight="bold"),
        )
        self._status_label.pack(side="left", padx=14, pady=10)
        self._summary_label = ctk.CTkLabel(
            header, text="0 achados", text_color=self.palette.text_muted,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self._summary_label.pack(side="left", padx=14)

    def _build_scan_controls(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        self._quick_scan_btn = ctk.CTkButton(row, text="Quick Scan", width=110, command=self._on_quick_scan)
        self._quick_scan_btn.pack(side="left", padx=(0, 6))
        self._scan_file_btn = ctk.CTkButton(row, text="Scan File", width=110, command=self._on_scan_file)
        self._scan_file_btn.pack(side="left", padx=6)
        self._scan_folder_btn = ctk.CTkButton(row, text="Scan Folder", width=110, command=self._on_scan_folder)
        self._scan_folder_btn.pack(side="left", padx=6)
        self._cancel_btn = ctk.CTkButton(
            row, text="Cancelar", width=90, fg_color=self.palette.danger, state="disabled", command=self._on_cancel
        )
        self._cancel_btn.pack(side="left", padx=6)

        self._progress_label = ctk.CTkLabel(
            self, text="", text_color=self.palette.text_muted, anchor="w",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self._progress_label.pack(fill="x", padx=14, pady=(0, 4))

    def _build_findings_table(self) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=8, pady=4)

        self.tree = ttk.Treeview(container, columns=_COLUMNS, show="headings", style="SteveDashboard.Treeview", height=8)
        for column in _COLUMNS:
            self.tree.heading(column, text=_HEADINGS[column])
            self.tree.column(column, anchor="w", stretch=(column == "title"), width=200 if column == "title" else 110)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_finding)

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

    def _build_details_panel(self) -> None:
        self._details_label = ctk.CTkLabel(
            self, text="Selecione um achado para ver detalhes.", text_color=self.palette.text_muted,
            justify="left", anchor="w", wraplength=560,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZE_SMALL),
        )
        self._details_label.pack(fill="x", padx=14, pady=(4, 10))

    @staticmethod
    def _style_treeview(palette: Palette) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
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

    # --- state applied by DashboardWindow (EventBus-driven) -------------------------
    def apply_state(self, state: ProtectionState) -> None:
        color = {
            ProtectionState.PROTECTED: self.palette.success,
            ProtectionState.MONITORING: self.palette.accent,
            ProtectionState.DEGRADED: self.palette.warning,
            ProtectionState.DISABLED: self.palette.text_muted,
        }[state]
        self._status_label.configure(text=f"● {_STATE_LABELS[state]}", text_color=color)

    def apply_findings(self, findings: list[SecurityFinding]) -> None:
        self._findings = list(findings)
        self._render_findings()

    def add_finding(self, finding: SecurityFinding) -> None:
        self._findings.append(finding)
        self._render_findings()

    def apply_scan_started(self, kind: str) -> None:
        self._quick_scan_btn.configure(state="disabled")
        self._scan_file_btn.configure(state="disabled")
        self._scan_folder_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._progress_label.configure(text=f"Scan ({kind}) iniciado...")

    def apply_scan_progress(self, scanned: int, total: int | None) -> None:
        total_text = str(total) if total is not None else "?"
        self._progress_label.configure(text=f"Analisando... {scanned}/{total_text}")

    def apply_scan_finished(self, text: str) -> None:
        self._quick_scan_btn.configure(state="normal")
        self._scan_file_btn.configure(state="normal")
        self._scan_folder_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        self._progress_label.configure(text=text)

    # --- rendering ------------------------------------------------------------------
    def _render_findings(self) -> None:
        counts: dict[str, int] = {}
        for finding in self._findings:
            counts[finding.severity.name] = counts.get(finding.severity.name, 0) + 1
        summary = ", ".join(f"{count} {name}" for name, count in counts.items()) or "nenhum achado"
        self._summary_label.configure(text=f"{len(self._findings)} achado(s) — {summary}")

        self.tree.delete(*self.tree.get_children())
        for finding in list(reversed(self._findings))[:_MAX_DISPLAYED_FINDINGS]:
            self.tree.insert(
                "", "end", iid=finding.id,
                values=(finding.severity.name, finding.title, finding.category.value, _STATUS_LABELS[finding.status]),
            )

    def _on_select_finding(self, _event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        finding_id = selection[0]
        finding = next((f for f in self._findings if f.id == finding_id), None)
        if finding is None:
            return
        evidence_text = "\n".join(f"• {reason}" for reason in finding.evidence) or "Nenhuma evidência registrada."
        process_text = f"Processo: {finding.process_name} (PID {finding.pid})\n" if finding.process_name else ""
        path_text = f"Caminho: {finding.executable_path}\n" if finding.executable_path else ""
        self._details_label.configure(
            text=(
                f"{finding.title}\n"
                f"Severidade: {finding.severity.name} — Confiança: {finding.confidence:.0%}\n"
                f"{process_text}{path_text}"
                f"Motivos:\n{evidence_text}"
            )
        )

    # --- scan triggers (needs security_engine/runner directly, not just events) -----
    def _on_quick_scan(self) -> None:
        self._start_scan(ScanKind.QUICK.value, self._engine.quick_scan)

    def _on_scan_file(self) -> None:
        path = filedialog.askopenfilename(title="Selecionar arquivo para análise")
        if not path:
            return
        self._start_scan(ScanKind.FILE.value, lambda: self._engine.scanner.scan_file_path(path))

    def _on_scan_folder(self) -> None:
        folder = filedialog.askdirectory(title="Selecionar pasta para análise")
        if not folder:
            return
        self._start_scan(ScanKind.FOLDER.value, lambda: self._engine.scanner.scan_folder_path(folder))

    def _start_scan(self, kind: str, scan_fn) -> None:
        if self._engine.scanner.is_scanning:
            return
        self.apply_scan_started(kind)
        self._runner.run(scan_fn, on_done=self._on_scan_call_done, on_error=self._on_scan_call_error)

    def _on_scan_call_done(self, _summary) -> None:
        """Most UI updates for a completed scan arrive via SECURITY_SCAN_COMPLETED on
        the EventBus (see DashboardWindow) — this only guards against the scan call
        itself never having published anything (shouldn't happen, but a stuck button
        state would be a worse failure mode than a redundant re-enable)."""
        if not self.winfo_exists():
            return
        if self._cancel_btn.cget("state") == "normal":
            self.apply_scan_finished("Scan concluído.")

    def _on_scan_call_error(self, _exc) -> None:
        if not self.winfo_exists():
            return
        self.apply_scan_finished("Falha ao executar o scan.")

    def _on_cancel(self) -> None:
        self._engine.scanner.cancel()
