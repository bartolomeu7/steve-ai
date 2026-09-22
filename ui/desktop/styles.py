"""Color palettes and fonts for the desktop UI. No widget/business logic here."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    # --- core surfaces ---
    bg: str
    surface: str
    surface_alt: str
    border: str
    # --- text ---
    text: str
    text_muted: str
    # --- accent ---
    accent: str
    accent_text: str
    # --- chat bubbles ---
    bubble_user: str
    bubble_user_text: str
    bubble_steve: str
    bubble_steve_text: str
    bubble_system: str
    bubble_system_text: str
    # --- semantic ---
    success: str
    warning: str
    danger: str
    # --- modern additions ---
    glow: str
    glow_success: str
    glow_danger: str
    header_bg: str
    input_bg: str
    card_bg: str
    card_border: str
    orb_bg: str
    divider: str
    shadow: str
    hover: str
    selected: str
    tag_bg: str
    tag_text: str


LIGHT = Palette(
    bg="#F4F5F7",
    surface="#FFFFFF",
    surface_alt="#ECEEF1",
    border="#E8EAEE",
    text="#1B1F24",
    text_muted="#6B7280",
    accent="#2F6FED",
    accent_text="#FFFFFF",
    bubble_user="#2F6FED",
    bubble_user_text="#FFFFFF",
    bubble_steve="#FFFFFF",
    bubble_steve_text="#1B1F24",
    bubble_system="#ECEEF1",
    bubble_system_text="#6B7280",
    success="#1F9254",
    warning="#B7791F",
    danger="#D64545",
    glow="#2F6FED",
    glow_success="#1F9254",
    glow_danger="#D64545",
    header_bg="#FFFFFF",
    input_bg="#F4F5F7",
    card_bg="#FFFFFF",
    card_border="#EEF0F3",
    orb_bg="#F4F5F7",
    divider="#EEF0F3",
    shadow="#00000008",
    hover="#F0F2F5",
    selected="#E8F0FE",
    tag_bg="#E8F0FE",
    tag_text="#2F6FED",
)

DARK = Palette(
    bg="#15171C",
    surface="#1E2128",
    surface_alt="#262A32",
    border="#2A2F3A",
    text="#E9EBEF",
    text_muted="#9BA1AC",
    accent="#5B8DEF",
    accent_text="#0B0D10",
    bubble_user="#5B8DEF",
    bubble_user_text="#0B0D10",
    bubble_steve="#262A32",
    bubble_steve_text="#E9EBEF",
    bubble_system="#1E2128",
    bubble_system_text="#9BA1AC",
    success="#3FCB7C",
    warning="#E0B23D",
    danger="#F06868",
    glow="#5B8DEF",
    glow_success="#3FCB7C",
    glow_danger="#F06868",
    header_bg="#15171C",
    input_bg="#15171C",
    card_bg="#1E2128",
    card_border="#262A32",
    orb_bg="#15171C",
    divider="#262A32",
    shadow="#00000040",
    hover="#262A32",
    selected="#1E2A3A",
    tag_bg="#1E2A3A",
    tag_text="#5B8DEF",
)

DARK_CYBER = Palette(
    bg="#05080F",
    surface="#0A0E18",
    surface_alt="#0F1522",
    border="#1A2A3A",
    text="#E8EEF7",
    text_muted="#7A8499",
    accent="#00E5FF",
    accent_text="#001018",
    bubble_user="#00E5FF",
    bubble_user_text="#001018",
    bubble_steve="#121821",
    bubble_steve_text="#E8EEF7",
    bubble_system="#0F131A",
    bubble_system_text="#7A8499",
    success="#00F5A0",
    warning="#FFB020",
    danger="#FF4D6A",
    glow="#00E5FF",
    glow_success="#00F5A0",
    glow_danger="#FF4D6A",
    header_bg="#05080F",
    input_bg="#05080F",
    card_bg="#0F131A",
    card_border="#121821",
    orb_bg="#05080F",
    divider="#121821",
    shadow="#00E5FF18",
    hover="#121821",
    selected="#0F1A28",
    tag_bg="#0F1A28",
    tag_text="#00E5FF",
)

JARVIS = DARK_CYBER  # alias for navy+cyan HUD

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"
FONT_SIZE_TITLE = 20
FONT_SIZE_SUBTITLE = 15
FONT_SIZE_BODY = 14
FONT_SIZE_SMALL = 12
FONT_SIZE_TINY = 10
FONT_SIZE_CLOCK = 28


def palette_for(mode: str) -> Palette:
    """"cyber"/"thomas" select the same dark cyan-neon palette (an alias, not two
    different themes) — "thomas" is kept only because that's the name the user knows
    the look by."""
    m = mode.lower().strip()
    if m in ("cyber", "thomas", "dark_cyber", "jarvis"):
        return DARK_CYBER
    return DARK if m == "dark" else LIGHT
