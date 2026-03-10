"""
Theme engine – generates Qt stylesheets from a theme dictionary
and provides helper utilities.
"""
from typing import Optional


# Default panda-themed dark palette
DEFAULT_THEME = {
    "name": "Panda Dark",
    "background": "#1a1a2e",
    "surface": "#16213e",
    "primary": "#0f3460",
    "accent": "#e94560",
    "text": "#eaeaea",
    "text_secondary": "#a0a0b0",
    "border": "#2a2a4a",
    "success": "#4caf50",
    "warning": "#ff9800",
    "error": "#f44336",
    "tab_selected": "#e94560",
    "button_bg": "#0f3460",
    "button_hover": "#e94560",
    "panda_white": "#f0f0f0",
    "panda_black": "#1a1a1a",
    "progress_bar": "#e94560",
    "input_bg": "#0d1b3e",
    "scrollbar": "#2a2a4a",
    "scrollbar_handle": "#e94560",
    "_effect": "panda",
    "_cursor": "Pointing Hand",
    "_trail_color": "#e94560",
}

LIGHT_THEME = {
    "name": "Panda Light",
    "background": "#f5f5f5",
    "surface": "#ffffff",
    "primary": "#3d5a80",
    "accent": "#e94560",
    "text": "#1a1a2e",
    "text_secondary": "#555577",
    "border": "#c8c8d8",
    "success": "#2e7d32",
    "warning": "#e65100",
    "error": "#c62828",
    "tab_selected": "#e94560",
    "button_bg": "#3d5a80",
    "button_hover": "#e94560",
    "panda_white": "#ffffff",
    "panda_black": "#1a1a1a",
    "progress_bar": "#e94560",
    "input_bg": "#e8eaf6",
    "scrollbar": "#c8c8d8",
    "scrollbar_handle": "#e94560",
    "_effect": "panda",
    "_cursor": "Pointing Hand",
    "_trail_color": "#e94560",
}

NEON_THEME = {
    "name": "Neon Panda",
    "background": "#0d0d0d",
    "surface": "#111111",
    "primary": "#1a003a",
    "accent": "#00ff88",
    "text": "#e0ffe0",
    "text_secondary": "#80c080",
    "border": "#003322",
    "success": "#00ff88",
    "warning": "#ffdd00",
    "error": "#ff3355",
    "tab_selected": "#00ff88",
    "button_bg": "#1a003a",
    "button_hover": "#00ff88",
    "panda_white": "#e0ffe0",
    "panda_black": "#0d0d0d",
    "progress_bar": "#00ff88",
    "input_bg": "#050505",
    "scrollbar": "#111111",
    "scrollbar_handle": "#00ff88",
    "_effect": "neon",
    "_cursor": "Cross",
    "_trail_color": "#00ff88",
}

GORE_THEME = {
    "name": "Gore",
    "background": "#1a0000",
    "surface": "#2b0000",
    "primary": "#4a0000",
    "accent": "#cc0000",
    "text": "#ffcccc",
    "text_secondary": "#aa6666",
    "border": "#660000",
    "success": "#228822",
    "warning": "#cc5500",
    "error": "#ff0000",
    "tab_selected": "#cc0000",
    "button_bg": "#4a0000",
    "button_hover": "#cc0000",
    "panda_white": "#ffcccc",
    "panda_black": "#1a0000",
    "progress_bar": "#cc0000",
    "input_bg": "#220000",
    "scrollbar": "#330000",
    "scrollbar_handle": "#cc0000",
    "_effect": "gore",
    "_cursor": "Cross",
    "_trail_color": "#cc0000",
}

BAT_THEME = {
    "name": "Bat Cave",
    "background": "#0a0a1a",
    "surface": "#10102a",
    "primary": "#1e003a",
    "accent": "#7b2dff",
    "text": "#ddddf0",
    "text_secondary": "#8888bb",
    "border": "#2a1a4a",
    "success": "#44aa77",
    "warning": "#cc8800",
    "error": "#ff3366",
    "tab_selected": "#7b2dff",
    "button_bg": "#1e003a",
    "button_hover": "#7b2dff",
    "panda_white": "#ddddf0",
    "panda_black": "#0a0a1a",
    "progress_bar": "#7b2dff",
    "input_bg": "#08081a",
    "scrollbar": "#1a1a2e",
    "scrollbar_handle": "#7b2dff",
    "_effect": "bat",
    "_cursor": "Default",
    "_trail_color": "#7b2dff",
}

RAINBOW_THEME = {
    "name": "Rainbow Chaos",
    "background": "#ff00ff",
    "surface": "#ff88ff",
    "primary": "#ff44cc",
    "accent": "#ffff00",
    "text": "#1a001a",
    "text_secondary": "#550055",
    "border": "#ff00cc",
    "success": "#00ff88",
    "warning": "#ff8800",
    "error": "#ff0000",
    "tab_selected": "#ffff00",
    "button_bg": "#ff44cc",
    "button_hover": "#ffff00",
    "panda_white": "#ffffff",
    "panda_black": "#1a001a",
    "progress_bar": "#00ffff",
    "input_bg": "#ff66ff",
    "scrollbar": "#ff44cc",
    "scrollbar_handle": "#ffff00",
    "_effect": "rainbow",
    "_cursor": "Pointing Hand",
    "_trail_color": "#ffff00",
}

OTTER_THEME = {
    "name": "Otter Cove",
    "background": "#1a1206",
    "surface": "#2e1f09",
    "primary": "#4a3210",
    "accent": "#e8a040",
    "text": "#f0e8d0",
    "text_secondary": "#c0a880",
    "border": "#6a4820",
    "success": "#4caf50",
    "warning": "#ff9800",
    "error": "#f44336",
    "tab_selected": "#e8a040",
    "button_bg": "#4a3210",
    "button_hover": "#e8a040",
    "panda_white": "#f0e8d0",
    "panda_black": "#1a1206",
    "progress_bar": "#e8a040",
    "input_bg": "#120c04",
    "scrollbar": "#2e1f09",
    "scrollbar_handle": "#e8a040",
    "_effect": "otter",
    "_cursor": "emoji:🤘",
    "_trail_color": "#e8a040",
}

GALAXY_THEME = {
    "name": "Galaxy",
    "background": "#03030f",
    "surface": "#070720",
    "primary": "#0d0d3a",
    "accent": "#4477ff",
    "text": "#e0e8ff",
    "text_secondary": "#8090cc",
    "border": "#1a1a60",
    "success": "#00ddaa",
    "warning": "#ffcc00",
    "error": "#ff4477",
    "tab_selected": "#4477ff",
    "button_bg": "#0d0d3a",
    "button_hover": "#4477ff",
    "panda_white": "#e0e8ff",
    "panda_black": "#03030f",
    "progress_bar": "#4477ff",
    "input_bg": "#020210",
    "scrollbar": "#070720",
    "scrollbar_handle": "#4477ff",
    "_effect": "galaxy",
    "_cursor": "Cross",
    "_trail_color": "#4477ff",
}

GALAXY_OTTER_THEME = {
    "name": "Galaxy Otter",
    "background": "#04030f",
    "surface": "#0f0820",
    "primary": "#1a1040",
    "accent": "#a06aff",
    "text": "#ece0f8",
    "text_secondary": "#9080b0",
    "border": "#2a1a50",
    "success": "#44ddaa",
    "warning": "#ffaa44",
    "error": "#ff4477",
    "tab_selected": "#a06aff",
    "button_bg": "#1a1040",
    "button_hover": "#a06aff",
    "panda_white": "#ece0f8",
    "panda_black": "#04030f",
    "progress_bar": "#a06aff",
    "input_bg": "#030210",
    "scrollbar": "#0f0820",
    "scrollbar_handle": "#a06aff",
    "_effect": "galaxy_otter",
    "_cursor": "emoji:🤘",
    "_trail_color": "#a06aff",
}

