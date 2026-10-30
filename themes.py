"""Built-in colour themes and accent colours.

A theme supplies the surface/text colours; an accent supplies the highlight
colour. ``build_palette`` merges them into the colour names the interface
already uses, so any theme can be combined with any accent.
"""

from __future__ import annotations

DEFAULT_THEME = "Ion Midnight"
DEFAULT_ACCENT = "Cyan"

# Surface colours per theme. "light" themes use the deeper accent shades.
THEMES: dict[str, dict] = {
    "Ion Midnight": {
        "light": False,
        "Background": "#05070D", "Panel": "#0E1628", "Control": "#16264A", "Header": "#060914",
        "Text": "#DFEAFF", "Muted text": "#6F87B3",
        "Success": "#2FBF71", "Danger": "#E5484D", "Info": "#2A7FD4",
    },
    "Ion Carbon": {
        "light": False,
        "Background": "#0A0A0C", "Panel": "#141417", "Control": "#212126", "Header": "#070709",
        "Text": "#ECECF0", "Muted text": "#8A8A96",
        "Success": "#2FBF71", "Danger": "#E5484D", "Info": "#2A7FD4",
    },
    "Ion Deep Sea": {
        "light": False,
        "Background": "#04100F", "Panel": "#0B1E1D", "Control": "#14302E", "Header": "#030C0B",
        "Text": "#D8F5F0", "Muted text": "#6FA39B",
        "Success": "#2FBF71", "Danger": "#E5484D", "Info": "#2A8FB4",
    },
    "Graphite": {
        "light": False,
        "Background": "#14161A", "Panel": "#191C21", "Control": "#262A32", "Header": "#101216",
        "Text": "#ECEEF2", "Muted text": "#8D94A3",
        "Success": "#3DB76B", "Danger": "#D9534F", "Info": "#3A8DDE",
    },
    "Paper": {
        "light": True,
        "Background": "#F4F3EE", "Panel": "#FBFAF6", "Control": "#E3E1D6", "Header": "#FFFFFF",
        "Text": "#15161A", "Muted text": "#6D6E72",
        "Success": "#14733F", "Danger": "#B3261E", "Info": "#1F5FBF",
    },
}

# name -> (accent, accent hover) for dark themes and for the light theme
ACCENTS: dict[str, dict[str, tuple[str, str]]] = {
    # Light shades keep 4.5:1 contrast on every Paper surface (checked by tests/test_ui_theme.py).
    "Cyan":    {"dark": ("#3DE1FF", "#7AEBFF"), "light": ("#086A8A", "#0C7FA3")},
    "Violet":  {"dark": ("#A98BFF", "#C4B0FF"), "light": ("#6A3FD9", "#7F57E6")},
    "Emerald": {"dark": ("#34E0A1", "#6AEBBF"), "light": ("#0C704D", "#10865D")},
    "Amber":   {"dark": ("#FFB547", "#FFCB7A"), "light": ("#8D5500", "#A16309")},
    "Rose":    {"dark": ("#FF6B9A", "#FF94B6"), "light": ("#B22E5A", "#BE446C")},
}


def theme_names() -> list[str]:
    return list(THEMES)


def accent_names() -> list[str]:
    return list(ACCENTS)


def accent_swatch(name: str, theme: str) -> str:
    """Display colour of an accent as it appears in the given theme."""
    mode = "light" if THEMES.get(theme, THEMES[DEFAULT_THEME])["light"] else "dark"
    return ACCENTS.get(name, ACCENTS[DEFAULT_ACCENT])[mode][0]


def build_palette(theme: str = DEFAULT_THEME, accent: str = DEFAULT_ACCENT) -> dict[str, str]:
    base = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    mode = "light" if base["light"] else "dark"
    main, hover = (ACCENTS.get(accent) or ACCENTS[DEFAULT_ACCENT])[mode]
    palette = {key: value for key, value in base.items() if key != "light"}
    palette["Accent"] = main
    palette["Accent hover"] = hover
    return palette


def is_light(theme: str) -> bool:
    return bool((THEMES.get(theme) or {}).get("light", False))
