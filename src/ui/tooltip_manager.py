"""
tooltip_manager.py – Cycling multi-variant tooltips with four modes.

Modes
-----
  Normal        – helpful tips, cycles through 5 variants
  Off           – no tooltips
  Dumbed Down   – simplified/funny tips that gently mock the user
  No Filter 🤬 – extremely vulgar, profanity-filled, still helpful

Usage
-----
    mgr = TooltipManager(settings_manager)
    mgr.install_on_app(QApplication.instance())
    mgr.register(my_button, "add_files")

Tip variants are stored in this file.  Add new tip keys for new widgets.

The active mode is stored in settings under the key "tooltip_mode".
"""

# QtCore (QEvent, QObject) does not require a display server.
# QtWidgets (QToolTip) does – so it is imported lazily inside eventFilter.
from PyQt6.QtCore import QEvent, QObject

from ..core.settings_manager import SettingsManager as _SettingsManager

# ---------------------------------------------------------------------------
# Tip variant database
# ---------------------------------------------------------------------------

# Normal – 5 helpful variants per key
_NORMAL: dict[str, list[str]] = {
    "add_files": [
        "Click to add image files to the processing queue.",
        "Supports PNG, DDS, JPEG, BMP, TIFF, WEBP, TGA, ICO, and GIF.",
        "You can also drag and drop files directly into the list below.",
        "Hold Ctrl in the file dialog to select multiple files at once.",
        "Shortcut: Ctrl+O opens the Add Files dialog.",
    ],
    "add_folder": [
        "Click to add an entire folder of images at once.",
        "Enable 'Include subfolders' to recurse into nested directories.",
        "Only supported image formats are picked up automatically.",
        "Great for batch-processing large game asset folders.",
        "Shortcut: Ctrl+Shift+O opens the Add Folder dialog.",
    ],
    "clear_list": [
        "Removes all files from the queue. Does not delete files on disk.",
        "Files stay intact on disk – only the processing list is cleared.",
        "Useful when you want to start fresh without restarting the app.",
        "You can also right-click individual items to remove just one.",
        "Press Delete to remove selected items from the list.",
    ],
    "process_btn": [
        "Start processing all queued files with the current settings.",
        "Alpha adjustments are applied non-destructively (unless overwrite is on).",
        "Check the log below for per-file results after processing.",
        "Shortcut: F5 starts processing from anywhere in the Alpha Fixer tab.",
        "Large batches? The progress bar shows completion %. You can stop with Esc.",
    ],
    "stop_btn": [
        "Stop processing after the current file completes.",
        "In-progress file will finish; queued files will be skipped.",
        "Shortcut: Esc stops processing from anywhere.",
        "Files already processed will keep their changes.",
        "You can resume by clicking Process again.",
    ],
    "preset_combo": [
        "Choose a preset alpha profile for common platforms.",
        "Hover over each preset in the dropdown to see a detailed description.",
        "PS2 Full Opaque: uses alpha=128 (PS2 GS max).  Full Opacity: alpha=255 for PC.",
        "N64 / GameCube / Wii / PSP all expect fully opaque textures (alpha=255).",
        "Custom presets you save also appear here and have their description as a tooltip.",
    ],
    "save_preset": [
        "Save the current fine-tune settings as a named preset.",
        "Custom presets are stored in the app settings and persist between sessions.",
        "Name it something descriptive so you remember what it does.",
        "You can have as many custom presets as you want.",
        "Custom presets appear in the preset dropdown above.",
    ],
    "delete_preset": [
        "Delete the currently selected custom preset.",
        "Built-in presets (PS2, N64, etc.) cannot be deleted.",
        "Don't worry – you can always recreate a preset with the same settings.",
        "This action cannot be undone, so be sure before clicking.",
        "You can save it again with a different name if you change your mind.",
    ],
    "alpha_slider": [
        "Drag to set the alpha value (0 = transparent, 255 = opaque).",
        "This slider and the number box above are linked – they stay in sync.",
        "Only applies when 'Use preset' is unchecked.",
        "Mode 'set' replaces all alpha with this value.",
        "Mode 'multiply' scales existing alpha by (this / 255).",
    ],
    "threshold_spin": [
        "Only process pixels with alpha below this threshold.",
        "0 = process every pixel regardless of current alpha.",
        "Useful for preserving already-transparent areas.",
        "128 = only adjust pixels less than 50% opaque.",
        "255 = process only fully transparent pixels.",
    ],
    "clamp_min_spin": [
        "Clamp Min: pixels with alpha below this value are raised to this value.",
        "Used with clamp_min/clamp_max modes to enforce a minimum alpha floor.",
        "Example: set to 128 to ensure no pixel is more than 50% transparent.",
        "0 = no lower clamp (default). Increase to raise transparency floor.",
        "PS2 textures: set to 0 to preserve full transparency range.",
    ],
    "clamp_max_spin": [
        "Clamp Max: pixels with alpha above this value are lowered to this value.",
        "Used with clamp_max mode to cap the maximum alpha of the image.",
        "Example: set to 128 to replicate PS2's 0–128 alpha scale.",
        "255 = no upper clamp (default). Decrease to cap opacity.",
        "PS2 Normalize: set max to 128 if targeting PS2-accurate renderers.",
    ],
    "invert_check": [
        "Invert the alpha channel after applying the other operations.",
        "Flips opaque ↔ transparent across all processed pixels.",
        "Combine with threshold for creative masking effects.",
        "Useful for converting 'transparency maps' to 'opacity maps'.",
        "The result is: new_alpha = 255 − computed_alpha.",
    ],
    "binary_cut_check": [
        "Binary cut: pixels at or above the threshold become 255 (fully opaque); below become 0 (fully transparent).",
        "Produces hard-edge transparency with no soft gradients or anti-aliasing.",
        "The threshold spinbox above determines the cut point for binary mode.",
        "Useful for sprite textures that require crisp, clean alpha edges.",
        "Result: every pixel's alpha is either 0 or 255 — nothing in between.",
    ],
    "out_dir": [
        "Specify a custom output folder for processed files.",
        "Leave blank to save output alongside each source file.",
        "Processed files get a '_fixed' suffix by default.",
        "Use the Browse button to pick the folder visually.",
        "The folder will be created automatically if it doesn't exist.",
    ],
    "recursive_check": [
        "When enabled, subfolders inside the selected folder are also scanned.",
        "Useful for processing entire project trees of images in one go.",
        "Disable if you only want images directly in the selected folder.",
        "Works for both Add Folder in Alpha Fixer and the Converter tab.",
        "Deep nested directories are all included when this is checked.",
    ],
    "compare_widget": [
        "Drag the red ◀▶ handle left or right to compare before and after.",
        "Left side = original image.  Right side = processed image.",
        "The comparison updates automatically when you change settings.",
        "Select a file from the list above to start comparing.",
        "Works for any image format supported by the app.",
    ],
    "file_list": [
        "Files queued for alpha processing. Drag & drop files or folders here.",
        "Right-click any item to remove it from the queue.",
        "Press Delete to remove selected items.",
        "Select a file to see a before/after preview below.",
        "The counter shows how many files are in the queue.",
    ],
    "convert_btn": [
        "Convert all queued files to the selected output format.",
        "Shortcut: F5 starts conversion from anywhere in the Converter tab.",
        "Output files are placed in the configured output folder.",
        "Quality setting affects JPEG/WEBP output; ignored for lossless formats.",
        "Progress is shown in the bar below.",
    ],
    "format_combo": [
        "Choose the target image format for conversion.",
        "Hover over each item in the dropdown to see a description of that format.",
        "PNG: lossless with alpha.  JPEG: lossy, no alpha, small file.  WEBP: modern, small.",
        "DDS: DirectX GPU textures.  TGA: classic game/3D format with alpha.",
        "AVIF: cutting-edge, excellent compression.  QOI: fast lossless with alpha.",
    ],
    "quality_spin": [
        "Quality percentage for lossy formats (JPEG, WEBP).",
        "Higher = better quality, larger file. Lower = smaller file.",
        "100 = maximum quality (near-lossless for WEBP).",
        "85 is a good balance of quality and size for most uses.",
        "Ignored for lossless formats like PNG, BMP, and TGA.",
    ],
    "settings_btn": [
        "Open the Settings dialog to customize themes, effects, and behavior.",
        "You can change color themes, enable mouse trails, and adjust fonts.",
        "Settings are saved automatically and persist between sessions.",
        "You can also export/import your settings via the Settings menu.",
        "Shortcut: Ctrl+, opens Settings.",
    ],
    "theme_combo": [
        "Choose a visual theme for the application.",
        "Hover over each theme in the dropdown to see a description of its style.",
        "Themes change colors, button shapes, and enable unique click particle effects.",
        "Locked hidden themes show 🔓 when unlocked — earn them by clicking and processing files.",
        "You can create custom color themes using the color pickers in the Theme tab.",
    ],
    "theme_color_btn": [
        "Click this color swatch to change that part of the theme's color palette.",
        "The color picker opens — choose any color and it applies to the app immediately.",
        "Changes apply live so you can see exactly how the color looks in context.",
        "Save your custom color scheme using the Save Theme button below.",
        "Any of the 15 palette roles can be independently customised.",
    ],
    "effect_combo": [
        "Choose the click particle effect style for this theme.",
        "Each effect matches its theme — try Gore for blood, Galaxy for stars, Neon for lightning ⚡.",
        "Select 'Custom' to fire your own emoji as particles on every click.",
        "New effects: Fire 🔥 (rising flames), Ice ❄ (snowflakes), Panda 🐼, Sakura 🌸 (cherry blossoms).",
        "Mix and match: any color theme can use any effect style you like!",
    ],
    "custom_emoji": [
        "Type or paste emoji here and click Add to create custom click particles.",
        "Multiple emoji can be added at once — separate them with spaces.",
        "Custom emoji are used when the effect is set to 'Custom' in the Theme tab.",
        "Clear All removes all emoji from the list. Use Add to start fresh.",
        "Try 🐼 🎉 💥 or any emoji your system supports — sky's the limit!",
    ],
    "tooltip_mode_combo": [
        "Controls how tooltips appear throughout the application.",
        "Normal: cycles through 5 helpful tips per widget.",
        "Off: disables all tooltips.",
        "Dumbed Down: simplified tips with some light roasting.",
        "No Filter 🤬: extremely vulgar, funny, and still helpful.",
    ],
    "tooltip_style_combo": [
        "Controls the visual shape of tooltip boxes — separate from their text content.",
        "Auto follows the active theme (Gore gets angular, Fairy gets bubbly, etc.).",
        "Angular: sharp corners, left accent bar — clinical and precise.",
        "Bubbly: large rounded corners — soft and friendly.",
        "Rounded: medium corners — clean and modern.",
        "Icy: alternating corner radii — crystalline and asymmetric.",
        "Wavy: wide/narrow alternating corners — organic and flowing.",
        "Neon: monospace text, glowing accent border — cyberpunk terminal vibes.",
        "Classic: plain dark box, grey border — classic tooltip look.",
    ],
    "patreon_btn": [
        "Support development on Patreon!",
        "Your support funds new features and themes.",
        "Patrons get early access to new hidden themes.",
        "Even $1/month helps keep the panda well-fed 🐼",
        "Visit patreon.com/c/DeadOnTheInside",
    ],
    "use_theme_sound": [
        "Play a click sound that matches the active theme.",
        "Gore theme gets a deep thud; Panda gets a soft chime; Alien gets a bright ping.",
        "Each of the 7 sound profiles is tailored to the theme's personality.",
        "When disabled your custom .wav (or the default blip) is used instead.",
        "Enable 'Use theme sound' for the full immersive themed experience.",
    ],
    "alpha_fixer_tab": [
        "The Alpha Fixer tab — fix and adjust transparency in image files.",
        "Add files, pick a preset or manual mode, then hit Process (F5). Ctrl+1 to jump here.",
        "Live preview shows you the before/after side by side.",
        "Presets include PS2, GameCube, N64, and PSP alpha corrections.",
        "You can also fine-tune R/G/B/A channel deltas individually.",
    ],
    "converter_tab": [
        "The Converter tab — convert images between different file formats.",
        "Supports AVIF, BMP, DDS, GIF, ICO, JPEG, PCX, PNG, PPM, QOI, TGA, TIFF, WEBP. Ctrl+2 to jump here.",
        "Set quality and whether to keep original metadata.",
        "Batch convert whole folders at once with the Add Folder button.",
        "Resize and suffix options let you process files without overwriting originals.",
    ],
    "history_tab": [
        "The History tab — browse your recently processed files. Ctrl+3 to jump here.",
        "Click any entry to see what was processed and where the output went.",
        "History is saved between sessions so you can track past work.",
        "Use this to verify a batch job completed without errors.",
        "History auto-refreshes each time you switch to this tab.",
    ],
    "history_refresh_btn": [
        "Refresh the history list to show the latest processing sessions.",
        "Click this after a batch run to see the new entries appear.",
        "History updates automatically when you switch tabs, but this forces a reload.",
        "Useful if you just finished a batch and want to verify the results.",
        "Pulls the latest session logs from settings storage.",
    ],
    "history_export_btn": [
        "Export the currently visible history sub-tab to a CSV file.",
        "Saves Time, Format/Preset, file counts, and file names to a .csv file.",
        "Useful for auditing batch runs, tracking files processed, or making a backup log.",
        "Only the active sub-tab is exported — switch to Converter or Alpha Fixer first.",
        "The CSV is standard format and can be opened in Excel, Google Sheets, or any text editor.",
    ],
    "history_clear_btn": [
        "Clear all history entries for both the Converter and Alpha Fixer tabs.",
        "This permanently removes past session records — there's no undo.",
        "Use this to clean up after testing or to reset your session log.",
        "Only clears the history list, not any processed files on disk.",
        "Your settings and presets are NOT affected — just the history log.",
    ],
    "history_conv_sub": [
        "The Converter sub-tab lists every batch conversion session you have run.",
        "Each row shows the time, output format, total file count, successes, and errors.",
        "The last column shows the first 10 file names processed in that session.",
        "Rows with errors are highlighted in yellow so they stand out.",
        "History entries are kept for the last 50 sessions.",
    ],
    "history_alpha_sub": [
        "The Alpha Fixer sub-tab lists every alpha-fixing batch session you have run.",
        "Each row shows the timestamp, preset or mode used, file count, and error count.",
        "Rows with errors are highlighted in yellow — click to review what went wrong.",
        "The first 10 file names are shown for quick reference.",
        "History is saved automatically at the end of every batch run.",
    ],
    "history_conv_tree": [
        "Each row is one converter batch run. Columns: time, format, files, successes, errors.",
        "Hover the column headers for details on what each column means.",
        "Yellow rows contain errors — these batches had at least one file that failed to convert.",
        "Click any column header to sort the list by that column.",
        "Double-click a row to expand the file list (first 10 files shown in the last column).",
    ],
    "history_alpha_tree": [
        "Each row is one alpha-fix batch run. Columns: time, preset/mode, files, successes, errors.",
        "Hover column headers for descriptions of what each column tracks.",
        "Yellow rows had errors — check the preset or file format if errors are appearing.",
        "The 'Preset / Mode' column shows 'manual' when fine-tune was used without a preset.",
        "History is capped at 50 entries. Older runs are removed as new ones are added.",
    ],
    "history_conv_summary": [
        "Summary of your converter history — total sessions and files converted.",
        "Shows aggregate counts across all logged conversion batches.",
        "Total files = all files submitted. ✔ OK = successfully converted. ✘ Err = failures.",
        "A high error count usually means unsupported input formats or permission issues.",
        "Counts reset when you clear history using the 'Clear All History' button.",
    ],
    "history_alpha_summary": [
        "Summary of your alpha-fix history — total sessions and files processed.",
        "Shows aggregate counts across all logged alpha-fix batches.",
        "Total files = all files submitted. ✔ OK = processed cleanly. ✘ Err = failures.",
        "Errors can mean the file couldn't be opened or saved — check paths and formats.",
        "Counts reset when you clear history using the 'Clear All History' button.",
    ],
    "settings_theme_tab": [
        "The Theme tab: choose a preset theme or build your own custom color scheme.",
        "Click any color swatch to change that part of the app's palette — changes apply live.",
        "Export your custom theme to share it, or import a theme someone else made.",
        "Hidden themes appear here once you unlock them — shown with 🔓 prefix.",
        "Use the search box to filter themes when you have many custom ones saved.",
    ],
    "settings_general_tab": [
        "The General tab: configure click effects, trails, cursor, sounds, font, and tooltip style.",
        "All settings apply immediately — no need to click Save or Apply.",
        "Trail and cursor options include theme-matched styles that change automatically with the theme.",
        "Tooltip Mode changes how tips are written — Normal, Dumbed Down, or No Filter.",
        "Font size, sound effects, and reset options are also here.",
    ],
    "alpha_file_count_lbl": [
        "Shows how many files are in the list and the keyboard shortcuts to run or stop.",
        "F5 = start processing. Escape = stop a running batch. Ctrl+O = open files.",
        "File count updates every time you add or remove files from the list.",
        "During processing this label shows per-file progress and ETA for large batches.",
        "Add files by dragging them here, using the Add Files button, or Ctrl+O.",
    ],
    "conv_file_count_lbl": [
        "Shows how many files are queued for conversion and the keyboard shortcuts.",
        "F5 = start converting. Escape = stop. Ctrl+O = add files. Ctrl+Shift+O = add folder.",
        "File count updates when you add or remove files from the queue.",
        "During conversion this shows per-file progress and ETA for large batches.",
        "Files can also be added by dragging them directly onto the list.",
    ],
    "processing_log": [
        "The log panel shows real-time messages from the processing worker.",
        "Each line shows a file path and whether it succeeded (✔) or failed (✘).",
        "Error messages include the reason — useful for diagnosing why a file failed.",
        "Scroll up to see earlier messages; the log auto-scrolls to the latest line.",
        "The log is cleared each time you start a new batch run.",
    ],
    "processing_progress": [
        "The progress bar fills as each file in the batch is processed.",
        "It goes from 0 % to 100 % over the course of the current batch.",
        "Stays at 100 % after the batch completes until you start a new one.",
        "For large batches the ETA is shown in the file count label above.",
        "Jumps ahead quickly for small files; slower for large images.",
    ],
    "alpha_status_lbl": [
        "Shows the current status of the Alpha Fixer — Ready, processing, or a done summary.",
        "After a batch finishes this shows the count of successes and failures.",
        "Reads 'Ready.' when no batch is running and files can be added or settings changed.",
        "Shows '✔ N succeeded, ✘ M failed' after each completed batch.",
        "Processing files with errors shows the failure count here — check the log for details.",
    ],
    "conv_status_lbl": [
        "Shows the current status of the Converter — Ready, converting, or a done summary.",
        "After a batch finishes this shows the count of successes and failures.",
        "Reads 'Ready.' when no batch is running.",
        "Shows '✔ N succeeded, ✘ M failed' after each completed conversion batch.",
        "Check the log panel below for per-file details if errors occurred.",
    ],
    "theme_search": [
        "Type part of a theme name here to instantly filter the theme dropdown.",
        "Useful when you have many saved custom themes and want to find one quickly.",
        "Clears back to the full list as soon as you delete your search text.",
        "Press the × button on the right of the field to clear the filter.",
        "Search is case-insensitive — 'ocean' will match 'Deep Ocean' and 'Ocean Blue'.",
    ],
    "sound_check": [
        "Enable or disable click sound effects throughout the app.",
        "Sounds play on button clicks and other interactions.",
        "You can set a custom .wav file in the box below this checkbox.",
        "Leave the sound path blank to use the built-in click sound.",
        "Disable if you're in a library, or just hate fun.",
    ],
    "trail_check": [
        "Toggle the mouse trail overlay on or off.",
        "The trail draws a colored streak wherever your cursor moves.",
        "Use the Trail Color button below to change the trail color.",
        "Trail is rendered on a transparent overlay that ignores clicks.",
        "Turn it on for extra visual flair — it's surprisingly satisfying.",
    ],
    "trail_color": [
        "Click to pick the color for the mouse trail effect.",
        "Any RGB color works — go neon green, blood red, galaxy blue…",
        "The color updates live on the trail overlay after you apply settings.",
        "Pair with a matching theme for maximum visual coherence.",
        "Hot pink and rainbow theme: chaotic perfection.",
    ],
    "trail_style": [
        "Choose the visual style of the mouse trail.",
        "Ribbon/Noodle draws a smooth connected line through the mouse path.",
        "Comet draws a tapered tail — bright head, fading end.",
        "Fairy/Wave/Sparkle use themed emoji that float and fade along the trail.",
        "Dots is the classic default — small fading circles.",
    ],
    "use_theme_trail": [
        "When enabled, the trail color is chosen automatically to match the active theme.",
        "Fairy Garden switches the trail to a sparkling emoji fairy-dust mode (✨💫⭐).",
        "Themes using ocean/mermaid/ripple effects get a wave emoji trail (🫧💧🌊🐠).",
        "Themes using ice/sparkle effects get a crystal sparkle emoji trail (✦❄✧💎).",
        "Disable to manually control the trail color with the picker above.",
    ],
    "trail_length_slider": [
        "Controls how many trail points are kept — longer trail or shorter trail.",
        "10 = ultra-short snappy trail; 200 = long lingering ghost trail.",
        "Long trails look amazing with the ribbon or comet styles.",
        "Short trails feel crisp and responsive. Long trails feel dramatic.",
        "Slide right for a longer trail, left for a shorter one.",
    ],
    "trail_fade_slider": [
        "Controls how fast the trail fades from full opacity to invisible.",
        "1 = very slow fade (trail lingers a long time); 10 = instant snap-fade.",
        "Slow fade + long length = dramatic ghost trail effect.",
        "Fast fade + short length = barely-there subtle sparkle.",
        "Combine with Intensity to get the exact look you want.",
    ],
    "trail_intensity_slider": [
        "Controls the maximum brightness/opacity of the trail (10–100%).",
        "100% = trail is fully visible at its brightest; 10% = very faint ghost trail.",
        "Low intensity is great for a subtle hint of trail without distraction.",
        "Full intensity looks best with vivid theme colors.",
        "Pair high intensity with slow fade for maximum drama.",
    ],
    "cursor_combo": [
        "Change the mouse cursor shape used throughout the application.",
        "Default is the standard arrow. Cross gives you a precision crosshair.",
        "Pointing Hand looks like you're about to poke the screen.",
        "Open Hand is great for a relaxed, browsing feel.",
        "Cursor changes apply immediately when you click Apply & Close.",
    ],
    "use_theme_cursor": [
        "When enabled, the cursor automatically matches the active theme.",
        "Otter Cove + Galaxy Otter get a 🤘 rock-on emoji cursor.",
        "Neon, Gore, Galaxy, Volcano, and Arctic use a precision crosshair.",
        "Panda themes and Rainbow Chaos use a pointing hand.",
        "Overrides the manual Cursor Style selector above.",
    ],
    "font_size": [
        "Adjust the global font size (in points) for all text in the app.",
        "Range is 8–24 pt. Default is 10 pt for a clean, compact look.",
        "Increase if you're squinting at the screen. We don't judge.",
        "Decrease if you want to cram more information on screen.",
        "Changes take effect immediately after clicking Apply & Close.",
    ],
    "click_effects_check": [
        "Enable or disable the per-theme click particle effects.",
        "When enabled, clicking anywhere spawns themed particles at the cursor.",
        "Each theme has its own effect — bats, blood, stars, pandas, etc.",
        "Disable if particles are distracting during heavy batch work.",
        "You can also change the effect style in the Theme tab above.",
    ],
    "use_theme_effect": [
        "When enabled, the click effect automatically matches the active theme.",
        "Gore gets blood splatter, Bat Cave gets bats, Ocean gets bubbles — auto-selected.",
        "Overrides the manual Effect Style selector below.",
        "Disable to pick your own effect style regardless of the active theme.",
        "Each of the 40 themes has its own default effect hard-coded to match its vibe.",
    ],
    "save_custom_theme": [
        "Save the current colour values as a new custom theme.",
        "Give it a name and it will appear in the theme dropdown for future use.",
        "Custom themes are saved to the settings INI file next to the application.",
        "You can save as many custom themes as you like — they persist between sessions.",
        "Export the saved theme as JSON using the Export button for sharing or backup.",
    ],
    "delete_custom_theme": [
        "Delete the currently selected custom theme.",
        "Only custom (saved) themes can be deleted — built-in preset themes are protected.",
        "The deletion is permanent. Export first if you want to keep a backup.",
        "After deleting, the app switches to the default Panda Dark theme.",
        "You cannot delete the active theme — switch to another theme first.",
    ],
    "export_custom_theme": [
        "Export the currently selected theme as a JSON file for sharing or backup.",
        "The JSON file contains all colour values and effect settings for the theme.",
        "Share it with others — they can import it using the Import button.",
        "Exported JSON files can also be edited in a text editor for fine-tuning.",
        "Works for both preset and custom themes.",
    ],
    "import_custom_theme": [
        "Import a theme from a JSON file exported by this app.",
        "The file must contain background, surface, primary, accent, and text colour values.",
        "The imported theme is saved immediately and applied on import.",
        "Downloaded community themes can be imported this way.",
        "Invalid files are rejected with an error message — nothing is overwritten.",
    ],
    "sound_path": [
        "Path to a custom .wav file to use as the click sound.",
        "Leave blank to use the built-in default click sound.",
        "The path is stored in the settings INI file next to the application.",
        "WAV files work best; other formats may not be supported on all platforms.",
        "Click Browse… to navigate to a file rather than typing the path manually.",
    ],
    "sound_browse": [
        "Open a file browser to choose a custom .wav sound file.",
        "Navigates to the currently configured path if one is already set.",
        "Select a .wav file and its path is inserted into the sound path field.",
        "The sound plays immediately when you click anything in the app.",
        "Leave the sound path empty to revert to the built-in default sound.",
    ],
    "reset_all_settings": [
        "Reset ALL application settings to their factory defaults.",
        "This includes theme, cursor, trail, sound, presets, and click effects.",
        "You will be asked to confirm before any changes are made.",
        "Processed files on disk are NOT affected — only the app configuration.",
        "Useful for a clean slate when testing or after a config gets corrupted.",
    ],
    "mode_combo": [
        "Choose how the alpha value is applied to each pixel.",
        "'set' replaces every pixel's alpha with the exact value specified.",
        "'multiply' scales existing alpha: new = old × (value / 255).",
        "'add' increases alpha by the value, clamping at 255.",
        "'subtract' decreases alpha by the value, clamping at 0.",
    ],
    "alpha_spin": [
        "Set the alpha value (0 = fully transparent, 255 = fully opaque).",
        "This box and the slider below are linked — they always stay in sync.",
        "In 'set' mode, all pixels get this exact alpha value.",
        "In 'multiply' mode, 255 = no change; lower values dim transparency.",
        "In 'add'/'subtract' mode, this amount is added to or removed from each pixel's alpha.",
    ],
    "use_preset_check": [
        "When checked, the preset settings override the manual fine-tune controls.",
        "Uncheck to fine-tune alpha manually using the mode, value, and threshold controls.",
        "Useful for quick platform-specific targets like PS2 or N64.",
        "The preset and fine-tune controls are mutually exclusive — only one applies at a time.",
        "Custom presets can be saved from the fine-tune settings using the Save button.",
    ],
    "red_spin": [
        "Adjust the Red channel of every pixel by this delta (\u2013255 to +255).",
        "Positive values make the image redder; negative values reduce red.",
        "Requires 'Apply RGBA adjustments' checkbox to be ticked.",
        "Combined with Green, Blue, and Alpha deltas for full RGBA correction.",
        "Useful for warming or cooling game textures, e.g. PS2 palette fixes.",
    ],
    "green_spin": [
        "Adjust the Green channel of every pixel by this delta (\u2013255 to +255).",
        "Positive values push pixels toward green; negative values reduce green.",
        "Requires 'Apply RGBA adjustments' checkbox to be ticked.",
        "Handy for fixing tinted textures from consoles like PSP or GameCube.",
        "Works together with Red, Blue, and Alpha adjustments for full RGBA control.",
    ],
    "blue_spin": [
        "Adjust the Blue channel of every pixel by this delta (\u2013255 to +255).",
        "Positive values increase blue; negative values decrease it.",
        "Requires 'Apply RGBA adjustments' checkbox to be ticked.",
        "Cool-tone correction: add Blue to shift warm textures into cooler hues.",
        "Combine all four RGBA deltas to achieve precise colour-matching.",
    ],
    "alpha_delta_spin": [
        "Shift the Alpha channel of every pixel by this delta (\u2013255 to +255).",
        "Positive values increase transparency; negative values decrease it.",
        "Requires 'Apply RGBA adjustments' checkbox to be ticked.",
        "Use this to globally brighten or darken transparency across a texture.",
        "Works alongside R/G/B deltas — all four channels adjust in one pass.",
    ],
    "apply_rgb_check": [
        "Enable RGBA channel adjustments in addition to the alpha processing.",
        "When unchecked, the Red/Green/Blue/Alpha delta spinboxes have no effect.",
        "Tick this to colour-correct textures while also fine-tuning their alpha.",
        "Alpha preset runs first, then the RGBA deltas are applied on top.",
        "Leave unchecked if you only need to fix transparency, not colours.",
    ],
    "suffix_edit": [
        "Append a suffix to output filenames to avoid overwriting originals.",
        "Example: '_fixed' → 'image.png' becomes 'image_fixed.png'.",
        "Leave blank to overwrite the source files in-place (use carefully!).",
        "Suffix is inserted before the file extension.",
        "Tip: use a unique suffix per batch to track which settings were applied.",
    ],
    "resize_check": [
        "Enable image resizing during conversion.",
        "When checked, the width and height fields below become active.",
        "Images are resized before format conversion.",
        "Leave unchecked to convert without changing dimensions.",
        "Width and height define the exact output resolution.",
    ],
    "lock_aspect_check": [
        "Lock the aspect ratio when resizing — change width and height auto-adjusts.",
        "Uses the selected file's original dimensions to compute the correct height.",
        "Uncheck to set width and height independently (may distort the image).",
        "Works per-file: the ratio is read from whichever file is selected at run time.",
        "Checked by default — most images look best with their original proportions preserved.",
    ],
    "width_spin": [
        "Target output width in pixels when resize is enabled.",
        "Set to 0 to preserve the original image width.",
        "Images wider than this value will be scaled down.",
        "Images narrower than this value will be scaled up.",
        "Best results when aspect ratio is kept consistent with the original.",
    ],
    "height_spin": [
        "Target output height in pixels when resize is enabled.",
        "Set to 0 to preserve the original image height.",
        "Images taller than this value will be scaled down.",
        "Images shorter than this value will be scaled up.",
        "Best results when aspect ratio is kept consistent with the original.",
    ],
    "out_dir_browse": [
        "Click to choose the output directory using a folder browser.",
        "You can also type the path directly into the text field.",
        "Leave blank to save output files next to each source file.",
        "A suffix in the filename field helps avoid overwriting originals.",
        "The selected path is remembered between sessions.",
    ],
    "keep_metadata_check": [
        "Preserve metadata (EXIF/ICC profiles) in the converted output file.",
        "EXIF data includes camera info, GPS tags, and other image properties.",
        "ICC profiles control how colors are displayed across different devices.",
        "Useful when converting photos that need to retain color accuracy.",
        "Leave unchecked when processing game textures that don't need metadata.",
    ],
    "before_stats_panel": [
        "Alpha channel statistics for the BEFORE (original) image.",
        "min = darkest alpha value (0 = fully transparent pixel found).",
        "max = brightest alpha value (255 = fully opaque pixel found).",
        "mean = average alpha across all pixels — lower means more transparency.",
        "Use these values to tune the clamp min/max and threshold spinboxes.",
    ],
    "after_stats_panel": [
        "Alpha channel statistics for the AFTER (processed) image.",
        "Compare with the BEFORE panel to see how your settings changed the alpha.",
        "min / max / mean update every time you adjust the fine-tune controls.",
        "A max of 255 and high mean means most pixels are now fully opaque.",
        "If min=0 and max=0, every pixel was set to fully transparent — check settings.",
    ],
    "rom_banner": [
        "Game/ROM folder detected! The app identified the console based on folder structure.",
        "PS2 folders often contain SLUS_xxx files or SYSTEM.CNF — these are recognised automatically.",
        "GameCube/Wii: boot.bin and bi2.bin are fingerprints the detector looks for.",
        "The disc ID (e.g. SLUS-20626) can be used to look up game info online.",
        "If cover art is found in your emulator's covers directory, the path is shown here.",
    ],
}

