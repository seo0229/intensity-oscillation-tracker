# Intensity

A local fluorescence analysis app for stationary cells. Cellpose identifies cell boundaries, fixed circles sample each accepted region, and selecting a circle shows its intensity trace. Five simple metrics summarize peaks in the checked regions.

## Mac desktop app

On an Apple silicon Mac running macOS 14 Sonoma or later, double-click **Intensity-macOS-Apple-Silicon-v0.2.2.zip** in Finder. Move **Intensity.app** to Applications and open it. Choose **Open recording** to select a local recording. Cellpose runs on the CPU using weights included in the app.

The Mac bundle contains its own arm64 Python runtime, Qt WebEngine, and analysis libraries. Logs and browser storage live in `~/Library/Application Support/Intensity`. This development build ships without Developer ID signing or Apple notarization. macOS may request approval on first open.

From the project checkout, run `uv run --locked python -m tracker.build_macos` to create the ZIP and SHA-256 checksum in `dist/macos-v0.2.2`. The builder selects Mac wheels from `uv.lock`, verifies the downloaded runtime and native launcher's checksums, and preserves Unix file permissions and symbolic links in the ZIP. It uses the cached `.cache/cellpose-models/cpsam_v2` weights or a file supplied with `--model`. On a Mac, `/Applications/Intensity.app/Contents/MacOS/Intensity --check-desktop --report ~/Desktop/intensity-check.json` checks the bundled viewer. Use `--self-test "/path/to/recording.tif"` in place of `--check-desktop` to check the analysis engine.

## Windows desktop app

Extract the entire **`Intensity-Windows-v0.2.2.zip`**, then double-click **`Intensity.exe`** inside the extracted **Intensity** folder. Keep that entire folder together, including `_internal`; the executable depends on its bundled libraries and model. Python, a terminal, and a separate browser are not needed to run this version.

The desktop window uses the same frontend, FastAPI backend, Cellpose model, circle rules, measurements, and exports as the browser version. It starts a private loopback server on an available port and stops it when the window closes. The NVIDIA GPU is used when available. Without a compatible GPU, Cellpose automatically uses the CPU. If GPU initialization or inference fails (for example, insufficient GPU memory or a driver error), version 0.2.2 retries once on the CPU and keeps using the CPU for that app session. The status text identifies CPU analysis. The same model, circle rules, and intensity measurements are used; CPU segmentation uses float32 instead of GPU bfloat16, so boundaries can differ slightly. CPU processing can take substantially longer. Cellpose weights and CUDA libraries are included, so the portable folder is several gigabytes and does not download a model on first use. Since version 0.2.1, the window uses bundled Qt WebEngine/Chromium instead of Python.NET, removing the `Python.Runtime.Loader.Initialize` loading failure from the startup path.

Use **Open recording** to choose a file with the Windows file picker. The existing TIFF/TID companion-file requirements still apply. Recordings are not included in the build. A `dataset` folder beside `Intensity.exe` appears in the library; `INTENSITY_DATASET` can point to an existing folder elsewhere. Exports open a Windows Save dialog. Export results before closing the app.

Requires 64-bit Windows 10/11. The viewer and its Chromium runtime are included; Python.NET, .NET, and Microsoft Edge WebView2 are not required. Desktop logs and window storage are written to `%LOCALAPPDATA%\Intensity`, not into the application folder. Set `INTENSITY_APP_DATA` to use another writable location.

To build the portable folder on Windows from the source:

```powershell
uv sync --locked --extra desktop --extra build
uv run --no-sync python -m tracker.build_windows --dist-dir dist/windows-v0.2.2 --zip
```

The build uses cached `.cache/cellpose-models/cpsam_v2` weights. Run an analysis once if they are missing, or pass `--model C:\path\to\cpsam_v2`. This command writes `dist/windows-v0.2.2/Intensity`, a ZIP64 archive, and a SHA-256 checksum beside it. Without `--dist-dir`, the parent output directory is `dist`. Every archived file is checked for integrity. The folder contains dependency versions and the model checksum in `build-info.json`, runtime licenses in `_internal/licenses`, and a short `Read me.txt`. Build output, datasets, tests, and local caches are excluded from Git. Rebuild after changing application source; an existing executable does not read the source checkout. Existing app folders are never replaced automatically: move the previous folder first or use a new `--dist-dir` to keep any recordings and exports beside it safe.

To share the app, send that ZIP and have the recipient extract it into a new local folder, separately from previous builds. Data files are not included. No installer or manual DLL-unblocking step is part of this version's setup. The build checks for required Chromium files and rejects packages containing the obsolete Python.NET bridge. `Intensity.exe --check-desktop --report desktop-check.json` checks the packaged viewer libraries without opening a window; startup errors also record their actual exception in the desktop log.

