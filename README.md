# 🐼 Alpha Fixer & File Converter

A panda-themed desktop application for image, animation, and video processing:

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
Confirmed builder exports pause preview playback. Canceling the save dialog leaves playback unchanged. GIF/Video exports also honor cancellation after the last rendered frame, and Video Builder checks again before starting audio muxing. Encoding and muxing themselves are synchronous and cannot be interrupted mid-call.
GIF Builder reordering preserves the playing frame and refreshes the selected frame's delay controls. Frames using global timing show the current global delay; enabling an override starts from that value.
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
Painted comparison-preview messages, Before/After labels and statistics inherit the live UI font. Messages wrap and narrow comparison labels/statistics use ellipses rather than drawing across the divider.
GIF Builder delay, loop and dimension readouts grow with their text and live UI font instead of using fixed pixel widths, keeping units and boundary values readable.
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