# Dumbed Down – mocking the user for needing tooltips, but still informative
_DUMBED: dict[str, list[str]] = {
    "add_files": [
        "You click this button. Files appear. It's amazing, really.",
        "It says 'Add Files'. That means… it adds files. You're doing great.",
        "Wow, you're hovering over Add Files. Incredible deductive reasoning.",
        "Congratulations! This button does exactly what it says. Wild, right?",
        "If you can't figure this out you should maybe try MS Paint first.",
    ],
    "add_folder": [
        "It's like Add Files but for a WHOLE FOLDER. Mind = blown.",
        "A folder. You know, that thing with the yellow icon. Yeah, that.",
        "Add Folder: for when you have so many images you need a whole folder.",
        "Yes, you can put multiple files in a folder. Yes, this adds them all.",
        "Subfolders too! It goes all the way down. Like a turtle tower.",
    ],
    "clear_list": [
        "Click to make the list empty. Files on disk are fine, don't panic.",
        "This removes things from the LIST. Not from your computer. Breathe.",
        "Clear = gone from the list. Still on disk. You're okay.",
        "It's like closing a tab. The website still exists. You're welcome.",
        "Congratulations on finding the Clear button. We're so proud of you.",
    ],
    "process_btn": [
        "Green means go! Click it and things will HAPPEN.",
        "This is the 'do the thing' button. Click it. Do the thing.",
        "Pressing this button starts processing. Shocking, I know.",
        "F5 also works. You know, if clicking is too much effort.",
        "The progress bar will fill up. Exciting. Wait for it. Waaait.",
    ],
    "stop_btn": [
        "Having second thoughts? This button stops things. Use it wisely.",
        "STOP! Hammertime. Or just processing. Either works.",
        "Like a pause button but it actually stops. Details matter.",
        "Changed your mind? No judgment. Just click this.",
        "Esc also works if the button is too far away for your little hands.",
    ],
    "preset_combo": [
        "A preset is a saved recipe. You pick one. It applies. Simple.",
        "Hover each item in the dropdown to see exactly what it does. We wrote descriptions.",
        "PS2 makes it half transparent (128). N64 makes it fully opaque (255). There ya go.",
        "If you don't know which to pick, PS2 is usually a safe bet.",
        "You can also make your own. Click 'Save'. Mind blown, I know.",
    ],
    "alpha_slider": [
        "Drag left for more transparent, right for more opaque. Basic stuff.",
        "0 = invisible. 255 = solid. Everything else is in between. Crazy.",
        "This slider controls how see-through the image is. Slide it. Go on.",
        "The number box does the same thing. Pick your poison.",
        "Alpha is fancy for 'transparency'. Now you know. You're welcome.",
    ],
    "threshold_spin": [
        "Higher number = more pixels get processed. Lower = fewer. Simple math.",
        "Set to 0 to process ALL pixels. Set to 255 to process almost NONE.",
        "Threshold means 'the line you draw'. Pixels above it are ignored.",
        "If you're confused, just leave it at 0. It works fine.",
        "Yes, you can type a number in there. No, it won't break anything.",
    ],
    "clamp_min_spin": [
        "Pixels below this alpha get raised to it. Like a floor for transparency.",
        "Set to 0 and it does nothing. Set higher and no pixel goes below that.",
        "Pair with clamp_max to squeeze the alpha into a specific range.",
        "128 = nothing gets more transparent than 50%. Useful for some PS2 stuff.",
        "Leave at 0 if you don't need a floor. Most people do.",
    ],
    "clamp_max_spin": [
        "Pixels above this alpha get lowered to it. It's a ceiling for opacity.",
        "Set to 255 and it does nothing. Lower it and nothing exceeds that value.",
        "128 = nothing gets more opaque than 50%. That's the PS2 range.",
        "Pair with clamp_min for tight alpha range control.",
        "Leave at 255 if you don't need a cap. Easy default.",
    ],
    "invert_check": [
        "Check this to flip transparent ↔ opaque. It's like turning inside out.",
        "Invert = opposite. What was transparent is now opaque. Simple.",
        "This one's a bit advanced. You sure you need it? No pressure.",
        "Unchecked = normal. Checked = opposite. There you go.",
        "Use with threshold for fancy effects you can pretend you intended.",
    ],
    "binary_cut_check": [
        "Binary cut: pixels above the threshold get set to 255 (solid). Below get set to 0 (invisible).",
        "Check this when you need hard edges with no soft transparent bits in between.",
        "The threshold value above determines who lives (255) and who dies (0).",
        "Great for retro sprites and game textures that need clean crisp edges.",
        "On = binary mode. Off = soft gradients allowed. Pick based on your texture needs.",
    ],
    "out_dir": [
        "Where do you want the fixed files to go? Type or browse. Simple.",
        "Leave blank and they go in the same folder as the originals. Easy.",
        "Browse = open a folder picker. Typing also works if you remember paths.",
        "Output folder is just where the results end up. You got this.",
        "Pro tip: create a folder called 'FIXED' first. Very professional.",
    ],
    "recursive_check": [
        "Check this to include ALL subfolders. Uncheck to stay shallow.",
        "Subfolders. It goes deeper. Check it. Or don't. Your call.",
        "Recursive means it digs through ALL your nested folders. Very thorough.",
        "Leave it checked unless you specifically want only the top folder. Simple.",
        "Subfolders go in. All of them. If that's what you want, check it.",
    ],
    "compare_widget": [
        "See that red handle? Drag it. Look at the pretty before/after.",
        "Left = old. Right = new. Drag the handle. That's it. That's the tip.",
        "The handle is that red vertical line. Drag it. We believe in you.",
        "Before on the left. After on the right. Like a before/after photo. Wild.",
        "Pick a file from the list first! Nothing to compare if nothing's loaded.",
    ],
    "file_list": [
        "This is where your files live. Drag some in. Or use the buttons above.",
        "The list shows your files. Click one to preview it. Revolutionary.",
        "Right-click to remove a file. Or press Delete. You have options.",
        "If the list is empty, you should probably add some files first.",
        "Files go IN here, then you click Process. That's the whole thing.",
    ],
    "convert_btn": [
        "Convert = change format. Click it. Watch the magic.",
        "This is the Convert button. You're converting things. It makes sense.",
        "Formats change here. PNG, JPEG, DDS – they're just file types. Easy.",
        "F5 also works. Just in case clicking is still too hard.",
        "Progress bar goes up. Files come out as the new format. Wow.",
    ],
    "format_combo": [
        "PNG is usually the right answer. Just pick PNG.",
        "These are image formats. PNG = good. JPEG = compressed. DDS = games.",
        "Hover over each format in the list to see what it actually does. Helpful!",
        "They're just containers. Like choosing between a bag and a box.",
        "WEBP is like PNG but smaller. Try it. Live on the edge.",
    ],
    "quality_spin": [
        "Higher = prettier but bigger file. Lower = uglier but smaller. Tradeoffs.",
        "100 is max quality. 1 is potato quality. 85 is for normal people.",
        "Quality only matters for JPEG and WEBP. For PNG it does nothing.",
        "If you're unsure, leave it at 90. It's fine. It's always fine.",
        "Move the number up or down. The changes are minor, don't overthink it.",
    ],
    "settings_btn": [
        "This opens Settings. Where you make the app look different.",
        "Themes! Sounds! Effects! It's all in here. Knock yourself out.",
        "Ctrl+, also works. There are two ways to do everything.",
        "You can break nothing in settings. Well, almost nothing.",
        "Settings: where you can make it look extra fancy for no reason.",
    ],
    "theme_combo": [
        "Choose pretty colors. There's blood and bats and rainbows. You're welcome.",
        "Hover each theme in the dropdown to see what it looks like. Descriptions exist.",
        "Bat Cave makes bats fly across the screen. Because why not.",
        "Gore theme has blood splatter. It's... tasteful. Mostly.",
        "Rainbow Chaos will do things to your eyes. You've been warned.",
    ],
    "theme_color_btn": [
        "Color swatch. Click it. Color picker opens. Pick a color. App changes. Wow.",
        "This button IS the color. Click it to change the color it represents.",
        "Changes apply instantly. If it looks bad, click again and pick a better color.",
        "Save your changes with the Save Theme button. Or don't. Live dangerously.",
        "15 different colors you can customize. Yes, all of them. One at a time.",
    ],
    "effect_combo": [
        "It's the sparkle chooser. Pick how things explode when you click.",
        "Yes, you can pick which particles fly out — it's the dropdown above.",
        "Select 'Custom' and then add your own emoji in the section below.",
        "New ones: Fire 🔥, Ice ❄, Panda 🐼, Sakura 🌸. Each one does a different thing. Apparently this needs explaining.",
        "Press Apply and Close. The sparkles change. That's it. You did it.",
    ],
    "custom_emoji": [
        "Paste emoji in the box. Click Add. Watch them fly when you click stuff.",
        "Yes, custom emoji. Yes, you can add your own. The box is right there.",
        "Type emoji like 🐼 and click Add. It's literally that simple.",
        "Clear All removes them all. Add adds new ones. That's all there is to it.",
        "The emoji you add will shoot out when you click things. Congrats.",
    ],
    "tooltip_mode_combo": [
        "This changes how tooltips work. You're reading one right now. Meta.",
        "Pick 'Off' to stop being told things. We won't take it personally.",
        "Dumbed Down mode is this mode. How's it going? Feeling talked down to?",
        "No Filter 🤬 mode is the BEST mode. Trust us on this one.",
        "Normal mode has 5 helpful tips per widget. Very sensible. Boring.",
    ],
    "tooltip_style_combo": [
        "Tooltip Style. Separate from the mode — this one changes how the box looks.",
        "Auto = follows the theme. Gore gets pointy corners. Fairy gets big round ones.",
        "Pick Angular if you like sharp things. Bubbly if you like round things.",
        "Neon makes tooltips look like hacker terminal output. Very cool.",
        "Classic is the boring normal box. You probably already know what it looks like.",
    ],
    "save_preset": [
        "Click Save, type a name. Wow, technology.",
        "Save your settings as a preset. It'll be there next time. Magic.",
        "Name it something memorable. 'aaa' works but you'll regret it.",
        "Saved presets show in the dropdown. Very useful. Very exciting.",
        "You can have as many presets as you want. Go nuts.",
    ],
    "delete_preset": [
        "This deletes the preset. Gone. The built-in ones are safe though.",
        "Delete. It removes the preset. There's literally nothing more to say.",
        "You can recreate it. It's a few clicks. Breathe.",
        "Confirm the dialog. It's deleted. The end.",
        "Only your custom presets can be deleted. The built-ins survive everything.",
    ],
    "patreon_btn": [
        "Click this to give the dev money. Very simple concept.",
        "Patreon. Money. Developer. Creates more stuff. You can help.",
        "Think of it as a tip jar. For software. For a panda.",
        "Your dollar could fund the next hidden theme. Worth it.",
        "Even $1 helps! That's like… one coffee. You can do that.",
    ],
    "use_theme_sound": [
        "Tick this. Theme plays its own sound. Untick. Normal sound. That's it.",
        "Theme sound = each theme gets its own click noise. Gore = thud. Panda = ping. Simple.",
        "Think of it as sound effects that match what you're looking at. Toggle. Done.",
        "Gore theme goes THUD. Fairy garden goes tinkle. Ice cave goes crystalline ding. Enable it.",
        "It's just sounds. The button says 'theme sound'. It plays theme sounds. You're overthinking.",
    ],
    "alpha_fixer_tab": [
        "This is the Alpha Fixer tab. You click on it. You were already on it. Ctrl+1 also works.",
        "Alpha Fixer: fixing transparency since whenever this app was made.",
        "The tab with the picture frame icon. Pretty self-explanatory, no?",
        "Presets, sliders, batch processing — all the exciting alpha-fixing action lives here.",
        "If you needed a tooltip to find the Alpha Fixer tab you might be in trouble.",
    ],
    "converter_tab": [
        "The Converter tab converts files. Mind-blowing, I know. Press Ctrl+2 to jump here.",
        "You put files in, you pick a format, and… it converts them. Shocker.",
        "For when Alpha Fixer is too exciting and you just want boring format changes.",
        "Supports like a dozen formats. PNG, JPEG, WEBP, etc. Click it already.",
        "Converter. Con-vert-er. Files go in one format, come out another. There you go.",
    ],
    "history_tab": [
        "History. Like browser history but for files. And we judge you less. Ctrl+3 to jump here.",
        "Shows what you've processed recently. In case you forgot. Which you did.",
        "Click it to see recent processing logs. Exciting stuff.",
        "If something went wrong this tab might tell you what. Might.",
        "History tab: for when you can't remember what you broke.",
    ],
    "history_refresh_btn": [
        "Refresh. Click it. History updates. Not hard.",
        "Reloads the history list. Just like it says on the button.",
        "Click to reload recent sessions. It's a refresh button. Very standard.",
        "If your new entries aren't showing, click this. Problem solved.",
        "Refresh button. Does refreshing. You got this.",
    ],
    "history_export_btn": [
        "Export the current history to a CSV file. Opens a save dialog. Very intuitive.",
        "Saves the visible sub-tab (Converter or Alpha Fixer) as a spreadsheet-friendly CSV.",
        "Open in Excel. Google Sheets. Notepad. Whatever. It's just a CSV.",
        "Exports only the currently active sub-tab. Switch tabs to export the other one.",
        "Good for auditing big batches or sending logs to someone who doesn't have this app.",
    ],
    "history_clear_btn": [
        "This erases all history. Gone. Forever. No takebacks.",
        "Clear All History = delete everything in this list. Simple.",
        "Clears your processing history. Doesn't delete actual files, just the records.",
        "Warning: this permanently wipes the log. Or don't, up to you.",
        "The nuclear option for your history tab. Use carefully.",
    ],
    "history_conv_sub": [
        "Converter history sub-tab. Lists your file conversion sessions. You convert files, it logs it.",
        "Each row = one conversion session. Time, format, files processed, errors. Simple.",
        "Yellow rows have errors in them. That's your hint something didn't go perfectly.",
        "You can see up to 50 sessions here. After that, old ones fall off the list.",
        "Converter sub-tab. Conversion logs. That's it.",
    ],
    "history_alpha_sub": [
        "Alpha Fixer history sub-tab. Lists your alpha-fixing sessions. Very niche, very useful.",
        "Shows what preset was used, how many files, how many errors. Pretty handy.",
        "If it's yellow, there were errors. Click Refresh to make sure it's up to date.",
        "Up to 50 sessions are logged. After 50, the oldest ones are removed.",
        "Alpha fix logs. For checking what you broke and when.",
    ],
    "history_conv_tree": [
        "This table shows your conversion sessions. Each row = one batch. Very simple concept.",
        "Yellow rows had errors. Something didn't convert. Usually the format's fault.",
        "Columns: time, format, total files, how many worked, how many didn't, file names.",
        "Hover the column headers if you're confused about what they mean.",
        "Sorted by time. Most recent batch is at the top. Probably.",
    ],
    "history_alpha_tree": [
        "Alpha fix session log. Each row = one time you ran a batch fix. There it is.",
        "Yellow rows had errors. Something exploded. Check your preset and file formats.",
        "Columns: time, which preset/mode, total files, successes, errors, file names.",
        "50 rows max. Older ones disappear. Like memories. Fleeting.",
        "Use this to figure out when you accidentally fixed the wrong files.",
    ],
    "history_conv_summary": [
        "Summary line below the converter tree. Totals for all logged sessions.",
        "Shows: total sessions + total files converted. Quick overview.",
        "If the error count is high, you should probably look into that.",
        "Resets to zero when you clear history. That's how resetting works.",
        "Aggregate stats. For the big picture. The forest, not the trees.",
    ],
    "history_alpha_summary": [
        "Summary line below the alpha tree. Totals for all logged alpha-fix sessions.",
        "Total sessions, total files, successes, errors. The overview.",
        "High error count = something is wrong. Low error count = well done you.",
        "Resets when you clear history. Very logical.",
        "Shows aggregate data, not per-run data. That's what the table above is for.",
    ],
    "settings_theme_tab": [
        "Theme tab. Pick colors. Make the app look like you want. Very self-explanatory.",
        "Click color swatches to change them. They update immediately. Magic.",
        "Unlocked themes show up here. Keep clicking stuff and they'll appear.",
        "Export = save your theme to a file. Import = steal someone else's. Fair game.",
        "Search box filters the theme list. Very useful when you've made 40 custom themes.",
    ],
    "settings_general_tab": [
        "General tab. Effects, sounds, trails, cursor, fonts. All the fun stuff.",
        "Everything applies immediately. No save button needed. Just change it and it happens.",
        "Trail, effect, and cursor combos have theme-matched options that auto-update.",
        "Tooltip Mode is here. Dumbed Down is honestly the funniest one.",
        "Font size, reset button, sound file — all buried in here. Explore.",
    ],
    "alpha_file_count_lbl": [
        "File count. How many files are in the list. Not rocket science.",
        "Shows file count + keyboard shortcuts. F5 = go. Esc = stop. Very intuitive.",
        "Add more files and the number goes up. Remove them and it goes down. Basic math.",
        "During processing this updates with progress + ETA for big batches.",
        "If it says 0 files, maybe add some files first. Just a suggestion.",
    ],
    "conv_file_count_lbl": [
        "Converter file count. How many things are waiting to be converted.",
        "F5 = convert. Esc = stop. Ctrl+O = add files. This label tells you everything.",
        "Watch the number go up as you add files. Very satisfying.",
        "Shows progress during conversion. ETA shows up for big batches (500+ files).",
        "If it says 0 files, add some. They won't add themselves.",
    ],
    "processing_log": [
        "The log. Where all the ✔ and ✘ messages live. Each line = one file.",
        "Errors show up here with a reason. Useful for knowing what you broke.",
        "Auto-scrolls to the latest message. You can scroll up to see the older ones.",
        "Clears at the start of every new batch. Fresh log, fresh start.",
        "If everything is ✔, you're golden. If it's all ✘, something is very wrong.",
    ],
    "processing_progress": [
        "Progress bar. Goes from empty to full. That's how progress bars work.",
        "0% = hasn't started. 100% = done. Everything in between = in progress.",
        "Stays at 100% when done because done is done and it's not going backwards.",
        "Fast for small files, slower for big ones. This is expected.",
        "ETA appears in the file count label above this. For the truly impatient.",
    ],
    "alpha_status_lbl": [
        "Status label. Tells you what the Alpha Fixer is currently doing.",
        "'Ready.' means nothing is running. Congratulations on your free time.",
        "Shows ✔ and ✘ counts after each batch. Green numbers are good. Red are bad.",
        "Check the log below if errors appear here. The log has the details.",
        "It changes during processing too. Watch it update in real time. Exciting.",
    ],
    "conv_status_lbl": [
        "Status label for the Converter. Like the one in Alpha Fixer but for converting.",
        "'Ready.' = idle. Numbers after = batch results.",
        "Shows how many converted successfully vs how many exploded.",
        "Log panel below has the per-file breakdown if anything went wrong.",
        "Green-ish = fine. Red-ish = not fine. You can figure it out.",
    ],
    "theme_search": [
        "Type a theme name here to filter the dropdown. Like searching but for themes.",
        "Type letters, the list gets shorter. Delete them, the list comes back.",
        "It's a search box. For themes. Not complicated.",
        "Can't find your custom theme? Type its name here. There it is.",
        "Filter themes. Type. Done. Very high-tech.",
    ],
    "sound_check": [
        "Check = sounds on. Uncheck = silence. Riveting decision.",
        "This enables the clickety-clack noises. You know what sounds are.",
        "Sounds. Noises. Enabled or disabled. This checkbox handles it.",
        "Custom sound goes in the box below. Or leave it blank for the default blip.",
        "If you uncheck it, the app goes quiet. Like a mime. A software mime.",
    ],
    "trail_check": [
        "Mouse trail means a colorful line follows your cursor. Decorative. Fancy.",
        "Check for trail. Uncheck for no trail. Easy decisions are fun.",
        "It follows your mouse and looks cool. That's the whole point.",
        "The color is set in the Trail Color picker below. Very logical.",
        "Turn it on. Wiggle your mouse. It's honestly quite pleasing.",
    ],
    "trail_color": [
        "Click this to pick a pretty color for the trail. Color picker appears. Amazing.",
        "It's a color for the trail. The trail that follows your mouse. Yes, that one.",
        "Click. Pick color. Done. This is genuinely that simple.",
        "Go wild. Neon pink. Boring gray. Radioactive green. Sky's the limit.",
        "The color only shows if Trail is enabled. You did check that, right?",
    ],
    "trail_style": [
        "This lets you change what the trail looks like. Dots, ribbon, comet, emoji.",
        "Ribbon = connected squiggle. Comet = bright head, fading tail. Dots = dots.",
        "Fairy/Wave/Sparkle use emoji that float around. They're cute. You're welcome.",
        "Pick whichever style you like. They all follow your mouse. That's the whole deal.",
        "Use theme trail (below) to skip this and auto-pick the right style for your theme.",
    ],
    "use_theme_trail": [
        "Check this and the trail changes color automatically for each theme. Smart.",
        "Fairy Garden gets sparkly emoji dust trail (✨💫⭐). Yes, really.",
        "Themes with ocean/mermaid effects get bubble trail (🫧💧🌊). Ice effects get crystals (✦❄✧).",
        "Uncheck to go back to manually picking a color. Boring but valid.",
        "Theme trail = automatic colors AND styles. Manual trail = DIY. Your call.",
    ],
    "trail_length_slider": [
        "Slide right = more trail points = longer trail. Slide left = shorter snappy trail.",
        "Think of it as 'how long is my tail'. Literally.",
        "200 points = long ghost trail. 10 points = tiny little blip behind your cursor.",
        "The slider controls length. The fade speed controls how fast it disappears.",
        "Long trail + slow fade = maximum ghosting effect.",
    ],
    "trail_fade_slider": [
        "How fast the trail fades: 1 = super slow, 10 = super fast.",
        "Slow = trail stays on screen a long time. Fast = trail vanishes quickly.",
        "Think of it as 'persistence'. High = persistent. Low = fast.",
        "Combo with the length slider to tune the exact feel you want.",
        "1 is basically a ghost trail. 10 is basically no trail.",
    ],
    "trail_intensity_slider": [
        "How bright/visible the trail is. 100% = full color. 10% = barely there.",
        "Low intensity = subtle hint that something was there.",
        "High intensity = bright vivid trail that stands out.",
        "Combine with a bold theme color for maximum pop.",
        "Turn it all the way down if you want the trail but don't want to notice it much.",
    ],
    "cursor_combo": [
        "Your cursor shape. You can change it. Here. With this dropdown.",
        "Default = normal arrow. The rest are slightly fancier arrows.",
        "Pointing Hand feels very 'I'm a web developer circa 2002'.",
        "Cross cursor is great for feeling like a precise, serious person.",
        "Pick one. Click Apply. Your cursor changes. Life continues.",
    ],
    "use_theme_cursor": [
        "Check this to let the theme decide your cursor. Hands off the wheel.",
        "Otter Cove gets 🤘. Because otters rock. That's the whole reason.",
        "When checked, the manual cursor dropdown above does literally nothing.",
        "Uncheck it if you want your boring arrow cursor back. Fair enough.",
        "Theme cursor = automatic. Manual cursor = your problem.",
    ],
    "font_size": [
        "Makes text bigger or smaller. Spinbox. Number. You know how this works.",
        "Higher number = bigger text. Lower number = smaller text. Math!",
        "8pt is tiny. 24pt is huge. 10pt is normal. Pick one.",
        "Squinting? Go bigger. Too big? Go smaller. The controls are right there.",
        "This changes fonts everywhere. In the app. Not on your computer.",
    ],
    "click_effects_check": [
        "This enables the fancy particles that explode when you click stuff.",
        "Checked = sparkles happen. Unchecked = no sparkles. Your choice.",
        "The particles match the theme. Bats, blood, stars… all configurable.",
        "Turn it off if the constant explosions are too distracting. Fair.",
        "Each theme has different particles. See Theme tab to change which ones.",
    ],
    "use_theme_effect": [
        "This makes the particles automatically match your theme. Pretty cool right?",
        "On = particles are automatic. Off = you pick them yourself from the list.",
        "Checked = app picks the right particles for you. Unchecked = your problem now.",
        "Gore theme? Blood. Bat Cave? Bats. You get it. This just does it automatically.",
        "Turn it off if you want to mix themes up. A panda theme with bat effects? Sure.",
    ],
    "save_custom_theme": [
        "Saves the current colors as a custom theme. Name it something clever.",
        "Press this to immortalize your color choices in the settings file.",
        "Custom themes persist. They're yours. They live in the INI file. They're real.",
        "Give it a name. Saves it. That's how saving works. You knew that.",
        "You can save as many themes as you want. Go nuts. Go absolutely nuts.",
    ],
    "delete_custom_theme": [
        "Deletes the selected custom theme. Gone. Poof. No undo.",
        "Only custom themes. You can't delete the built-in ones. They're protected.",
        "Click this, confirm the dialog, theme dies. Simple operation.",
        "Export first if you want a backup. Don't come crying later.",
        "Works on your user-created themes only. Leave the presets alone.",
    ],
    "export_custom_theme": [
        "Exports the selected theme to a JSON file. For sharing, or just hoarding.",
        "Click → pick a location → theme is saved as JSON. Easy.",
        "Share your masterpiece of a theme with others. Or keep it private. Whatever.",
        "JSON files can be imported on any machine with this app.",
        "Even imports back into this app. Very circular.",
    ],
    "import_custom_theme": [
        "Imports a theme from a JSON file. Someone made it, you imported it, welcome home.",
        "Click → find the JSON → theme appears in the list. Magic but actually just code.",
        "JSON must have the right color keys. Invalid file = rejected. Rules exist.",
        "Imported themes become your custom themes. You own them now.",
        "Community themes go in here. If the community makes themes. Which they might.",
    ],
    "sound_path": [
        "Path to your custom click sound. WAV file. Put the path here or browse.",
        "Leave it blank for the default 'blip' sound. Totally fine.",
        "Custom WAV path. For when the default sound isn't aggressive enough.",
        "Type the path or use Browse. Both work. Pick your adventure.",
        "If the path is wrong, the sound won't play. It'll just be silent. Sad.",
    ],
    "sound_browse": [
        "Opens a file browser. For finding WAV files. You know what a file browser is.",
        "Click this if typing the path seems like too much work. It is too much work.",
        "Navigate. Find WAV. Click Open. Done. Very advanced stuff.",
        "Find your custom click sound file here. WAV format.",
        "Browse = look through folders for a file. That's literally what it means.",
    ],
    "reset_all_settings": [
        "This resets EVERYTHING. Theme, sound, cursors, presets, all of it. Nuclear option.",
        "Warning: it asks first. So you'd have to be REALLY determined to mess yourself up.",
        "Reset. All. Settings. To. Default. It's in the button name. Very descriptive.",
        "Useful for when you break everything and just want to start over.",
        "Doesn't delete files on disk. Just resets the app config. Not THAT destructive.",
    ],
    "mode_combo": [
        "Pick how the alpha gets applied. 'set' is probably the one you want.",
        "Alpha mode. It changes how alpha is calculated. Science!",
        "You have: set, multiply, add, subtract, clamp_min, clamp_max. Fun times.",
        "'set' = everyone gets the same alpha. Very democratic.",
        "'multiply' = math happens. Less democratic. Still useful.",
    ],
    "alpha_spin": [
        "Type a number here. 0 = invisible. 255 = visible. Easy math!",
        "Alpha value. You know what alpha means? Great. Type it here.",
        "0 = you can't see it. 255 = you can see it perfectly. In between = somewhere.",
        "The slider below does the same thing. Use whichever one your hand can reach.",
        "This number applies to every pixel in the image. That's a lot of pixels.",
    ],
    "use_preset_check": [
        "Check this to use the preset above. Uncheck to do it manually. Wow.",
        "Preset = someone already did the thinking for you. Lucky you.",
        "Unchecked = you're in charge of the alpha. No pressure.",
        "If you checked this and nothing changed, try clicking Process first.",
        "This just decides which settings win: the preset up top or the sliders below.",
    ],
    "red_spin": [
        "This makes the image more red (positive) or less red (negative). Colors!",
        "Change the red channel. Add red: positive. Remove red: negative. Simple.",
        "Nothing happens unless you also check 'Apply RGBA adjustments'. Gotcha.",
        "Works together with green, blue, and alpha sliders for full RGBA control.",
        "Use it to fix weirdly-tinted game textures. Or make everything red. You do you.",
    ],
    "green_spin": [
        "This makes the image more green (positive) or less green (negative). Yep.",
        "Adjust green channel. Positive = more green. Negative = less green. Science!",
        "Nothing happens unless you also check 'Apply RGBA adjustments'.",
        "Works with red, blue, and alpha for complete color correction.",
        "Great for making everything look like it's in a forest. Or not.",
    ],
    "blue_spin": [
        "This makes the image more blue (positive) or less blue (negative). Cool.",
        "Adjust blue channel. Positive = more blue. Negative = less blue. Cold!",
        "Nothing happens unless you also check 'Apply RGBA adjustments'.",
        "Works with red and green for full color adjustment.",
        "Add blue to make things look colder. Remove blue for warm tones.",
    ],
    "alpha_delta_spin": [
        "Shift the alpha (transparency) of every pixel up or down. Simple!",
        "Positive = more opaque. Negative = more transparent. That's it.",
        "Nothing happens unless you also check 'Apply RGBA adjustments'.",
        "Works alongside R/G/B so you can fix color AND transparency in one go.",
        "Use this when every pixel needs a little more or less see-through-ness.",
    ],
    "apply_rgb_check": [
        "Check this to make the Red/Green/Blue/Alpha adjustments actually do something.",
        "Without this checked those RGBA spinboxes are just decoration.",
        "Enables color AND alpha correction on top of the alpha fix. Double the fun.",
        "Alpha preset runs first, then the RGBA deltas kick in. Order matters.",
        "If nothing looks different, check this box first. Yeah, that's why.",
    ],
    "suffix_edit": [
        "Type a word here to add it to the output filename. Or don't. Up to you.",
        "Example: '_fixed' turns 'pic.png' into 'pic_fixed.png'. Neat trick.",
        "Leave it blank and it'll overwrite your original. Hope you have backups.",
        "You probably want something here. '_fixed' or '_out' works great.",
        "It goes before the file extension. Like a name tag for your file.",
    ],
    "resize_check": [
        "Check this if you want the images to come out a different size.",
        "It enables the width and height boxes below, which were just sitting there.",
        "Unchecked = same size as the original. Checked = you're in control. Maybe.",
        "Resizing changes the dimensions. You probably knew that.",
        "This does not resize your brain. Just the images.",
    ],
    "lock_aspect_check": [
        "Lock aspect ratio. So when you change width, height updates automatically.",
        "Checked = proportional scaling. Unchecked = stretch mode. Your call.",
        "This reads the selected file's dimensions to compute the correct height.",
        "Checked by default because squishing images is usually bad.",
        "Lock = smart resize. Unlock = manual resize. Both are valid options.",
    ],
    "width_spin": [
        "Type the width you want. In pixels. Not centimetres. Pixels.",
        "0 means 'keep the original width'. Useful if you only want to change height.",
        "Width in pixels. Goes sideways. You know this.",
        "Put a number here if you want the image to be that many pixels wide.",
        "Big number = wide image. Small number = narrow image. Physics!",
    ],
    "height_spin": [
        "Type the height you want. In pixels. Up and down. You know how height works.",
        "0 means 'keep the original height'. Smart shortcut!",
        "Height in pixels. Goes up-down. The other direction from width.",
        "Put a number here for how tall you want the output image to be.",
        "Big number = tall image. Small number = short image. You've got this.",
    ],
    "out_dir_browse": [
        "Click this to find a folder. You know how to use a folder browser, right?",
        "Browse = look through folders. Click one. That's where your files go.",
        "You can type the path too. But clicking is more fun apparently.",
        "Pick a folder. Files go there. Revolutionary concept.",
        "This stores where your output files end up. Important button, honestly.",
    ],
    "keep_metadata_check": [
        "Check this to keep EXIF and ICC stuff in your converted files. Important for photo people.",
        "Metadata = camera settings, GPS, color profiles. Check if you need them, uncheck if not.",
        "Game textures? Don't care about EXIF. Photos? Probably check this.",
        "ICC profiles are what keep colors looking correct. Leave checked if you care about accuracy.",
        "Unchecked = smaller files, no extra info. Checked = full metadata intact. Simple.",
    ],
    "before_stats_panel": [
        "This shows the alpha stats BEFORE you did anything. Original image. Untouched.",
        "Min = the lowest alpha value. Max = the highest. Mean = the average. It's math. Basic math.",
        "These numbers tell you how transparent your original image is. Lower mean = more transparent.",
        "Compare this to the AFTER panel on the right to see if your settings are doing anything.",
        "If min and max are both 255, your image had zero transparency to begin with. Congrats?",
    ],
    "after_stats_panel": [
        "These are the alpha stats AFTER your fix ran. Right side. After. You get it.",
        "Compare with BEFORE on the left to see if your settings actually changed anything.",
        "If min/max/mean look the same as before, you might not have changed anything useful.",
        "Mean going up = more opaque. Mean going down = more transparent. Simple concept.",
        "If after stats show all zeros, you set everything to transparent. Check your settings.",
    ],
    "rom_banner": [
        "The app spotted a game folder! It recognized the console from file/folder names.",
        "PS2 = SLUS/SCUS files or SYSTEM.CNF. GameCube = boot.bin. N64 = .z64 files.",
        "The disc ID shown here can help you look up the game online or find cover art.",
        "This banner appears automatically when the tool detects a known game format.",
        "If it detected the wrong console, the folder structure might use common names.",
    ],
}