GOTH_THEME = {
    "name": "Goth",
    "background": "#0a0a0a",
    "surface": "#111111",
    "primary": "#1a001a",
    "accent": "#8800aa",
    "text": "#e8d8ee",
    "text_secondary": "#aa88bb",
    "border": "#330033",
    "success": "#336633",
    "warning": "#664400",
    "error": "#cc0044",
    "tab_selected": "#8800aa",
    "button_bg": "#1a001a",
    "button_hover": "#8800aa",
    "panda_white": "#e8d8ee",
    "panda_black": "#0a0a0a",
    "progress_bar": "#8800aa",
    "input_bg": "#080808",
    "scrollbar": "#111111",
    "scrollbar_handle": "#8800aa",
    "_effect": "goth",
    "_cursor": "Default",
    "_trail_color": "#8800aa",
}

VOLCANO_THEME = {
    "name": "Volcano",
    "background": "#1a0800",
    "surface": "#2a1000",
    "primary": "#3a1800",
    "accent": "#ff4400",
    "text": "#ffddcc",
    "text_secondary": "#cc8866",
    "border": "#6a2800",
    "success": "#559933",
    "warning": "#ff8800",
    "error": "#ff1100",
    "tab_selected": "#ff4400",
    "button_bg": "#3a1800",
    "button_hover": "#ff6600",
    "panda_white": "#ffddcc",
    "panda_black": "#1a0800",
    "progress_bar": "#ff5500",
    "input_bg": "#120500",
    "scrollbar": "#2a1000",
    "scrollbar_handle": "#ff4400",
    "_effect": "fire",
    "_cursor": "Cross",
    "_trail_color": "#ff4400",
}

ARCTIC_THEME = {
    "name": "Arctic",
    "background": "#030d1a",
    "surface": "#071525",
    "primary": "#0d2040",
    "accent": "#44aaff",
    "text": "#e8f4ff",
    "text_secondary": "#88aabb",
    "border": "#1a3a5a",
    "success": "#33ddaa",
    "warning": "#aaccff",
    "error": "#ff4488",
    "tab_selected": "#44aaff",
    "button_bg": "#0d2040",
    "button_hover": "#66ccff",
    "panda_white": "#e8f4ff",
    "panda_black": "#030d1a",
    "progress_bar": "#44aaff",
    "input_bg": "#020810",
    "scrollbar": "#071525",
    "scrollbar_handle": "#44aaff",
    "_effect": "ice",
    "_cursor": "Cross",
    "_trail_color": "#44aaff",
}

# Hidden / unlockable themes  (not shown in normal selector until unlocked)
SECRET_SKELETON_THEME = {
    "name": "Secret Skeleton",
    "background": "#ffffff",
    "surface": "#f0f0f0",
    "primary": "#dddddd",
    "accent": "#1a1a1a",
    "text": "#1a1a1a",
    "text_secondary": "#444444",
    "border": "#cccccc",
    "success": "#336633",
    "warning": "#886600",
    "error": "#990000",
    "tab_selected": "#1a1a1a",
    "button_bg": "#dddddd",
    "button_hover": "#1a1a1a",
    "panda_white": "#ffffff",
    "panda_black": "#1a1a1a",
    "progress_bar": "#1a1a1a",
    "input_bg": "#f8f8f8",
    "scrollbar": "#dddddd",
    "scrollbar_handle": "#1a1a1a",
    "_effect": "goth",
    "_cursor": "Cross",
    "_trail_color": "#1a1a1a",
    "_unlock": "skeleton",
}

SECRET_SAKURA_THEME = {
    "name": "Secret Sakura",
    "background": "#1a0810",
    "surface": "#2a1020",
    "primary": "#3d1530",
    "accent": "#ff6699",
    "text": "#ffe8f4",
    "text_secondary": "#cc88aa",
    "border": "#6a2045",
    "success": "#88cc88",
    "warning": "#ffcc88",
    "error": "#ff4477",
    "tab_selected": "#ff6699",
    "button_bg": "#3d1530",
    "button_hover": "#ff88bb",
    "panda_white": "#ffe8f4",
    "panda_black": "#1a0810",
    "progress_bar": "#ff6699",
    "input_bg": "#120510",
    "scrollbar": "#2a1020",
    "scrollbar_handle": "#ff6699",
    "_effect": "sakura",
    "_cursor": "Pointing Hand",
    "_trail_color": "#ff6699",
    "_unlock": "sakura",
}

FAIRY_THEME = {
    "name": "Fairy Garden",
    "background": "#0d0022",
    "surface": "#160038",
    "primary": "#2a0055",
    "accent": "#dd44ff",
    "text": "#f8e8ff",
    "text_secondary": "#cc99ee",
    "border": "#5500aa",
    "success": "#88ffcc",
    "warning": "#ffdd88",
    "error": "#ff55aa",
    "tab_selected": "#dd44ff",
    "button_bg": "#2a0055",
    "button_hover": "#dd44ff",
    "panda_white": "#f8e8ff",
    "panda_black": "#0d0022",
    "progress_bar": "#dd44ff",
    "input_bg": "#08001a",
    "scrollbar": "#160038",
    "scrollbar_handle": "#dd44ff",
    "_effect": "fairy",
    "_cursor": "emoji:🪄",
    "_trail_color": "#ffccee",
}

SECRET_DEEP_OCEAN_THEME = {
    "name": "Deep Ocean",
    "background": "#020d1a",
    "surface": "#051828",
    "primary": "#082840",
    "accent": "#00d4ff",
    "text": "#b0f0ff",
    "text_secondary": "#5aa8c0",
    "border": "#0a3a55",
    "success": "#00ffaa",
    "warning": "#ffdd00",
    "error": "#ff4488",
    "tab_selected": "#00d4ff",
    "button_bg": "#082840",
    "button_hover": "#00d4ff",
    "panda_white": "#b0f0ff",
    "panda_black": "#020d1a",
    "progress_bar": "#00d4ff",
    "input_bg": "#010810",
    "scrollbar": "#051828",
    "scrollbar_handle": "#00d4ff",
    "_effect": "ocean",
    "_cursor": "emoji:🦑",
    "_trail_color": "#00d4ff",
    "_unlock": "ocean",
}

PRESET_THEMES = {
    "Panda Dark": DEFAULT_THEME,
    "Panda Light": LIGHT_THEME,
    "Neon Panda": NEON_THEME,
    "Gore": GORE_THEME,
    "Bat Cave": BAT_THEME,
    "Rainbow Chaos": RAINBOW_THEME,
    "Otter Cove": OTTER_THEME,
    "Galaxy": GALAXY_THEME,
    "Galaxy Otter": GALAXY_OTTER_THEME,
    "Goth": GOTH_THEME,
    "Volcano": VOLCANO_THEME,
    "Arctic": ARCTIC_THEME,
    "Fairy Garden": FAIRY_THEME,
}

# ---------------------------------------------------------------------------
# New preset themes
# ---------------------------------------------------------------------------

MERMAID_THEME = {
    "name": "Mermaid",
    "background": "#050f1a",
    "surface": "#0a1e2e",
    "primary": "#0d2c40",
    "accent": "#00ccaa",
    "text": "#ccffee",
    "text_secondary": "#66ddbb",
    "border": "#0a3344",
    "success": "#00cc88",
    "warning": "#ffcc44",
    "error": "#ff4488",
    "tab_selected": "#00ccaa",
    "button_bg": "#0d2c40",
    "button_hover": "#00ccaa",
    "panda_white": "#ccffee",
    "panda_black": "#050f1a",
    "progress_bar": "#00ccaa",
    "input_bg": "#040d16",
    "scrollbar": "#0a1e2e",
    "scrollbar_handle": "#00ccaa",
    "_effect": "mermaid",
    "_cursor": "emoji:🧜",
    "_trail_color": "#00ccaa",
}

SHARK_BAIT_THEME = {
    "name": "Shark Bait",
    "background": "#020c18",
    "surface": "#041828",
    "primary": "#062040",
    "accent": "#1177cc",
    "text": "#aaddff",
    "text_secondary": "#5599bb",
    "border": "#0a2244",
    "success": "#1199aa",
    "warning": "#cc8800",
    "error": "#cc1133",
    "tab_selected": "#1177cc",
    "button_bg": "#062040",
    "button_hover": "#1177cc",
    "panda_white": "#aaddff",
    "panda_black": "#020c18",
    "progress_bar": "#1177cc",
    "input_bg": "#010810",
    "scrollbar": "#041828",
    "scrollbar_handle": "#1177cc",
    "_effect": "shark",
    "_cursor": "emoji:🦈",
    "_trail_color": "#1177cc",
}

