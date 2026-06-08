
## One-command launch

Run the app with:

```bash
python3 run_neuro_signal_app.py
```

This checks whether the software and required libraries are installed. If they are missing, it installs them automatically with `pip install -e '.[all,frontend,live,dev]'`. If they are already installed from this folder, it skips reinstalling, starts the local server, and opens `http://127.0.0.1:8787`.

# Neuro Signal Importer v0.5.5

Continuous-neural-signal-only importer for EEG/ECoG/MEA/organoid-style files.

v0.5.5 keeps the project boundary deliberately strict:

- raw/continuous signal first
- no spike tables
- no stimulation/event alignment
- no amplitude/frequency/location/block logic

## Install

```bash
pip install -e '.[all,dev]'
```

For the optional desktop frontend:

```bash
pip install -e '.[all,frontend]'
```

## CLI quickstart

Inspect:

```bash
neuro-import inspect path/to/file.h5
```

Convert:

```bash
neuro-import convert path/to/file.h5 --output converted_recording
```

Batch convert a folder:

```bash
neuro-import batch raw_dataset/ --output converted_dataset/
```

Launch the GUI:

```bash
neuro-import-gui
```

or:

```bash
neuro-import gui
```

## v0.5.5 additions

- Manual mapping YAML support for unknown files
- Mapping-template generation from inspected HDF5/MAT-like structures
- Unit calibration and unit metadata
- Large-file export controls: `.npy`, memory-mapped `.npy`, HDF5, optional Zarr
- Reproducibility hashes for source/config/mapping
- Plugin adapter discovery
- Better failure reports
- Optional PySide6 desktop frontend with drag-and-drop files/folders

## Mapping workflow for unknown files

```bash
neuro-import generate-mapping weird_file.h5 --output mapping_help --sampling-rate 30000
```

Edit `mapping_help/mapping_template.yaml`, then run:

```bash
neuro-import convert weird_file.h5 --mapping mapping_help/mapping_template.yaml --output converted_weird
```

Example mapping:

```yaml
signal_path: /recording/samples
sampling_rate: 30000
time_path: null
channel_names_path: /metadata/channel_names
orientation: auto
original_units: adc_counts
target_units: microvolts
scale_factor: 0.195
offset: 0
metadata:
  source_family: custom_hdf5
```

## Desktop frontend

The GUI is a thin wrapper over the same backend pipeline. It supports:

- drag-and-drop files/folders
- choose output folder
- inspect selected files
- run single-file or batch conversion
- optional mapping/config YAML
- sampling-rate and signal-path overrides
- unit calibration
- preprocessing/windowing controls
- large-file export options
- output-folder link after completion

## Main outputs

Typical converted folder:

```text
signal.npy
signal.csv              # skipped automatically for very large files if configured
time.npy
channels.csv
electrodes.csv          # if available
metadata.json
quality_report.json
provenance.json
export_report.json
qc_report.html
file_tree_report.json   # for HDF5-like files
```

## Plugin adapters

External packages can register adapters through the `neuro_importer.adapters` entry-point group, or through:

```bash
export NEURO_IMPORTER_ADAPTERS="my_module:MyAdapter"
```

The adapter must implement the same `score(raw)` and `convert(raw, ...)` protocol as built-in adapters.

## v0.5.5.2 GUI progress-bar patch

This patch fixes a frontend state issue where the backend conversion could complete but the Qt progress bar remained in loading mode. The GUI now uses explicit progress percentages and a failsafe terminal cleanup signal that stops the progress bar and re-enables buttons on success, failure, or worker-thread completion.


## v0.5.5 GUI completion + large CSV fix

This patch fixes the frontend state for long MAT conversions by keeping the progress bar determinate, leaving a visible 100% `Conversion complete` bar after completion, and disabling slow notebook-style CSV exports by default. The GUI now writes the canonical fast outputs by default (`signal.npy`, `time.npy`, `channels.csv`, metadata/provenance/QC files). To also create `eeg_df.csv` and `neural_signal_with_time.csv`, enable `Create notebook CSV exports` in the Export options.

## v0.5.5 GUI completion fix

The desktop app now uses fast canonical-only defaults for conversion:

- `signal.npy`
- `time.npy`
- `channels.csv`
- `metadata.json`
- `quality_report.json`
- `provenance.json`

Slow notebook CSV exports are disabled unless explicitly enabled. The GUI also watches the output folder for completed canonical files and forces the progress bar to 100% with the message **Conversion complete** once the outputs are present.

If the app title does not say `Neuro Signal Importer v0.5.5`, you are still running an older installed copy.

## v0.5.5 heartbeat GUI progress

The desktop GUI now receives structured heartbeat events from the backend pipeline.
The Progress tab shows:

- current running stage
- most recently completed stage
- stage-based percentage
- a compact detail area instead of an endlessly growing noisy log

The bar reaches `100% — Conversion complete` when the backend emits the final `Complete` event.


## v0.6 variable-electrode live analysis backend

v0.6 adds a new live backend package, `neuro_importer_live`, that removes the old fixed 32-channel assumption from the replay/analysis stack.

The live flow is now metadata-driven:

```text
signal.npy + channels.csv + metadata.json
        ↓
variable player reads signal.shape[1]
        ↓
receiver initializes ring buffer from incoming n_channels
        ↓
analyzer computes per-channel spectral metrics for any channel count
        ↓
WebSocket bridges forward the channel manifest
        ↓
HTML viewers render whatever channel count is present
```