# No Filter 🤬 – vulgar, funny, profanity, but actually still helpful
_VULGAR: dict[str, list[str]] = {
    "add_files": [
        "Click this damn button and add your freaking images already. PNG, DDS, all that crap.",
        "Oh for fuck's sake, just click it. It adds files. What the hell were you waiting for?",
        "Drag your ass-backwards images in here or click 'Add Files'. Either works, genius.",
        "This bastard button opens a file dialog. Pick your shit and let's get processing.",
        "Ctrl+O also works, in case you're too damn lazy to click. Love you. 🐼",
        "Supports PNG, DDS, JPEG, BMP, TIFF, WEBP, TGA, ICO, GIF — basically every format you forgot existed.",
        "Holy shit you found the Add Files button! First try! Give yourself a medal, champ.",
        "It's literally just a file picker. Click. Select. Done. Why are you hovering here for so long?",
    ],
    "add_folder": [
        "Add a whole fucking folder at once. Because clicking one file at a time is for suckers.",
        "Got a billion images? Shove the whole damn folder in here. That's what this is for.",
        "Ctrl+Shift+O works too, smartass. One folder. All the images. Let's go.",
        "Enable subfolders if you've got a nested hellscape of directories. It handles it.",
        "It scans the entire fucking folder for supported images. Sit back and let it work.",
        "You have ONE folder with 10,000 images and you're adding them ONE AT A TIME? God help you. Use this.",
        "Recursive folder scanning. Goes deeper than your last relationship. Check the subfolder option too.",
        "Press this, pick your chaotic mess of a folder, and watch the app sort it all out. You're welcome.",
    ],
    "clear_list": [
        "Clear this shit out and start over. Your files on disk are FINE, calm the fuck down.",
        "Panic? Don't. This only clears the list, not your actual damn files.",
        "Starting fresh? Just nuke the list. It's not that serious.",
        "Delete key removes one item. This button removes all of it. Pick your chaos.",
        "It's FINE. Press it. Everything on disk stays. Go nuts.",
        "Oh no, wrong files! Hit this button and pretend it never happened. We won't tell anyone.",
        "The nuclear option for your file queue. Everything disappears. Nothing on disk changes. Deep breath.",
        "Clear the list, start fresh, add the right files this time. We believe in you. Sort of.",
    ],
    "process_btn": [
        "Hit this big-ass green button and make the magic happen. F5 also works, lazy.",
        "CLICK THE DAMN PROCESS BUTTON. This is literally what we've been building toward.",
        "Every file in that list is about to get its alpha fixed. Hell yeah. Let's GO.",
        "It'll process everything. The progress bar will fill up like a beautiful river of results.",
        "F5, motherfucker. Keyboard shortcuts exist for a reason.",
        "You've added the files, set the settings, and now you're HOVERING instead of clicking. Just press it!",
        "This is the big moment. All that setup. One click to become a legend. DO IT.",
        "All those files in the queue waiting to get their alpha fixed. One click. That's it.",
    ],
    "stop_btn": [
        "Changed your mind, chickenshit? Click Stop. The current file finishes first.",
        "Hit this if you screwed up the settings and need to abort. No judgment. Mostly.",
        "Esc also works. It stops without your cursor having to move an inch.",
        "The current file won't be half-processed. It finishes. Then everything stops.",
        "Stop is for cowards. Kidding. Stop whenever the hell you want.",
        "OH SHIT wrong settings! Mash this button! Current file finishes but the rest are saved.",
        "Esc key is faster than reaching for the mouse. Just saying. Also this button exists.",
        "Changed your mind halfway through? Valid. This button exists for chaotic decision-makers.",
    ],
    "preset_combo": [
        "Pick a damn preset. PS2 = alpha times 0.5 for PlayStation 2 textures. Classic.",
        "N64 sets alpha to 255 (fully fucking opaque). Old-school Nintendo vibes.",
        "Hover each item to see the full description. They're detailed. Written by someone who cared.",
        "Max Alpha makes everything opaque but keeps the channel. Subtle difference.",
        "Make your own preset with the fine-tune controls below. Save it. Name it creatively.",
        "PS2 Half Alpha is literally just multiplying by 0.502. Someone calculated this shit carefully.",
        "These presets were made by someone who reverse-engineered game consoles. Show some respect.",
        "Picking a preset is faster than figuring out PS2 alpha math yourself. Trust the preset.",
    ],
    "save_preset": [
        "Save your damn preset so you don't have to redo this every time.",
        "Name it something useful, not 'aaaa'. Future you will be grateful.",
        "It saves the current settings as a named preset. Click it, genius.",
        "This button literally saves your work. Use it.",
        "Saved presets live in the dropdown. Useful as hell.",
        "You spent 20 minutes tweaking. SAVE THEM. Right now. Before you close the app.",
        "Custom preset name tip: 'PS2_texture_fix' is useful. 'asdfgh' is not. Future you will curse you.",
        "One click, your settings are saved forever. Or until you delete them. Either way, click it.",
    ],
    "delete_preset": [
        "Deletes the preset. It's gone. Built-ins can't be deleted. Don't even try.",
        "Click delete, confirm the dialog, and that preset is fucking dead.",
        "You can recreate it in 30 seconds. It's not that serious.",
        "Gone. Poof. It's done. The app continues. You continue. Life goes on.",
        "Built-ins survive everything. Your custom ones? Gone with a click.",
        "Deleting a preset feels weirdly satisfying, doesn't it? Gone. Forever. No takebacks.",
        "If you're deleting a preset you made, I hope you wrote those settings down. You didn't, did you?",
        "Built-in presets are immortal. Your custom ones are mortal. Choose who dies wisely.",
    ],
    "alpha_slider": [
        "Drag this slider, goddamnit. 0 = invisible ghost. 255 = solid-ass opaque.",
        "The slider and the number box are the same fucking thing. Use whichever.",
        "Mode 'set' replaces everything with this value. Mode 'multiply' scales it. Simple.",
        "Alpha is just how see-through a pixel is. 0 = glass. 255 = brick wall.",
        "Only matters when 'Use preset' is UNCHECKED. Check that first, genius.",
        "Sliding to 128 = 50% transparent. Sliding to 0 = completely gone. 255 = solid.",
        "This slider controls alpha, which is the fancy word for 'how see-through is this pixel'. Drag it.",
        "Linked to the number box. Change one, they both change. Not two separate things, smartass.",
    ],
    "threshold_spin": [
        "Threshold: only process pixels with alpha BELOW this number. 0 = process all the shit.",
        "Set to 255 and you'll process almost nothing. Set to 0 and everything gets the treatment.",
        "Leave it at 0 if you want every pixel touched. That's usually what you want.",
        "It's a filter. Below the threshold: processed. Above: left the fuck alone.",
        "128 = only touch the semi-transparent half. Advanced stuff for fancy people.",
        "Think of threshold as a bouncer. Only alphas below this value get processed.",
        "0 = everyone gets in, every pixel processed. 255 = basically nobody processed. You decide.",
        "Useful when you only want to fix semi-transparent parts without touching solid ones.",
    ],
    "clamp_min_spin": [
        "Clamp Min: no pixel's alpha can go BELOW this. 0 = no floor. Very fucking simple.",
        "Raise it above 0 and you're saying 'nothing gets more transparent than this'. Power move.",
        "Set to 128 and no pixel is more than 50% see-through. Good for PS2 stuff.",
        "Leave at 0 unless you're specifically trying to clamp the transparency floor.",
        "Works with clamp mode. Useless in other modes. Read the mode label.",
        "Minimum alpha value enforced across all pixels. Like a speed limit for transparency.",
        "If your texture keeps going to zero alpha where it shouldn't, bump this up. Fixes it.",
        "Setting a floor for alpha values. No pixel will be more transparent than this. Transparency budget.",
    ],
    "clamp_max_spin": [
        "Clamp Max: no pixel's alpha can go ABOVE this. 255 = no cap. Default. Boring but correct.",
        "Lower it and you're saying 'nothing gets more opaque than this'. That's the PS2 range.",
        "128 = mimics PS2 GS alpha ceiling. Useful for targeting old-ass hardware.",
        "Leave at 255 unless you need an opacity ceiling. Most don't.",
        "Works with clamp_max mode. Pair it with clamp_min for a tight alpha sandwich.",
        "Setting a ceiling for opacity. No pixel will be more opaque than this number.",
        "PS2 GS hardware maxes out around 128 for alpha. Set this to 128 and you're golden.",
        "The maximum alpha value enforced across all pixels. A speed limit for opacity.",
    ],
    "invert_check": [
        "Invert flips transparent to opaque and back. It's the 'fuck it, reverse everything' option.",
        "Checking this makes solid stuff invisible and invisible stuff solid. Wild chaos.",
        "Combine with threshold for effects you can pretend were intentional.",
        "The math: new alpha = 255 minus computed alpha. Not rocket science.",
        "Leave unchecked unless you really know what you're doing. Or don't. Not your mom.",
        "Invert: the 'what if I made everything backwards' checkbox. Sometimes actually useful.",
        "Inverts masks and alpha channels for specific effects. Used wrong: makes everything invisible.",
        "255 becomes 0. 0 becomes 255. 128 stays 128. Everything flips. It's art.",
    ],
    "binary_cut_check": [
        "Binary cut: pixels above threshold go to 255 (solid). Below go to 0 (invisible). No in-between.",
        "Check this for hard-edge transparency. Every pixel is either fully opaque or completely gone.",
        "Threshold value above determines the cut point. Binary cut enforces it with extreme prejudice.",
        "Great for sprites that need crisp, no-aliasing alpha edges. Retro game shit.",
        "The nuclear option for alpha values. No soft edges. Pure binary. Pixels choose a side.",
        "This turns your smooth alpha gradient into hard yes/no transparency. Old-school game vibes.",
        "Above threshold = 255, fully solid. Below = 0, completely invisible. Nothing in between. Brutal.",
        "Use binary cut for crisp sprite edges. Discard the soft fuzzy gradient. Choose violence.",
    ],
    "out_dir": [
        "Where do you want your freshly fucked-with files to go? Pick a damn folder.",
        "Leave it blank and files save next to the originals. Easy mode.",
        "Pro move: make an 'output' folder so your organized ass can find things.",
        "Browse button works. Typing a path works too if you know where your stuff is.",
        "The folder gets CREATED if it doesn't exist. The app has your back, you messy bastard.",
        "Output directory. Where processed files land. Empty = saves next to source files.",
        "Set this to a dedicated folder so you don't have to dig through source files for results.",
        "Type a path or use the browse button. The app creates the folder if needed. Magic.",
    ],
    "recursive_check": [
        "Check this to dig through ALL your subfolders like the organized bastard you are.",
        "Recursive = it goes deeper than your last therapy session. Check it or don't.",
        "Subfolders, sub-subfolders, sub-sub-subfolders. It finds ALL of them. Insane.",
        "Leave it on and the app will hunt down every image in every nested folder.",
        "Uncheck it if you only want the top folder. Sometimes shallow is fine.",
        "If your folder structure looks like a corporate org chart, enable this. Goes all the way down.",
        "Recursive folder scanning finds every supported image file, no matter how deep it goes.",
        "Deep dive mode. On = scan everything in every folder. Off = surface level only.",
    ],
    "compare_widget": [
        "Drag the red handle and see what the fuck you just did to your image.",
        "Left = original. Right = fixed. Drag to compare. This is literally the point.",
        "The handle dragging is satisfying as hell. You'll do it way more than necessary.",
        "Select a file from the list above first, dumbass. Nothing to show without a file.",
        "Change settings and watch the right side update automatically. Beautiful chaos.",
        "Before/after comparison. Drag the divider. Cry or celebrate based on what you see.",
        "If the right side looks like garbage, your settings are garbage. Adjust them.",
        "Live preview updates as you change settings. Watch the alpha change in real time like a god.",
    ],
    "file_list": [
        "Drop your damn files here or use the buttons. Either way, fill this list up.",
        "Click a file to see the before/after comparison below. That's why we made it.",
        "Right-click to remove one file. Delete key works too. Power is yours.",
        "Empty list = nothing to process, you absolute walnut. Add something first.",
        "Drag folders right in here. The app sorts out which files are images. Magic.",
        "This list is your batch queue. Fill it up. The more the merrier. Process them all.",
        "Click any file to instantly preview the before/after in the compare widget below.",
        "You can add thousands of files. The app handles it. Batch processing is the whole point.",
    ],
    "convert_btn": [
        "Convert this pile of images to your format of choice. F5 also works, keyboard warrior.",
        "Hit it. Watch the progress bar. Revel in the format changing.",
        "Every file gets converted. Old format stays unless you set overwrite.",
        "Quality matters for JPEG/WEBP. For PNG, quality is a meaningless concept.",
        "F5, baby. The keyboard shortcut of champions.",
        "You've selected the format, set the quality, added the files. NOW CLICK THIS.",
        "All those PNG files about to become WEBP files. Or whatever format you chose. Hit it.",
        "The big convert button. All the files, all the format changes, one click. Let's go.",
    ],
    "format_combo": [
        "PNG or go home. DDS if it's for games. WEBP if you want to feel modern.",
        "PNG = lossless perfection. JPEG = lossy garbage (but small).",
        "Hover each format in the dropdown for a real description. We wrote them.",
        "WEBP is like PNG had a baby with JPEG and the baby turned out pretty good.",
        "TGA is old-school. ICO is for Windows icons. GIF makes it animate (kinda).",
        "AVIF is the new hotness. Smaller than WEBP, better quality than JPEG.",
        "QOI is a meme format that's actually decent. Fast as hell. Few people use it.",
        "DDS, TGA, BMP — retro game formats. For everything modern, use PNG or WEBP.",
    ],
    "quality_spin": [
        "Higher quality = better image, bigger file. Lower = potato, but tiny.",
        "Leave it at 90 and move on. It's fine. I promise it's fucking fine.",
        "Only JPEG and WEBP care about this number. PNG laughs at your quality setting.",
        "100 = best quality. 1 = garbage pile. 85-95 is the sweet spot for normal people.",
        "Move this number and absolutely nothing visible will change at 90+. You'll do it anyway.",
        "Quality 85 is basically indistinguishable from 100 at half the file size. Math is wild.",
        "JPEG at 95 still has artifacts. PNG doesn't care about quality. Use PNG if it matters.",
        "For web images, 75-85 is fine. For game textures, use lossless formats.",
    ],
    "settings_btn": [
        "Open settings and make this app look less like a corporate hellscape.",
        "Themes! Gore! Bats! Rainbows! It's all in here. Go nuts.",
        "Ctrl+, also works. Settings: where you waste 20 minutes choosing a theme.",
        "Mouse trail is in here. Turn it on. It looks rad as hell.",
        "You can break nothing in here. Except maybe your color taste.",
        "Settings button. Gateway to every cool cosmetic thing this app offers.",
        "Hidden themes unlock when you do certain things. Settings shows what's unlocked.",
        "Themes, click effects, cursor styles, mouse trails — all in settings. Enjoy.",
    ],
    "theme_combo": [
        "Choose a theme. Gore has blood splatter. Bat Cave has literal fucking bats. You're welcome.",
        "Rainbow Chaos will assault your retinas. You'll love it or hate it. No in-between.",
        "Hover each theme to see a short description. We wrote them. They're good.",
        "Otter Cove is cute and cozy. Galaxy Otter is cuter AND cosmic. Best of both worlds.",
        "Goth theme for when you're feeling angsty and want skulls everywhere.",
        "Mermaid theme: teal and more teal. Trident cursor. Ripple clicks. Underwater vibes.",
        "Alien theme: grey-green and eerie. UFO cursor. Tractor beam effects.",
        "Some themes are hidden until you unlock them. Keep clicking and converting. Secrets await.",
    ],
    "theme_color_btn": [
        "Color swatch button. You click it, a color picker opens, you pick a color, the app changes. Simple fucking concept.",
        "Change any of the 15 color roles. Background, text, accent, buttons — all editable.",
        "Changes apply live. If it looks horrible, that's on you. Click again and fix it.",
        "The color YOU choose is the color the app becomes. Cause and effect. Very powerful.",
        "Save the result with Save Theme. Or keep tweaking forever. We've seen it happen.",
    ],
    "effect_combo": [
        "Choose your fucking particle style. Gore shoots blood. Rainbow shoots unicorns. Pick one.",
        "This controls what explodes out of your cursor. Fire 🔥, Ice ❄, Panda 🐼, Sakura 🌸.",
        "Custom lets you use your own emoji. What kind of unhinged particles will you pick?",
        "Galaxy shoots stars. Otter shoots otters. Sakura shoots cherry blossoms. Life is good.",
        "If you pick Default and complain about the sparks, that's entirely on you.",
        "Mermaid effect has bubble and sea creature particles. It's adorable and you will love it.",
        "Shark effect summons sharks from your cursor. This is a real thing that exists in this app.",
        "Custom emoji effect: whatever the hell you want flying off your cursor. Pure chaos.",
    ],
    "custom_emoji": [
        "Type your deranged emoji and watch them blast across the screen like beautiful chaos.",
        "Add whatever weird-ass emoji you want as click particles. No judgment. Mostly.",
        "These fly out when you click. Choose wisely. Or chaotically. Both work.",
        "Clear All nukes your entire emoji list. Gone. You did that. Own it.",
        "Paste multiple emoji at once and they all join the flying circus. 🎪",
        "Your personal click effect particles. Customize your chaos.",
        "Someone typed 💀💀💀 here and now skulls fly out every time they click. You can do that.",
        "Mix and match emoji for maximum personality. 🔥❄️💀🌸🐼 all at once? Valid.",
    ],
    "tooltip_mode_combo": [
        "You're using No Filter 🤬 mode. Outstanding fucking choice. This is the way.",
        "Pick 'Off' to turn all this off. Boring, but we get it.",
        "Normal mode is helpful but lacks the spice. You're clearly above that.",
        "Dumbed Down is for when you want to be gently insulted. You're above that too.",
        "This right here? This mode? Best mode. You chose correctly. 🤬",
        "No Filter mode: helpful tips, real profanity, actual jokes. The default for a reason.",
        "Switching to Normal would be a step backwards. You've tasted the forbidden fruit. Stay.",
        "The tooltip mode selector. Currently set to the objectively correct option. Don't touch it.",
    ],
    "tooltip_style_combo": [
        "Tooltip visual style. You can finally make these little boxes look less shit.",
        "Auto follows the theme. If your theme is sharp and pointy the tooltips will be too. 🤌",
        "Angular means zero radius, left accent bar. Like if a tooltip was built in a prison.",
        "Bubbly makes the corners so round they look like a baby designed them. Very soft. Very pink.",
        "Neon mode makes the tooltip look like a fucking hacker terminal. Pick it. Do it.",
        "Classic = boring plain box. Grandma-safe. Works fine. Not impressive.",
        "Icy style has alternating corners that look like a crystal. Fancy as fuck.",
        "This dropdown does not affect what the tips *say*. Just how the box looks. Different thing.",
    ],
    "sound_check": [
        "Toggle sounds on or off. Enabled = satisfying clicks. Disabled = sad silence.",
        "Check this box and the app makes noise. Uncheck it for quiet mode, you antisocial gremlin.",
        "Custom sound path below if the built-in click isn't annoying enough for you.",
        "Library mode? Uncheck it. Having fun? Leave it on. Living life? Both work.",
        "It's a sound checkbox. Stop hovering and just check it.",
        "Sounds make the app feel alive. Uncheck only if you're in a meeting. We get it.",
        "The click sounds are satisfying. Give them a chance before you turn them off.",
        "Custom sound support means you could make every click play a fart sound. We don't judge.",
    ],
    "trail_check": [
        "Turn on the mouse trail so your cursor leaves a glowing streak of chaos behind it.",
        "Enable this and wiggle your mouse. It looks fucking incredible, I promise.",
        "Trail color is set below. Trail enabled here. Two separate controls. You got this.",
        "It's a cosmetic overlay. Doesn't interfere with clicks. Just pure visual delight.",
        "If you don't turn on the mouse trail, you're missing out and that's on you.",
        "Mouse trail: the feature that makes everyone who walks by ask 'wait, what is that?'",
        "Enable the trail, set a neon color, match it to your theme. Then be unproductive for 10 minutes.",
        "Trail enabled = every mouse movement is art. Trail disabled = boring cursor doing boring things.",
    ],
    "trail_color": [
        "Pick the damn color for your trail. Click the button. Color picker appears. Simple.",
        "Go neon green. Go bloody red. Go whatever the hell matches your soul.",
        "The trail won't show a new color until you click Apply & Close. Just so you know.",
        "Pair it with the matching theme for a cohesive aesthetic. Or don't. Chaos is valid.",
        "Any hex color works. If you pick beige I will be personally disappointed.",
        "Neon colors look best for mouse trails. Hot pink, electric blue, screaming green. Go bold.",
        "The color picker has a hex input. Type #FF00FF for maximum chaos.",
        "Trail color + matching theme + emoji trail style = most visually unhinged setup possible.",
    ],
    "trail_style": [
        "Pick what your mouse trail looks like. Dots, comet, ribbon, emoji — your call.",
        "Ribbon = smooth squiggly noodle following your cursor. Comet = tapered glow tail.",
        "Fairy/Wave/Sparkle gives you floating emoji trailing your cursor. It's unhinged. We love it.",
        "Dots is the boring default. You're better than dots. Use comet or ribbon at least.",
        "If Use Theme Trail is checked, this setting is overridden. The theme picks for you.",
        "Wave trail: ocean-themed emoji floating behind your cursor. 🫧💧🌊🐠 Perfection.",
        "Sparkle trail: ✦❄✧💎 trailing behind you. Ice cave theme + sparkle = aesthetic af.",
        "Fairy trail: ✨💫⭐ following your cursor. Fairy Garden theme + fairy trail = magical bullshit.",
    ],
    "use_theme_trail": [
        "Check this and your trail auto-matches the theme. Fairy Garden gets fairy fucking dust. ✨",
        "Ocean/Mermaid gets bubble emoji trail (🫧💧🌊). Ice gets crystal trail (✦❄✧). Gorgeous.",
        "The color picker above becomes useless when this is checked. Enjoy the automation.",
        "Uncheck if you want to manually pick your trail color like a goddamn adult.",
        "Theme trail ON = the app is fabulous. Theme trail OFF = boring person energy.",
        "Auto-matching trail to theme is the lazy genius move. Recommended.",
        "Each theme has a hand-picked trail style that just works. Trust the system. Check this.",
        "With this on, switching themes also switches your trail. Cohesive chaos on demand.",
    ],
    "trail_length_slider": [
        "Slide this right to make your trail longer and more dramatic, or left to keep it short and snappy.",
        "10 trail points = baby little blip behind your cursor. 200 = a goddamn ghost following you.",
        "Long trail + comet style = you look like a shooting star. Do it.",
        "Short trail for subtle; long trail for maximum 'look at my cursor' energy.",
        "The more trail points, the more CPU. Keep it under 150 unless your PC is a beast.",
        "Slide it to max and pretend you're a comet blazing through the cosmos. You're welcome.",
        "Trail length = how far behind you your past haunts you. Philosophically speaking.",
        "More trail = more visual chaos. You want that. I know you want that.",
    ],
    "trail_fade_slider": [
        "Speed 1: your trail fades so slowly it follows you like an ex. Speed 10: it vanishes instantly.",
        "Slow fade = dramatic lingering ghost trail. Fast fade = crisp snappy streak.",
        "Turn it to 1 and move your mouse slowly. Looks incredible. Don't lie.",
        "Fast fade (10) paired with short trail = barely a hint. Subtle enough for work. Maybe.",
        "Slow fade + long trail = ghostly haunted cursor. Perfect for the Halloween theme.",
        "This controls persistence. How long the trail clings to existence before fading into nothing.",
        "Speed 5 is the sweet spot for most people. But you're not most people, are you?",
        "1 = the trail overstays its welcome. 10 = it has commitment issues.",
    ],
    "trail_intensity_slider": [
        "100% = your trail is screaming. 10% = your trail is whispering. Pick your vibe.",
        "Low intensity for a subtle barely-there ghost trail. High intensity for full neon assault.",
        "Turn it to 10% if your boss is watching. Turn it to 100% when they leave.",
        "Full intensity + neon color + long trail = maximum visual chaos. You deserve this.",
        "This controls how bright/opaque the trail renders. It's basically a transparency dial.",
        "High intensity = the trail actually looks like something. Low intensity = almost invisible.",
        "Fade speed controls how fast it disappears. Intensity controls how bright it is at its peak.",
        "100% intensity. Every single time. Why would you make it less bright? Explain yourself.",
    ],
    "cursor_combo": [
        "Change your fucking cursor. Default arrow, crosshair, pointing finger, open hand. Pick one.",
        "Pointing Hand makes you feel like you're clicking everything on purpose. Very powerful.",
        "Cross cursor for when you want to feel like a precision surgeon.",
        "Open Hand is chill. Relaxed. Like you've got everything under control. Do you really?",
        "It changes your cursor. Just pick the one that speaks to your soul.",
        "Some themes have special cursors. Enable 'Use Theme Cursor' to let the theme decide.",
        "Crosshair = precision vibes. Pointing finger = clickbait vibes. Arrow = coward vibes.",
        "The cursor you pick reflects your entire personality. Choose accordingly.",
    ],
    "use_theme_cursor": [
        "Check this and your cursor changes automatically to match the theme. Otter Cove gets 🤘. YES, REALLY.",
        "The app literally picks your cursor for you based on the theme. Sit back and enjoy.",
        "Otter theme. Rock emoji cursor. If that doesn't make you happy, nothing will.",
        "Uncheck this to go back to manually choosing your boring-ass cursor. We forgive you.",
        "Theme cursor is ON = the app has taste. Theme cursor is OFF = you're on your own.",
        "Each theme has a custom cursor that fits its vibe. Enable this for the full experience.",
        "Mermaid theme + theme cursor = trident. Because of course it is. Enable this.",
        "Auto-cursor is the move. Match cursor to theme automatically. One less decision.",
    ],
    "font_size": [
        "Crank the font size up if you're squinting at this screen like a damn mole.",
        "8pt is tiny as hell. 24pt is enormous. 10pt is what normal humans use.",
        "This changes the text size everywhere in the app. Your OS is unaffected.",
        "Go big. Go small. Find your font size soulmate. We'll wait.",
        "If you need it bigger, no one's judging. Make it readable.",
        "Font size too small? Increase it. Too large? Decrease it. Not a hard problem.",
        "If you're reading this without squinting, your font size is probably fine.",
        "12pt is comfortable for most. 14pt on a big monitor. 24pt if you're dramatic.",
    ],
    "click_effects_check": [
        "Enable the particle explosions that happen every time you click. It's glorious.",
        "Uncheck this if you hate joy and visual delight. We still love you. Mostly.",
        "Every click spawns themed particles. Bats fly. Blood splatters. Pandas explode.",
        "Turn it off for serious batch work. Turn it back on when you remember why this is fun.",
        "The particles match the theme. Check the Theme tab to configure which chaos you prefer.",
        "Click effects make every button interaction feel like a tiny celebration. Highly recommended.",
        "Enabling this is possibly the best decision you'll make today. Everything explodes.",
        "Gore theme + click effects = blood splatters every time you press Process. Incredible.",
    ],
    "use_theme_effect": [
        "Let the app auto-pick the particle effect based on your theme. It knows what's best.",
        "Check this and the app handles effects for you. Uncheck to choose your own chaos.",
        "With this on, Gore = blood, Bats = bats, Mermaid = magical sea shit. Makes sense.",
        "Turn it off if you want to mix shit up — alien effects with gore theme? Why not.",
        "This is just an automation toggle. On = auto. Off = manual. Not complicated.",
        "Each theme has a hand-picked matching effect. Auto-mode uses those. Trust it.",
        "Mixing effects and themes manually is fun but chaotic. Auto-mode keeps it cohesive.",
        "Galaxy auto-picks galaxy particles. Shark auto-picks shark effects. Clean.",
    ],
    "save_custom_theme": [
        "Saves your fucking color choices as a named custom theme. Very important button.",
        "Your masterpiece gets a name and lives in the INI file next to the app. Congratulations.",
        "Name it. Save it. Now you own it. That's the whole deal.",
        "Custom themes persist through app restarts. Your ugly creation will survive everything.",
        "You can save unlimited themes. The INI file will judge you silently for each one.",
    ],
    "delete_custom_theme": [
        "Deletes a custom theme. PERMANENTLY. Gone forever. No undo. Think before you click.",
        "It asks for confirmation because we're not monsters. But after that? Gone.",
        "Can't delete presets. They're hardcoded. You can only delete your own stuff.",
        "If you delete the active theme, the app switches to Panda Dark. Awkward but fine.",
        "Export first if you want it back someday. This ain't a trash bin with a restore option.",
    ],
    "export_custom_theme": [
        "Exports the selected theme as JSON. Share your terrible color choices with the world.",
        "JSON file, any location, easy to share. Even easier to lose in your downloads folder.",
        "The exported JSON has all color values. Any text editor can open it. Don't break it.",
        "Share your theme, back it up, or just admire the JSON structure. Your call.",
        "Import button on another machine does the reverse. Full theme transfer system.",
    ],
    "import_custom_theme": [
        "Imports a theme from a JSON file. Somebody's colors become your colors. Exciting.",
        "Valid JSON = theme appears. Invalid JSON = rejected with an error. Simple rules.",
        "Community themes, downloaded JSON, your own exports — all valid inputs here.",
        "Import it and it's saved immediately. It's yours now. You're committed.",
        "If the JSON is missing required color keys, it gets rejected. Format matters, asshole.",
    ],
    "sound_path": [
        "Path to your custom click sound. WAV format. Type it here or use the browse button.",
        "Leave blank for the default built-in click sound. It's fine, don't worry about it.",
        "Wrong path = silence. Silence is sad. Get the path right.",
        "Custom WAV path. For your weird sound effects collection. No judgment.",
        "This field saves automatically. Type the path and tab away. Done.",
    ],
    "sound_browse": [
        "Click to browse for a WAV file. You know how file browsers work. Right?",
        "Opens a file picker. Navigate to your sound file. Click Open. Not hard.",
        "Finds a WAV file and drops the path in the sound path field. Seamless.",
        "Use this instead of typing the path by hand. One wrong character = silence.",
        "Browse for sounds. This button does that. Very focused. Very dedicated.",
    ],
    "reset_all_settings": [
        "RESET EVERYTHING. Factory defaults. The nuclear option. You sure about this?",
        "All your settings? Gone. Back to defaults. Every. Single. One.",
        "It asks for confirmation. Twice in your head should be enough. Or maybe just once.",
        "It does NOT delete files on disk. Just the app config. Calm down.",
        "After reset: default theme, default sounds, no presets, no custom themes. Clean slate.",
    ],
    "mode_combo": [
        "'set' replaces all alpha values with your number. Use this one first, genius.",
        "'multiply' does math on your alpha. Useful if you want to scale transparency.",
        "'add'/'subtract' bumps the alpha up or down. Like turning a dial.",
        "Pick your alpha mode here. They all do different things.",
        "Six options: set, multiply, add, subtract, clamp_min, clamp_max. 'set' is safe.",
        "'multiply' at 128 = 50% of original alpha. The classic PS2 trick.",
        "clamp_min/clamp_max enforce a floor or ceiling on alpha values. Advanced shit.",
        "Start with 'set' mode. Get comfortable. Then experiment with the others.",
    ],
    "alpha_spin": [
        "Type your goddamn alpha value here. 0 = invisible, 255 = fully opaque. Simple.",
        "0 to 255. Your image's transparency depends on this number. Don't type 256.",
        "This and the slider below are linked. Move one, the other follows.",
        "In 'multiply' mode, 255 = no change. Less = dimmer. More math = more misery.",
        "Set the alpha you want applied. Just type a number.",
        "128 = 50% opacity. 64 = 25% opacity. 255 = solid. 0 = ghost. Pick your level.",
        "PS2 textures often need around 128-130. N64 wants 255. Type accordingly.",
        "This number directly controls how transparent or opaque the output alpha will be.",
    ],
    "use_preset_check": [
        "Check this to use the preset instead of the manual crap below. Quick and easy.",
        "Uncheck this if you think you know better than the preset. Maybe you do.",
        "The preset and fine-tune controls don't play nice together. Pick one.",
        "When checked, the sliders below are grayed out. They just sit there, useless.",
        "Presets are pre-configured by someone who already figured this out. Use them.",
        "Preset mode: fast, easy, reliable. Manual mode: more control, more responsibility.",
        "Checking this tells the app to use the dropdown preset and ignore fine-tuning.",
        "Toggle between preset and manual based on how much control you need.",
    ],
    "red_spin": [
        "Crank up the red channel, you deranged pixel artist. Or drop it. Your call.",
        "Positive = more red, like blood. Negative = less red, less blood. Science.",
        "Doesn't do jack shit unless you tick 'Apply RGBA adjustments'. Rookie mistake.",
        "Pairs with green, blue, and alpha for full RGBA surgery on your textures.",
        "PS2 textures look weird? Crank this and see.",
        "+50 makes things warmer and more intense. -50 makes them cooler and sadder.",
        "Combined R/G/B adjustments let you fix tinted textures from game consoles.",
        "Red adjustment range: -255 to +255. You have full control. Don't break anything.",
    ],
    "green_spin": [
        "Adjust green. Positive = Shrek mode. Negative = anti-Shrek mode.",
        "This changes the green channel on every pixel. Big deal.",
        "Won't do anything unless 'Apply RGBA adjustments' is checked. Read the UI.",
        "Works with red, blue, and alpha. It's called RGBA for a reason.",
        "GameCube textures turning everything into a salad? Maybe fix that here.",
        "Green channel adjustment. Sometimes textures are too green. You decide.",
        "+255 green makes everything look like the Matrix. Cool. Accidental.",
        "GameCube and N64 sometimes have green tinting artifacts. This channel helps.",
    ],
    "blue_spin": [
        "More blue = underwater. Less blue = warm and toasty.",
        "Blue channel adjustment. Positive = cold. Negative = less cold. Basic.",
        "Still needs 'Apply RGBA adjustments' checked. That checkbox exists for a reason.",
        "Works with red and green to complete your unholy color correction trinity.",
        "Make everything look like a sad indie film. Or fix busted console textures.",
        "PSP textures often have blue tinting issues. This channel is your fix.",
        "+255 blue makes everything look underwater. Accidentally beautiful sometimes.",
        "Blue adjustment for the cold-cool-frozen look. Or just fixing tinted game textures.",
    ],
    "alpha_delta_spin": [
        "Nudge every pixel's alpha up or down. Positive = opaquer. Negative = more see-through.",
        "Alpha delta. Add transparency or remove it globally. Not complicated.",
        "Does jack squat unless 'Apply RGBA adjustments' is ticked. Same as the others.",
        "Use this to globally darken or brighten the alpha layer. Power move.",
        "Four channels: R, G, B, and now A. Full RGBA control. You're welcome.",
        "Positive alpha delta = everything gets more opaque. Negative = everything fades.",
        "Global alpha adjustment on top of whatever alpha processing you've set up.",
        "Think of this as a fine-tuning knob for alpha after the main processing is done.",
    ],
    "apply_rgb_check": [
        "Flip this on or the RGBA spinboxes do absolutely nothing. Not a bug.",
        "Enable RGBA adjustments. Without this, all four channel changes = void.",
        "Check it. Apply it. Watch colors AND alpha shift. Feel the power.",
        "Alpha preset runs first, then the RGBA fuckery. Order of operations.",
        "Turn this off if you just want alpha fixed and don't need channels messed with.",
        "This checkbox activates the entire R/G/B/A delta system. Off = none of those sliders do anything.",
        "For color correction along with alpha fixing, check this box.",
        "The master switch for RGBA adjustments. Flip it on, and your spinboxes wake up.",
    ],
    "suffix_edit": [
        "Add a suffix so you don't overwrite the originals, you reckless bastard.",
        "Example: '_fixed' makes 'image.png' become 'image_fixed.png'. Easy.",
        "Leave blank to overwrite source files. Pray you have backups, hero.",
        "Type something here. '_out', '_processed', '_done', whatever floats your boat.",
        "This goes before the file extension. Not after. Before. Got it?",
        "Without a suffix, you're overwriting originals. That's brave. Or stupid.",
        "Good suffix ideas: '_fixed', '_ps2', '_alpha'. Bad idea: nothing. Don't do nothing.",
        "Suffix = safety net. Use it. Your future self will thank your current self.",
    ],
    "resize_check": [
        "Enable this to change the output image dimensions. Shocking feature.",
        "Check it if you want the images to come out a different size. Uncheck to not.",
        "When checked, the width and height boxes below actually do something.",
        "Resizing images. It's a thing apps do. This one included.",
        "If you need a different size, check this. If you don't, don't.",
        "Batch resizing while converting. Check this to enable the resize fields.",
        "Power feature: convert format AND resize in one pass. Check this box.",
        "Most conversions don't need resizing. Only check when you need different dimensions.",
    ],
    "lock_aspect_check": [
        "Lock aspect ratio. Change width, height follows. Like a decent resize should.",
        "Unchecked = manual width/height. Your image might end up a cursed squish. Your call.",
        "Checked by default because nobody wants their otter stretched into a hotdog shape.",
        "Uses the selected file's real dimensions to calculate the locked height.",
        "Locked = proportional. Unlocked = chaotic. Both technically valid. One looks better.",
        "Lock aspect ratio. Keep it checked unless you specifically want distorted output.",
        "Width changes → height auto-updates to match the original ratio. Smart.",
        "Uncheck if you intentionally want a non-square or weirdly-proportioned output.",
    ],
    "width_spin": [
        "Output width in pixels. Zero means keep the original. Type a number.",
        "How wide do you want the image? Type that number. In pixels.",
        "Wider = bigger number. Narrower = smaller number.",
        "0 = original width. Non-zero = you've overridden the width. Congrats.",
        "Resize only works when the checkbox above is checked. In case you forgot.",
        "1920 for full HD. 2048 for textures. 512 for retro game vibes.",
        "Set to 0 to only control the height. The app figures out the other dimension.",
        "Target output width. Set it, enable resize, convert. Simple.",
    ],
    "height_spin": [
        "Output height in pixels. Zero means keep the original. Vertical this time.",
        "How tall do you want the image? Shove that number in here.",
        "Taller = bigger number. Shorter = smaller. Up is up. Down is down.",
        "0 = original height preserved. Works alongside the width field.",
        "Resize is only active when the checkbox above is enabled. Still true.",
        "1080 for full HD. 2048 for square textures.",
        "Set to 0 to only control the width. The app handles proportional scaling.",
        "Target output height. Works together with width. Both at 0 = no resize.",
    ],
    "out_dir_browse": [
        "Click to pick a folder for your output files. Literally just a folder picker.",
        "Browse for a directory. Click one. Done. Your files will go there.",
        "You can also type the path directly. But this button exists for a reason.",
        "Choose your output folder wisely. Or don't. It's reversible.",
        "Leave the path empty to save next to the source files.",
        "Folder picker button. Click it. Navigate. Select. Done.",
        "Pro tip: create the output folder first so you know where things are going.",
        "The browse button exists so you don't have to type long paths. Use it.",
    ],
    "alpha_fixer_tab": [
        "Alpha Fixer tab: where broken-ass alpha channels go to get fixed. Click it. Or press Ctrl+1.",
        "This is the tab that actually does the real work, unlike you apparently.",
        "Alpha channels, presets, batch processing — stop reading tooltips and use it.",
        "If your textures look like shit, THIS is where you fix that. You're welcome.",
        "The image frame icon. The main tab. The whole damn point of the app.",
        "Alpha Fixer: the reason this app exists. The main event. The big kahuna.",
        "Broken transparency in your game textures? This tab fixes it. That's the whole thing.",
        "Hovering on the Alpha Fixer tab instead of being IN the Alpha Fixer tab. Interesting life choice.",
    ],
    "converter_tab": [
        "Converter tab: turn your PNG into a WEBP or whatever the fuck you need. Ctrl+2 to jump here.",
        "File goes in, different format comes out. It's conversion. Not complicated.",
        "Supports a ridiculous number of formats. Stop asking, just click it.",
        "Need to batch convert 500 files? This is your tab, you file-hoarding maniac.",
        "It's the converter. For converting. Stop hovering and do the conversion already.",
        "13 output formats supported. If your format isn't there, you have exotic taste.",
        "Batch format conversion. Add files, pick format, hit convert. Three steps to glory.",
        "From PNG to DDS to AVIF to TGA — this tab handles all your format conversion needs.",
    ],
    "history_tab": [
        "History tab: proof you actually did something today. Rare. Ctrl+3 to get here directly.",
        "Shows your recent processing history. Unlike your therapist, we don't judge.",
        "Scroll through it to find that file you swear you processed but can't remember.",
        "If it's not in here, you didn't do it. Sorry. That's just facts.",
        "History. Past work. Evidence of productivity. Click it before you spiral.",
        "Processing history lives here. Every alpha fix, every conversion. Archived.",
        "Can't remember if you processed that folder? Check history. It remembers everything.",
        "History tab: your receipts. Proof of work. Digital paper trail of your image adventures.",
    ],
    "history_refresh_btn": [
        "Refresh button. In case you forgot what you did three seconds ago.",
        "Hit refresh because you have zero trust in the app updating automatically. Valid.",
        "Forces the history to reload. Because apparently you needed to ask twice.",
        "Click refresh. See new entries. Marvel at your own productivity. Repeat.",
        "It's a refresh button. Click it if things look stale.",
        "Paranoid the history didn't update? Hit refresh. It updates. Trust issues are valid.",
        "New entries appear after processing. Refresh if you need to convince yourself.",
        "The history auto-refreshes on tab switch, but this button forces an immediate reload.",
    ],
    "history_export_btn": [
        "Export to CSV. Because sometimes you need to prove to yourself that you actually worked.",
        "Saves the history as a spreadsheet CSV. Excel will open it. Google Sheets will open it.",
        "Click it, save a file, send it to someone, look professional. Three easy steps.",
        "Exports only the current sub-tab. Converter or Alpha Fixer. Your choice. Your file.",
        "A CSV file of your entire processing history. For auditing, archiving, or bragging.",
        "It makes a CSV. You can open it in anything. It has all your session data in it.",
        "100 conversions and want a receipt? Export CSV. There's your receipt.",
        "Exports what you see in the active sub-tab. Switch tabs first if you want the other one.",
    ],
    "history_clear_btn": [
        "Nuke the entire history. No backup. No recovery. No regrets.",
        "Clear All History: scorched earth mode for your processing log. You sure?",
        "One click, your entire session history is gone forever. Fun! 🔥",
        "Your past work, erased. Doesn't touch actual files. Just the receipts.",
        "This is the 'pretend it never happened' button. Use wisely, you chaotic gremlin.",
        "Gone. All of it. Every entry. Vanished. Your files are fine. The history is gone.",
        "Clearing history is a fresh start vibe. No records. Clean slate. Very liberating.",
        "Once cleared, history cannot be recovered. So, you know, be sure about this.",
    ],
    "history_conv_sub": [
        "Converter history. Every single batch conversion you ran, logged here like a digital crime scene.",
        "Yellow rows mean errors. Because red would've been too on the nose.",
        "Time, format, file count, successes, failures — all logged so you can't deny what happened.",
        "50 sessions max. After that the oldest ones fall off. Like memories but with more WEBP files.",
        "Batch convert a folder, come here, feel proud or ashamed depending on the error count.",
        "Each row is a conversion session. A moment in your file-processing history. Precious.",
        "The first 10 filenames show in the last column. The other 490 are implied.",
        "Converter history. For when you need receipts on your format crimes.",
    ],
    "history_alpha_sub": [
        "Alpha Fixer history. Every session where you attempted to unfuck your alpha channels. Logged.",
        "Preset name, file count, errors — all here so you know exactly what broke and when.",
        "Yellow rows have errors. The universe is telling you something. Listen to it.",
        "Up to 50 sessions logged. Like your own personal hall of alpha-fixing shame and glory.",
        "If it's in here, you ran it. Can't blame the app. Can't blame the files. Just you.",
        "Alpha fix logs: the evidence trail of every transparency you've ever massacred.",
        "Useful for catching which preset caused problems. Spoiler: it's always the manual one.",
        "Your alpha-processing receipts. Organized. Merciless. Completely honest.",
    ],
    "history_conv_tree": [
        "Conversion history table. Every batch you've ever run. All of them. Your legacy.",
        "Yellow rows = errors. Something failed. Could be the file, could be you. Probably both.",
        "Columns tell you when, what format, how many files, how many worked, how many didn't.",
        "Hover the column headers to understand what each one measures. There are tooltips.",
        "Sort by clicking headers. Filter doesn't exist — just scroll, it's not that long.",
    ],
    "history_alpha_tree": [
        "Alpha fix history table. Your personal record of transparency manipulation.",
        "Yellow = errors. Non-zero error count = something fucked up. Address it.",
        "Columns: timestamp, preset/mode used, total files, successes, failures, file names.",
        "Hover those column headers. We put tooltips there. Use them.",
        "50 row limit. Oldest entries vanish as new ones appear. Like time itself.",
    ],
    "history_conv_summary": [
        "Aggregate converter stats. Total sessions + total files converted + failures. The big picture.",
        "If your error count is growing, something is consistently wrong. Fix it.",
        "These totals span your entire logged history. Not just the most recent batch.",
        "Cleared when you hit 'Clear All History'. Fresh start. Clean slate. Zen.",
        "Numbers. They represent your conversion history. That's it.",
    ],
    "history_alpha_summary": [
        "Aggregate alpha-fix stats. Every batch you've ever run, summarised into numbers.",
        "High errors = recurring problem. Low errors = you actually know what you're doing.",
        "Total sessions × files = your actual workload. Look at it. Be proud. Or horrified.",
        "Resets on 'Clear All History'. Wipes the slate. Clean history. No evidence.",
        "Summary stats. For when you want numbers instead of rows.",
    ],
    "settings_theme_tab": [
        "Theme tab. Colors. Presets. Custom schemes. Where the aesthetic decisions get made.",
        "Click any color button to change that aspect of the app. Changes are live and immediate.",
        "Unlocked themes show up here with a little 🔓. Everything else stays hidden until earned.",
        "Export = save your theme color JSON to a file. Import = load someone else's colors. Both valid.",
        "The search box filters themes. If you've made 40 custom themes you need this. We don't judge.",
    ],
    "settings_general_tab": [
        "General tab. Effects, sounds, trails, cursors, fonts, tooltip mode. The good stuff.",
        "Everything in here applies instantly. No Apply button. No Save. Just pure real-time chaos.",
        "Use-theme checkboxes auto-pick the right trail/effect/cursor for whatever theme is active.",
        "Tooltip Mode: Normal = helpful. Dumbed Down = snarky. No Filter = this mode. Very meta.",
        "Font size, reset button, sound path — all lurking in this tab. Check every corner.",
    ],
    "alpha_file_count_lbl": [
        "File count. Currently how many files are in the list. That's it.",
        "Also shows F5 = run, Esc = stop. The keyboard shortcut cheat sheet is right here.",
        "Adds up as you drop files. Goes down when you remove them. Mathematics.",
        "Progress and ETA appear here during a batch run. Big batches get the ETA treatment.",
        "Zero files means nothing to process. Add something, genius.",
    ],
    "conv_file_count_lbl": [
        "Converter file count. Tells you how many files are waiting for their format change.",
        "Shows F5 = convert, Esc = stop, Ctrl+O = add. The keyboard cheat sheet you never asked for.",
        "During conversion, progress replaces the file count. Very informative.",
        "ETA kicks in at 500+ files. Because that's when you start wondering if it's working.",
        "It says 0? Add files. That's the whole debug process for this.",
    ],
    "processing_log": [
        "The processing log. Every ✔ and ✘ message from the worker. Your file-crunching receipt.",
        "Errors show up here with actual reasons. Use them. They're there to help you.",
        "Auto-scrolls to the bottom. If you need to scroll up to see the carnage, do it manually.",
        "Clears on every new batch because yesterday's failures aren't today's problem.",
        "All ✔ = you did it right. All ✘ = something is fundamentally broken. Check the errors.",
    ],
    "processing_progress": [
        "Progress bar. Fills up as each file gets processed. Left = empty. Right = done.",
        "Stuck at 0%? Maybe click the run button. Just a thought.",
        "100% = finished. The bar doesn't reset itself — start a new batch to reset it.",
        "Moves faster with small files, slower with large ones. Physics. Sort of.",
        "The ETA lives in the file count label above. This bar just shows completion percentage.",
    ],
    "alpha_status_lbl": [
        "Status label for the Alpha Fixer. Tells you what the hell is happening.",
        "'Ready.' = idle and ready to destroy some alpha channels. Click run.",
        "Batch results appear here: ✔ N succeeded, ✘ M failed. Pretty self-explanatory.",
        "If errors appear, scroll the log. The log knows what went wrong. It was there.",
        "It updates in real time during processing. Staring at it doesn't make it faster.",
    ],
    "conv_status_lbl": [
        "Status label for the Converter. Like the Alpha Fixer one but for converting formats.",
        "'Ready.' = nothing happening. Could be worse. Could be crashing.",
        "Shows success/error counts after each batch. Numbers don't lie. Files don't care.",
        "Errors? Check the log below. It has the full story.",
        "Updates live during conversion. Yes, you can watch it. No, it doesn't speed it up.",
    ],
    "theme_search": [
        "Search for a theme. Type letters, list shrinks. Delete letters, list grows. Profound.",
        "If you have 40 custom themes and can't find the right one, maybe lay off the theme hoarding.",
        "Type part of a theme name. The combo filters in real time. It's 2026, this is expected.",
        "Theme filter. For when scrolling through the whole dropdown feels like cardio.",
        "Type 'panda' and only panda themes show up. That's how search works. Mind-blowing.",
        "Case-insensitive. 'OCEAN', 'ocean', 'OcEaN' — all find the same damn themes.",
        "Filter box: because nobody wants to scroll through 50 themes manually. Work smarter.",
        "Hit the × button to clear the filter and bring back all themes in their chaotic glory.",
    ],
    "patreon_btn": [
        "Give the dev your money! They made this beautiful shit and they deserve it.",
        "Patreon: because software doesn't write itself and developers need to eat.",
        "Even a dollar helps! That's less than your daily coffee, you caffeinated maniac.",
        "Your support funds new themes, more effects, and better pandas. Worth it.",
        "patreon.com/c/DeadOnTheInside — click it. Do it. Be a hero. 🐼",
        "The developer spent months building this app. patreon.com/c/DeadOnTheInside - show some love.",
        "Patreon support = more features, more themes, more everything. Good karma too.",
        "Link opens to DeadOnTheInside on Patreon. No payment required to follow. Just click.",
    ],
    "use_theme_sound": [
        "Enable this and your click sounds will be as themed as the rest of this shit.",
        "Gore theme = bone-crunching thud. Panda = cute ping. Alien = weird-ass beep. Choose your destiny.",
        "Theme sounds are actually different. Not just the same blip in a different color. Real audio variety.",
        "Check this or leave it unchecked, your sound adventure either starts here or it fucking doesn't.",
        "7 sound profiles, 38 themes. Math says some themes share sounds. Theme sound: it's a thing. Enable it.",
    ],
    "keep_metadata_check": [
        "Keep metadata checked = EXIF, GPS, camera info, ICC profiles all survive the conversion. Congrats.",
        "EXIF contains all the nerdy camera data. ICC profiles keep your colors from looking like ass.",
        "If you're converting photos and want GPS/camera data to survive, check this. Otherwise whatever.",
        "Uncheck this for game textures — they don't give a single fuck about EXIF. Less clutter.",
        "ICC profiles are color management data. Check this or your colors might look slightly off. Your call.",
        "Checked = metadata preserved. Unchecked = stripped out. Simple binary choice. Make it.",
        "Your EXIF data contains your life story (camera model, date, GPS). Check this to keep that story intact.",
        "For photographers: check this always. For game devs: don't bother. Now you know.",
    ],
    "before_stats_panel": [
        "BEFORE stats. Min, max, mean alpha. The raw untouched shit before you fucked with it.",
        "This is what your alpha channel looked like before you did anything. Pure, virgin data.",
        "Compare this to the AFTER panel. If the numbers changed, your settings did something. Congrats.",
        "Min=0 means some pixel is fully transparent. Max=255 means some pixel is fully opaque. Now you know.",
        "Mean alpha is the average. Low mean = mostly transparent garbage. High mean = mostly solid. Simple math.",
    ],
    "after_stats_panel": [
        "AFTER stats. This is what your alpha looks like after you messed with it.",
        "If these numbers match the BEFORE panel exactly, you changed absolutely nothing. Check your settings.",
        "Mean going up = you made shit more opaque. Mean going down = you transparent-ified it. Pick wisely.",
        "All zeros here means everything is now fully transparent. Unless that's what you wanted, fix it.",
        "High max and high mean after processing means your alpha fix actually worked. Good job, genius.",
    ],
    "rom_banner": [
        "Holy shit, the app figured out what game this is from the folder structure. Pretty smart, right?",
        "PS2 disc detected! SLUS_xxx files or SYSTEM.CNF gave it away. Your game textures are in good hands.",
        "Console detected from file patterns. The app isn't psychic — it just reads the damn folder names.",
        "Disc ID shown here. Use it to look up the game or find cover art. Also nice to know what you're fixing.",
        "If it detected the wrong console, blame the modder who named their folders weirdly. Not our fault.",
    ],
}