ALIEN_THEME = {
    "name": "Alien",
    "background": "#030a04",
    "surface": "#071a08",
    "primary": "#0a240d",
    "accent": "#00ff88",
    "text": "#ccffdd",
    "text_secondary": "#44cc66",
    "border": "#0a3010",
    "success": "#00ff88",
    "warning": "#aaff00",
    "error": "#ff2255",
    "tab_selected": "#00ff88",
    "button_bg": "#0a240d",
    "button_hover": "#00ff88",
    "panda_white": "#ccffdd",
    "panda_black": "#030a04",
    "progress_bar": "#00ff88",
    "input_bg": "#020804",
    "scrollbar": "#071a08",
    "scrollbar_handle": "#00ff88",
    "_effect": "alien",
    "_cursor": "emoji:🛸",
    "_trail_color": "#00ff88",
}

PRESET_THEMES.update({
    "Mermaid": MERMAID_THEME,
    "Shark Bait": SHARK_BAIT_THEME,
    "Alien": ALIEN_THEME,
})

SECRET_BLOOD_MOON_THEME = {
    "name": "Blood Moon",
    "background": "#110005",
    "surface": "#1e0008",
    "primary": "#300010",
    "accent": "#cc1133",
    "text": "#ffcccc",
    "text_secondary": "#aa5566",
    "border": "#550015",
    "success": "#994422",
    "warning": "#cc6600",
    "error": "#ff0033",
    "tab_selected": "#cc1133",
    "button_bg": "#300010",
    "button_hover": "#cc1133",
    "panda_white": "#ffcccc",
    "panda_black": "#110005",
    "progress_bar": "#cc1133",
    "input_bg": "#0a0003",
    "scrollbar": "#1e0008",
    "scrollbar_handle": "#cc1133",
    "_effect": "gore",
    "_cursor": "emoji:🩸",
    "_trail_color": "#cc1133",
    "_unlock": "blood_moon",
}

SECRET_ICE_CAVE_THEME = {
    "name": "Ice Cave",
    "background": "#020a14",
    "surface": "#061828",
    "primary": "#0d2a40",
    "accent": "#88ddff",
    "text": "#ddf8ff",
    "text_secondary": "#6699bb",
    "border": "#1a4060",
    "success": "#44ddaa",
    "warning": "#ffcc44",
    "error": "#ff4488",
    "tab_selected": "#88ddff",
    "button_bg": "#0d2a40",
    "button_hover": "#88ddff",
    "panda_white": "#ddf8ff",
    "panda_black": "#020a14",
    "progress_bar": "#88ddff",
    "input_bg": "#010810",
    "scrollbar": "#061828",
    "scrollbar_handle": "#88ddff",
    "_effect": "sparkle",
    "_cursor": "emoji:❄",
    "_trail_color": "#88ddff",
    "_unlock": "ice_cave",
}

SECRET_CYBER_OTTER_THEME = {
    "name": "Cyber Otter",
    "background": "#030d18",
    "surface": "#071a2e",
    "primary": "#0a2840",
    "accent": "#00ffcc",
    "text": "#ccfff8",
    "text_secondary": "#44aaaa",
    "border": "#0f3a55",
    "success": "#00ffaa",
    "warning": "#ffdd00",
    "error": "#ff4488",
    "tab_selected": "#00ffcc",
    "button_bg": "#0a2840",
    "button_hover": "#00ffcc",
    "panda_white": "#ccfff8",
    "panda_black": "#030d18",
    "progress_bar": "#00ffcc",
    "input_bg": "#020b14",
    "scrollbar": "#071a2e",
    "scrollbar_handle": "#00ffcc",
    "_effect": "sparkle",
    "_cursor": "emoji:🦦",
    "_trail_color": "#00ffcc",
    "_unlock": "cyber_otter",
}

SECRET_TOXIC_NEON_THEME = {
    "name": "Toxic Neon",
    "background": "#050a00",
    "surface": "#0a1400",
    "primary": "#142000",
    "accent": "#aaff00",
    "text": "#ddffaa",
    "text_secondary": "#88aa44",
    "border": "#2a4400",
    "success": "#66ff33",
    "warning": "#ffee00",
    "error": "#ff3300",
    "tab_selected": "#aaff00",
    "button_bg": "#142000",
    "button_hover": "#aaff00",
    "panda_white": "#ddffaa",
    "panda_black": "#050a00",
    "progress_bar": "#aaff00",
    "input_bg": "#030700",
    "scrollbar": "#0a1400",
    "scrollbar_handle": "#aaff00",
    "_effect": "default",
    "_cursor": "emoji:☢",
    "_trail_color": "#aaff00",
    "_unlock": "toxic_neon",
}

SECRET_LAVA_CAVE_THEME = {
    "name": "Lava Cave",
    "background": "#0f0400",
    "surface": "#1e0800",
    "primary": "#2e1000",
    "accent": "#ff6600",
    "text": "#ffd4aa",
    "text_secondary": "#aa6633",
    "border": "#551f00",
    "success": "#dd9900",
    "warning": "#ffcc00",
    "error": "#ff2200",
    "tab_selected": "#ff6600",
    "button_bg": "#2e1000",
    "button_hover": "#ff6600",
    "panda_white": "#ffd4aa",
    "panda_black": "#0f0400",
    "progress_bar": "#ff6600",
    "input_bg": "#0a0300",
    "scrollbar": "#1e0800",
    "scrollbar_handle": "#ff6600",
    "_effect": "gore",
    "_cursor": "emoji:🌋",
    "_trail_color": "#ff6600",
    "_unlock": "lava_cave",
}

SECRET_SUNSET_BEACH_THEME = {
    "name": "Sunset Beach",
    "background": "#110a00",
    "surface": "#1e1200",
    "primary": "#2e1c00",
    "accent": "#ff9944",
    "text": "#ffeecc",
    "text_secondary": "#cc8844",
    "border": "#553300",
    "success": "#88cc44",
    "warning": "#ffcc00",
    "error": "#ff4422",
    "tab_selected": "#ff9944",
    "button_bg": "#2e1c00",
    "button_hover": "#ff9944",
    "panda_white": "#ffeecc",
    "panda_black": "#110a00",
    "progress_bar": "#ff9944",
    "input_bg": "#0c0800",
    "scrollbar": "#1e1200",
    "scrollbar_handle": "#ff9944",
    "_effect": "sakura",
    "_cursor": "emoji:🌅",
    "_trail_color": "#ff9944",
    "_unlock": "sunset_beach",
}

SECRET_MIDNIGHT_FOREST_THEME = {
    "name": "Midnight Forest",
    "background": "#020a04",
    "surface": "#05140a",
    "primary": "#082010",
    "accent": "#44cc66",
    "text": "#aaffcc",
    "text_secondary": "#447755",
    "border": "#0f3320",
    "success": "#44ff88",
    "warning": "#aacc00",
    "error": "#ff4444",
    "tab_selected": "#44cc66",
    "button_bg": "#082010",
    "button_hover": "#44cc66",
    "panda_white": "#aaffcc",
    "panda_black": "#020a04",
    "progress_bar": "#44cc66",
    "input_bg": "#010703",
    "scrollbar": "#05140a",
    "scrollbar_handle": "#44cc66",
    "_effect": "default",
    "_cursor": "emoji:🌲",
    "_trail_color": "#44cc66",
    "_unlock": "midnight_forest",
}

HIDDEN_THEMES = {
    "Secret Skeleton": SECRET_SKELETON_THEME,
    "Secret Sakura": SECRET_SAKURA_THEME,
    "Deep Ocean": SECRET_DEEP_OCEAN_THEME,
    "Blood Moon": SECRET_BLOOD_MOON_THEME,
    "Ice Cave": SECRET_ICE_CAVE_THEME,
    "Cyber Otter": SECRET_CYBER_OTTER_THEME,
    "Toxic Neon": SECRET_TOXIC_NEON_THEME,
    "Lava Cave": SECRET_LAVA_CAVE_THEME,
    "Sunset Beach": SECRET_SUNSET_BEACH_THEME,
    "Midnight Forest": SECRET_MIDNIGHT_FOREST_THEME,
}

