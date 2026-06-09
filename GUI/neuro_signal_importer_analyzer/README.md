# Neuro Signal Importer + NeuroMouse Workbench

**Version:** `0.9.3-readme-update`  
**Purpose:** Convert, standardize, analyze, replay, and visualize continuous neural signal data from many file formats using a local Python backend and an HTML/NeuroMouse browser workbench.

**NeuroMouse source:** https://github.com/UlaYuga/NeuroMouse

This project is designed for **continuous neural signal data**: EEG, ECoG, iEEG, MEA, organoid recordings, FinalSpark-style data, Cortical Labs-style data, HDF5/NWB-style recordings, MATLAB structures, NumPy arrays, tabular exports, and related neurophysiology signal files.

It deliberately does **not** center the pipeline on spike tables, stimulation events, or fixed EEG-only assumptions. The main data model is:

```text
samples × channels continuous signal
+ channel/electrode metadata
+ sampling/time metadata
+ quality/provenance reports
+ optional offline/live/comparative analysis
```

---

## Current System Overview

The system now has four major layers:

```text
1. Conversion backend
   raw neural file/folder → canonical converted recording

2. Offline analysis backend
   converted recording → features, summaries, NeuroMouse-compatible data.json

3. Live replay backend
   converted signal.npy → prerecorded live stream + raw/spectral WebSocket views

4. HTML frontend + NeuroMouse workbench
   drag/drop, convert, analyze, compare, live replay, interactive visualization
```


> **Compatibility note:** Some internal routes, command options, filenames, and package folders still use the lowercase `speedmouse` identifier for backward compatibility with the existing backend code. User-facing documentation and project links now refer to **NeuroMouse**.

The two main browser pages are:

```text
http://127.0.0.1:8787/             Neuro Signal launcher/frontend
http://127.0.0.1:8787/speedmouse/  NeuroMouse workbench, integrated
```

The launcher page is our control center. NeuroMouse is the interactive neural signal visualization and comparison workbench.

---

## Important Design Boundary

This project is currently focused on:

- raw/continuous neural signals
- variable channel/electrode counts
- variable channel/electrode names
- batch conversion
- offline feature extraction
- live replay from prerecorded recordings
- feature-level dataset/group comparison
- NeuroMouse-compatible visualization outputs

This project intentionally does **not** currently focus on:

- spike-event extraction as the primary data product
- stimulation start/stop alignment
- amplitude/frequency/location/block experimental labels
- assuming all files are 32-channel EEG

Those can be added later, but the current system is built around continuous neural dynamics.

---

## One-Command Launch

From the project folder, run:

```bash
python3 run_neuro_signal_app.py
```

The launcher script automatically:

1. Checks whether the local package is already installed from this folder.
2. Checks whether required Python libraries are importable.
3. Runs installation only if something is missing.
4. Starts the local FastAPI backend server.
5. Waits until the server health check passes.
6. Opens the browser.

By default, it opens the launcher app. Depending on launch options, it can also open NeuroMouse.

### Launcher options

```bash
python3 run_neuro_signal_app.py                 # normal install-check + launch
python3 run_neuro_signal_app.py --open app      # open only our launcher
python3 run_neuro_signal_app.py --open speedmouse
python3 run_neuro_signal_app.py --open both
python3 run_neuro_signal_app.py --no-browser    # start server only
python3 run_neuro_signal_app.py --force-install # intentionally reinstall
python3 run_neuro_signal_app.py --port 8790
python3 run_neuro_signal_app.py --workspace ~/neuro_signal_workspace
```

`--force-install` is not required for normal use. It is only for intentionally reinstalling.

---

## Manual Install, If Needed

The launcher usually handles this automatically, but the manual development install is:

```bash
pip install -e '.[all,frontend,live,dev]'
```

The package extras include:

```text
all       broad file-format support: MAT/HDF5/NWB/EDF/Excel/Zarr/etc.
frontend  FastAPI, Uvicorn, multipart upload support
live      ZeroMQ/WebSocket live replay backend
frontend  local HTML web app server dependencies
dev       pytest and development tools
```

---

## Supported Input Types

The importer can inspect/convert many neural signal sources, including:

```text
MATLAB .mat
  - DSamp-style files
  - EEGLAB-style structures
  - FieldTrip-style structures
  - generic MAT arrays with mapping support

HDF5-like files
  - generic HDF5
  - NWB-style HDF5
  - FinalSpark LiveMEA-style HDF5
  - Cortical Labs CL1-style HDF5
  - MaxWell/HD-MEA-style HDF5-like layouts

Standard neuro formats
  - EDF/BDF through optional MNE support
  - EEGLAB .set through MNE where available
  - NWB through optional PyNWB support

Simple array/table formats
  - .npy
  - .npz
  - .csv
  - .tsv
  - .xlsx
```