# Fallback tips when a key isn't in the specific mode dict
_FALLBACK_NORMAL = ["Hover for more info.", "Check the docs for details.",
                    "Click to interact.", "Part of the Alpha Fixer interface.",
                    "Contact support if you need help."]
_FALLBACK_DUMBED = ["It does a thing. Click it.", "Hover longer next time.",
                    "Looks important. Probably is.", "Just try clicking it.",
                    "Instructions unclear. Try again."]
_FALLBACK_VULGAR = ["It's a button. Click the damn thing.",
                    "No tip here. Just click it and see.",
                    "Figure it out, genius. You're doing great.",
                    "This widget exists. That's all we've got.",
                    "Click. It does something. Trust the process."]

_MODE_TIPS = {
    "Normal":           (_NORMAL, _FALLBACK_NORMAL),
    "Dumbed Down":      (_DUMBED, _FALLBACK_DUMBED),
    "No Filter 🤬": (_VULGAR, _FALLBACK_VULGAR),
}

TOOLTIP_MODES = ["Normal", "Off", "Dumbed Down", "No Filter 🤬"]

# ---------------------------------------------------------------------------
# TooltipManager
# ---------------------------------------------------------------------------

class TooltipManager(QObject):
    """
    Install on the QApplication.  Register widgets with a tip key.
    Intercepts QEvent.Type.ToolTip events and shows mode-appropriate cycling tips.
    """

    def __init__(self, settings, parent: QObject = None):
        super().__init__(parent)
        self._settings = settings
        # Map widget id → tip key
        self._widget_keys: dict[int, str] = {}
        # Per-key: next index to show (advances only on widget change)
        self._cycle: dict[str, int] = {}
        # Per-key: index most recently displayed (used to re-show same tip)
        self._shown_idx: dict[str, int] = {}
        # Track the last widget key shown so we only advance when user moves
        # to a different widget and comes back, not on every tiny mouse move.
        self._last_shown_key: str | None = None
        # Map QTabBar id → list of tip keys (one per tab index)
        self._tab_bar_keys: dict[int, list[str]] = {}
        # Strong references to registered QTabBar Python wrapper objects so
        # that id() remains stable even between re-lookups (PyQt6 may return
        # different wrapper objects from tabBar() on each call).
        self._tab_bar_refs: dict[int, object] = {}
        # Map QTabWidget id → QTabBar so we can handle tooltip events that
        # Qt dispatches to the container rather than the bar child.
        self._tab_widget_to_bar: dict[int, object] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install_on_app(self, app) -> None:
        app.installEventFilter(self)

    def register(self, widget, tip_key: str) -> None:
        """Map widget → tip_key so tooltips cycle through variants."""
        self._widget_keys[id(widget)] = tip_key
        # Ensure native tooltip is cleared so we control it fully
        widget.setToolTip("")

    def register_tab_bar(self, tab_bar, tip_keys: list) -> None:
        """Register a QTabBar so each tab index maps to its own tip_key.

        *tip_keys* must be a list with one entry per tab (same order as tabs).

        Also registers the parent QTabWidget (if any) so tooltip events
        dispatched at the container level are handled correctly.
        """
        bar_id = id(tab_bar)
        self._tab_bar_keys[bar_id] = list(tip_keys)
        # Keep a strong Python reference so id(tab_bar) stays stable.
        self._tab_bar_refs[bar_id] = tab_bar
        tab_bar.setToolTip("")
        # Also clear per-tab native tooltips
        for i in range(len(tip_keys)):
            try:
                tab_bar.setTabToolTip(i, "")
            except Exception:
                pass
        # Register the parent QTabWidget so events coming from that level are
        # routed to the bar.
        try:
            parent_widget = tab_bar.parent()
            if parent_widget is not None:
                self._tab_widget_to_bar[id(parent_widget)] = tab_bar
                parent_widget.setToolTip("")
        except Exception:
            pass

    def mode(self) -> str:
        default = _SettingsManager._DEFAULTS.get("tooltip_mode", "No Filter 🤬")
        return self._settings.get("tooltip_mode", default)

    # ------------------------------------------------------------------
    # Event filter
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ToolTip:
            return False

        obj_id = id(obj)

        # Check if this is a registered QTabBar with per-tab keys
        tab_keys = self._tab_bar_keys.get(obj_id)
        if tab_keys is None:
            # Fallback: scan registered bar refs for identity match.
            # PyQt6 sometimes creates a new Python wrapper for the same C++
            # QTabBar object, giving a different id().  Comparing with `is`
            # will still catch the case when we happen to have the same wrapper.
            for bar_id, bar_ref in self._tab_bar_refs.items():
                try:
                    if bar_ref is obj:
                        tab_keys = self._tab_bar_keys.get(bar_id)
                        obj_id = bar_id  # update so key lookup below is consistent
                        break
                except RuntimeError:
                    # Wrapped C++ object may have been deleted; skip safely.
                    pass
        if tab_keys is not None:
            return self._handle_tab_bar_tooltip(obj, event, tab_keys)

        # Check if this is the parent QTabWidget whose tab bar is registered.
        # Qt sometimes dispatches the ToolTip event to the QTabWidget rather
        # than the child QTabBar, so we remap it here.
        bar_ref = self._tab_widget_to_bar.get(id(obj))
        if bar_ref is not None:
            bar_id = id(bar_ref)
            # Try stable bar_id first, then scan refs for identity match
            tab_keys = self._tab_bar_keys.get(bar_id)
            if tab_keys is None:
                for _bid, _bref in self._tab_bar_refs.items():
                    try:
                        if _bref is bar_ref:
                            tab_keys = self._tab_bar_keys.get(_bid)
                            break
                    except RuntimeError:
                        # Wrapped C++ object may have been deleted; skip safely.
                        pass
            if tab_keys is not None:
                # Map the event position from QTabWidget coordinates to QTabBar
                # coordinates (the bar sits at the top of the widget).
                try:
                    bar_pos = bar_ref.mapFrom(obj, event.pos())
                    tab_idx = bar_ref.tabAt(bar_pos)
                except (RuntimeError, AttributeError):
                    tab_idx = -1
                if 0 <= tab_idx < len(tab_keys):
                    return self._show_tip_for_key(bar_ref, event, tab_keys[tab_idx])

        key = self._widget_keys.get(id(obj))
        if key is None:
            return False

        return self._show_tip_for_key(obj, event, key)

    def _handle_tab_bar_tooltip(self, tab_bar, event, tab_keys: list) -> bool:
        """Show a per-tab tooltip based on which tab is under the cursor."""
        mode = self.mode()
        if mode == "Off":
            from PyQt6.QtWidgets import QToolTip
            QToolTip.hideText()
            return True
        try:
            tab_idx = tab_bar.tabAt(event.pos())
        except Exception:
            tab_idx = -1
        if tab_idx < 0 or tab_idx >= len(tab_keys):
            return False
        key = tab_keys[tab_idx]
        return self._show_tip_for_key(tab_bar, event, key)

    def _show_tip_for_key(self, obj, event, key: str) -> bool:
        """Resolve the cycling tip for *key* and show it."""
        mode = self.mode()
        if mode == "Off":
            from PyQt6.QtWidgets import QToolTip
            QToolTip.hideText()
            return True

        tips_dict, fallback = _MODE_TIPS.get(mode, _MODE_TIPS["Normal"])
        variants = tips_dict.get(key, fallback)
        n = len(variants)

        # Only advance the cycle counter when the user moves to a different
        # widget.  Tiny mouse moves within the same widget fire multiple
        # ToolTip events; advancing on each one makes the tip spin too fast.
        if key != self._last_shown_key:
            idx = self._cycle.get(key, 0) % n
            self._cycle[key] = (idx + 1) % n
            self._shown_idx[key] = idx
            self._last_shown_key = key
        else:
            # Re-show the same tip that was last displayed for this widget.
            idx = self._shown_idx.get(key, 0)

        tip_text = variants[idx]
        try:
            from PyQt6.QtWidgets import QToolTip
            QToolTip.showText(event.globalPos(), tip_text, obj)
        except (AttributeError, ImportError):
            pass
        return True  # suppress the default tooltip