# ---------------------------------------------------------------------------
# Extra hidden themes (unlocked at higher click milestones 1250–5000)
# ---------------------------------------------------------------------------

SECRET_CANDY_LAND_THEME = {
    "name": "Candy Land",
    "background": "#1a0020",
    "surface": "#2a0030",
    "primary": "#380040",
    "accent": "#ff55bb",
    "text": "#ffddff",
    "text_secondary": "#cc77cc",
    "border": "#550044",
    "success": "#ff88cc",
    "warning": "#ffcc00",
    "error": "#ff2255",
    "tab_selected": "#ff55bb",
    "button_bg": "#380040",
    "button_hover": "#ff55bb",
    "panda_white": "#ffddff",
    "panda_black": "#1a0020",
    "progress_bar": "#ff55bb",
    "input_bg": "#120015",
    "scrollbar": "#2a0030",
    "scrollbar_handle": "#ff55bb",
    "_effect": "rainbow",
    "_cursor": "emoji:🍭",
    "_trail_color": "#ff55bb",
    "_unlock": "candy_land",
}

SECRET_ZOMBIE_THEME = {
    "name": "Zombie Apocalypse",
    "background": "#0a0f00",
    "surface": "#111800",
    "primary": "#1a2200",
    "accent": "#55cc00",
    "text": "#bbff44",
    "text_secondary": "#557722",
    "border": "#223300",
    "success": "#66ff00",
    "warning": "#aacc00",
    "error": "#ff4444",
    "tab_selected": "#55cc00",
    "button_bg": "#1a2200",
    "button_hover": "#55cc00",
    "panda_white": "#bbff44",
    "panda_black": "#0a0f00",
    "progress_bar": "#55cc00",
    "input_bg": "#070c00",
    "scrollbar": "#111800",
    "scrollbar_handle": "#55cc00",
    "_effect": "gore",
    "_cursor": "emoji:🧟",
    "_trail_color": "#55cc00",
    "_unlock": "zombie",
}

SECRET_DRAGON_THEME = {
    "name": "Dragon Fire",
    "background": "#110500",
    "surface": "#1e0a00",
    "primary": "#2e1000",
    "accent": "#ff6600",
    "text": "#ffcc88",
    "text_secondary": "#cc7733",
    "border": "#441500",
    "success": "#ff9900",
    "warning": "#ffcc00",
    "error": "#ff2200",
    "tab_selected": "#ff6600",
    "button_bg": "#2e1000",
    "button_hover": "#ff6600",
    "panda_white": "#ffcc88",
    "panda_black": "#110500",
    "progress_bar": "#ff6600",
    "input_bg": "#0c0400",
    "scrollbar": "#1e0a00",
    "scrollbar_handle": "#ff6600",
    "_effect": "fire",
    "_cursor": "emoji:🐉",
    "_trail_color": "#ff6600",
    "_unlock": "dragon_fire",
}

SECRET_BUBBLEGUM_THEME = {
    "name": "Bubblegum",
    "background": "#140020",
    "surface": "#220030",
    "primary": "#300045",
    "accent": "#ff77ff",
    "text": "#ffccff",
    "text_secondary": "#cc88cc",
    "border": "#440055",
    "success": "#ff99ff",
    "warning": "#ffee44",
    "error": "#ff2255",
    "tab_selected": "#ff77ff",
    "button_bg": "#300045",
    "button_hover": "#ff77ff",
    "panda_white": "#ffccff",
    "panda_black": "#140020",
    "progress_bar": "#ff77ff",
    "input_bg": "#0e0018",
    "scrollbar": "#220030",
    "scrollbar_handle": "#ff77ff",
    "_effect": "fairy",
    "_cursor": "emoji:🫧",
    "_trail_color": "#ff77ff",
    "_unlock": "bubblegum",
}

SECRET_THUNDER_THEME = {
    "name": "Thunder Storm",
    "background": "#080810",
    "surface": "#101020",
    "primary": "#181830",
    "accent": "#aaaaff",
    "text": "#ccccff",
    "text_secondary": "#7777cc",
    "border": "#222244",
    "success": "#88aaff",
    "warning": "#ffcc44",
    "error": "#ff4444",
    "tab_selected": "#aaaaff",
    "button_bg": "#181830",
    "button_hover": "#aaaaff",
    "panda_white": "#ccccff",
    "panda_black": "#080810",
    "progress_bar": "#aaaaff",
    "input_bg": "#05050c",
    "scrollbar": "#101020",
    "scrollbar_handle": "#aaaaff",
    "_effect": "neon",
    "_cursor": "emoji:⚡",
    "_trail_color": "#aaaaff",
    "_unlock": "thunder_storm",
}

SECRET_ROSE_GOLD_THEME = {
    "name": "Rose Gold",
    "background": "#160808",
    "surface": "#221210",
    "primary": "#301818",
    "accent": "#cc7766",
    "text": "#ffddcc",
    "text_secondary": "#bb8877",
    "border": "#442222",
    "success": "#cc9977",
    "warning": "#ffcc44",
    "error": "#ff4444",
    "tab_selected": "#cc7766",
    "button_bg": "#301818",
    "button_hover": "#cc7766",
    "panda_white": "#ffddcc",
    "panda_black": "#160808",
    "progress_bar": "#cc7766",
    "input_bg": "#100606",
    "scrollbar": "#221210",
    "scrollbar_handle": "#cc7766",
    "_effect": "sakura",
    "_cursor": "emoji:🌹",
    "_trail_color": "#cc7766",
    "_unlock": "rose_gold",
}

SECRET_SPACE_CAT_THEME = {
    "name": "Space Cat",
    "background": "#050510",
    "surface": "#0a0a20",
    "primary": "#100f30",
    "accent": "#ff99ff",
    "text": "#ffccff",
    "text_secondary": "#cc88cc",
    "border": "#220044",
    "success": "#aa77ff",
    "warning": "#ffaa44",
    "error": "#ff4488",
    "tab_selected": "#ff99ff",
    "button_bg": "#100f30",
    "button_hover": "#ff99ff",
    "panda_white": "#ffccff",
    "panda_black": "#050510",
    "progress_bar": "#ff99ff",
    "input_bg": "#03030c",
    "scrollbar": "#0a0a20",
    "scrollbar_handle": "#ff99ff",
    "_effect": "galaxy",
    "_cursor": "emoji:🐱",
    "_trail_color": "#ff99ff",
    "_unlock": "space_cat",
}

SECRET_MUSHROOM_THEME = {
    "name": "Magic Mushroom",
    "background": "#0a0514",
    "surface": "#140a22",
    "primary": "#1e1030",
    "accent": "#ff5599",
    "text": "#ffccee",
    "text_secondary": "#cc7799",
    "border": "#330033",
    "success": "#88ff44",
    "warning": "#ffcc00",
    "error": "#ff2255",
    "tab_selected": "#ff5599",
    "button_bg": "#1e1030",
    "button_hover": "#ff5599",
    "panda_white": "#ffccee",
    "panda_black": "#0a0514",
    "progress_bar": "#ff5599",
    "input_bg": "#07030e",
    "scrollbar": "#140a22",
    "scrollbar_handle": "#ff5599",
    "_effect": "rainbow",
    "_cursor": "emoji:🍄",
    "_trail_color": "#ff5599",
    "_unlock": "magic_mushroom",
}

SECRET_ABYSSAL_THEME = {
    "name": "Abyssal Void",
    "background": "#000000",
    "surface": "#050505",
    "primary": "#0a0a0a",
    "accent": "#5500aa",
    "text": "#9966ff",
    "text_secondary": "#553388",
    "border": "#220044",
    "success": "#7744cc",
    "warning": "#884400",
    "error": "#aa0044",
    "tab_selected": "#5500aa",
    "button_bg": "#0a0a0a",
    "button_hover": "#5500aa",
    "panda_white": "#9966ff",
    "panda_black": "#000000",
    "progress_bar": "#5500aa",
    "input_bg": "#000000",
    "scrollbar": "#050505",
    "scrollbar_handle": "#5500aa",
    "_effect": "goth",
    "_cursor": "emoji:🕳",
    "_trail_color": "#5500aa",
    "_unlock": "abyssal_void",
}

