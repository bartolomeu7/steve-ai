"""Central configuration for Steve: paths, persisted settings and first-run setup."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
CONFIG_FILE = DATA_DIR / "config.json"
DATABASE_FILE = DATA_DIR / "steve.db"
AUDIT_LOG_FILE = LOGS_DIR / "audit.log"
APP_LOG_FILE = LOGS_DIR / "steve.log"


class ProactivityLevel(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class PermissionAutoApprove(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AIRoutingMode(str, Enum):
    """Persisted mirror of ai.router.RoutingMode — kept as its own type here (rather than
    importing ai.router) so settings/ stays a leaf module with no dependency on core/ai/
    tools, same as every other enum in this file. main.py converts between the two at
    the wiring boundary."""

    AUTO = "auto"
    MANUAL = "manual"


class SettingsValidationError(ValueError):
    """Raised by Settings.validate() for a value that must never be silently accepted —
    e.g. an empty model name, or a routing mode that isn't 'auto'/'manual'. This is the
    fix for a real incident: a bad model name ('SIM') once got saved into config.json and
    only surfaced as a confusing Ollama 404 much later. Structurally-invalid values must
    be rejected where they're set, not accepted and left to fail somewhere downstream."""


@dataclass
class Settings:
    user_name: str = "Usuário"
    language: str = "pt-BR"
    proactivity: ProactivityLevel = ProactivityLevel.NORMAL
    ai_model: str = "llama3.2"
    ai_provider: str = "ollama"
    ai_routing_mode: AIRoutingMode = AIRoutingMode.AUTO
    ollama_host: str = "http://localhost:11434"
    silent_mode: bool = False
    voice_output_enabled: bool = False
    voice_input_enabled: bool = False
    voice_input_device: str | None = None
    voice_output_device: str | None = None
    stt_engine: str = "faster-whisper"
    stt_model_size: str = "small"
    tts_engine: str = "auto"  # "auto"/"edge" (EdgeTTS neural voice, falls back to pyttsx3 if unavailable) | "pyttsx3" (force offline)
    tts_voice: str | None = None
    speaking_rate: int = 175
    speaking_volume: float = 1.0
    push_to_talk_key: str = "v"
    wake_word_enabled: bool = True
    wake_word: str = "steve"
    wake_ack_phrase: str = "Estou te ouvindo."
    theme: str = "light"  # "light" | "dark" | "cyber"/"thomas" — applied to the GUI at startup
    ui_mode: str = "hud"  # "hud" | "workspace"
    tools_preset: str = "completo"  # "essencial" | "completo"
    #: Speaks the model's reply sentence-by-sentence as it's generated instead of
    #: waiting for the full response — lower time-to-first-audio on voice turns. Default
    #: off: correctness depends on the (usually true, but not guaranteed for every
    #: model) assumption that a turn never mixes spoken prose with a tool call — see
    #: core/orchestrator.py::Orchestrator.handle_message_stream and
    #: docs/ARCHITECTURE.md. Opt in once you've tried a few voice turns that trigger
    #: tools and confirmed nothing odd gets spoken.
    voice_streaming_enabled: bool = False
    autostart_enabled: bool = False
    start_minimized: bool = False  # GUI opens hidden (tray-style) instead of visible — only meaningful when minimize_to_tray is also True, otherwise there'd be no way back
    minimize_to_tray: bool = False  # closing the window hides it instead of quitting; explicit "Sair" still quits
    system_monitor_interval_seconds: float = 1.0  # how often the System Dashboard's background collector samples CPU/GPU/RAM/disk/network
    ai_timeout_seconds: float = 60.0  # timeout para chamadas ao provedor de IA (segundos)
    permission_auto_approve_level: PermissionAutoApprove = PermissionAutoApprove.LOW
    first_run_completed: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Rejects structurally-invalid values immediately instead of persisting them —
        see SettingsValidationError. This is a syntactic check only (non-empty, a known
        routing mode); whether a model actually exists in Ollama is a separate, existing
        check done at runtime by OllamaProvider.is_available()/AIRouter — Settings has no
        way to know that at load/construction time."""
        if not self.ai_model or not self.ai_model.strip():
            raise SettingsValidationError("ai_model não pode ser vazio.")
        if not self.ai_provider or not self.ai_provider.strip():
            raise SettingsValidationError("ai_provider não pode ser vazio.")
        if not isinstance(self.ai_routing_mode, AIRoutingMode):
            try:
                self.ai_routing_mode = AIRoutingMode(self.ai_routing_mode)
            except ValueError as exc:
                raise SettingsValidationError(
                    f"ai_routing_mode inválido: {self.ai_routing_mode!r}. Use 'auto' ou 'manual'."
                ) from exc
        if not (0.1 <= self.system_monitor_interval_seconds <= 60.0):
            raise SettingsValidationError(
                f"system_monitor_interval_seconds inválido: {self.system_monitor_interval_seconds!r}. "
                "Use um valor entre 0.1 e 60 segundos."
            )
        if not (1.0 <= self.ai_timeout_seconds <= 300.0):
            raise SettingsValidationError(
                f"ai_timeout_seconds inválido: {self.ai_timeout_seconds!r}. "
                "Use um valor entre 1 e 300 segundos."
            )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["proactivity"] = self.proactivity.value
        data["permission_auto_approve_level"] = self.permission_auto_approve_level.value
        data["ai_routing_mode"] = self.ai_routing_mode.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        if "proactivity" in filtered:
            filtered["proactivity"] = ProactivityLevel(filtered["proactivity"])
        if "permission_auto_approve_level" in filtered:
            filtered["permission_auto_approve_level"] = PermissionAutoApprove(
                filtered["permission_auto_approve_level"]
            )
        if "ai_routing_mode" in filtered:
            filtered["ai_routing_mode"] = AIRoutingMode(filtered["ai_routing_mode"])
        return cls(**filtered)


class ConfigManager:
    """Loads, persists and bootstraps Steve's settings on disk."""

    def __init__(self, config_file: Path | None = None, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self.config_file = config_file or (self.data_dir / "config.json")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def is_first_run(self) -> bool:
        if not self.config_file.exists():
            return True
        try:
            settings = self.load()
        except (json.JSONDecodeError, OSError, ValueError):
            # ValueError also covers a corrupt/invalid persisted value (a bad enum, or
            # now SettingsValidationError from Settings.validate()) — treat it the same
            # as an unreadable file: re-run setup instead of crashing the whole app.
            return True
        return not settings.first_run_completed

    def load(self) -> Settings:
        raw = json.loads(self.config_file.read_text(encoding="utf-8"))
        return Settings.from_dict(raw)

    def save(self, settings: Settings) -> None:
        settings.updated_at = datetime.now(timezone.utc).isoformat()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(
            json.dumps(settings.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def run_first_run_wizard(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> Settings:
        output_fn("Bem-vindo. Vamos configurar seu assistente.")

        name = input_fn("Como você gostaria de ser chamado? ").strip() or "Usuário"

        lang = input_fn("Idioma preferido [pt-BR]: ").strip() or "pt-BR"

        proactivity_raw = (
            input_fn("Nível de proatividade (LOW/NORMAL/HIGH) [NORMAL]: ").strip().upper()
            or "NORMAL"
        )
        try:
            proactivity = ProactivityLevel(proactivity_raw)
        except ValueError:
            output_fn(f"Valor '{proactivity_raw}' inválido, usando NORMAL.")
            proactivity = ProactivityLevel.NORMAL

        model = input_fn("Modelo Ollama a utilizar [llama3.2]: ").strip() or "llama3.2"

        settings = Settings(
            user_name=name,
            language=lang,
            proactivity=proactivity,
            ai_model=model,
            first_run_completed=True,
        )
        self.save(settings)
        output_fn("Configuração inicial concluída e salva.")
        return settings

    def load_or_run_wizard(
        self,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> Settings:
        if self.is_first_run():
            return self.run_first_run_wizard(input_fn=input_fn, output_fn=output_fn)
        return self.load()