For unknown MAT/HDF5 files, the system can generate a mapping template so the user can tell the importer which dataset is the signal, where channel names live, what the sampling rate is, and how units should be interpreted.

---

## Main Converted Output Format

A typical converted recording folder contains:

```text
converted_recording/
├── signal.npy              continuous signal, shape samples × channels
├── time.npy                optional time vector
├── channels.csv            channel/electrode names and metadata
├── electrodes.csv          electrode geometry if available
├── metadata.json           sampling rate, source info, units, etc.
├── quality_report.json     validation results and warnings
├── provenance.json         source paths, adapter used, hashes, config info
├── export_report.json      export choices and file sizes
├── qc_report.html          quick quality-control report
└── file_tree_report.json   HDF5-like structure report when applicable
```

The canonical rule is:

```text
signal.npy shape = samples × channels
```

Channel count and names are read from the data whenever possible. If names are missing, generated names are used:

```text
ch_000, ch_001, ch_002, ...
```

---

## Variable-Electrode Support

The system is no longer fixed to 32 channels. Channel count and channel names flow from the converted data.

The live and analysis pipeline supports:

```text
8, 16, 32, 64, 128, 256, 1024, or any other valid channel count
```

as long as the signal is a 2D continuous array:

```text
samples × channels
```

Channel metadata priority:

```text
1. channels.csv
2. metadata.json channel_manifest
3. source-file channel labels
4. selected channel profile
5. generated names ch_000, ch_001, ...
```

Included channel profiles include:

```text
generated_numeric
common EEG 10-10 32-channel profile
FinalSpark-style 32-electrode MEA profile
```

For FinalSpark-style 32-electrode MEA naming, the profile uses organoid/electrode-style labels such as:

```text
mea0_organoid0_e0
...
mea0_organoid3_e7
```

---

## Neuro Signal Launcher Frontend

Open:

```text
http://127.0.0.1:8787/
```

The launcher frontend supports:

- drag/drop file upload
- raw-file conversion
- path-based conversion for larger files/folders
- heartbeat progress from backend pipeline stages
- live replay setup
- NeuroMouse analysis setup
- group comparison setup
- quick HTML preview plots
- output-folder and report links

### Main launcher modes

```text
Import / Convert
  Convert raw files into canonical signal.npy + metadata outputs.

NeuroMouse
  Analyze current uploads in NeuroMouse, build data.json from converted folders,
  or launch live replay into NeuroMouse.

Live Replay
  Replay a converted recording like a live stream.

Compare
  Compare one dataset/group against another using feature-level summaries.

Results
  Open output folders, reports, generated data.json, and NeuroMouse links.
```

---

## NeuroMouse Integration

The project integrates the **NeuroMouse workbench**.

NeuroMouse source: https://github.com/UlaYuga/NeuroMouse

The local integration keeps reference and served copies inside the GUI package so the backend can generate compatible visualization datasets.

They are stored in two places:

```text
neuro_signal_webapp/speedmouse/    served integrated copy
vendor/speedmouse_original/        untouched reference copy
```

NeuroMouse is served at:

```text
http://127.0.0.1:8787/speedmouse/
```

The integrated NeuroMouse copy has minimal compatibility patches so it can:

- load backend-generated `data.json` through a query parameter
- distinguish demo data from backend-generated datasets
- show a visible backend-dataset banner only for real backend outputs
- accept variable channel counts instead of requiring 32 channels
- load NeuroMouse comparison manifests
- connect to NeuroMouse-compatible live replay WebSocket streams
- fall back to generic channel/electrode layouts when EEG 10-20/10-10 names are not available

---

## Analyze in NeuroMouse

This is the recommended workflow for interactive offline analysis.

In the launcher:

1. Drag/drop a raw neural file or files.
2. Choose options if needed.
3. Click **Analyze in NeuroMouse**.

The backend then:

```text
raw file/folder
    ↓
convert if needed
    ↓
load signal.npy + channels.csv + metadata.json
    ↓
compute offline NeuroMouse features
    ↓
write outputs/<job_id>/speedmouse/data.json
    ↓
open NeuroMouse with:
    /speedmouse/?dataset=/api/jobs/<job_id>/speedmouse/data.json&backend_job=<job_id>&backend=1
```

Important: NeuroMouse still plots a `data.json` file, because that is its native plotting format. The difference is that the `data.json` should now be **generated from your dragged/converted data**, not the bundled demo file.