SECRET_SPRING_THEME = {
    "name": "Spring Bloom",
    "background": "#051205",
    "surface": "#0a1e0a",
    "primary": "#102a10",
    "accent": "#88dd44",
    "text": "#ccffcc",
    "text_secondary": "#66aa44",
    "border": "#1a4410",
    "success": "#88ff44",
    "warning": "#ffcc22",
    "error": "#ff4444",
    "tab_selected": "#88dd44",
    "button_bg": "#102a10",
    "button_hover": "#88dd44",
    "panda_white": "#ccffcc",
    "panda_black": "#051205",
    "progress_bar": "#88dd44",
    "input_bg": "#030c03",
    "scrollbar": "#0a1e0a",
    "scrollbar_handle": "#88dd44",
    "_effect": "fairy",
    "_cursor": "emoji:🌷",
    "_trail_color": "#88dd44",
    "_unlock": "spring_bloom",
}

SECRET_GOLD_RUSH_THEME = {
    "name": "Gold Rush",
    "background": "#120c00",
    "surface": "#1e1400",
    "primary": "#2e1e00",
    "accent": "#ffcc00",
    "text": "#ffeeaa",
    "text_secondary": "#cc9900",
    "border": "#443300",
    "success": "#ffaa00",
    "warning": "#ff8800",
    "error": "#ff2200",
    "tab_selected": "#ffcc00",
    "button_bg": "#2e1e00",
    "button_hover": "#ffcc00",
    "panda_white": "#ffeeaa",
    "panda_black": "#120c00",
    "progress_bar": "#ffcc00",
    "input_bg": "#0c0800",
    "scrollbar": "#1e1400",
    "scrollbar_handle": "#ffcc00",
    "_effect": "default",
    "_cursor": "emoji:💰",
    "_trail_color": "#ffcc00",
    "_unlock": "gold_rush",
}

SECRET_NEBULA_THEME = {
    "name": "Nebula",
    "background": "#030510",
    "surface": "#060a1e",
    "primary": "#0a102c",
    "accent": "#cc44ff",
    "text": "#eeccff",
    "text_secondary": "#9966cc",
    "border": "#220044",
    "success": "#8844ff",
    "warning": "#ff9900",
    "error": "#ff3366",
    "tab_selected": "#cc44ff",
    "button_bg": "#0a102c",
    "button_hover": "#cc44ff",
    "panda_white": "#eeccff",
    "panda_black": "#030510",
    "progress_bar": "#cc44ff",
    "input_bg": "#02040c",
    "scrollbar": "#060a1e",
    "scrollbar_handle": "#cc44ff",
    "_effect": "galaxy",
    "_cursor": "emoji:🌌",
    "_trail_color": "#cc44ff",
    "_unlock": "nebula",
}

HIDDEN_THEMES.update({
    "Candy Land":        SECRET_CANDY_LAND_THEME,
    "Zombie Apocalypse": SECRET_ZOMBIE_THEME,
    "Dragon Fire":       SECRET_DRAGON_THEME,
    "Bubblegum":         SECRET_BUBBLEGUM_THEME,
    "Thunder Storm":     SECRET_THUNDER_THEME,
    "Rose Gold":         SECRET_ROSE_GOLD_THEME,
    "Space Cat":         SECRET_SPACE_CAT_THEME,
    "Magic Mushroom":    SECRET_MUSHROOM_THEME,
    "Abyssal Void":      SECRET_ABYSSAL_THEME,
    "Spring Bloom":      SECRET_SPRING_THEME,
    "Gold Rush":         SECRET_GOLD_RUSH_THEME,
    "Nebula":            SECRET_NEBULA_THEME,
})

# Which effects each theme uses (name → effect key)
THEME_EFFECTS = {t["name"]: t.get("_effect", "default") for t in {
    **PRESET_THEMES, **HIDDEN_THEMES,
}.values()}

import os as _os
_SVG_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "assets", "svg")

# SVG decoration file for each theme (name → relative filename in assets/svg/)
THEME_SVG = {
    "Panda Dark":       "panda_dark.svg",
    "Panda Light":      "panda_light.svg",
    "Neon Panda":       "neon.svg",
    "Gore":             "gore.svg",
    "Bat Cave":         "bat_cave.svg",
    "Rainbow Chaos":    "rainbow.svg",
    "Otter Cove":       "otter_cove.svg",
    "Galaxy":           "galaxy.svg",
    "Galaxy Otter":     "galaxy_otter.svg",
    "Goth":             "goth.svg",
    "Volcano":          "volcano.svg",
    "Arctic":           "arctic.svg",
    "Fairy Garden":     "fairy_garden.svg",
    "Secret Skeleton":  "secret_skeleton.svg",
    "Secret Sakura":    "secret_sakura.svg",
    "Deep Ocean":       "deep_ocean.svg",
    "Blood Moon":       "blood_moon.svg",
    "Ice Cave":         "ice_cave.svg",
    "Cyber Otter":      "otter_cove.svg",
    "Toxic Neon":       "neon.svg",
    "Lava Cave":        "volcano.svg",
    "Sunset Beach":     "fairy_garden.svg",
    "Midnight Forest":  "bat_cave.svg",
    # New preset themes (dedicated SVGs)
    "Mermaid":          "mermaid.svg",
    "Shark Bait":       "shark_bait.svg",
    "Alien":            "alien.svg",
    # New hidden themes
    "Candy Land":       "rainbow.svg",
    "Zombie Apocalypse": "gore.svg",
    "Dragon Fire":      "volcano.svg",
    "Bubblegum":        "fairy_garden.svg",
    "Thunder Storm":    "neon.svg",
    "Rose Gold":        "secret_sakura.svg",
    "Space Cat":        "galaxy_otter.svg",
    "Magic Mushroom":   "fairy_garden.svg",
    "Abyssal Void":     "bat_cave.svg",
    "Spring Bloom":     "fairy_garden.svg",
    "Gold Rush":        "galaxy.svg",
    "Nebula":           "galaxy.svg",
}


def get_theme_svg_path(theme_name: str) -> str:
    """Return the absolute path of the SVG decoration for *theme_name*, or ''."""
    filename = THEME_SVG.get(theme_name, "")
    if not filename:
        return ""
    path = _os.path.join(_SVG_DIR, filename)
    return path if _os.path.isfile(path) else ""


# ---------------------------------------------------------------------------
# Per-theme banner text and status-bar flavor messages
# ---------------------------------------------------------------------------

