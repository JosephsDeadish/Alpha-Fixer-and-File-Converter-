# 🐼 Alpha Fixer & File Converter

A panda-themed desktop application for image, animation, and video processing:

The desktop interface uses **Qt 6 through PyQt6**, not Tkinter. Both release
bundle specifications exclude Tkinter; no toolkit migration is required.

## Tools

### 🖼 Alpha Fixer
Fix, adjust, and batch-process alpha channels on image files (PNG, DDS, JPG, JPEG/JFIF/JPE, BMP, TIFF, GIF, WEBP, TGA, ICO, PBM, PGM, PNM, PPM, PCX, AVIF, QOI, SVG, JPEG2000, XNB, TIM).

SVG, XNB, and TIM can be saved after processing.

**Features:**
- Built-in presets: **PS2** (128), **N64** (255), **No Alpha**, **Max Alpha**, **Transparent**, **Half Transparent**, **Invert Alpha**, **Threshold Cut**
- Presets reflect their exact values when selected
- Save and manage your own custom presets
- Fine-tune mode: set, multiply, add, subtract, clamp (per-pixel control)
- Threshold: only affect pixels below a certain alpha value
- Batch process entire folders and subfolders
- Custom output folder / filename suffix

### 🔄 File Converter
Convert between image formats with optional resize and quality control.

**Supported formats:** PNG, JPEG (including `.jfif` / `.jpe`), BMP, TIFF, WEBP, TGA, ICO, GIF, DDS, PBM, PGM, PNM, PPM, PCX, AVIF, QOI, SVG, JPEG2000, XNB, TIM

**Features:**
- Convert any supported format to any other
- Batch convert whole folders and subfolders (preserves directory structure)
- Optional resize (width × height)
- Live previews reflect enabled resize dimensions, including aspect-linked width/height edits.
- JPEG/WEBP/AVIF/JPEG2000 quality control
- Custom output folder

### 🎨 Selective Alpha Tool
Paint or erase alpha adjustments, work with visible zones and masks, and undo/redo edits before saving.
Single-zone clipboard paste and full-layout slot paste have independent availability in the canvas menu. Shared zones work through both sidebar and menu paste; copied masks scale to the newly opened image. Failed image loads preserve existing edits and their save target.

### 🎞 GIF Builder
Arrange image/GIF frames, preview animation, adjust frame timing, and export a GIF. Preview playback respects per-frame delays from its first tick and during live timing edits; removing frames immediately stops playback when fewer than two remain.

### 🎬 Video Builder
Build a timeline from images, animations, and video clips, trim clips, and export GIF or MP4. Video import and MP4 export use the bundled FFmpeg runtime.
GIF Builder and Alpha Painter stage saves beside the destination and replace it only after encoding succeeds, preserving existing files on save failure. Video Builder also stages final output on the destination filesystem for cross-drive exports. Painter filenames without an extension use the selected save format (PNG by default), and its original-deletion prompt defaults to Keep Original.
All three tools confirm replacing an existing file when an automatically added or changed extension produces a different destination; declining leaves the file untouched.
History exports (TXT, CSV, JSON, HTML) and Converter failure reports (TXT, JSON) use the same overwrite confirmation and staged-save protection, including preserving an existing report if writing fails.
Confirmed builder exports pause preview playback. Canceling the save dialog leaves playback unchanged. GIF Builder copies frames incrementally, then prepares and encodes them on a background worker so the interface remains responsive. Editing/import and duplicate exports are disabled until cleanup completes. Cancel during Pillow encoding discards the staged result after that encoding call returns; it does not forcibly interrupt the codec. Cancellation accepted before atomic publication preserves an existing destination and adds no successful-export history. Closing the builder or application requests cancellation and waits asynchronously for cleanup; a direct application exit also drains pending cleanup before releasing Qt objects.
Video Builder also renders, encodes and muxes audio on a background worker, using independent render sources rather than the preview decoder. Editing/import and duplicate exports are disabled until cleanup. Cancellation during native encoding discards the staged result after the current codec call returns; FFmpeg audio muxing is polled for cancellation and its process is stopped before cleanup. Accepted cancellation preserves existing output and records no successful export. Audio-mux failure still permits a silent MP4 recovery with explicit completion/history notes. Builder/application closure waits for both GIF and Video export workers without forcibly terminating their threads; cancel requests arriving after atomic publication begins cannot undo that completed export.
Video snapshots retain only the selected trim/speed frames for in-memory sources, so unreadable discarded frames do not prevent export. Closing or accepting/rejecting Video Builder releases its sources and resets the timeline/preview; reopening starts with a consistent empty session.
GIF Builder reordering preserves the playing frame and refreshes the selected frame's delay controls. Frames using global timing show the current global delay; enabling an override starts from that value.

**Editable projects and recovery:** Both builders provide **Save Project…**, **Open Project…**
and **Recover…** buttons, separate from media export (**Ctrl+S still exports media**).
Version-1, strictly validated JSON projects retain timeline order, timing, selection,
playhead and export settings. GIF projects embed lossless PNG frames, including generated
frames and original per-frame holds; they do not need their import sources. Video projects
reference original media relative to the project (absolute paths across Windows drives),
retaining stream choices, trim/speed, split video sampling and continuous audio spans,
still duration, adjustments and audio settings. Streaming SHA-256 signatures identify
source contents at save/open in the project worker (not on polling ticks). Loaded and
successfully saved clips retain their validated hash; saves/autosaves reject changed
content rather than silently adopting replacements. Before the first save, imports
retain device/inode/size/mtime/ctime identity across decoding and worker hashing.
This detects ordinary replacement but is not a content guarantee on filesystems
with unreliable timestamps or against metadata-spoofing writers. No file lock spans
hashing and publication; concurrent changes after validation remain possible.
Keep original media available; changed,
missing, corrupt or unsupported media abort opening without replacing current edits.

Project encoding, disk I/O and media preparation run in a background worker. Project
opening temporarily disables editing; saves capture a bounded snapshot and allow further
edits, which remain unsaved. Atomic sibling-file replacement preserves an existing project
on write failure. Dirty replacement/closure requires confirmation; choosing Save on close
waits for successful project publication. Application shutdown keeps every editor's
recovery session and media resources active if another editor cancels; all media is
released only after every editor approves shutdown and pending I/O finishes.
Supplied imports, including imports
into a reopened builder, remain unsaved and eligible for recovery without further edits.
Export workers retain their existing cancellation
and asynchronous-close lifecycle.

Autosave polls lightweight metadata every 2 seconds, writing after approximately 5 seconds
of inactivity or 30 seconds of continuous editing; it never encodes images on each slider
tick. Each editor session has a unique locked recovery file in the per-user
`AlphaFixerConverter/editor-recovery` directory. **Recover…** lists retained closed/crashed
sessions (not live writers), with timestamps and identities, without startup popups.
Recovered edits require Save Project. Last complete recovery copies remain after save,
close and failed writes; errors appear in project status, with retries no sooner than
30 seconds. Remove old copies manually from that directory when no longer needed.

