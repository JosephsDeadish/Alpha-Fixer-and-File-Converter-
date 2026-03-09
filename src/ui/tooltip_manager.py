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

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QToolTip

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
        "PS2: alpha × 0.502 (half intensity for PlayStation 2).",
        "N64: alpha set to 255 (fully opaque for Nintendo 64).",
        "No Alpha: removes transparency entirely.",
        "Max Alpha: sets all pixels to full opacity.",
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
    "invert_check": [
        "Invert the alpha channel after applying the other operations.",
        "Flips opaque ↔ transparent across all processed pixels.",
        "Combine with threshold for creative masking effects.",
        "Useful for converting 'transparency maps' to 'opacity maps'.",
        "The result is: new_alpha = 255 − computed_alpha.",
    ],
    "out_dir": [
        "Specify a custom output folder for processed files.",
        "Leave blank to save output alongside each source file.",
        "Processed files get a '_fixed' suffix by default.",
        "Use the Browse button to pick the folder visually.",
        "The folder will be created automatically if it doesn't exist.",
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
        "PNG is lossless and supports transparency.",
        "DDS is used in game engines (DirectX surface format).",
        "JPEG is lossy but small; does not support transparency.",
        "WEBP offers smaller file sizes with optional losslessness.",
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
        "Themes change colors and enable unique click particle effects.",
        "The Bat Cave theme adds periodic bat flyovers across the window!",
        "Try Gore, Rainbow Chaos, or Galaxy for dramatic visual effects.",
        "You can create custom color themes in the Theme tab of Settings.",
    ],
    "effect_combo": [
        "Choose the click particle effect style for this theme.",
        "Each effect matches its theme — try Gore for blood, Galaxy for stars.",
        "Select 'Custom' to fire your own emoji as particles on every click.",
        "The effect activates whenever you click anywhere in the main window.",
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
    "patreon_btn": [
        "Support development on Patreon!",
        "Your support funds new features and themes.",
        "Patrons get early access to new hidden themes.",
        "Even $1/month helps keep the panda well-fed 🐼",
        "Visit patreon.com/c/DeadOnTheInside",
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
        "PS2, N64, etc. – they're just names for how the alpha gets changed.",
        "PS2 makes it half transparent. N64 makes it fully opaque. There ya go.",
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
    "invert_check": [
        "Check this to flip transparent ↔ opaque. It's like turning inside out.",
        "Invert = opposite. What was transparent is now opaque. Simple.",
        "This one's a bit advanced. You sure you need it? No pressure.",
        "Unchecked = normal. Checked = opposite. There you go.",
        "Use with threshold for fancy effects you can pretend you intended.",
    ],
    "out_dir": [
        "Where do you want the fixed files to go? Type or browse. Simple.",
        "Leave blank and they go in the same folder as the originals. Easy.",
        "Browse = open a folder picker. Typing also works if you remember paths.",
        "Output folder is just where the results end up. You got this.",
        "Pro tip: create a folder called 'FIXED' first. Very professional.",
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
        "Pick the format you need. If you don't know, ask Google.",
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
        "The theme changes how the app looks. Some have cool effects!",
        "Bat Cave makes bats fly across the screen. Because why not.",
        "Gore theme has blood splatter. It's... tasteful. Mostly.",
        "Rainbow Chaos will do things to your eyes. You've been warned.",
    ],
    "effect_combo": [
        "It's the sparkle chooser. Pick how things explode when you click.",
        "Yes, you can pick which particles fly out — it's the dropdown above.",
        "Select 'Custom' and then add your own emoji in the section below.",
        "Each option makes different things shoot out. Apparently this needs explaining.",
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
}

# No Filter 🤬 – vulgar, funny, profanity, but actually still helpful
_VULGAR: dict[str, list[str]] = {
    "add_files": [
        "Click this damn button and add your freaking images already. PNG, DDS, all that crap.",
        "Oh for f**k's sake, just click it. It adds files. What the hell were you waiting for?",
        "Drag your ass-backwards images in here or click 'Add Files'. Either works, genius.",
        "This bastard button opens a file dialog. Pick your sh*t and let's get processing.",
        "Ctrl+O also works, in case you're too damn lazy to click. Love you. 🐼",
    ],
    "add_folder": [
        "Add a whole f**king folder at once. Because clicking one file at a time is for suckers.",
        "Got a billion images? Shove the whole damn folder in here. That's what this is for.",
        "Ctrl+Shift+O works too, smartass. One folder. All the images. Let's go.",
        "Enable subfolders if you've got a nested hellscape of directories. It handles it.",
        "It scans the entire f**king folder for supported images. Sit back and let it work.",
    ],
    "clear_list": [
        "Clear this sh*t out and start over. Your files on disk are FINE, calm the f**k down.",
        "Panic? Don't. This only clears the list, not your actual damn files.",
        "Starting fresh? Just nuke the list. It's not that serious.",
        "Delete key removes one item. This button removes all of it. Pick your chaos.",
        "It's FINE. Press it. Everything on disk stays. Go nuts.",
    ],
    "process_btn": [
        "Hit this big-ass green button and make the magic happen. F5 also works, lazy.",
        "CLICK THE DAMN PROCESS BUTTON. This is literally what we've been building toward.",
        "Every file in that list is about to get its alpha fixed. Hell yeah. Let's GO.",
        "It'll process everything. The progress bar will fill up like a beautiful river of results.",
        "F5, motherf**ker. Keyboard shortcuts exist for a reason.",
    ],
    "stop_btn": [
        "Changed your mind, chickenshit? Click Stop. The current file finishes first.",
        "Hit this if you screwed up the settings and need to abort. No judgment. Mostly.",
        "Esc also works. It stops without your cursor having to move an inch.",
        "The current file won't be half-processed. It finishes. Then everything stops.",
        "Stop is for cowards. Kidding. Stop whenever the hell you want.",
    ],
    "preset_combo": [
        "Pick a damn preset. PS2 = alpha × 0.5 for PlayStation 2 textures. Classic.",
        "N64 sets alpha to 255 (fully f**king opaque). Old-school Nintendo vibes.",
        "No Alpha removes transparency entirely. Bye-bye see-through. Hello solid block.",
        "Max Alpha makes everything opaque too but keeps the channel. Subtle difference.",
        "Make your own preset with the fine-tune controls below. Save it. Name it cursively.",
    ],
    "alpha_slider": [
        "Drag this slider, goddamnit. 0 = invisible ghost. 255 = solid-ass opaque.",
        "The slider and the number box are the same f**king thing. Use whichever.",
        "Mode 'set' replaces everything with this value. Mode 'multiply' scales it. Simple.",
        "Alpha is just how see-through a pixel is. 0 = glass. 255 = brick wall.",
        "Only matters when 'Use preset' is UNCHECKED. Check that first, genius.",
    ],
    "threshold_spin": [
        "Threshold: only process pixels with alpha BELOW this number. 0 = process all the sh*t.",
        "Set to 255 and you'll process almost nothing. Set to 0 and everything gets the treatment.",
        "Leave it at 0 if you want every pixel touched. That's usually what you want.",
        "It's a filter. Below the threshold: processed. Above: left the f**k alone.",
        "128 = only touch the semi-transparent half. Advanced stuff for fancy people.",
    ],
    "invert_check": [
        "Invert flips transparent ↔ opaque. It's the 'f**k it, reverse everything' option.",
        "Checking this makes what was solid go invisible and vice versa. Wild chaos.",
        "Combine with threshold for effects you can pretend were intentional.",
        "The math: new alpha = 255 minus the computed alpha. That's it. Not rocket science.",
        "Leave unchecked unless you really know what you're doing. Or don't. We're not your mom.",
    ],
    "out_dir": [
        "Where do you want your freshly f**ked-with files to go? Pick a damn folder.",
        "Leave it blank and files save next to the originals. Easy mode.",
        "Pro move: make an 'output' folder first so your organized ass can find things.",
        "Browse button works. Typing a path works too if you remember where the hell your stuff is.",
        "The folder gets CREATED if it doesn't exist. The app has your back, you messy bastard.",
    ],
    "compare_widget": [
        "Drag the red handle and see what the f**k you just did to your image.",
        "Left = original. Right = fixed. Drag to compare. This is literally the point.",
        "The handle dragging is satisfying as hell. You'll do it way more than necessary.",
        "Select a file from the list above first, dumbass. Nothing to show without a file.",
        "Changes settings and watch the right side update automatically. Beautiful chaos.",
    ],
    "file_list": [
        "Drop your damn files here or use the buttons. Either way, fill this list up.",
        "Click a file to see the before/after comparison below. That's why we made it.",
        "Right-click to remove one file. Delete key works too. Power is yours.",
        "Empty list = nothing to process, you absolute walnut. Add something first.",
        "Drag folders right in here. The app sorts out which files are images. Magic.",
    ],
    "convert_btn": [
        "Convert this pile of images to your format of choice. F5 also works, keyboard warrior.",
        "Hit it. Watch the progress bar. Revel in the format changing.",
        "Every file gets converted. The old format stays put unless you specifically set overwrite.",
        "Quality matters for JPEG/WEBP. For PNG, quality is a meaningless concept.",
        "F5, baby. The keyboard shortcut of champions.",
    ],
    "format_combo": [
        "PNG or go home. No wait, DDS if it's for games. WEBP if you want to feel modern.",
        "These are image formats. PNG = lossless perfection. JPEG = lossy garbage (but small).",
        "DDS is for game engines. If you don't know what that is, pick PNG.",
        "WEBP is like PNG had a baby with JPEG and the baby turned out pretty good actually.",
        "TGA is old-school. ICO is for Windows icons. GIF makes it animate (kinda).",
    ],
    "quality_spin": [
        "Higher quality = better image, bigger file. Lower = potato, but tiny.",
        "Leave it at 90 and move on. It's fine. I promise it's f**king fine.",
        "Only JPEG and WEBP care about this number. PNG laughs at your quality setting.",
        "100 = best quality. 1 = garbage pile. 85-95 is the sweet spot for normal people.",
        "Move this number and absolutely nothing visible will change. You'll do it anyway.",
    ],
    "settings_btn": [
        "Open settings and make this app look less like a corporate hellscape.",
        "Themes! Gore! Bats! Rainbows! It's all in here. Go nuts.",
        "Ctrl+, also works. Settings: where you waste 20 minutes choosing a theme.",
        "Mouse trail is in here. Turn it on. It looks rad as hell.",
        "You can also break nothing in here. Except maybe your color taste.",
    ],
    "theme_combo": [
        "Choose a theme. Gore has blood splatter. Bat Cave has literal f**king bats. You're welcome.",
        "Rainbow Chaos will assault your retinas. You'll love it or hate it. No in-between.",
        "Galaxy theme is for when you want to feel like you're coding in space.",
        "Otter Cove is cute and cozy. Galaxy Otter is cuter AND cosmic. Best of both worlds.",
        "Goth theme for when you're feeling angsty and want skulls everywhere.",
    ],
    "effect_combo": [
        "Choose your f**king particle style. Gore shoots blood. Rainbow shoots unicorns. Pick one.",
        "This controls what explodes out of your cursor. It's important. Choose wisely.",
        "Custom lets you use your own emoji. What kind of unhinged particles will you pick?",
        "Galaxy shoots stars. Otter shoots otters. What more do you want from life?",
        "If you pick Default and complain about the sparks, that's entirely on you.",
    ],
    "custom_emoji": [
        "Type your deranged emoji and watch them blast across the screen like beautiful chaos.",
        "Add whatever weird-ass emoji you want as click particles. No judgment. Mostly.",
        "These fly out when you click. Choose wisely. Or chaotically. Both work.",
        "Clear All nukes your entire emoji list. Gone. You did that. Own it.",
        "Paste multiple emoji at once and they all join the flying circus. 🎪",
    ],
    "tooltip_mode_combo": [
        "You're using No Filter 🤬 mode. Good f**king choice. Respect.",
        "Pick 'Off' to turn all this off. Boring, but we get it.",
        "Normal mode is helpful but lacks the spice. You're above that.",
        "Dumbed Down is for when you want to be gently insulted. You're above that too.",
        "This right here? This mode? Best mode. You chose correctly. 🤬",
    ],
    "save_preset": [
        "Save your damn preset so you don't have to redo this every time.",
        "Name it something useful, not 'aaaa'. Future you will be grateful.",
        "It saves the current settings as a named preset. Click it, genius.",
        "This button literally saves your work. Use it.",
        "Saved presets live in the dropdown. Useful as hell.",
    ],
    "delete_preset": [
        "Deletes the preset. It's gone. The built-ins can't be deleted. Don't even try.",
        "Click delete, confirm the dialog, and that preset is f**king dead.",
        "You can recreate it in 30 seconds. It's not that serious.",
        "Gone. Poof. It's done. The app continues. You continue. Life goes on.",
        "Built-ins survive everything. Your custom ones? Gone with a click.",
    ],
    "patreon_btn": [
        "Give the dev your money! They made this beautiful sh*t and they deserve it.",
        "Patreon: because software doesn't write itself and developers need to eat.",
        "Even a dollar helps! That's less than your daily coffee, you caffeinated maniac.",
        "Your support funds new themes, more effects, and better pandas. Worth it.",
        "patreon.com/c/DeadOnTheInside – click it. Do it. Be a hero. 🐼",
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
        # Per-key cycle counter
        self._cycle: dict[str, int] = {}

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

    def mode(self) -> str:
        return self._settings.get("tooltip_mode", "Normal")

    # ------------------------------------------------------------------
    # Event filter
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ToolTip:
            return False

        key = self._widget_keys.get(id(obj))
        if key is None:
            return False

        mode = self.mode()
        if mode == "Off":
            # Suppress all tooltips
            QToolTip.hideText()
            return True

        tips_dict, fallback = _MODE_TIPS.get(mode, _MODE_TIPS["Normal"])
        variants = tips_dict.get(key, fallback)

        idx = self._cycle.get(key, 0) % len(variants)
        self._cycle[key] = (idx + 1) % len(variants)

        tip_text = variants[idx]
        try:
            QToolTip.showText(event.globalPos(), tip_text, obj)
        except AttributeError:
            pass
        return True  # suppress the default tooltip