THEME_BANNER = {
    "Panda Dark":      "🐼  Alpha Fixer  &  File Converter",
    "Panda Light":     "🐼  Alpha Fixer  &  File Converter  🤍",
    "Neon Panda":      "⚡🐼  Alpha Fixer  &  File Converter  🐼⚡",
    "Gore":            "🩸  Alpha Fixer  &  File Converter  🩸",
    "Bat Cave":        "🦇  Alpha Fixer  &  File Converter  🦇",
    "Rainbow Chaos":   "🌈  Alpha Fixer  &  File Converter  🌈",
    "Otter Cove":      "🦦🤘  Alpha Fixer  &  File Converter  🤘🦦",
    "Galaxy":          "✦  Alpha Fixer  &  File Converter  ✦",
    "Galaxy Otter":    "🦦✦  Alpha Fixer  &  File Converter  ✦🦦",
    "Goth":            "💀  Alpha Fixer  &  File Converter  💀",
    "Volcano":         "🌋  Alpha Fixer  &  File Converter  🔥",
    "Arctic":          "❄  Alpha Fixer  &  File Converter  ❄",
    "Fairy Garden":    "🧚✨🪄  Alpha Fixer  &  File Converter  🪄✨🧚",
    "Secret Skeleton": "☠  Alpha Fixer  &  File Converter  ☠",
    "Secret Sakura":   "🌸  Alpha Fixer  &  File Converter  🌸",
    "Deep Ocean":      "🌊🦑  Alpha Fixer  &  File Converter  🦑🌊",
    "Blood Moon":      "🩸🌕  Alpha Fixer  &  File Converter  🌕🩸",
    "Ice Cave":        "❄🧊  Alpha Fixer  &  File Converter  🧊❄",
    "Cyber Otter":     "🦦💻  Alpha Fixer  &  File Converter  💻🦦",
    "Toxic Neon":      "☢⚡  Alpha Fixer  &  File Converter  ⚡☢",
    "Lava Cave":       "🌋🔥  Alpha Fixer  &  File Converter  🔥🌋",
    "Sunset Beach":    "🌅🏖  Alpha Fixer  &  File Converter  🏖🌅",
    "Midnight Forest": "🌲🌙  Alpha Fixer  &  File Converter  🌙🌲",
    # New preset themes
    "Mermaid":         "🧜🐚  Alpha Fixer  &  File Converter  🐚🧜",
    "Shark Bait":      "🦈🩸  Alpha Fixer  &  File Converter  🩸🦈",
    "Alien":           "🛸👽  Alpha Fixer  &  File Converter  👽🛸",
    # New hidden themes
    "Candy Land":        "🍭🌈  Alpha Fixer  &  File Converter  🌈🍭",
    "Zombie Apocalypse": "🧟💀  Alpha Fixer  &  File Converter  💀🧟",
    "Dragon Fire":       "🐉🔥  Alpha Fixer  &  File Converter  🔥🐉",
    "Bubblegum":         "🫧🍬  Alpha Fixer  &  File Converter  🍬🫧",
    "Thunder Storm":     "⚡🌩  Alpha Fixer  &  File Converter  🌩⚡",
    "Rose Gold":         "🌹✨  Alpha Fixer  &  File Converter  ✨🌹",
    "Space Cat":         "🐱🚀  Alpha Fixer  &  File Converter  🚀🐱",
    "Magic Mushroom":    "🍄✨  Alpha Fixer  &  File Converter  ✨🍄",
    "Abyssal Void":      "🕳🌑  Alpha Fixer  &  File Converter  🌑🕳",
    "Spring Bloom":      "🌷🌿  Alpha Fixer  &  File Converter  🌿🌷",
    "Gold Rush":         "💰✦  Alpha Fixer  &  File Converter  ✦💰",
    "Nebula":            "🌌💫  Alpha Fixer  &  File Converter  💫🌌",
}

THEME_STATUS_MESSAGES = {
    "Panda Dark":      "🐼  Panda Dark — Ready to chew some bamboo!",
    "Panda Light":     "🐼  Panda Light — Squeaky clean and ready!",
    "Neon Panda":      "⚡  Neon Panda — Electrifying and ready!",
    "Gore":            "🩸  Gore — Proceed with caution…",
    "Bat Cave":        "🦇  Bat Cave — Darkness welcome here.",
    "Rainbow Chaos":   "🌈  Rainbow Chaos — Pure chromatic madness!",
    "Otter Cove":      "🦦  Otter Cove — Chillin' by the water. 🤘",
    "Galaxy":          "✦  Galaxy — Lost in space, found in pixels.",
    "Galaxy Otter":    "🦦✦  Galaxy Otter — Rockin' the cosmos!",
    "Goth":            "💀  Goth — Into the abyss we go.",
    "Volcano":         "🌋  Volcano — Things are heating up!",
    "Arctic":          "❄  Arctic — Stay frosty.",
    "Fairy Garden":    "🧚✨  Fairy Garden — Sprinkle some magic!",
    "Secret Skeleton": "☠  Secret Skeleton — The dead have awakened…",
    "Secret Sakura":   "🌸  Secret Sakura — Petals on the wind.",
    "Deep Ocean":      "🌊  Deep Ocean — Something stirs in the deep…",
    "Blood Moon":      "🩸  Blood Moon — The crimson tide rises.",
    "Ice Cave":        "❄  Ice Cave — Stay cool, it's freezing in here.",
    "Cyber Otter":     "🦦  Cyber Otter — Hacking the planet, one fish at a time.",
    "Toxic Neon":      "☢  Toxic Neon — Radioactively ready.",
    "Lava Cave":       "🌋  Lava Cave — Things are melting down nicely.",
    "Sunset Beach":    "🌅  Sunset Beach — Golden hour, every hour.",
    "Midnight Forest": "🌲  Midnight Forest — Rustling in the dark.",
    # New preset themes
    "Mermaid":         "🧜  Mermaid — Dive in, the water's divine.",
    "Shark Bait":      "🦈  Shark Bait — You're gonna need a bigger app.",
    "Alien":           "🛸  Alien — They've come for your pixels.",
    # New hidden themes
    "Candy Land":        "🍭  Candy Land — Everything is sweet here.",
    "Zombie Apocalypse": "🧟  Zombie Apocalypse — Brains… and bitmaps.",
    "Dragon Fire":       "🐉  Dragon Fire — Scorching hot processing.",
    "Bubblegum":         "🫧  Bubblegum — Pop!",
    "Thunder Storm":     "⚡  Thunder Storm — Charged up and crackling.",
    "Rose Gold":         "🌹  Rose Gold — Elegant and timeless.",
    "Space Cat":         "🐱  Space Cat — Exploring the final meow-tier.",
    "Magic Mushroom":    "🍄  Magic Mushroom — Reality is optional.",
    "Abyssal Void":      "🕳  Abyssal Void — There is no escape.",
    "Spring Bloom":      "🌷  Spring Bloom — Fresh pixels, fresh start.",
    "Gold Rush":         "💰  Gold Rush — Pixel gold guaranteed.",
    "Nebula":            "🌌  Nebula — Stardust and secrets.",
}


def get_theme_banner(theme_name: str) -> str:
    """Return the header banner text for *theme_name*, falling back to default."""
    return THEME_BANNER.get(theme_name, "🐼  Alpha Fixer  &  File Converter")


def get_theme_status(theme_name: str) -> str:
    """Return the status-bar flavor message for *theme_name*."""
    return THEME_STATUS_MESSAGES.get(theme_name, "Ready  🐼")