Honest project limits: 512 entries, 64 MiB JSON, 8 MiB per embedded PNG, dimensions
up to 8192 per axis, and 8,388,608 total initial decoded pixels (32 MiB RGBA;
for Video, each still/GIF frame and the initial frame of each video clip count).
Video timelines are capped at 10,000,000 output frames, source FPS at 0.1–1000,
and still duration at 1–3600 frames. GIF inherited delay is 10–3000 ms; stored
individual source/override holds support 1–3,600,000 ms. Recovery discovery lists
at most 1000 files per editor type. These bounds apply to project persistence, not
the general import/export workflow; oversized sessions report a save error rather
than silently omitting frames.
Floating comparisons release their windows when redocked or closed. Alpha helper switches stay synchronized only with the currently open comparison, and Converter restores GIF speed controls on redock without resetting playback speed. Clearing a Converter preview also closes its floating comparison.
Open floating comparisons also follow new preview images, processing state, statistics, overlays, and divider theme changes. GIF frames continue to mirror the current source animation after preview refreshes without starting a second decoder; floating zoom and divider position remain independent.
Settings asks before replacing a named custom theme on save or import, with No selected by default; declining keeps the saved theme and active appearance unchanged. Theme deletion also defaults to No. JSON theme exports use staged writes and confirm overwrites after an automatic extension is added.
Settings separates Theme, General, Sound, and Effects. Optional click/background/mouse/cursor/button effects live in Effects instead of crowding theme editing. Settings save live; Close retains changes. “Use theme” follows the active theme, while disabling it selects a manual style; disabling an effect retains its preferences.
Click-effect theme mode updates the displayed style immediately and uses imported/custom theme effect metadata when no built-in mapping exists. Applying a preset or imported theme does not replace the remembered manual click style; turning theme mode off restores it, including after reopening Settings.
Cursor and hold-click dropdowns also show their effective themed styles while disabled in theme mode, without replacing saved manual choices. Theme emoji cursors are displayed even when their glyph is not in the standard cursor list. Turning theme mode off restores the manual selection.
Font size and UI scale apply live. The splash-screen preference applies on the next launch; a full factory reset requires restarting to fully apply defaults, as stated before confirmation. Opening Settings loads these preferences without reapplying scale or emitting live-save notifications.
Settings backup imports validate recognized preference types and supported numeric ranges before changing anything. Invalid values reject the whole import with the offending preference and its allowed range; legacy boolean/integer strings remain supported and unknown keys are ignored. History limits accept 10–5000 globally, or 0–5000 per tool (0 follows the global limit).
Settings backup exports from the application add `.json` when needed, confirm replacement when that changes the destination, and stage writes beside the destination so a failed export preserves the previous backup.
Backup imports also validate shortcut maps (text bindings), Painter zone alpha arrays and RGBA color rows before saving anything. Zone components must be integers in 0–255; legacy integer strings and up to 40 stored zones remain supported, while the Painter still displays seven zones. Empty custom colors and shortcut maps retain their default behavior.
Active and saved theme backups must contain JSON objects with valid supplied Qt colors, non-empty supplied names and text effect/cursor metadata. Invalid themes reject the complete backup without changing saved preferences. Partial legacy themes still inherit missing colors from defaults, and unknown non-metadata extension fields are preserved.
Custom-preset backups are checked for usable names, descriptions, boolean flags and alpha ranges/thresholds in 0–255 before import. Legacy fixed-alpha records and integer-string range endpoints remain supported; invalid records reject the whole backup instead of being silently dropped when presets reload.
Settings guidance and theme-status hints follow the active theme's text colors and UI font size, with wrapping for narrow windows. The reset button uses the theme's error color instead of a fixed light-red style.
Shared push buttons, text inputs, dropdowns, numeric inputs, checkboxes and radio buttons show dotted theme-colored borders when focused, without changing their border widths. Accent actions reserve border space so keyboard focus does not shift the layout.
Shared horizontal slider handles and toolbar buttons also show dotted keyboard-focus cues with reserved border space; disabled controls do not use these active-focus cues.
Settings footer actions stack vertically when a narrow window or large UI font cannot fit the horizontal button row, and return to a row when space permits, including after live font or theme changes without resizing. Screen fitting accounts for this before sizing the dialog.
History export/clear actions also stack in compact windows and adapt to live font changes. History headings and all five sub-tab summaries wrap instead of forcing a wider window.
Tutorial step counters, tips and shortcut hints use the active theme text color and follow live UI font sizing rather than fixed small text.
Tutorial navigation buttons stack when a compact window cannot fit their labels and restore the horizontal row when space permits, including after live font and Next/Finish label changes.
Embedded preview zoom and undock controls and floating Alpha/Converter redock buttons follow shared theme colors, UI font sizing and keyboard-focus styling. Zoom controls have explicit accessible names; embedded controls reposition after live styling changes, stack without overlapping in narrow previews, and use a compact undock/redock icon with a full accessible name and tooltip when its text cannot fit.
Painter zoom buttons and percentage now use the same scalable theme styling. On narrow canvases, the percentage moves below the buttons; live styling and zoom changes refresh its size and position.
Painter undo/redo and visibility controls also follow shared theme/font styling. Undo/redo counts are no longer constrained by fixed pixel-width caps, have accessible action descriptions, and refresh overlay sizing and zoom separation after live changes.
On narrow Painter canvases, visibility controls use compact labels with full accessible names and tooltips, and undo/redo buttons stack when their counts cannot fit side by side.
On short Painter canvases, history/visibility controls scroll at their normal font size, reserving space for zoom controls. Keyboard focus scrolls the selected action into view instead of leaving it outside the canvas.
Painted comparison-preview messages, Before/After labels and statistics inherit the live UI font. Messages wrap and narrow comparison labels/statistics use ellipses rather than drawing across the divider.
GIF Builder delay, loop and dimension readouts grow with their text and live UI font instead of using fixed pixel widths, keeping units and boundary values readable.
Video Builder trim, clip-speed, FPS, visual-adjustment and audio-volume readouts also grow with their text and font; adjustment names no longer use fixed-width caps.
GIF/Video rewind and play/pause buttons have explicit accessible names, and Video rewind grows with the UI font. Space activates focused buttons and checkboxes or enters text in editors instead of being intercepted by preview playback; Space still toggles playback from the frame/clip list, and customized playback shortcuts remain supported.
Alpha range/channel and Converter format/resize inputs are associated with their visible labels and have explicit accessible names. GIF/Video timing, stream, preview and export controls also have descriptive accessible names with units; GIF loop/size controls explain their zero-value behavior.
Settings appearance/history inputs and Video adjustment sliders are also associated with their labels. Settings sound/trail sliders have descriptive accessible names, independent of tooltip display preferences.
Painter brush/eraser sizes, overlay opacity, active zone alpha and slot selectors have label associations and distinct accessible names. Active-zone naming/color controls and History search fields also have explicit names that remain available when tooltips are off.
Painter slot action names distinguish single-zone masks from full-layout snapshots, including separate save/paste/rename/clear actions.
Alpha Fixer, Converter and History guidance also follows theme text colors and UI scaling, including Alpha preview-helper messages. Their capability banners use readable theme text on a theme surface, with warning/success borders; capability details remain available as text and tooltips.
GIF and Video Builder guidance and import/recovery messages use the same readable, scalable theme styling. Success, warning and error messages keep descriptive text and diagnostics, with theme-colored borders rather than fixed low-contrast text colors.
Painter workflow hints, single-zone/full-layout slot messages and shared-zone import guidance also follow the active theme and UI font size. Filled and empty slots remain distinguished by their text and paste-button availability.
Disabled buttons and input fields use a shared muted surface and dashed border, including accent actions. Disabled text selects a readable theme color; built-in palettes are checked for at least 4.5:1 text contrast. Painter canvas status and coordinate text follow UI scaling, and the status summary wraps instead of being cut off.
Trim edits immediately refresh the paused preview, including a playhead clamped by a shorter timeline. Preview FPS changes update active playback and duration summaries without restarting playback. Single-frame timelines remain exportable, but play and scrubbing are disabled.

History views record the tools' operations. Stopping a conversion prevents new work from starting; already-running files can finish, and their results are included in the final counts.
Alpha/Converter stopped batches retain their actual completion percentage and report unprocessed files instead of displaying 100% “Done”. History tooltips retain that status, searchable with `status:stopped`; TXT, CSV, JSON, and HTML exports also include Status and Not processed fields.
Original-file deletion defaults to **Keep Originals** and is offered only after a fully successful batch for recorded, existing, distinct outputs. Failed/unprocessed originals, in-place outputs, and sources sharing an output are never offered. Large batches retain an output manifest even when per-file success logging is suppressed.
History exports follow the visible, filtered row order and preserve complete filenames, including in plain text. GIF history thumbnails pause when their view is hidden, and export destination failures provide a recovery warning instead of an unhandled error.

## UI & Customization
- 🐼 **18 built-in themes**: Panda Dark (default), Panda Light, Neon Panda, Gore, Bat Cave, Rainbow Chaos, Otter Cove, Galaxy, Galaxy Otter, Goth, Volcano 🌋, Arctic ❄, Fairy Garden 🧚, Mermaid 🧜, Shark Bait 🦈, Alien 🛸, Noodle 🍜, Pancake 🥞
- **🔓 39 hidden unlockable themes** – earn them through use (clicks, alpha fixes, and conversions):
  - **Secret Skeleton** – unlocks at 100 total clicks
  - **Secret Sakura 🌸** – unlocks at 250 total clicks
  - Plus 37 more hidden themes that unlock progressively — keep using the app!
- Fully customizable color palette via Settings → Theme (15 editable colors)
- Save your own named themes and switch between them
- Theme imports validate colors, names, and effect metadata before changing preferences. Custom names retain their exact identity, and theme-following controls update immediately. Closing Settings applies pending live changes; resetting cancels them so old timers cannot restore erased preferences.
- “Use theme” sound and background-effect previews preserve your manual choices; unchecking the option restores them, even after switching themes or disabling/re-enabling the effect.
- Opening Settings preserves saved custom-background paths and theme-background selections instead of rewriting them during control initialization.
- **Per-theme click particle effects**: blood splatter (Gore), bat swarms + periodic flyovers (Bat Cave), unicorn sparkles (Rainbow Chaos), otter emojis (Otter Cove), star clusters (Galaxy/Galaxy Otter), skulls (Goth), rising flames (Volcano 🔥), snowflakes (Arctic ❄), pandas (Panda Dark/Light/Secret Sakura 🐼), electric bolts (Neon Panda ⚡)
- **11 mouse trail styles**: Dots, Ribbon, Noodle 🍜 (physics), Comet, Fairy Dust ✨, Wave 🌊, Sparkle ❄, Rainbow 🌈, Distortion Wave, Fire 🔥, Lightning ⚡ — configurable color and intensity
- **Animated banner** with 10 styles: Spin, Bounce, Shake, Pendulum, Pulse, Float, Flip, Orbit, Glitch, Drip (Settings → Theme). Legacy flock-themed banners display Bounce; independent flocks remain available under Background Effects.
- **Animated emoji cursors**: spin (⭐🔮💎), wobble (🦈🐉🌋), and symbol-cycling — automatically selected per theme or fully customizable (Settings → General)
- Custom cursor style (Default, Cross, Pointing Hand, Open Hand, and more)
- Click sound effects (built-in synthetic beep or point to your own .wav file)
- Font size control (8–24pt)
- **Cycling tooltips** with 4 modes (Settings → General → Tooltip Mode):
  - **Normal** – 5 helpful variants per widget, cycles on each hover
  - **Off** – tooltips disabled
  - **Dumbed Down** – simplified tips with gentle user-roasting
  - **No Filter 🤬** – extremely vulgar, profanity-filled, and *still actually helpful*