For desktop development without building: `uv run --extra desktop python -m tracker.desktop`. The original browser launcher remains available below.

## Run from source in a browser

After setup, double-click **`start.bat`** on Windows. The app opens at **http://127.0.0.1:8765**. Keep the launcher running while using the app; close it to stop the server.

For a new installation, install Python 3.11+ and [uv](https://docs.astral.sh/uv/), then run from this folder:

```powershell
uv sync --locked
uv run python -m tracker
```

Cellpose uses the NVIDIA GPU when CUDA is available, otherwise the CPU. The Windows uv configuration installs the tested CUDA 12.8 PyTorch build. The first analysis downloads the official Cellpose-SAM weights (about 1.2 GB) to `.cache/cellpose-models`; later analyses reuse that download and the loaded model. Set `CELLPOSE_LOCAL_MODELS_PATH` to use another model-cache location.

Alternatively, use an existing Python virtual environment. For an NVIDIA GPU on Windows, install the matching PyTorch build first:

```powershell
python -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -e .
python -m tracker
```

Use `python -m tracker --port 8766` if the default port is occupied, or `--no-browser` to start without opening a browser. There is no external frontend build step or CDN dependency. Files and measurements stay on this computer; the server listens only on loopback.

## Workflow

1. Select a recording from the dataset library, or use **Open recording** to browse for a local `.tif`, `.tiff`, or `.tid` file. A native file picker and a path-entry fallback are available. Selecting a new recording automatically starts analysis.
2. Cellpose is the default detector. Adjust **Circle radius** and **Detection sensitivity** if needed, then run **Analyze recording**. **Standard** sensitivity reproduces the validated trial settings. Radius controls circle placement, not the cell outlines. A dashed outline on the selected region previews a changed radius. Rerunning detection replaces circle edits for that recording.
3. Review the circles. Click a circle or a region-list entry to select it. Use **Move** to drag a circle or **Add** to place another. Circles must fit within the detected region with a 2-pixel margin and cannot overlap. **Outlines** shows the segmentation used for placement.
4. Scrub or play the recording. Click either graph to jump to a frame. Scroll over the image to zoom and drag the background to pan. **Mean reference** shows the reference used for detection.
5. Use **Metrics** to summarize only the checked regions. Checkboxes control the metrics and the secondary **Average trace** tab; highlighting a circle only chooses its individual trace. **Uncheck all** clears the subset; **Check all** restores it, including regions hidden by a search. Individual traces remain available. Use **Sort → Most peaks first** to rank regions by descending peak count, or **Region ID** to return to ID order. Raw intensity / **ΔF/F₀** changes the displayed traces, not the metrics.
6. Confirm the frame interval to display seconds and peak rates. Without a verified interval, traces use frame numbers and rate fields remain blank.
7. Export checked-region metrics as CSV, checked-region traces and their average as CSV, all region measurements as CSV, or the complete analysis and settings as JSON. JSON includes the detector, model version, weights checksum, sensitivity cutoff, normalization, processing device, and circle-fit settings.

Switching between recordings in the same browser tab restores their recent analyses and circle edits. Analyses are held in memory, with at most a small number of recent sessions retained. Export before closing or reloading the page; JSON export is an archival record, and importing it is not yet implemented.

## Measurement definitions

- **Reference and detection:** the arithmetic mean of up to 12 frames evenly distributed through the recording. Cellpose 4.2.1.1 uses the pretrained `cpsam_v2` model on a copy of this reference, with 1st/99th percentile normalization, flow threshold 0.4, minimum mask size 15 pixels, and no diameter rescaling. There is no additional spatial smoothing or sharpening. The full circle-fit rule below then removes masks too narrow to sample. Segmentation is an initial estimate and should be reviewed; touching cells may be merged or split.
- **Detection sensitivity:** the slider ranges from 0 to 100, with 50 labeled **Standard**. It maps to Cellpose's cell-probability cutoff as `3 - 6 * sensitivity`, where the stored sensitivity ranges from 0 to 1. Standard therefore uses the default cutoff of 0. Higher sensitivity lowers this cutoff and can admit more or larger regions; it is not a percentage confidence score. Circle radius and display brightness never enter Cellpose inference.
- **Circle placement:** a center close to each segmented region's geometric centroid, constrained so that the complete circle plus a 2-pixel margin remains inside. Positions and radii are fixed across the recording. There is no motion tracking or registration.
- **Raw intensity:** the arithmetic mean of original pixel values in the circle for each stored frame. Viewing contrast, JPEG previews, and segmentation processing do not alter the measured data.
- **ΔF/F₀:** `(F - F₀) / F₀`, where `F₀` is that region's mean over the first N frames (default 10). The graph displays percent change; CSV and JSON exports store the dimensionless ratio. This starting interval is an explicit reference, not an assertion that the recording contains a pre-stimulus baseline. Regions without a positive baseline are excluded from the relative view.
- **Peaks:** local maxima on the original raw trace, separated by at least the configured number of frames. Required prominence is the larger of the configured baseline percentage and three times the noise estimate (robust MAD of first differences). There is no hidden smoothing or detrending.
- **Peak rate:** detected peaks divided by the elapsed time from the first to the last frame, expressed as peaks/minute. A peak rate does not establish periodic oscillation. The median interval between detected peaks is included in summary exports when at least two peaks and a frame interval are available.
- **Population shading:** one population standard deviation across included regions at each frame, not a confidence interval. Averaging out-of-phase traces can hide oscillations, so individual traces and their rates remain available.

The **Metrics** tab summarizes only the checked regions across the full recording. It shows five measurements:

- **Average peaks:** mean peak count per checked region, including regions with zero peaks.
- **No peaks:** number of checked regions with zero detected peaks.
- **Exactly 1 peak:** number of checked regions with one detected peak.
- **More than 1 peak:** number of checked regions with at least two detected peaks. These three counts add up to the number of checked regions.
- **Time between peaks:** the median of each eligible region's median gap, in seconds. Requires confirmed timing and at least two peaks in a region. The contributing region count is shown.

With no checked regions, counts are zero and the average and time are shown as “—”. Metrics update when checkboxes, regions, timing, or signal settings change. Display brightness, highlighting a circle, search, sorting, and raw/relative graph mode do not change them. The metrics CSV contains one checked-region column, plus timing and peak settings; JSON also contains the per-region measurements.

Background subtraction, bleaching correction, stimulus annotation, automatic biological classification, and batch condition comparisons are not part of this version. Region IDs refer to measurement regions, not guaranteed individual cells. Display brightness is scaled consistently within a recording; preview brightness should not be used to compare different recordings.

## Dataset and file support

The local dataset is excluded from Git. Each acquisition has a small metadata TIFF, an associated BigTIFF image stack with a `.tid` extension, and `Experiment.json`. The reader resolves the sibling TID reference and processes frames progressively without loading the whole video into RAM. It also supports ordinary single-channel, single-Z grayscale TIFF page stacks, including LZW-compressed data. RGB and volumetric pages are rejected with an explanation.

The current acquisitions contain **199**, **200**, and **800** frames. `Experiment.json` specifies **800 ms** intervals while the OME metadata specifies **1 s**; the app exposes that conflict and does not automatically choose a frequency scale. The baseline's requested 300 frames are also distinguished from its actual 199 stored frames. Timing assumes a uniform interval and cannot account for unrecorded dropped frames.

The dataset library defaults to the project's `dataset` directory. Set `INTENSITY_DATASET` to scan another directory. Selecting another file directly does not require moving it into the dataset folder.

## Validation and repository scope

This repository includes the application, setup instructions, launcher, and dependency lockfile. The dataset, tests, helper scripts, virtual environment, caches, and model weights are intentionally excluded from Git. The optional `dev` dependencies are available for workspaces that retain the local validation files; those files are not included in a fresh checkout.

Local unit and API tests cover companion-file loading, compressed stacks, actual frame counts and timing conflicts, Cellpose input-copy protection, sensitivity mapping, circle containment, rejecting small or thin masks, exact pixel means, known periodic signals, out-of-phase averaging, zero baselines, checked-region metrics, manual edits, and CSV/JSON exports. Model inference is stubbed in these fast tests.

Separate validation with the real Cellpose model read all stored frames of the three local recordings, checked that the raw reference remained unchanged, and independently verified sampled intensity means and circle containment. Standard settings produced 561, 521, and 770 circles respectively. These are candidate measurement regions, not verified cell counts. There are no manually annotated ground-truth masks for this dataset.

Source layout: `tracker/recordings.py` handles file loading; `tracker/cellpose_detector.py` loads and runs the model; `tracker/analysis.py` places circles and measures traces; `tracker/metrics.py` summarizes peaks for the checked regions; `tracker/server.py` exposes the local API; `tracker/static/` contains the browser interface. `tracker/desktop.py` owns the desktop/server lifecycle, `tracker/desktop_window.py` contains the Qt viewer and native dialogs, `tracker/paths.py` locates bundled assets and writable data, and `tracker/build_windows.py` plus `tracker/packaging/` build the executable and distribution ZIP.
