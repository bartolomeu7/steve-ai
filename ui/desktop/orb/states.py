"""OrbState: the small set of real Steve states the orb ever visualizes. The orb never
invents a state of its own — every transition here mirrors something the Orchestrator,
ToolManager, VoiceService or AIProvider is actually doing (see ui/desktop/controller.py
for how each state gets triggered)."""
from __future__ import annotations

from enum import Enum


class OrbState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    TOOL_EXECUTION = "tool_execution"
    SPEAKING = "speaking"
    ERROR = "error"


STATE_LABELS: dict[OrbState, str] = {
    OrbState.IDLE: "●",
    OrbState.LISTENING: "● Ouvindo",
    OrbState.THINKING: "● Pensando",
    OrbState.TOOL_EXECUTION: "● Executando",
    OrbState.SPEAKING: "● Falando",
    OrbState.ERROR: "● Erro",
}
