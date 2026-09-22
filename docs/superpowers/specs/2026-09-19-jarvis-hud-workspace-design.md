# Steve dual-mode UI — Jarvis HUD + Workspace

**Date:** 2026-09-19 (America/Sao_Paulo)  
**Status:** Implemented (package + apply script)  
**Theme:** `cyber` / `DARK_CYBER` (navy `#05080F` + cyan `#00E5FF`)

## Goals

Two runtime UI modes, toggleable without destroying the Orb or voice pipeline:

| Mode | Default | Layout |
|------|---------|--------|
| **HUD** | yes (`ui_mode=hud`) | Full-bleed navy Jarvis: STEVE title (TL), clock (TR), hero Orb center, big mic cluster (BC), history/settings side icons, faint corner status |
| **Workspace** | opt-in | 3-column: icon sidebar \| chat + text input + compact orb \| Projeto Atual panel |

## Persistence

- Field: `ui_mode: "hud" | "workspace"` in `data/config.json` and Settings dataclass
- Toggle: header button (“Workspace” / “HUD”) or **Ctrl+Shift+U**
- Callback `on_ui_mode_change(mode)` writes config from app layer

## Palette

`DARK_CYBER` / alias `JARVIS`:

- bg `#05080F`, surface `#0A0E18`, accent `#00E5FF`
- Mono clock font: `Consolas` (`FONT_MONO`), `FONT_SIZE_CLOCK=28`

## Modules

| File | Role |
|------|------|
| `ui/desktop/hud_chrome.py` | `HudClock`, `HudCornerLabel`, `HudMicCluster` |
| `ui/desktop/workspace_sidebar.py` | Left icon rail |
| `ui/desktop/project_panel.py` | Right “Projeto Atual” (placeholders OK) |
| `ui/desktop/controls.py` | `InputBar.set_voice_only` — HUD hides text entry |
| `ui/desktop/window.py` | `MainWindow.set_ui_mode` / single Orb re-pack |
| `ui/desktop/styles.py` | Navy cyber + mono fonts |

## Non-goals

- No LangChain / multi-agent frameworks
- Do not break EventBus / LearningRouter / AlwaysListen / Orb
- Status pills stay faint in HUD corners only

## Refs

`docs/superpowers/refs/steve-hud-ref-a.png`, `steve-hud-ref-b.png`

## Manual check

1. Restart `Steve.exe` (or `python -m ui.desktop.app`)
2. Confirm HUD: cyan clock, large Orb, mic cluster
3. Press **Ctrl+Shift+U** → Workspace columns appear; text input visible
4. Toggle back; Orb still responds to mic / wake
5. Confirm `data/config.json` has `"ui_mode": "..."` after toggle