Run a complete variable-electrode backend:

```bash
neuro-live-backend \
  --source converted_recording/signal.npy \
  --channels-csv converted_recording/channels.csv \
  --metadata-json converted_recording/metadata.json \
  --fs 1000 \
  --channel-profile auto
```

For FinalSpark-style 32-electrode MEA naming:

```bash
neuro-live-backend \
  --source converted_recording/signal.npy \
  --fs 30000 \
  --channel-profile finalspark_32
```

Individual components are also available:

```bash
neuro-live-player --source signal.npy --fs 1000 --channel-profile generated_numeric
neuro-live-receiver
neuro-live-analyzer
```

Open the packaged HTML files if needed:

```text
neuro_importer_live/raw_visualizer_variable.html
neuro_importer_live/spectral_visualizer_variable.html
```

Supported channel-count behavior:

- 8, 16, 32, 64, 128, 256, 1024, or any other 2D `samples × channels` array.
- Uses `channels.csv` names when available.
- Uses `metadata.json`/`channel_manifest` when available.
- Falls back to generated names: `ch_000`, `ch_001`, ...
- FinalSpark profile: `mea0_organoid0_e0` through `mea0_organoid3_e7`.
- EEG 32 profile: common 10-10 32-channel names.

---

# v0.8.1 HTML Frontend

v0.8.1 replaces the optional PySide desktop GUI with a local HTML/FastAPI web application.
The backend still runs locally on your machine and writes finished outputs to your filesystem.

## Install

```bash
pip install -e '.[all,frontend,live,dev]'
```

## Launch the HTML app

```bash
neuro-signal-app
```

The app opens at:

```text
http://127.0.0.1:8787
```

## What the HTML app does

- Drag/drop raw neural signal files into the browser.
- Convert raw files into canonical outputs: `signal.npy`, `channels.csv`, `metadata.json`, QC/provenance reports.
- Show heartbeat progress from real backend pipeline stages.
- Start prerecorded live replay analysis from a converted `signal.npy` file.
- Open the raw and spectral browser visualizers.
- Run feature-level comparative analysis for one dataset/group vs another.
- Show and open the exact output folder where results were written.

## Modes

### Import / Convert

Use this for raw `.mat`, `.h5`, `.npy`, `.csv`, `.edf`, `.nwb`, etc.

### Live Replay

Use this for prerecorded data that should be streamed like live neural data.
Provide a converted `signal.npy` path and optional `channels.csv` / `metadata.json`.

### Compare

Use this to compare converted recording folders. The comparison is feature-level by default,
which is safe when datasets have different channel counts or different electrode naming schemes.

## Important browser note

Browsers cannot reliably pass arbitrary local folder paths from drag/drop for every operating system.
For large folders, use path-based conversion or put the folder path into the backend/CLI. For individual files,
drag/drop upload works directly.


## v0.8.1 SpeedMouse Integration

This version includes a SpeedMouse-compatible browser workbench under `/speedmouse/`. Our backend remains responsible for conversion, large/offline analysis, variable-electrode manifests, live replay, and comparative processing. The SpeedMouse view loads generated `data.json` files, live WebSocket sample streams, and comparison manifests.

Main run command:

```bash
python3 run_neuro_signal_app.py
```

Then open or use the launcher buttons for:

- Convert only
- Analyze in SpeedMouse
- Build SpeedMouse from converted folders
- SpeedMouse live replay
- SpeedMouse group comparison



## v0.8.1 Original SpeedMouse Integration

This build vendors the original uploaded SpeedMouse repository under `neuro_signal_webapp/speedmouse/` and also keeps an untouched reference copy under `vendor/speedmouse_original/`. The app launcher at `/` remains our Neuro Signal HTML frontend. SpeedMouse is served at `/speedmouse/` and receives datasets produced by the Neuro Signal backend via `data.json` URLs or live WebSocket URLs. The served SpeedMouse copy is the original workbench with minimal compatibility patches for variable channel counts, external dataset query loading, and local live WebSocket auto-connect.


## v0.9.1 SpeedMouse Auto-Open Launcher

`python3 run_neuro_signal_app.py` now opens both pages automatically after the local server is ready:

- `http://127.0.0.1:8787/` for the Neuro Signal launcher/frontend
- `http://127.0.0.1:8787/speedmouse/` for the original SpeedMouse workbench

The launcher still performs install/library checks first and skips `pip install` when the editable install and required libraries are already present.

Launch options:

```bash
python3 run_neuro_signal_app.py                 # open both app + SpeedMouse
python3 run_neuro_signal_app.py --open app      # open only our launcher
python3 run_neuro_signal_app.py --open speedmouse
python3 run_neuro_signal_app.py --open both
python3 run_neuro_signal_app.py --no-browser    # start server only
```


## v0.9.1 complete SpeedMouse workbench integration

This version keeps the original SpeedMouse workbench vendored under `/speedmouse/` and uses the Neuro Signal App as the launcher/orchestrator. The launcher can convert raw neural files, build SpeedMouse-compatible `data.json`, render quick HTML preview plots, open SpeedMouse on the generated dataset, run live replay into SpeedMouse, and package group comparisons for SpeedMouse's session/comparison workbench.

Normal run:

```bash
python3 run_neuro_signal_app.py
```

Then use **Analyze in SpeedMouse** from the Import tab, or **Compare Groups in SpeedMouse** from the SpeedMouse/Compare tabs.