# Per-theme animated banner frames.  Themes listed here have a cycling banner;
# each element in the list is displayed in turn (one frame per ~800 ms).
# Themes not listed fall back to their single THEME_BANNER entry.
THEME_BANNER_FRAMES: dict[str, list[str]] = {
    # ---------------------------------------------------------------------------
    # Original preset themes
    # ---------------------------------------------------------------------------
    "Panda Dark": [
        "🐼  Alpha Fixer  &  File Converter",
        "🐼🎋  Alpha Fixer  &  File Converter  🎋🐼",
        "🐼✨  Alpha Fixer  &  File Converter  ✨🐼",
    ],
    "Panda Light": [
        "🐼  Alpha Fixer  &  File Converter  🤍",
        "🤍🐼  Alpha Fixer  &  File Converter  🐼🤍",
        "🐼🌸🤍  Alpha Fixer  &  File Converter  🤍🌸🐼",
    ],
    "Neon Panda": [
        "⚡🐼  Alpha Fixer  &  File Converter  🐼⚡",
        "🐼⚡🌟  Alpha Fixer  &  File Converter  🌟⚡🐼",
        "⚡✦🐼  Alpha Fixer  &  File Converter  🐼✦⚡",
    ],
    "Gore": [
        "🩸  Alpha Fixer  &  File Converter  🩸",
        "💀🩸  Alpha Fixer  &  File Converter  🩸💀",
        "🩸☠💀  Alpha Fixer  &  File Converter  💀☠🩸",
    ],
    "Bat Cave": [
        "🦇🌙💜  Alpha Fixer  &  File Converter  💜🌙🦇",
        "🌙🦇  Alpha Fixer  &  File Converter  🦇🌙",
        "💜🦇🌙  Alpha Fixer  &  File Converter  🌙🦇💜",
    ],
    "Rainbow Chaos": [
        "🌈  Alpha Fixer  &  File Converter  🌈",
        "🌈🦄  Alpha Fixer  &  File Converter  🦄🌈",
        "🌈✨🦄  Alpha Fixer  &  File Converter  🦄✨🌈",
    ],
    "Otter Cove": [
        "🦦🤘  Alpha Fixer  &  File Converter  🤘🦦",
        "🤘🦦🐟  Alpha Fixer  &  File Converter  🐟🦦🤘",
        "🦦💧🤘  Alpha Fixer  &  File Converter  🤘💧🦦",
    ],
    "Galaxy": [
        "✦  Alpha Fixer  &  File Converter  ✦",
        "⭐✦  Alpha Fixer  &  File Converter  ✦⭐",
        "✦🌌⭐  Alpha Fixer  &  File Converter  ⭐🌌✦",
    ],
    "Galaxy Otter": [
        "🦦✦  Alpha Fixer  &  File Converter  ✦🦦",
        "✦🦦⭐  Alpha Fixer  &  File Converter  ⭐🦦✦",
        "🦦🌌✦  Alpha Fixer  &  File Converter  ✦🌌🦦",
    ],
    "Goth": [
        "💀  Alpha Fixer  &  File Converter  💀",
        "💀🕷  Alpha Fixer  &  File Converter  🕷💀",
        "💀🖤🕷  Alpha Fixer  &  File Converter  🕷🖤💀",
    ],
    "Volcano": [
        "🌋  Alpha Fixer  &  File Converter  🔥",
        "🔥🌋  Alpha Fixer  &  File Converter  🌋🔥",
        "🌋💥🔥  Alpha Fixer  &  File Converter  🔥💥🌋",
    ],
    "Arctic": [
        "❄  Alpha Fixer  &  File Converter  ❄",
        "❄🧊  Alpha Fixer  &  File Converter  🧊❄",
        "❄✦🧊  Alpha Fixer  &  File Converter  🧊✦❄",
    ],
    "Fairy Garden": [
        "🧚✨🪄  Alpha Fixer  &  File Converter  🪄✨🧚",
        "✨🌟🧚  Alpha Fixer  &  File Converter  🧚🌟✨",
        "🪄💜✨  Alpha Fixer  &  File Converter  ✨💜🪄",
        "🌸🧚🌟  Alpha Fixer  &  File Converter  🌟🧚🌸",
    ],
    # ---------------------------------------------------------------------------
    # New preset themes
    # ---------------------------------------------------------------------------
    "Mermaid": [
        "🧜🐚  Alpha Fixer  &  File Converter  🐚🧜",
        "🐚🌊🧜  Alpha Fixer  &  File Converter  🧜🌊🐚",
        "🧜🐠🐚  Alpha Fixer  &  File Converter  🐚🐠🧜",
        "🌊🧜✨  Alpha Fixer  &  File Converter  ✨🧜🌊",
    ],
    "Shark Bait": [
        "🦈🩸  Alpha Fixer  &  File Converter  🩸🦈",
        "🩸🦈💦  Alpha Fixer  &  File Converter  💦🦈🩸",
        "🦈💥🩸  Alpha Fixer  &  File Converter  🩸💥🦈",
    ],
    "Alien": [
        "🛸👽  Alpha Fixer  &  File Converter  👽🛸",
        "👽🌌🛸  Alpha Fixer  &  File Converter  🛸🌌👽",
        "🛸⭐👽  Alpha Fixer  &  File Converter  👽⭐🛸",
    ],
    # ---------------------------------------------------------------------------
    # Hidden / unlockable themes
    # ---------------------------------------------------------------------------
    "Secret Skeleton": [
        "☠  Alpha Fixer  &  File Converter  ☠",
        "☠💀  Alpha Fixer  &  File Converter  💀☠",
        "💀☠🦴  Alpha Fixer  &  File Converter  🦴☠💀",
    ],
    "Secret Sakura": [
        "🌸  Alpha Fixer  &  File Converter  🌸",
        "🌸🌺  Alpha Fixer  &  File Converter  🌺🌸",
        "🌺🌸🌷  Alpha Fixer  &  File Converter  🌷🌸🌺",
    ],
    "Deep Ocean": [
        "🌊🦑  Alpha Fixer  &  File Converter  🦑🌊",
        "🦑🐙🌊  Alpha Fixer  &  File Converter  🌊🐙🦑",
        "🌊🐠🦑  Alpha Fixer  &  File Converter  🦑🐠🌊",
        "🐙🌊🐟  Alpha Fixer  &  File Converter  🐟🌊🐙",
    ],
    "Blood Moon": [
        "🩸🌕  Alpha Fixer  &  File Converter  🌕🩸",
        "🌕🩸🌑  Alpha Fixer  &  File Converter  🌑🩸🌕",
        "🩸🌑💀  Alpha Fixer  &  File Converter  💀🌑🩸",
    ],
    "Ice Cave": [
        "❄🧊  Alpha Fixer  &  File Converter  🧊❄",
        "🧊❄💎  Alpha Fixer  &  File Converter  💎❄🧊",
        "❄✦🧊  Alpha Fixer  &  File Converter  🧊✦❄",
    ],
    "Cyber Otter": [
        "🦦💻  Alpha Fixer  &  File Converter  💻🦦",
        "💻🦦⚡  Alpha Fixer  &  File Converter  ⚡🦦💻",
        "🦦✦💻  Alpha Fixer  &  File Converter  💻✦🦦",
    ],
    "Toxic Neon": [
        "☢⚡  Alpha Fixer  &  File Converter  ⚡☢",
        "⚡☢💚  Alpha Fixer  &  File Converter  💚☢⚡",
        "☢✦⚡  Alpha Fixer  &  File Converter  ⚡✦☢",
    ],
    "Lava Cave": [
        "🌋🔥  Alpha Fixer  &  File Converter  🔥🌋",
        "🔥🌋💥  Alpha Fixer  &  File Converter  💥🌋🔥",
        "🌋🔴🔥  Alpha Fixer  &  File Converter  🔥🔴🌋",
    ],
    "Sunset Beach": [
        "🌅🏖  Alpha Fixer  &  File Converter  🏖🌅",
        "🏖🌅🌊  Alpha Fixer  &  File Converter  🌊🌅🏖",
        "🌅🌴🏖  Alpha Fixer  &  File Converter  🏖🌴🌅",
    ],
    "Midnight Forest": [
        "🌲🌙  Alpha Fixer  &  File Converter  🌙🌲",
        "🌙🌲🦉  Alpha Fixer  &  File Converter  🦉🌲🌙",
        "🌲✨🌙  Alpha Fixer  &  File Converter  🌙✨🌲",
    ],
    "Candy Land": [
        "🍭🌈  Alpha Fixer  &  File Converter  🌈🍭",
        "🌈🍭🍬  Alpha Fixer  &  File Converter  🍬🍭🌈",
        "🍭✨🌈  Alpha Fixer  &  File Converter  🌈✨🍭",
    ],
    "Zombie Apocalypse": [
        "🧟💀  Alpha Fixer  &  File Converter  💀🧟",
        "💀🧟🦠  Alpha Fixer  &  File Converter  🦠🧟💀",
        "🧟☣💀  Alpha Fixer  &  File Converter  💀☣🧟",
    ],
    "Dragon Fire": [
        "🐉🔥  Alpha Fixer  &  File Converter  🔥🐉",
        "🔥🐉💥  Alpha Fixer  &  File Converter  💥🐉🔥",
        "🐉🔥🌋  Alpha Fixer  &  File Converter  🌋🔥🐉",
    ],
    "Bubblegum": [
        "🫧🍬  Alpha Fixer  &  File Converter  🍬🫧",
        "🍬🫧💜  Alpha Fixer  &  File Converter  💜🫧🍬",
        "🫧✨🍬  Alpha Fixer  &  File Converter  🍬✨🫧",
    ],
    "Thunder Storm": [
        "⚡🌩  Alpha Fixer  &  File Converter  🌩⚡",
        "🌩⚡🌪  Alpha Fixer  &  File Converter  🌪⚡🌩",
        "⚡🌑🌩  Alpha Fixer  &  File Converter  🌩🌑⚡",
    ],
    "Rose Gold": [
        "🌹✨  Alpha Fixer  &  File Converter  ✨🌹",
        "✨🌹💫  Alpha Fixer  &  File Converter  💫🌹✨",
        "🌹🌸✨  Alpha Fixer  &  File Converter  ✨🌸🌹",
    ],
    "Space Cat": [
        "🐱🚀  Alpha Fixer  &  File Converter  🚀🐱",
        "🚀🐱⭐  Alpha Fixer  &  File Converter  ⭐🐱🚀",
        "🐱🌌🚀  Alpha Fixer  &  File Converter  🚀🌌🐱",
    ],
    "Magic Mushroom": [
        "🍄✨  Alpha Fixer  &  File Converter  ✨🍄",
        "✨🍄🌟  Alpha Fixer  &  File Converter  🌟🍄✨",
        "🍄🌈✨  Alpha Fixer  &  File Converter  ✨🌈🍄",
    ],
    "Abyssal Void": [
        "🕳🌑  Alpha Fixer  &  File Converter  🌑🕳",
        "🌑🕳💜  Alpha Fixer  &  File Converter  💜🕳🌑",
        "🕳✦🌑  Alpha Fixer  &  File Converter  🌑✦🕳",
    ],
    "Spring Bloom": [
        "🌷🌿  Alpha Fixer  &  File Converter  🌿🌷",
        "🌿🌷🌸  Alpha Fixer  &  File Converter  🌸🌷🌿",
        "🌷✨🌿  Alpha Fixer  &  File Converter  🌿✨🌷",
    ],
    "Gold Rush": [
        "💰✦  Alpha Fixer  &  File Converter  ✦💰",
        "✦💰⭐  Alpha Fixer  &  File Converter  ⭐💰✦",
        "💰🌟✦  Alpha Fixer  &  File Converter  ✦🌟💰",
    ],
    "Nebula": [
        "🌌💫  Alpha Fixer  &  File Converter  💫🌌",
        "💫🌌✦  Alpha Fixer  &  File Converter  ✦🌌💫",
        "🌌⭐💫  Alpha Fixer  &  File Converter  💫⭐🌌",
        "💫🔮🌌  Alpha Fixer  &  File Converter  🌌🔮💫",
    ],
}