- All settings are persisted across sessions (last-used preset, format, quality, window geometry, etc.)
- **Export / Import preferences** to a portable JSON file (Help → Export / Import Settings), including theme effects, layout density, custom shortcuts, notification preferences, and history tracking/retention policies. History entries and unlock progress are not part of this preferences backup.
- Drag-and-drop files from Explorer/Finder directly onto the file lists
- Right-click or Delete key to remove items from file lists
- **Image preview pane** – select any file in the Converter list to see a live thumbnail + dimensions + size
- Converter format warnings clear when switching to a supported output and do not replace running/stopping batch feedback. Refreshing an animated source for format/quality edits preserves its preview playback speed; selecting a different source resets both the slider and animation to normal speed.
- **Before/After comparison slider** (Alpha Fixer) – select a file to see the original and processed result side by side, separated by a draggable red handle; drag left/right to reveal more of either side; auto-updates when preset or fine-tune settings change
- Alpha preview results and statistics are tied to the current file and adjustment request, so switching or clearing selections cannot restore stale images or errors. Queue clearing consistently cancels thumbnail work and notifies the host; repeated queue/painter menus release their temporary actions.
- **Processing history tab** – all past sessions (Converter **and** Alpha Fixer) recorded with timestamp, preset/format, and file count; split into two sub-tabs
- **Selective Alpha Tool** – paint alpha zones directly on an image with seven visible color-coded zones, brush/eraser tools, transform (move/rotate/scale), zone masks, and clipboard slots
- **Single-instance protection** – if you try to open the app a second time while it is already running, a friendly warning is shown instead of launching a duplicate window
- **HiDPI & multi-monitor aware** – fractional DPI scaling and multiple displays are handled by Qt; window position is corrected if a monitor is disconnected. Release qualification still requires native display testing on each supported platform.
- **Small-screen dialog access** – GIF/Video Builder windows fit the current screen, with scrollable controls when space or larger fonts require it. Tutorial, GIF frame selection, and shortcut dialogs also fit the available screen area.
- **GIF frame selection safeguards** – exporting requires at least one selected frame; unreadable GIFs cannot be accepted as empty exports.
- **❤ Patreon button** – support development at [patreon.com/c/DeadOnTheInside](https://www.patreon.com/c/DeadOnTheInside)
- **Keyboard shortcuts** (F1 for full list):
  - `F5` – Run / Process / Convert
  - `Esc` – Stop current operation
  - `Ctrl+O` – Add files
  - `Ctrl+Shift+O` – Add folder
  - `Delete` – Remove selected files from list
  - `Ctrl+,` – Open Settings
  - `Ctrl+Q` – Quit

## Requirements

- Python 3.10+
- PyQt6 ≥ 6.4.0
- Pillow ≥ 10.0.0
- numpy ≥ 1.24.0
- imageio ≥ 2.33.0
- Wand 0.7.2 (DDS compression through ImageMagick)
- vtracer 0.6.15 (raster-to-vector SVG export)

These are **source/build requirements**, not installations required on an end user's
machine. Release bundles include the Python runtime, these packages, QtSvg,
FFmpeg, ffprobe, and the ImageMagick libraries, coder modules, and configuration.
The build must fail if required native components are unavailable; a reduced-capability
build must not be published as a complete release.

Native dependencies must be installed on the **build host**. Windows/macOS/Linux
still need a compatible operating system, graphics/display stack, and audio device
for features that use them; packaging does not replace operating-system drivers.

Settings and logs remain next to the executable for writable portable installs.
Read-only installations use per-user application data instead (`LOCALAPPDATA` on
Windows, `~/Library/Application Support` on macOS, and `XDG_DATA_HOME` or
`~/.local/share` on Linux), so ordinary users do not need administrator access.

Install Python dependencies:
```bash
pip install -r requirements.txt
```

### Linux system libraries

PyQt6 requires several system-level shared libraries, including the extra X11/XCB packages needed by the packaged Linux app. Use the one-shot installer:

```bash
bash scripts/install_linux_deps.sh
```

Or install manually by distribution:

| Library | Ubuntu / Debian | Fedora / RHEL | Arch | openSUSE |
|---|---|---|---|---|
| libEGL (`libegl1`) | `sudo apt-get install -y libegl1` | `sudo dnf install -y mesa-libEGL` | `sudo pacman -S mesa` | `sudo zypper install -y libEGL1` |
| libGL (`libgl1`) | `sudo apt-get install -y libgl1` | `sudo dnf install -y mesa-libGL` | *(included)* | `sudo zypper install -y libGL1` |
| libpulse (`libpulse0`) | `sudo apt-get install -y libpulse0` | `sudo dnf install -y pulseaudio-libs` | `sudo pacman -S libpulse` | `sudo zypper install -y libpulse0` |
| Qt X11/XCB extras (`libxcb-cursor.so.0`, `libxkbcommon-x11.so.0`, etc.) | `sudo apt-get install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0` | `sudo dnf install -y libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm` | `sudo pacman -S libxkbcommon-x11 xcb-util xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm` | `sudo zypper install -y libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0` |

If any of these are missing, `main.py` will detect the problem at startup and print the exact install command for your distribution before exiting cleanly — no cryptic crashes.

For **source development/building**, also install [ImageMagick](https://imagemagick.org/).
Packaged users do not need a separate ImageMagick installation.
BC6H / BC7 / other advanced DX10 DDS variants are currently only supported when Pillow or ImageMagick/wand can decode them directly; the pure-Python fallback now fails clearly instead of fabricating placeholder pixels.

## Running

```bash
python main.py
```

## Building a Standalone Executable

You can package the app locally using [PyInstaller](https://pyinstaller.org/).

### Quick build

```bash
# Install build tools
pip install -r requirements-dev.txt

# Linux / macOS
bash scripts/build_exe.sh

# Windows
scripts\build_exe.bat
```

The finished application is placed in `dist/AlphaFixerConverter/`.
Run `AlphaFixerConverter` (Linux/macOS) or `AlphaFixerConverter.exe` (Windows) from that folder.
Both build scripts now reuse `scripts/verify_packaged_app.py` to smoke-launch the packaged app and dump runtime capabilities, so the same verification step can be re-run manually on a fresh machine later.
Every build also runs generated offline SVG, GIF, DDS (RGBA/DXT1/DXT3/DXT5),
and video checks before any optional external corpus validation. A missing or
failed required check stops the build. Qt's PyInstaller hooks collect the
application's required libraries/plugins rather than unrelated QML/WebEngine assets.

The build inputs must include FFmpeg/ffprobe and ImageMagick on the build host.
Python dependencies are installed from `requirements.txt`. Missing mandatory
native runtimes are build errors rather than optional omissions.

To verify a built folder bundle without external tool paths or sample downloads:

```bash
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --offline --run-selftest --require-selftest-pass --require-core-selftest-checks \
  --require-video-runtime --require-wand-runtime --require-bundled-ffmpeg \
  --require-bundled-ffprobe --require-bundled-imagemagick --require-packaged-bundle-ready
```

On Windows, use `dist\AlphaFixerConverter\AlphaFixerConverter.exe`. For a one-file
build, use the executable directly under `dist/`. `--offline` clears inherited
Python/native-library/tool paths and forbids downloads; it is not a network sandbox
or a substitute for testing on a clean target operating system.
The verifier clears inherited smoke/audit/self-test modes and sample settings so
each launch tests the mode requested on the command line. Sample downloads must
be explicitly enabled. Timed-out validations terminate their process tree, including
one-file bootloader and FFmpeg children, instead of leaving a hung validation running.
Linux CI additionally launches both bundle types as an unprivileged user in a
minimal Ubuntu container with networking disabled and the package mounted
read-only. No Python, FFmpeg, ImageMagick, or Qt packages are installed in that
container. Capability checks and generated conversions must pass there too;
build-host libraries alone are not evidence of a portable release.

### Single-file build (slower startup)

```bash
bash scripts/build_exe.sh --onefile      # Linux / macOS
scripts\build_exe.bat --onefile          # Windows
```

### Manual PyInstaller invocation

The app ships with a fully-configured spec file:

```bash
pyinstaller alpha_fixer.spec
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Format and media boundaries

Bundling a runtime does not make every variant of every file format supported.

| Area | Actual behavior / limit |
|---|---|
| SVG input | Bundled QtSvg rasterizes its supported SVG subset to RGBA. Browser-only scripting, external web resources, and arbitrary SVG/CSS features are not promised. Oversized raster surfaces are rejected before allocation. |
| SVG output | Bundled vtracer produces approximate vector paths, suited to logos and pixel art. Source runs without vtracer can embed a PNG instead; that fallback is not a vectorization feature and will not remain sharp beyond its original resolution. |
| DDS | Raw RGB/RGBA and compressed DXT1/DXT3/DXT5 paths are checked. BC6H/BC7 and other DX10 variants depend on the actual decoder. Arrays, cubemaps, and volumes are not preserved as complete multi-surface assets; the raw loader rejects them, and mipmapped inputs use the base level. |
| Disc-image video | ISO/UMD/BIN/CUE inputs are experimental and require demuxable video that FFmpeg can find. This is not a console disc filesystem extractor or universal PSP/PS1/PS2 player. |
| XNB | Texture2D formats supported by the reader are converted to an 8-bit image; export writes RGBA8888 Color textures, not arbitrary XNA assets or original texture encodings. |
| TIM | Export uses 16-bit direct colour and TIM's limited transparent/semi-transparent states; arbitrary alpha and original indexed palettes are not preserved. Mixed mode is unsupported. |
| Alpha Painter | Seven visible editable zones. The interface does not expose 40 independently configurable zone controls. |
| Audio | MP4 export can preserve supported source audio, mute it, or apply volume. Still images/GIFs contribute silence. Preview playback is intentionally silent; GIF output has no audio. |
| Optional image codecs | AVIF/JPEG2000 availability is checked against the packaged Pillow build. Missing advertised codecs must block release qualification, not be hidden by a successful PNG fallback. |

## Release qualification

Before publishing a release, retain the results for **both** bundle types on every
supported platform:

- Full Qt-enabled tests with no unexplained failures, hangs, or environment skips.
- Offline packaged capability/self-test checks, including SVG input/vector output,
  compressed DDS, generated video, and repeated-session stress checks.
- Clean-machine installation/launch with no Python, FFmpeg, ImageMagick, or developer
  directories available; validate native X11/Wayland, Windows, and macOS behavior.
- Visual checks of every tool/dialog/theme at 8–24pt fonts, 100/125/150/200% display
  scaling, small screens, long filenames, keyboard-only use, and monitor changes.
- Real audio playback/export verification, cancellation, corrupt files, missing
  inputs, unwritable output folders, overwrite decisions, and memory-growth checks.
- Review bundled dependency/license notices and exact build versions, and satisfy
  any applicable corresponding-source/source-offer obligations. Produce
  platform-specific signing/notarization/installer artifacts where required.

Passing an offscreen test on a Linux build host is not certification of Windows,
macOS, every graphics driver, or every experimental media variant. Signing requires
the distributor's credentials and must not be fabricated or committed.

Custom-background transitions leave input controls, scrollbars and their private
child widgets opaque and themed. Unchanged transparency settings do not repolish
the widget tree; disabling a background restores each host's previous background
fill setting. Regression checks cover repeated PNG/GIF background and theme changes
and unmocked builder shutdown, but native displays and screen readers still require
separate acceptance testing.

Main-window restore and monitor-change handling use the screen with the greatest
window overlap instead of always resizing or relocating to the primary display.
Disconnected-monitor recovery uses the primary (or first available) screen.
Window decorations, taskbar-reserved areas and smaller-screen minimum-size caps
are included when fitting the window; negative-coordinate monitors remain valid.

History regression qualification covers all five views with up to 5,000 entries,
missing media, refreshes, filtered ordering and TXT/CSV/JSON/HTML exports.
Error-row text inherits the active theme rather than a fixed yellow color;
counts, status and tooltips retain error details. Filtered-out GIF thumbnails
pause, and corrupt GIF outputs fall back to available source thumbnails.
These checks do not establish a sustained memory ceiling or qualify thousands
of simultaneously playing animations.

Settings acceptance checks use isolated on-disk INI files for change, backup,
reset, import and relaunch workflows, including legacy boolean/integer values,
live appearance/effects and history policies. Opening Settings no longer rewrites
legacy per-tool history values merely because a displayed control clamps its
range. Imported shortcut bindings still require the documented restart.

Alpha and Converter batch outputs are written to sibling staging files before
replacement, so a failed encoder does not truncate an existing destination.
Existing POSIX file modes are retained; new staged outputs use private
temporary-file permissions.
Within a batch, conflicting destinations and attempts to overwrite another
queued input are reported as failures rather than silently losing files.
Converter admission also includes completed results waiting behind earlier
inputs, preventing a stalled first item from buffering the rest of the queue.
Queue regressions exercise 1,000 generated tiny images, repeated completion and
cancellation, corrupt inputs, output failures and image/staging cleanup. They
do not establish a sustained RSS ceiling or qualify large real-world media.

Video audio export now pads/trims each clip's audio to its timeline slot before
concatenation. A short audio stream, or a trim beginning beyond audio EOF, no
longer pulls subsequent audio forward. Generated-media checks decode exported
audio to verify tone timing across trims, speed changes, silent slots, volume
and normalized retry using imageio's FFmpeg and native FFmpeg. These are not
packaged-codec certification or sustained long-timeline/native playback checks.

Painter workflow checks use generated images to exercise drawing, fill,
autocorrect, clipboard and mask/layout slots, undo/redo, and save/reopen.
New images keep painted zones visible. Invalid masks are rejected before changing
editing history; full-layout paste clears absent zones and resizes valid masks.
Mask and zone-alpha edits discard stale applied results. These checks do not
replace native mouse/tablet, screen-reader or large-canvas resource acceptance.

Release builds now require offline-generated encode/decode checks for every
advertised image output (`--require-output-codec-checks`). Missing, failed or
skipped codecs block that gate; PNG fallback cannot disguise another format.
Checks include dimensions and representative alpha where supported, DDS signature
and pixels, and rasterization of generated SVG paths. GIF conversion retains
fully transparent pixels, but GIF still cannot preserve arbitrary partial alpha.
Passing source-runtime probes is not proof of a clean-machine packaged release.

Linux x86_64 qualification on Ubuntu 24.04 exercised actual folder and single-file
PyInstaller builds with the strict `--offline` verifier. Both passed all 28 runtime
checks with two self-test iterations, including advertised outputs, compressed
DDS, SVG and generated video. No source/spec changes were needed for those builds.
This verifier sanitizes runtime search paths and disables sample downloads; it
does not enforce network isolation or remove installed host libraries. These
results therefore do not certify clean-machine operation, real display/audio
devices, sustained resource growth, Windows/macOS or signed release artifacts.

Follow-up qualification exercised three smoke launches and three self-test
sessions per Linux bundle, each with two ordinary iterations and three generated
stress loops. All 33 checks passed in each session. Across separate sessions,
peak RSS spread was 0.22 MiB (folder) and 0.06 MiB (single-file); these measurements
do not establish leak freedom in one long-running process. Both bundles also
passed bounded smoke/capability/stress checks in an Ubuntu 24.04 container with
network disabled, read-only root/artifacts and an unprivileged user. This adds
network-isolated container evidence, not full clean-machine/native acceptance.
The generated stress fixtures use short videos, image conversions, builder-dialog
cycles and history roundtrips, not long timelines or sustained builder exports.

Storage-failure regressions inject disk-full (ENOSPC), permission (EACCES) and
I/O-loss (EIO) errors, and exercise missing inputs, disappearing destinations,
queue continuation and retries. Staging cleanup preserves the original failure
instead of masking it with a secondary cleanup error; successful publication
is not reported as a failure due to unnecessary post-publication cleanup.
Cleanup is best-effort when storage is inaccessible, so partial staging files
may remain there. These checks do not qualify actual full disks, disconnected
network mounts or physical removable devices.

### Remaining acceptance and distribution work

Malformed-image and allocation-failure regressions cover GIF source/scratch image
cleanup, cancelled/failed frame-extraction staging, and converted-preview image
and buffer cleanup. Painter prepares new masks before replacing an existing edit
session, preserving edits/history if allocation fails. Generated fixtures verify
retry and successful outputs; this does not establish native-codec fuzzing,
sustained resource stability or physical storage recovery.

Timecode display menus are reachable with Shift+F10 or the Menu key. Cancelling
a menu preserves an uncommitted draft; unsupported SMPTE rates correctly show
Clock as the effective display. Invalid/skipped labels restore the current value
with tooltip and accessible-description explanations. Bounded keyboard,
trim/seek and drop-minute/hour regressions cover these workflows; physical
screen-reader, audio-device and native-popup acceptance remain outstanding.

SVG fallback and Wand DDS decoding close renderer-output buffers even when
Pillow rejects the header before returning an image. Bounded generated-media
regressions cover malformed/truncated outputs, memory failures, valid retries and
PNG/TIFF file-handle cleanup alongside orientation/metadata checks. Optional
renderer output is mocked in these failure tests; real-corpus, packaged-renderer
and sustained resource qualification remain outstanding.

Settings and theme backups publish through sibling staging rather than truncating
an existing backup before writing succeeds. Injected write, close and publication
failures verify prior bytes survive, original errors remain visible and retry
succeeds; existing POSIX destination permissions are preserved. This is
transactional failure-injection coverage, not power-loss durability or physical
full-disk/disconnected-storage qualification.

Editor autosave verifies recovery-lock identity before starting and immediately
before publication, and rejects directory replacement during lock acquisition.
Displaced locks are not unlocked through a pathname that could belong to another
writer; their native resources remain until process exit. Deterministic GIF/video
storage-replacement regressions preserve competing locks and last-good recovery
copies. Path checks are not atomic against subsequent external replacement.
Physical disconnection, power-loss durability and cross-platform lock behavior
still require qualification. Two bounded real-modal lifecycle checks timed out;
one identical timeout also reproduced against the unchanged baseline.

GIF Builder imports independent TIFF pages without carrying a preceding page's
palette or metadata into grayscale/RGBA decoding. Generated raw/LZW mixed-page
regressions verify pixels, alpha, dimensions, ordering and embedded project/recovery
roundtrips after source deletion. Video still imports only the first TIFF page.
An initial combined test group exposed an order-dependent worker assertion;
separate focused groups passed, so combined-process acceptance remains unproven.

Fourteen additional bounded project/recovery regressions cover partial-write
ENOSPC, flush EIO and close EACCES for both builders' explicit saves and recovery
writes. They verify last-good bytes, edits and session locks survive, staging
files are cleaned and retries succeed. Mixed-editor shutdown checks retain all
sessions/media after a save fails or another editor cancels; resources release
only after unanimous approval. No production defect was reproduced in this pass.
These injected/offscreen results do not establish physical storage, power-loss
durability or native shutdown acceptance.

Video Builder GIF imports use Pillow's already-composited frames rather than
applying disposal a second time, preserving background pixels outside partial
frame rectangles. Generated palette/transparency/disposal cases cover both
builders, project reopen and recovery, including timing/playhead restoration.
Generated FFmpeg roundtrips also check video/audio settings, stream selections
and selected clips. These bounded tests do not certify physical audio, sustained
resource stability or power-loss recovery.

Additional TIFF qualification covers 13 native image modes, raw/LZW compression,
mixed orientation, per-page metadata and truncated trailing strips. Palette-alpha
pages now resize through true-color RGBA rather than filtering palette indices,
and single-image exports materialize explicit palette alpha. Cyclic page-directory
chains fail instead of silently dropping pages; rollback and handle cleanup are
checked with generated corrupt files. This is Pillow/generated-media coverage,
not universal TIFF or real-corpus fidelity certification.

Bounded project/recovery lifecycle tests exercised three cycles per editor,
60 real Qt modal interactions and 60 project workers. Worker wrappers were deleted,
owned media closed once, and last-good recovery survived cancellation/reopening.
These checks found no production lifecycle defect and do not establish hours-long
resource stability, physical power-loss durability or complete native acceptance.

Conversion materializes palette/color-key transparency before resizing or saving
to TIFF, TGA, QOI and JPEG2000, avoiding silent alpha loss. Grayscale-alpha GIF
conversion uses the existing binary-transparency path. Generated-pixel checks
cover orientation, resizing, metadata choices and decoded alpha across supported
outputs; existing multipage TIFF regressions also passed. Real-corpus fidelity,
broader multipage editing and cross-platform packaged codecs remain unqualified.

Application project shutdown revalidates each editor's approved state after all
unsaved-project prompts finish. Edits or newly started I/O during a later editor's
modal prompt prevent shutdown from releasing earlier editors' recovery sessions.
Bounded offscreen regressions cover approval invalidation, rollback, locks and
repeated teardown; they do not resolve the earlier native teardown crash or
certify sustained stability and physical power-loss recovery.

Precise Video timecode fields now accept exact SMPTE frame labels and
29.97/59.94 drop-frame labels, reject skipped labels, and support timelines beyond
24 hours. The field context menu selects clock, SMPTE or drop-frame display;
source trims and output seeks retain their respective FPS and existing bounds.
Preview audio waits for loaded media, verified tracks and seekability regardless
of Qt signal order; readiness loss pauses audio and uses an independent deadline
even when video is paused. Device outages suspend that deadline, and stale
deadlines are cancelled on replacement/reset/close. Project validation rejects
oversized source FPS cleanly and has additional malformed/missing-timing checks.
Focused generated-media and fake-device regressions cover these changes; physical
audio/device latency, waveforms, full native recovery and sustained stress remain
outstanding.

GIF project/recovery loading preserves an explicit absence of imported source
timing instead of inventing a source hold from an edited delay. Generated-image
roundtrips cover reopening, resaving and undo, alongside bounded failed-load and
storage/source-identity regressions. Physical storage/power-loss recovery and full
native project/settings acceptance remain outstanding.

Standard-image previews, conversion and Alpha loading normalize EXIF/XMP
orientation before resizing or exporting. Retained metadata no longer reapplies
rotation, including TIFF XMP tag 700 and mixed-orientation multipage TIFFs.
Generated-image regressions cover all eight orientations, metadata retention,
preview reduction and malformed metadata; metadata preservation remains
best-effort. These checks do not replace real malformed-media corpora or native
multipage-editing acceptance.

Bounded repeated project/recovery resource tests exercise six successful
replacements, six cancellations and six failures per builder. Ownership checks
cover decoded GIF images, video clips, native FFmpeg remux readers/files, Qt jobs,
unchanged recovery snapshots and released session locks. Final assertions use
the production dialog close path rather than directly releasing resources.
These offscreen generated-media checks do not certify sustained hours-long
stress, native device behavior, physical storage failure or power-loss recovery.

Embedded GIF project frames undergo PNG structural verification before pixel
decoding, rejecting corrupt IDAT checksums and missing IEND chunks rather than
silently accepting damaged media. Open/recovery regressions verify prepared-image
cleanup and preservation of the existing dirty timeline, pixels, playhead,
history, locks and last-good project/recovery bytes. These bounded checks do not
qualify physical storage failure, power loss or complete native recovery workflows.

GIF Builder imports TIFF pages as independent RGBA images, preserving each page's
dimensions, colors, alpha and order instead of compositing pages onto one canvas.
Animated disposal/compositing is delegated to Pillow's decoder. Import diagnostics
include every page size; a malformed page rejects that source atomically, and
cancellation before publication closes decoded pages. Generated-media regressions
cover these behaviors. Video Builder TIFF import remains a first-page still;
dedicated multipage editing and real-corpus fidelity qualification remain pending.

Bounded project/recovery acceptance covers late malformed media and cancellation
for both builders. Forty rejected replacements preserved current edits, pixels,
selection, playhead, history, controls, locks and project/recovery bytes; checks
verified prepared-resource and actual FFmpeg-reader cleanup plus successful retries.
Fixtures use truncated embedded PNGs and generated MP4 frame-count mismatches.
No production defect was reproduced. These offscreen/generated-media checks do
not establish native recovery, long-running resource stability or power-loss safety.

GIF Builder shutdown clears its retained preview pixmap and resets frame count,
scrubber and transport state after releasing frames. Regressions cover close,
reject and parent shutdown plus 15 generated-project reopen/autosave/shutdown
cycles: 45 frame entries closed exactly once, 15 previews cleared and 15 locks
released, with recovery files preserved. Bounded project, teardown and storage
groups passed 124 tests without skips. These checks do not establish sustained
resource stability, physical storage/power-loss recovery or native device behavior.

Repeated builder project replacement and failed open/recovery checks exercise
image, reader, temporary-remux and audio-player cleanup. A truncated-GIF regression
now closes copied frames when decoding fails partway through. Bounded checks
released 140 entries, closed 30 readers, removed 20 remux files and destroyed 41
audio players; 20 failed open/recovery attempts preserved unsaved edits and closed
120 tracked image copies. These are bounded resource-ownership checks, not
sustained leak, physical audio, power-loss or native release certification.

Builder project/recovery storage tests exercise injected I/O/read-permission
failures, missing inputs and disappearing destination directories, followed by
retry. They verify last-good project bytes, dirty/saved/autosaved state, session
locks, snapshot ownership, cancelled save pickers and edits during close-save.
The focused storage/recovery group passed 62 tests without reproducing a production
defect. These injections do not establish physical disk-exhaustion, removable
device or network-storage recovery acceptance.

Project save/open/autosave operations defer while a builder is importing media.
Generated-media regressions reproduce nested import event processing and verify
that partial timelines cannot replace complete project or recovery files.
After a failed project open, recovery snapshots retain the original editor state
without altering the project file. These bounded Qt checks do not establish
physical storage recovery or complete native project acceptance.

Editor projects validate split cadence/audio spans against their original source
range before loading media. Malformed mappings cannot replace current unsaved
edits; project playhead validation uses the same rounded duration as the editor.
Generated-media tests cover nested splits, controls, recovery and failure-safe
state preservation. These bounded checks do not certify native project workflows
or physical storage recovery.

Video trim and timeline seek controls accept clock timestamps in `HH:MM:SS.mmm`
alongside frame and seconds entries. Trims use source FPS and inclusive endpoints;
seeking uses output FPS. Valid timestamps round to the nearest frame (ties forward)
and clamp to available frames; invalid input reverts without changing the edit.
Keyboard, fractional-FPS, undo and project restoration regressions cover these
controls. SMPTE/drop-frame entry and display are also supported as described above;
waveforms, embedded timecode interchange and physical audible synchronization
remain outstanding.

Failed project open/recovery keeps retained playback paused while the error
dialog is displayed, then resumes previously active playback after dismissal.
Generated-frame tests pump Qt events during read/decode errors in both builders
and verify retained playhead, edits and resources. These bounded checks do not
replace native project/recovery or physical audio acceptance.

Project media-identity checks reject nonfinite decoded frame rates before a
loaded or recovered project replaces the active editor session. Generated-file
regressions with injected decoder metadata cover cleanup and preservation of
unsaved media, edits, history, selection and playback. These bounded checks do
not establish malformed real-media fidelity or native recovery acceptance.

Audio-preview synchronization stops immediately when a transport call reports a
synchronous backend error, preserving the failure status instead of restarting
playback. Deterministic transport-failure/retry regressions and a generated
headless Qt load/seek/shutdown check pass. Native FFmpeg-dependent checks were
skipped in this pass; physical audible sync, device changes and latency still
require qualification. A combined preview/split/timing run reached its 120-second
limit and was not counted as completed validation.

Builder project failures retain an error even when an exception has no message,
and modal error reporting blocks reentrant project/close operations while
preserving the prior loading state. Generated-media regressions cover missing
destinations, injected disk-full errors, truncated projects/recoveries, retained
edits/history/session locks and successful retry. These checks do not qualify
physical full disks, disconnected storage or complete native recovery workflows.

Builder project close prompts and open/save pickers block competing project I/O,
autosave and recursive shutdown while their nested modal loops run. Generated-media
regressions cover modal polling, cancellation and shutdown decisions alongside
malformed/missing-source, recovery and storage-failure cases. These controlled
widget tests do not establish physical storage or native end-to-end acceptance.

Video preview audio can retry transient decoder failures on explicit recovery
actions or device return without reloading failed media on automatic timeline
wraps. Time without an output device does not consume the loading deadline, and
obsolete queued end-of-media notifications are ignored after a seek. Deterministic
backend regressions and bounded generated-media checks cover recovery logic;
audible synchronization, latency and physical device-change acceptance remain
outstanding.

Editor recovery rechecks the recovery-file lock while reading, rejecting a copy
claimed by an active editor after discovery. The recovery picker uses a stable
candidate list and blocks reentrant project I/O/shutdown while open. Generated
GIF/Video project tests cover lock races, malformed-read cleanup and picker
cancellation/reentry. Native concurrent-editor and physical storage recovery
qualification remain outstanding.

Builder project replacement keeps save/autosave actions blocked through modal
confirmation and installation. Edits made during confirmation prevent stale
replacement; completion restores editing controls on success, cancellation and
failure. Generated-project regressions also cover injected storage failures,
retry and malformed recovery media while retaining session resources/state.
These checks do not certify physical storage recovery or complete native workflows.

Video Builder supports named reusable export presets for format, FPS, source-audio
retention, mute and volume. Save/apply/delete persists through settings; presets
exclude source/destination paths, clip edits and automatic output sizing. Applying
a preset is one undoable edit and rejects unavailable MP4 or invalid values without
partially changing controls. Tests cover persistence, malformed records, compact
controls and undo/redo; device playback and native end-to-end acceptance remain
separate gates.

Failed editor project installation restores the previous session through the
normal load-failure path, including playback and GIF multi-selection. Generated
media and injected post-application failures verify controls, history, dirty state,
media ownership and saved project JSON remain intact. These bounded offscreen
checks do not replace native recovery or physical storage qualification.

GIF and Video project controls share the scrollable builder content below the
title. Save/Open/Recover actions stack when necessary, status text wraps within
the viewport, and keyboard-focused actions remain reachable. Accessible names
and Alt+S/Alt+O/Alt+R mnemonics distinguish project actions from Ctrl+S media
export; recovery refresh preserves its mnemonic. Compact/theme/font and project
workflow regressions are offscreen checks, not native accessibility certification.

Advanced summaries distinguish effective processing from retained preferences.
Alpha guidance explains binary cutting after inversion/range normalization and
RGBA adjustments after threshold protection; enabled zero deltas are not counted
as an active change. Converter's disclosure counts applicable current-format
settings, excluding retained options and GIF Builder handoff preferences.
Duplicated Alpha summary text is removed without removing controls.
This pass covered 69 layout/processing checks at DPR 2 and 72 existing contrast
checks across 57 palettes at DPR 1, plus 22 X11 screenshots across five themes.
Screenshots were not an exhaustive all-theme or custom-background visual audit;
physical mixed-DPI, screen-reader and native release acceptance remain outstanding.

Action/tab text now follows the actual theme fill, including composited custom
color transparency, instead of assuming white text is readable on every accent.
Focus cues follow the corresponding state. All 57 built-in themes retain the
same final styles after the transparency follow-up; arbitrary media backgrounds
still require visual review. Final targeted source checks passed 619 tests.
The exact rebuilt Linux folder executable passed 24 Alpha/Converter GUI cases
across scales 1/1.5/2, Dark/Light and 900×700/1200×800 windows, with 48 screenshots
and externally observed exit 0 at every scale. Only the scale-1 contact sheet was
visually reviewed; higher-scale screenshot review remains outstanding.
An earlier packaged scale-2 run and a wider source lifecycle run segfaulted;
neither is dismissed by the successful final matrix. Root-cause diagnosis and
sustained native teardown qualification remain release blockers.

The latest hierarchy pass removes duplicate in-tool next-step labels and shortens
Basic/Advanced guidance while retaining complete settings details in tooltips and
accessible descriptions. A freshly rebuilt Linux folder bundle passed 24 actual
Alpha/Converter window cases at verified scales 1/1.5/2, Dark/Light themes and
900×700/1200×800 sizes, producing 48 screenshots with externally observed exit 0
at every scale. Focused source regressions passed (60 plus three status tests).
The earlier scale-2 exit-139 teardown failure did not reproduce in bounded
before-change or final runs; no lifecycle fix or root-cause resolution is claimed.
Screenshot review still shows dense compact scrolling and weak inactive-tab/button
contrast in the Light theme; these need further visual/accessibility work.

Basic/Advanced usability checks now cover Enter/Return activation in Alpha and
shrinking Converter guidance after widening a window or shortening its text.
The opt-in `ALPHA_FIXER_GUI_ACCEPTANCE_OUT` launch hook exercises the actual
application window and writes screenshots/assertion results using private settings.
Its report cannot certify teardown: acceptance also requires an externally observed
zero process exit; a signal, timeout or missing report is failure.

A Linux folder bundle built from these changes passed 28 runtime checks and
eight packaged Alpha/Converter GUI cases at scale 1 on Xvfb/X11, with Dark/Light
themes, 900×700/1200×800 windows, 16 screenshots and a clean external exit.
An earlier artifact exercised 24 GUI cases across scales 1/1.5/2, but crashed
during final teardown at scale 2 (exit 139). The final artifact was only checked
at scale 1; the higher-scale shutdown failure remains unresolved. Screenshot
review also leaves further reduction of repeated status/guidance and compact
scrolling density desirable. These are bounded checks, not release certification.
Physical audio output, synchronized playback/device behavior, waveform navigation,
full timecode/export presets, multipage editing and real-corpus qualification
remain separate work, alongside installers/licensing/signing, physical DPI and
screen readers, and broader formats with explicit supported limits.

Converter keeps output format and conversion actions in Basic view, with an
explicit Advanced disclosure for quality, DDS, metadata and resizing. Collapsing
does not disable options: a summary explains active and retained settings, and
keyboard disclosure preserves preferences and preview state.

A freshly built Linux folder bundle passed 28 strict packaged runtime checks.
Its actual MainWindow was exercised under Xvfb/Openbox at scales 1, 1.5 and 2,
Dark/Light themes and logical sizes 900×700 and 1280×900 (12 combinations),
including Basic/Advanced disclosure and scrolling. Two further keyboard
collapse/reopen/close runs passed. A preliminary Light run segfaulted during SIP
teardown after reporting exit 0; it was not reproduced, and remains an unresolved
stability observation. This bounded packaged-window evidence is not an exhaustive
clipping audit, physical-device qualification or Windows/macOS release certificate.

Alpha Adjuster distinguishes Basic alpha-range controls from an accessible
Advanced disclosure for cutoff, selective processing and RGBA adjustments.
An always-visible summary describes retained advanced settings; collapsing does
not reset them. Wrapped guidance recomputes its height when width or text changes.
The rebuilt Linux folder bundle passed 28 packaged self-tests. Twelve packaged
window configurations (two sizes, three scales, two themes) were exercised before
follow-up clipping fixes; that full matrix has not been rerun on the final artifact.
Final source checks passed 38 tests and X11 geometry checks passed two tests.
Final screenshot review and exact packaged Advanced keyboard activation remain
unverified; duplicated guidance still needs visual simplification.

Video trim and playhead controls offer seconds alongside exact frame indices.
Trim seconds use source FPS; inclusive Out identifies the last retained frame's
timestamp, not the clip duration. Playhead seconds use output FPS after timeline
edits. Entries round to the nearest frame with decimal half-frame ties forward;
controls synchronize without extra edits. Generated-image/key regressions cover
fractional FPS, long ranges, selection, splits and boundary rounding. Timecode,
waveform navigation, synchronized audible preview and export presets remain
separate work.

Video trim controls now include precise numeric entry for inclusive, zero-based
source-frame boundaries. The timeline has a synchronized numeric entry for its
zero-based rendered output frame. Keyboard-entry, selection, undo/redo and timing
regressions cover these controls. Frame entry is not a full seconds/timecode
editor or waveform navigator; synchronized audible preview/device handling,
release/accessibility qualification remain work.

Opening Video projects offers to locate missing media once per unique source.
Replacement files must match the saved SHA-256 identity; verification and decoding
remain asynchronous. Cancellation or preparation failure keeps the current editor
intact, and relinked projects remain unsaved until Save Project records new paths.
Recovery files are not rewritten by opening them. Changed media must be imported
anew rather than silently substituted; embedded GIF project frames need no relink.
Generated-media tests cover relinking, cancellation, identity rejection and saving.

TIFF-to-TIFF conversion preserves all decodable pages in order, their native
Pillow-supported modes and dimensions, and applies an enabled resize to every
page. **Keep metadata** copies supported per-page EXIF/ICC/DPI; disabling it
omits these source metadata fields. Applied decoder orientation is not applied
twice. Compression and storage layout are rebuilt; arbitrary private tags,
SubIFDs, original compression and byte-identical archival preservation are not
guaranteed. Pages are decoded/written sequentially, not collected as raster
copies for the entire document (Pillow still indexes page offsets).

Other outputs export page 1 only; direct warnings and batch logs disclose this
limit. Previews/thumbnails still show page 1 only, and TIFF preview size estimates
are for that page, not the complete output. An unavailable page count does not
block a readable first-page preview or single-image export; TIFF export instead
fails rather than silently losing later pages. Sibling staging protects existing
destinations on decode/write failure and cooperative cancellation between pages.
Generated fixtures cover mixed modes, raw/LZW input, per-page metadata,
orientation, resize, in-place conversion and resource cleanup. Multipage editing,
real-corpus scale and packaged-codec qualification remain separate work.

Video Builder now offers opt-in **Preview audio** with independent volume/mute.
Audio follows the visual timeline through playback, scrubbing, trims, split
segments, speed/FPS changes and looping. Loading is asynchronous; missing devices,
unsupported/failed media and short audio tails leave video running silently with
inline status. Sources must expose one verified, seekable audio track; unverified
multi-stream routes stay silent. Playback rates outside 0.1–10× are unavailable.
Generated-media Qt decoding/seeking and deterministic timing/device/lifecycle
tests cover the implementation, not physical audibility or cross-platform sync.
Native audio-device, latency, codec and Windows media-handle cleanup acceptance
remain outstanding. A generated-media compact-layout probe also retained a
107px horizontal overflow already present on the baseline; this needs follow-up.

Video Builder provides Undo/Redo for timeline edits and editor settings, including
imports, removal, reordering, splitting, trims, speed and still duration. Slider
gestures form one edit; no-ops preserve redo. History retains media references
rather than copying decoded frames and is bounded by edit/resource budgets.
Opening a project resets edit history. Undo/Redo defaults yield to existing saved
shortcut assignments and remain customizable. Generated-media regressions cover
timing, resource cleanup, projects and shortcuts; native and sustained-stress
acceptance, including physical audible-preview synchronization, remains outstanding.

GIF Builder provides Undo/Redo for adding, removing, clearing and reordering
frames and editing per-frame delay overrides. Buttons and configurable Ctrl+Z /
Ctrl+Shift+Z shortcuts restore frame selection and timing; text fields retain
their own undo behavior. History retains references rather than copying pixels
and is capped at 50 edits and 32 million history-only pixels. Older edits may be
released. Global export settings are not part of this history. Generated-frame
regressions cover history branching, resource cleanup and project replacement;
Native workflow acceptance remains outstanding.

Converter metadata checks now cover EXIF dimensions after resize, nested
interoperability tags, TIFF metadata/storage-tag separation, AVIF ICC copying
and disabled-metadata stripping. Applied TIFF decoder orientation is normalized
without transposing unapplied JPEG pixels again. Generated roundtrips cover five
output formats; legacy TIFF behavior is simulated, not tested in a Pillow 10
runtime. LZW-to-TIFF output now uses detached pages with rebuilt storage tags;
metadata-aware TIFF preview parity remains follow-up work, alongside real-corpus
and packaged-codec coverage.

Bounded Linux X11 qualification used Qt's `xcb` platform with Xvfb/Openbox at
verified device-pixel ratios 1, 1.5 and 2. Sixteen existing Alpha, Converter, GIF
and Video layout/keyboard checks passed per scale (48 executions), including
480×400 windows, 32px fonts and Dark/Light themes. One existing real MainWindow
builder/queued-status teardown test also passed. Initial focus assertions raced
X11 window activation; execution-only synchronization waited for window exposure
and activation before event pumping. No production defect was reproduced.
These simulated-display results do not certify physical DPI, screen readers,
monitor hot-plug, sustained lifecycle stability or Windows/macOS behavior.

Undocked comparison dialogs fit their available screen after attached controls
and window decorations are laid out, instead of opening larger than compact
displays. Simulated-screen/font/theme checks cover bounds and keyboard redocking;
existing About/support-link and tutorial checks also pass. These checks do not
replace physical multi-monitor/DPI or native screen-reader qualification.

Painter's shared-zone clipboard picker wraps its prompt and fits the active
screen instead of exceeding compact displays at large fonts. Size/font/theme
regressions cover selection and cancellation. The existing About dialog passed
a bounded layout/keyboard audit without changes; neither audit establishes native
screen-reader or physical display acceptance.

About dialog keyboard traversal scrolls its support link into view instead of
leaving the focused anchor offscreen. Compact/font/theme regressions cover
forward/reverse traversal, link activation and closing using lightweight widget
hosts; native screen-reader and full MainWindow acceptance remain outstanding.

About fits the available screen, exposes scrollable information accessibly and
starts with its confirmation button focused. Deferred fitting uses a dialog-owned
timer. Feature guidance reflects current alpha processing and the configured help
shortcut, including remapped or unassigned bindings. Compact/font/theme and
keyboard tests use lightweight widget hosts; native acceptance remains separate.

Shortcut configuration and key-capture guidance scroll on compact screens and
at large fonts. Action columns retain readable widths, row buttons identify their
actions accessibly, and Enter activates focused Change/Reset buttons. Key capture
accepts Enter instead of accidentally activating Cancel; Escape still cancels.
Bounded widget-host tests cover these interactions, not full native MainWindow
or screen-reader acceptance.

Alpha Adjuster panes, output/settings grids and preview statistics reflow on
compact windows and at large fonts. Keyboard focus reveals complete controls.
Alpha and Converter background progress updates preserve manually scrolled views
rather than pulling them back to an unchanged focused control. Generated-image
layout, keyboard and progress regressions cover these behaviors offscreen; native
display and accessibility acceptance remain outstanding.

Converter panes, option grids and action rows stack on compact windows and at
large fonts. Outer scrolling keeps previews and controls reachable; keyboard focus
reveals complete controls and failure-report actions. Generated-image checks cover
compact/theme/font layouts, DDS selection, resize preferences and report saving.
Physical display, native screen-reader and full application acceptance remain
separate qualification gates.

Video Builder panes and control rows reflow for compact screens and large fonts.
Focused controls scroll into view, controls retain font-safe heights, and cached
preview images resize to their available area without decoding another frame.
Generated-image layout/action and timing regressions cover this behavior.
Native playback/export, physical DPI and screen-reader qualification remain
outstanding.

GIF Builder panes and control rows reflow on compact windows and at large fonts.
Buttons retain theme-sized heights, panes cannot collapse completely, and keyboard
focus scrolls controls into view. Compact/theme/font and generated-frame action
regressions cover these changes; physical DPI and native screen-reader acceptance
remain outstanding.

Comparison previews support keyboard divider control: Tab to the comparison,
use Left/Right to adjust it and Home/End to reveal either side. A visible focus
ring and accessible guidance apply to docked and floating previews. Compact
overlays prefer a named icon button when full undock text would crowd zoom controls.
Generated-image keyboard/mouse/layout checks preserve image pixels; physical
display and native screen-reader acceptance remain outstanding.

History guidance and actions remain reachable by vertical scrolling on compact
windows and at large fonts, with at least two usable tree rows. Local Ctrl+Tab
navigation works across history sub-tabs without conflicting with configured
global shortcuts. Compact/theme/font and keyboard regressions are offscreen
checks, not physical display or screen-reader certification.

Painter's controls shrink proportionally on compact windows rather than forcing
the window beyond the available width, preserving usable canvas space. Oversized
controls remain scrollable in both directions; keyboard focus reveals complete
spin boxes, including their arrows. Compact/font regressions also verify mask
slots, edits and undo history are preserved. Physical display and screen-reader
qualification remain separate from these offscreen checks.

Settings custom-sound buttons use natural font-scaled sizes and explicit
accessible names. Sound controls follow visual keyboard order; focused controls
are revealed through nested scroll areas. Settings tabs allow horizontal scrolling
when large fonts exceed a compact viewport rather than making controls unreachable.
Compact-size/font/theme and keyboard regressions are checked offscreen; native
screen-reader and physical display qualification remain outstanding.

Tutorial guidance scrolls as a whole so large-font headings, tips and shortcuts
remain reachable on compact screens. Navigation stays outside the scroll area
with font-sized buttons and visual-order keyboard navigation. Offscreen checks
cover compact layouts, theme/font variants and keyboard activation; physical DPI,
native decorations and screen-reader acceptance remain separate release gates.

Image preview loaders remain owned until their threads finish, including when
decoding outlives a closed/deleted pane. Results use GUI-affine queued slots;
close/clear/replacement invalidates old requests and cooperatively stops loading.
Bounded barrier-driven lifecycle tests cover both source and converted previews.
This fixes a reproduced preview ownership defect, not the unexplained MainWindow
crash; native application shutdown and blocked-decoder termination still need
separate qualification.

Settings backup imports and resets refresh registered keyboard shortcuts in the
running application and open tools through the existing deferred settings apply.
Restoration does not rewrite shortcut preferences or reload unsaved Painter media,
masks or editing history. Bounded tests use real tool widgets with a lightweight
host; complete native MainWindow reset/restore acceptance remains outstanding.

Video splits preserve the original sampled frame sequence and rounded timeline
length, including nested splits at non-unit speeds. Frozen exports retain the
same mapping; audio boundaries preserve the original continuous span and tempo.
Generated-frame/GIF and imageio-FFmpeg tone tests cover this behavior. Native
FFmpeg/ffprobe checks were skipped in this pass; native playback and extended
real-media synchronization still require qualification.

Painter shared-zone imports persist their alpha values for backup/relaunch.
Clearing an already-empty zone/layout leaves undo and redo history unchanged;
generated-pixel and settings roundtrip tests cover these state transitions.

GIF Builder retains imported per-frame delays, including long holds. Unrelated
live edits no longer restart the current playback hold; explicit scrub/rewind
starts the selected frame's full hold. Generated exports verify retained delays.
GIF stores timing in 10ms units, so exported timing may round down from the
millisecond value used by the preview.

Alpha binary-cut processing now honors an enabled cut at threshold zero rather
than silently skipping it. Generated-pixel checks cover threshold boundaries,
range/inversion, RGBA clipping and preview/statistics/PNG-export consistency.
Final range clamping still applies after the binary cut.

GIF frame selection adapts from one to six thumbnail columns as space changes,
stacks its toolbar when narrow, and scrolls keyboard-focused frames into view.
Frame tab order and accessible descriptions are covered by generated-GIF tests
at small window sizes and large fonts. Native screen-reader/display acceptance
still requires separate testing.

Settings theme filtering preserves the active theme and stored preferences;
clearing the filter restores the saved theme's identity. Empty searches have an
explicit no-results state. Saved-theme deletion updates available actions and
keeps the current colors as unsaved. Keyboard and accessible-label regressions
are checked offscreen, not certified against native screen readers.

Long-timeline regressions exercise 128 clips with 60,000 logical frames each
without eagerly allocating snapshot jobs, plus random seeks in a generated
120-second, 3,000-frame video. Finished export clips release their decoder/cache;
reader reuse and prefetched-frame cleanup are also checked. This is bounded
logical/generated-media qualification, not sustained real-corpus acceptance.
An earlier combined Qt run encountered a native MainWindow event-filter crash.
Follow-up runs did not reproduce it: two original combined-suite runs passed,
as did the combined suite with new unmocked MainWindow/builder lifecycle
regressions and a full run including geometry (2,558 tests; six corpus skips).
The regressions exercise queued callbacks, theme/show/hide transitions, immediate
deletion and subsequent windows. No speculative production fix was applied;
the earlier crash's cause remains unproven and native stability still needs
qualification.

The source-level checks above are partial qualification, not a release certificate.
Retain platform-specific evidence for these remaining gates:

- **Display and accessibility:** physical small screens, mixed DPI, monitor hot-plug,
  native window decorations, every dialog/theme, keyboard navigation and actual
  screen readers. Offscreen geometry checks cannot establish native accessibility.
- **Stability and scale:** sustained memory, file-handle and thread growth; native
  crashes; large processing queues/history; long timelines; audio synchronization;
  repeated import/export/cancel/reopen and application shutdown.
- **Output safety:** real permission failures, disk exhaustion, missing inputs,
  destination collisions and recovery, including network/removable storage and
  Windows/macOS path behavior.
- **Media coverage:** legally available real-world corpora, malformed/large files,
  transparency/metadata fidelity and every advertised codec in the actual bundle.
  The format boundaries above remain limitations, not completed feature promises.
- **Offline distribution:** clean-machine folder and single-file bundles on Windows,
  macOS and Linux with developer runtimes and network access unavailable.
- **Release obligations:** review actual bundled licenses, corresponding-source
  requirements and project licensing; retain exact reproducible build versions;
  qualify installers, upgrades/uninstall, signing and macOS notarization.
- **End-to-end acceptance:** first launch through every tool's successful export,
  including Painter editing, clipboard, undo/redo and saving; settings/backup
  restoration and relaunch, with native visual checks and retained results.
  Diagnose all failures and unexplained skips before claiming readiness.
- **Native visual consistency:** final layout and usability checks across every
  theme/dialog, including clipping, focus, scaling and input-device behavior.
- **Optional feature expansion:** DDS multi-surface/DX10 coverage, disc-image
  extraction and broader TIM/XNB and SVG support. Audible preview now requires
  native device/codec/synchronization qualification. These
  documented boundaries are distinct from acceptance of currently supported
  features; they are not silently treated as completed features.

### Real corpus validation

Optional real-world corpus tests already exist for odd video containers / disc images and DDS samples. Because many of the most relevant PSP / PS1 / PS2 corpora are large, private, or copyrighted, they are **not** bundled in this repository.

This repo now also ships small **public-download manifest examples** under `sample_manifests/` so fresh machines can exercise real external samples without needing private corpora:

- `sample_manifests/public_disc_video_manifest.json`
- `sample_manifests/public_dds_dx10_manifest.json`
- `sample_manifests/public_format_matrix_manifest.json`

Those built-in manifests intentionally use public sample files that are legally redistributable or publicly downloadable, but they are **not a replacement** for true copyrighted PSP / PS1 / PS2 validation corpora. They are meant to provide a reproducible baseline for packaged runtime checks and fresh-machine smoke coverage.

There are also starter **private manifest templates** for local corpora you cannot redistribute publicly:

- `sample_manifests/private_psp_ps1_ps2_disc_manifest_template.json`
- `sample_manifests/private_odd_container_manifest_template.json`
- `sample_manifests/private_odd_container_video_manifest_template.json`
- `sample_manifests/private_dds_complex_manifest_template.json`

Suggested split:

- `private_psp_ps1_ps2_disc_manifest_template.json` – disc-image / cue-sheet / UMD / PMF / PSS / STR style console media
- `private_odd_container_manifest_template.json` – console and legacy odd-container clips like `.pss`, `.str`, `.vob`, `.asf`, `.wmv`, `.mxf`, and odd-extension samples
- `private_odd_container_video_manifest_template.json` – broader non-console edge cases such as multi-stream MKV, cover-art containers, odd MOV/AVI metadata, and audio-only misdrops
- `private_dds_complex_manifest_template.json` – local cubemap / array / volume / mipmap / BC6H DDS corpora

You can point the tests at external corpora with either directories or JSON manifest files:

```bash
# Directory-based corpora
export ALPHA_FIXER_REAL_VIDEO_CORPUS="/path/to/video-corpus"
export ALPHA_FIXER_REAL_DISC_VIDEO_CORPUS="/path/to/psp-ps1-ps2-disc-samples"
export ALPHA_FIXER_REAL_DDS_CORPUS="/path/to/dds-corpus"
export ALPHA_FIXER_REAL_DDS_DX10_CORPUS="/path/to/dds-dx10-corpus"

# Manifest-file based corpora (preferred for curated PSP / PS1 / PS2 / DDS sample sets)
export ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST="/path/to/disc_video_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST="/path/to/odd_container_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST="/path/to/odd_container_video_manifest.json"
export ALPHA_FIXER_REAL_DDS_DX10_MANIFEST="/path/to/dds_dx10_manifest.json"
export ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST="/path/to/dds_complex_manifest.json"
```

Manifest environment variables may contain either:
- a JSON array directly, or
- a path to a JSON file on disk

Manifest files can use relative sample paths, which resolve relative to the manifest file itself. They may also wrap entries in a top-level object with `samples` or `entries`, and can provide a `base_dir`.
Entries may also include `url` / `download_url`, optional `sha256`, `download_name`, and `cache_subdir` fields when you want the same manifest to populate samples onto a fresh machine.

Example manifest:

```json
{
  "base_dir": "/mnt/corpora/psp-disc-video",
  "samples": [
    {
      "path": "sample01.iso",
      "expect": "load_or_explain",
      "hint_contains": ["Disc-image video inputs are experimental"]
    }
  ]
}
```

To populate a manifest into a local cache directory before running tests or packaged self-tests:

```bash
python scripts/populate_sample_manifest.py /path/to/disc_video_manifest.json \
  --cache-dir /tmp/alpha_fixer_corpus_cache \
  --copy-local \
  --allow-downloads \
  --output-manifest /tmp/materialized_disc_video_manifest.json
```

That `--copy-local` step is the easiest way to freeze a private local corpus into a clean test cache before packaging/fresh-machine runs. It also keeps sidecars (for example cue/bin companions) beside the copied primary sample when the manifest declares them.

You can also opt into download-backed manifest entries directly during test/self-test runs:

```bash
export ALPHA_FIXER_ALLOW_SAMPLE_DOWNLOADS=1
export ALPHA_FIXER_SAMPLE_CACHE_DIR=/tmp/alpha_fixer_corpus_cache
```

For the built-in public manifests:

```bash
python scripts/populate_sample_manifest.py sample_manifests/public_disc_video_manifest.json \
  --cache-dir /tmp/alpha_fixer_public_cache \
  --allow-downloads \
  --output-manifest /tmp/public_disc_video_manifest.materialized.json

python scripts/populate_sample_manifest.py sample_manifests/public_format_matrix_manifest.json \
  --cache-dir /tmp/alpha_fixer_public_cache \
  --allow-downloads \
  --output-manifest /tmp/public_format_matrix_manifest.materialized.json
```

Packaged runtime self-tests can also consume external manifests directly on fresh machines:

```bash
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --run-selftest \
  --selftest-iterations 8 \
  --selftest-sample-limit 12 \
  --selftest-stress-loops 3 \
  --disc-video-manifest sample_manifests/public_disc_video_manifest.json \
  --format-matrix-manifest sample_manifests/public_format_matrix_manifest.json \
  --allow-sample-downloads \
  --sample-cache-dir /tmp/alpha_fixer_packaged_cache \
  --require-selftest-pass \
  --require-stress-selftest-checks \
  --require-selftest-check external_disc_video_manifest \
  --require-selftest-check external_format_matrix_manifest
```

For private local corpora, you can repeat manifest flags to merge multiple edge-case sets into one packaged self-test run:

```bash
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --run-selftest \
  --selftest-iterations 6 \
  --selftest-sample-limit 16 \
  --selftest-stress-loops 3 \
  --disc-video-manifest /path/to/private_psp_ps1_ps2_disc_manifest.json \
  --disc-video-manifest /path/to/private_odd_container_manifest.json \
  --disc-video-manifest /path/to/private_odd_container_video_manifest.json \
  --dds-manifest /path/to/private_dds_complex_manifest.json \
  --allow-sample-downloads \
  --sample-cache-dir /tmp/alpha_fixer_private_cache \
  --require-selftest-pass \
  --require-video-selftest-checks \
  --require-stress-selftest-checks \
  --require-disc-manifest-group-checks \
  --require-dds-manifest-group-checks
```

That grouped-manifest mode is useful when you want separate pass/fail reporting for PSP vs PS1 vs PS2 clips, or for DDS groups like `cubemap`, `array`, and `volume`.

The packaged build scripts can also pick those private manifests up automatically from the same environment variables:

```bash
export ALPHA_FIXER_VERIFY_PRIVATE_SAMPLE_MANIFESTS=1
export ALPHA_FIXER_REAL_DISC_VIDEO_MANIFEST="/path/to/private_psp_ps1_ps2_disc_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_MANIFEST="/path/to/private_odd_container_manifest.json"
export ALPHA_FIXER_REAL_ODD_CONTAINER_VIDEO_MANIFEST="/path/to/private_odd_container_video_manifest.json"
export ALPHA_FIXER_REAL_DDS_COMPLEX_MANIFEST="/path/to/private_dds_complex_manifest.json"
export ALPHA_FIXER_PRIVATE_SAMPLE_LIMIT=16
export ALPHA_FIXER_PRIVATE_SELFTEST_ITERATIONS=4
export ALPHA_FIXER_PRIVATE_STRESS_LOOPS=3
bash scripts/build_exe.sh
```

Or call the verifier directly and let it collect the manifests from the environment:

```bash
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --run-selftest \
  --use-private-local-manifests \
  --require-selftest-pass \
  --require-video-selftest-checks \
  --require-disc-manifest-group-checks \
  --require-dds-manifest-group-checks
```

If you are on the machine that already has the private corpora, you can do the manifest-population plus packaged-validation flow in one step:

```bash
python scripts/run_private_packaged_validation.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --output-dir dist/private-runtime-validation \
  --manifest-limit 24 \
  --repeat 3 \
  --repeat-selftest-runs 2 \
  --selftest-stress-loops 4
```

That helper auto-populates private manifests from the configured `ALPHA_FIXER_REAL_*` corpus roots, writes them into the output directory, runs repeated smoke launches plus packaged self-tests, and records JSON timing / RSS summaries for relaunch and memory-trend baselining.

BC6H / BC7 note: the public manifests above improve real external validation coverage, but they do **not** add pure in-repo BC6H / BC7 software decoding. Advanced BC6H / BC7 DDS inspection still depends on the codecs supported by Pillow or ImageMagick/wand. Release builds require a bundled ImageMagick/wand runtime, but this does not guarantee support for every advanced DDS surface or codec.

Example packaged format-matrix manifest:

```json
{
  "base_dir": "/mnt/corpora/conversion-samples",
  "entries": [
    {
      "input": "transparent_ui.png",
      "target_format": "DDS",
      "dds_variant": "rgba"
    },
    {
      "input": "ps2_texture.bmp",
      "target_format": "PNG"
    },
    {
      "input": "hdr_texture.png",
      "target_format": "DDS",
      "dds_variant": "dxt1"
    }
  ]
}
```

To run only the optional real-corpus validations:

```bash
python -m pytest \
  tests/test_ui_components.py -k "optional_real_video_corpus or optional_real_disc_video" \
  tests/test_converter.py -k "optional_real_dds"
```

### Re-running packaged verification on a fresh machine

After copying a built app to another machine, you can rerun the same packaged smoke/capability checks without rebuilding. The executable itself needs no Python installation: set `ALPHA_FIXER_RUNTIME_SELFTEST=2`, `ALPHA_FIXER_RUNTIME_STRESS_LOOPS=2`, and `ALPHA_FIXER_VALIDATION_JSON_OUT` to a writable report-file path before launching it. It generates test media internally, writes its results, and exits with a failing status if a check fails. Leave both sample-download settings disabled for offline testing, and unset these validation variables before normal use.

The following developer/QA wrapper additionally requires Python:

```bash
# Linux / macOS example
python scripts/verify_packaged_app.py dist/AlphaFixerConverter/AlphaFixerConverter \
  --offline \
  --smoke-seconds 2 \
  --repeat 3 \
  --run-selftest \
  --selftest-iterations 4 \
  --selftest-sample-limit 8 \
  --selftest-stress-loops 2 \
  --require-selftest-pass \
  --require-selftest-check generated_mp4_load \
  --require-selftest-check mpegts_load \
  --require-stress-selftest-checks \
  --require-video-runtime \
  --require-no-missing-libs
```

The verifier smoke-launches the packaged app, performs the runtime capability dump, and can be repeated multiple times to catch packaging regressions that only appear after several launches. With `--run-selftest`, the packaged executable also generates a tiny built-in media/format matrix (GIF, DDS, MP4, MPEG-TS, and a synthetic odd-extension probe) so fresh-machine checks can validate more than just startup. When you provide `--disc-video-manifest`, `--dds-manifest`, or `--format-matrix-manifest`, the packaged app also executes those external real-sample sets in-process and reports them as `external_disc_video_manifest`, `external_dds_manifest`, and `external_format_matrix_manifest` self-test checks.

Required self-test checks must actually run; a skipped feature cannot satisfy a release gate. With `--json-out`, a companion UTF-8 `.log` retains launch output and exit status even if validation fails before a JSON report is available. CI uploads these diagnostics on failure as well as success. Console output safely escapes characters unsupported by legacy Windows encodings without changing the Unicode data in JSON reports.

Windows packaged example:

```powershell
python scripts/verify_packaged_app.py dist\AlphaFixerConverter\AlphaFixerConverter.exe `
  --smoke-seconds 2 `
  --repeat 3 `
  --timeout 45 `
  --run-selftest `
  --selftest-iterations 6 `
  --selftest-stress-loops 3 `
  --use-public-sample-manifests `
  --allow-sample-downloads `
  --sample-cache-dir artifacts\sample-cache `
  --require-selftest-pass `
  --require-video-selftest-checks `
  --require-stress-selftest-checks `
  --require-public-manifest-group-checks `
  --require-ffmpeg-selfcheck `
  --require-ffprobe-selfcheck `
  --require-bundled-ffmpeg `
  --require-bundled-ffprobe
```

The repository also includes a dedicated GitHub Actions workflow, `.github/workflows/fresh-machine-runtime.yml`, which qualifies folder and single-file bundles on hosted Ubuntu, Windows, and macOS machines with offline checks, repeated smoke launches, and runtime self-tests. Hosted runners are not substitutes for separate clean-machine GUI qualification.

## Architecture

```
src/
  version.py           - App version constant (2.0.0)
  core/
    alpha_processor.py   - Alpha channel processing logic
    file_converter.py    - Image format conversion
    presets.py           - Built-in and custom preset definitions
    worker.py            - Background QThread workers (non-blocking)
    settings_manager.py  - Persistent settings (QSettings) + export/import
  ui/
    main_window.py       - Main window + menu + Patreon link + unlock system
    alpha_tool.py        - Alpha Fixer tab (comparison slider, keyboard shortcuts)
    converter_tool.py    - File Converter tab (image preview, shortcuts, history recording)
    history_tab.py       - Conversion History tab (timestamped, colour-coded)
    preview_pane.py      - ImagePreviewPane thumbnail + BeforeAfterWidget comparison slider
    selective_alpha_tool.py - Selective Alpha Tool (zone painting, transform, mask slots)
    settings_dialog.py   - Settings dialog (themes, effects, tooltip mode, unlock display)
    theme_engine.py      - Qt stylesheet generator + 57 theme palettes (18 preset + 39 hidden) + THEME_EFFECTS map
    click_effects.py     - Per-theme click particle overlay (blood, bats, stars, skulls, otters)
    tooltip_manager.py   - Cycling tooltip engine: Normal / Off / Dumbed Down / No Filter
    drop_list.py         - DropFileList: drag-and-drop, Delete key, right-click remove
    mouse_trail.py       - Mouse trail particle overlay (11 styles)
    sound_engine.py      - Click sound engine (QSoundEffect + fallback)
    splash_screen.py     - Animated themed startup splash screen
tests/
  test_core.py           - Unit tests for alpha processing & presets
  test_converter.py      - Unit tests for file conversion
  test_ui_components.py  - Unit tests for all UI components
main.py                  - Entry point with crash prevention, logging, libEGL check
alpha_fixer.spec         - PyInstaller build spec
scripts/
  install_linux_deps.sh  - One-shot system-library installer (libegl1, libpulse0, ...)
  build_exe.sh           - Linux / macOS standalone build script
  build_exe.bat          - Windows standalone build script
```