### How to confirm it loaded your data

A correct backend-generated NeuroMouse page should show a source like:

```text
/api/jobs/<job_id>/speedmouse/data.json
```

It should not show:

```text
/speedmouse/data/data.json
```

If the source is `/speedmouse/data/data.json`, you are looking at the bundled NeuroMouse demo dataset, not the generated backend dataset.

Use the **Open generated NeuroMouse dataset** link in the launcher Results tab after analysis completes.

---

## NeuroMouse Data Output

The backend-generated NeuroMouse folder typically contains:

```text
speedmouse/
├── data.json
└── speedmouse_manifest.json
```

`data.json` includes NeuroMouse-compatible offline analysis arrays such as:

```text
meta
welch_psd
centroid
geometry
channel_summary
optional channel/group summaries
```

For large recordings, the backend samples/limits offline windows according to the NeuroMouse analysis options in the launcher. The original raw converted `signal.npy` is still preserved.

---

## Live Replay in NeuroMouse

Live replay is for prerecorded data streamed like a live source.

The data flow is:

```text
converted signal.npy
    ↓
variable-electrode live backend
    ↓
NeuroMouse-compatible WebSocket sample stream
    ↓
NeuroMouse live source view
```

The NeuroMouse live WebSocket endpoint uses metadata-driven frames:

```text
channel_names
n_channels
sampling_rate_hz
samples
```

This allows NeuroMouse to render data with any channel count, not only 32-channel EEG.

---

## Comparative Analysis

The system supports feature-level group comparison.

Use this when comparing:

```text
one dataset vs another dataset
one group of recordings vs another group
baseline vs treatment
trained vs untrained
subject/session A vs subject/session B
```

For comparisons, the backend can:

1. Convert raw files if needed.
2. Build NeuroMouse-compatible `data.json` files for each recording.
3. Compute feature-level summaries.
4. Build a comparison manifest.
5. Open NeuroMouse on the comparison.

This is safer than direct channel-to-channel comparison when datasets have different electrode counts or names.

### Same-channel vs feature-level comparison

Same-channel comparison is appropriate only when channel layouts match.

Feature-level comparison is appropriate when layouts differ, such as:

```text
32-channel EEG vs 64-channel EEG
FinalSpark 32-electrode MEA vs Cortical Labs 64-channel MEA
8-channel organoid recording vs 32-channel MEA recording
```

Feature-level comparison uses global/channel-distribution summaries instead of assuming channel `Fp1` equals `ch_000`, etc.

---

## CLI Quickstart

Inspect a file:

```bash
neuro-import inspect path/to/file.h5
```

Convert one file:

```bash
neuro-import convert path/to/file.h5 --output converted_recording
```

Batch convert a folder:

```bash
neuro-import batch raw_dataset/ --output converted_dataset/
```

Generate a mapping template for an unknown MAT/HDF5 file:

```bash
neuro-import generate-mapping weird_file.h5 --output mapping_help --sampling-rate 30000
```

Convert with a mapping file:

```bash
neuro-import convert weird_file.h5 --mapping mapping_help/mapping_template.yaml --output converted_weird
```

---

## Mapping YAML for Unknown Files

Example:

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

Use mapping files when the importer can open a file but cannot safely infer which array is the neural signal.

---

## Live Backend Commands

Run a complete variable-electrode live backend:

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

Individual components:

```bash
neuro-live-player --source signal.npy --fs 1000 --channel-profile generated_numeric
neuro-live-receiver
neuro-live-analyzer
```

Packaged standalone live HTML viewers:

```text
neuro_importer_live/raw_visualizer_variable.html
neuro_importer_live/spectral_visualizer_variable.html
```

---

## Backend API Summary

The local FastAPI server exposes endpoints used by the HTML launcher and NeuroMouse integration.

Important routes include:

```text
GET  /                                  launcher frontend
GET  /speedmouse/                       NeuroMouse workbench
GET  /api/health                        server health/version/workspace
POST /api/jobs/convert-upload           convert uploaded files
POST /api/jobs/convert-paths            convert path-based files/folders
POST /api/jobs/inspect-upload           inspect uploaded files
POST /api/jobs/analyze-speedmouse-upload
POST /api/jobs/analyze-speedmouse-paths
POST /api/jobs/speedmouse-from-converted
POST /api/jobs/compare
POST /api/jobs/compare-speedmouse
GET  /api/jobs/<job_id>
GET  /api/jobs/<job_id>/speedmouse/data.json
GET  /api/jobs/<job_id>/speedmouse/manifest.json
WS   /api/jobs/<job_id>/events          heartbeat progress events
WS   /ws/speedmouse/live                NeuroMouse-compatible live sample stream
GET  /api/file?path=...                 serve generated output file
GET  /api/open-output?path=...          ask OS to open output folder
```