def get_theme_banner_frames(theme_name: str) -> list[str]:
    """Return the animated banner frame list for *theme_name*.

    Returns a single-element list (no animation) if the theme has no animation
    frames defined, falling back to the static THEME_BANNER text.
    """
    if theme_name in THEME_BANNER_FRAMES:
        return list(THEME_BANNER_FRAMES[theme_name])
    return [get_theme_banner(theme_name)]


def build_stylesheet(theme: Optional[dict] = None) -> str:
    """Generate a full Qt stylesheet from the given theme dictionary."""
    t = {**DEFAULT_THEME, **(theme or {})}
    return f"""
/* ===== Global ===== */
QWidget {{
    background-color: {t['background']};
    color: {t['text']};
    font-family: "Segoe UI", "Ubuntu", "Arial", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {t['background']};
}}

/* ===== Tabs ===== */
QTabWidget::pane {{
    border: 1px solid {t['border']};
    background-color: {t['surface']};
    border-radius: 4px;
}}
QTabBar {{
    background: {t['primary']};
    border: none;
}}
QTabBar::tab {{
    background: {t['primary']};
    color: {t['text_secondary']};
    padding: 10px 22px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    background: {t['tab_selected']};
    color: {t['panda_white']};
}}
QTabBar::tab:hover:!selected {{
    background: {t['button_hover']};
    color: {t['panda_white']};
}}

/* ===== Buttons ===== */
QPushButton {{
    background-color: {t['button_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {t['button_hover']};
    color: {t['panda_white']};
    border-color: {t['accent']};
}}
QPushButton:pressed {{
    background-color: {t['accent']};
}}
QPushButton:disabled {{
    background-color: {t['border']};
    color: {t['text_secondary']};
}}

/* ===== Accent Buttons ===== */
QPushButton#accent {{
    background-color: {t['accent']};
    color: {t['panda_white']};
    border: none;
    font-size: 14px;
    padding: 9px 20px;
}}
QPushButton#accent:hover {{
    background-color: {t['button_hover']};
}}

/* ===== Line Edits / Inputs ===== */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {t['accent']};
}}
QLineEdit:focus, QTextEdit:focus {{
    border-color: {t['accent']};
}}

/* ===== Combo Boxes ===== */
QComboBox {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    padding: 5px 8px;
    padding-right: 28px;
    min-height: 28px;
}}
QComboBox:hover {{
    border-color: {t['accent']};
}}
QComboBox::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    border: none;
    border-left: 1px solid {t['border']};
    width: 22px;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background: {t['primary']};
}}
QComboBox::down-arrow {{
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border']};
    selection-background-color: {t['accent']};
    padding: 2px;
}}

/* ===== Spin Boxes ===== */
QSpinBox, QDoubleSpinBox {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    padding: 5px 8px;
    padding-right: 24px;
    min-height: 26px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {t['accent']};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    height: 14px;
    background: {t['primary']};
    border: 1px solid {t['border']};
    border-top-right-radius: 4px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    height: 14px;
    background: {t['primary']};
    border: 1px solid {t['border']};
    border-bottom-right-radius: 4px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {t['accent']};
}}

/* ===== Sliders ===== */
QSlider::groove:horizontal {{
    height: 6px;
    background: {t['border']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {t['accent']};
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}
QSlider::sub-page:horizontal {{
    background: {t['accent']};
    border-radius: 3px;
}}

/* ===== Progress Bar ===== */
QProgressBar {{
    background-color: {t['border']};
    border-radius: 5px;
    text-align: center;
    color: {t['text']};
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: {t['progress_bar']};
    border-radius: 5px;
}}

/* ===== Labels ===== */
QLabel {{
    color: {t['text']};
}}
QLabel#header {{
    font-size: 18px;
    font-weight: 700;
    color: {t['accent']};
}}
QLabel#subheader {{
    font-size: 14px;
    font-weight: 600;
    color: {t['text_secondary']};
}}
QLabel#section {{
    font-size: 13px;
    font-weight: 700;
    color: {t['accent']};
    padding: 4px 0;
}}

/* ===== Group Box ===== */
QGroupBox {{
    border: 1px solid {t['border']};
    border-radius: 6px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
    font-weight: 600;
    color: {t['text_secondary']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {t['accent']};
    font-size: 13px;
}}

/* ===== List Widget ===== */
QListWidget {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
}}
QListWidget::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}
QListWidget::item:hover {{
    background-color: {t['primary']};
}}

/* ===== Tree Widget ===== */
QTreeWidget {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    alternate-background-color: {t['surface']};
}}
QTreeWidget::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}
QHeaderView::section {{
    background-color: {t['primary']};
    color: {t['text']};
    padding: 5px;
    border: none;
    font-weight: 600;
}}

/* ===== Scrollbars ===== */
QScrollBar:vertical {{
    background: {t['scrollbar']};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {t['scrollbar_handle']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {t['scrollbar']};
    height: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scrollbar_handle']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ===== Check Box ===== */
QCheckBox {{
    color: {t['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {t['border']};
    border-radius: 4px;
    background: {t['input_bg']};
}}
QCheckBox::indicator:checked {{
    background: {t['accent']};
    border-color: {t['accent']};
}}

/* ===== Radio Button ===== */
QRadioButton {{
    color: {t['text']};
    spacing: 6px;
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {t['border']};
    border-radius: 8px;
    background: {t['input_bg']};
}}
QRadioButton::indicator:checked {{
    background: {t['accent']};
    border-color: {t['accent']};
}}

/* ===== Splitter ===== */
QSplitter::handle {{
    background-color: {t['border']};
}}

/* ===== Status Bar ===== */
QStatusBar {{
    background-color: {t['surface']};
    color: {t['text_secondary']};
    border-top: 1px solid {t['border']};
}}

/* ===== Tool Bar ===== */
QToolBar {{
    background-color: {t['surface']};
    border-bottom: 1px solid {t['border']};
    spacing: 6px;
    padding: 4px 8px;
}}
QToolBar QPushButton {{
    padding: 5px 12px;
    font-size: 12px;
    min-height: 26px;
}}
QToolBar QLabel {{
    padding: 0 6px;
    color: {t['text_secondary']};
    font-size: 12px;
}}

/* ===== Dialog ===== */
QDialog {{
    background-color: {t['background']};
}}

/* ===== Menu ===== */
QMenuBar {{
    background-color: {t['surface']};
    color: {t['text']};
    border-bottom: 1px solid {t['border']};
}}
QMenuBar::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}
QMenu {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border']};
}}
QMenu::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}

/* ===== Frame ===== */
QFrame#card {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}

/* ===== Tooltip ===== */
QToolTip {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['accent']};
    padding: 4px;
    border-radius: 4px;
}}
"""