---

## Output Folder Structure

By default, the app writes to a workspace like:

```text
~/neuro_signal_app_workspace/
├── uploads/
├── outputs/
│   └── <job_id>/
│       ├── converted/
│       ├── speedmouse/
│       │   ├── data.json
│       │   └── speedmouse_manifest.json
│       ├── comparison_manifest.json
│       ├── qc_report.html
│       └── job/result files
└── logs/
```

The exact output folder is shown in the launcher Results tab after a job completes.

---

## Quality Control and Provenance

Each conversion stores quality/provenance files when possible:

```text
quality_report.json
provenance.json
export_report.json
qc_report.html
```

These include information such as:

- adapter used
- original source path
- detected signal shape
- detected or overridden sampling rate
- channel count
- unit conversion / scale factor
- warnings
- file hashes where available
- assumptions made during conversion

---

## Troubleshooting

### NeuroMouse still shows the demo data

Check the NeuroMouse banner/source.

Correct generated backend data source:

```text
/api/jobs/<job_id>/speedmouse/data.json
```

Wrong demo data source:

```text
/speedmouse/data/data.json
```

If you see the demo path, use the **Open generated NeuroMouse dataset** link in the launcher Results tab after **Analyze in NeuroMouse** completes.

### Browser did not open automatically

Open manually:

```text
http://127.0.0.1:8787/
```

or:

```text
http://127.0.0.1:8787/speedmouse/
```

### Port already in use

Use another port:

```bash
python3 run_neuro_signal_app.py --port 8790
```

### Install check did not pick up new code

Force reinstall intentionally:

```bash
python3 run_neuro_signal_app.py --force-install
```

### Large files are slow in browser upload

For very large files/folders, prefer path-based conversion or CLI conversion rather than browser upload. Browser drag/drop may copy data into the local workspace first.

### Unknown MAT/HDF5 file cannot convert

Generate a mapping template:

```bash
neuro-import generate-mapping weird_file.h5 --output mapping_help --sampling-rate 30000
```

Then edit the YAML and rerun conversion with `--mapping`.

---

## Project Layout

```text
neuro_importer/
  conversion backend, adapters, readers, validators, exporters

neuro_importer_live/
  variable-electrode live player, receiver, analyzer, WebSocket bridges, live HTML viewers

neuro_importer_analysis/
  offline feature extraction and comparative analysis

neuro_importer_speedmouse/
  NeuroMouse-compatible data.json builder, comparison packager, live bridge

neuro_signal_webapp/
  local FastAPI app, launcher frontend, integrated served NeuroMouse copy

neuro_signal_webapp/static/
  our HTML launcher frontend

neuro_signal_webapp/speedmouse/
  integrated NeuroMouse workbench with compatibility patches

vendor/speedmouse_original/
  compatibility reference copy for the integrated NeuroMouse workbench

run_neuro_signal_app.py
  install-check, server startup, browser launcher
```

---

## Testing

Run tests with:

```bash
pytest
```

The test suite covers:

- synthetic DSamp MAT conversion
- next-generation adapters
- HDF5 continuous adapters
- batch/QC/window features
- mapping/unit/large-file exports
- heartbeat progress events
- HTML/FastAPI app routes
- variable-electrode live backend
- NeuroMouse integration routes
- progress-event regression handling

---

## Recommended User Workflow

For a normal user:

```text
1. Run: python3 run_neuro_signal_app.py
2. Browser opens at http://127.0.0.1:8787/
3. Drag/drop raw neural file(s).
4. Click Analyze in NeuroMouse.
5. Wait for heartbeat progress to finish.
6. Use the generated NeuroMouse dataset link in Results.
7. Explore the data in NeuroMouse.
```

For development/CLI:

```text
1. Convert raw files with neuro-import.
2. Inspect converted signal.npy / channels.csv / metadata.json.
3. Build NeuroMouse data.json through the launcher or backend API.
4. Run live replay or comparison as needed.
```

---

## Current Status

This is the first complete integrated version where:

```text
raw neural files/folders
    → canonical conversion
    → variable-electrode metadata
    → offline NeuroMouse-compatible analysis
    → integrated NeuroMouse visualization
    → optional live replay
    → optional group comparison
```

The system is still under active development, but the architecture is now set up so our backend can feed the NeuroMouse workbench while preserving our own HTML launcher/frontend and variable-electrode neuro-signal pipeline.
