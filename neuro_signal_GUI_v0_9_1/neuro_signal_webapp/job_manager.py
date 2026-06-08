from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import quote
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from neuro_importer.pipeline import NeuroImportPipeline
from neuro_importer.progress import ProgressEvent
from neuro_importer_analysis import run_comparative_analysis
from neuro_importer_speedmouse import write_speedmouse_dataset, build_speedmouse_comparison_pack


SUPPORTED_UPLOAD_SUFFIXES = {
    ".mat", ".h5", ".hdf5", ".nwb", ".npy", ".npz", ".csv", ".tsv", ".xlsx", ".xls", ".edf", ".bdf", ".set", ".vhdr", ".eeg", ".txt"
}


@dataclass
class JobRecord:
    job_id: str
    kind: str
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress_percent: int = 0
    current_step: str = "Queued"
    output_dir: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


class JobManager:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, JobRecord] = {}
        self.event_queues: dict[str, list[queue.Queue]] = {}
        self.lock = threading.RLock()
        self.live_processes: dict[str, list[subprocess.Popen]] = {}

    def create_job(self, kind: str, output_dir: str | Path | None = None) -> JobRecord:
        job_id = uuid.uuid4().hex[:12]
        job_output = Path(output_dir).expanduser().resolve() if output_dir else self.workspace / "outputs" / job_id
        job_output.mkdir(parents=True, exist_ok=True)
        record = JobRecord(job_id=job_id, kind=kind, output_dir=str(job_output))
        with self.lock:
            self.jobs[job_id] = record
            self.event_queues[job_id] = []
        self.emit(job_id, "state", {"status": "queued", "message": "Job queued.", "percent": 0})
        return record

    def subscribe(self, job_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.event_queues.setdefault(job_id, []).append(q)
            record = self.jobs.get(job_id)
            if record:
                for event in record.events[-25:]:
                    q.put(event)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue) -> None:
        with self.lock:
            queues = self.event_queues.get(job_id, [])
            if q in queues:
                queues.remove(q)

    def emit(self, job_id: str, event_type: str, data: dict[str, Any]) -> None:
        payload = {"type": event_type, "job_id": job_id, "time": time.time(), **data}
        with self.lock:
            record = self.jobs.get(job_id)
            if record:
                record.events.append(payload)
                record.updated_at = time.time()
                if "status" in data:
                    record.status = str(data["status"])
                if "percent" in data and data["percent"] is not None:
                    record.progress_percent = int(data["percent"])
                if "step" in data and data["step"]:
                    record.current_step = str(data["step"])
                if "output_dir" in data and data["output_dir"]:
                    record.output_dir = str(data["output_dir"])
                if "result" in data:
                    record.result = data["result"]
                if "error" in data:
                    record.error = str(data["error"])
            for q in self.event_queues.get(job_id, []):
                q.put(payload)

    def get(self, job_id: str) -> JobRecord | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _progress_callback(self, job_id: str) -> Callable[[ProgressEvent], None]:
        def callback(event: ProgressEvent) -> None:
            """Normalize backend progress events for the HTML job stream.

            v0.5.5/v0.7 ProgressEvent uses `stage`; an earlier webapp
            bridge accidentally referenced `step_name`.  This callback is now
            deliberately defensive so progress reporting can never fail the
            actual conversion job.
            """
            try:
                if hasattr(event, "to_dict"):
                    data = event.to_dict()
                elif isinstance(event, dict):
                    data = dict(event)
                else:
                    data = {
                        "stage": getattr(event, "stage", None)
                        or getattr(event, "step", None)
                        or getattr(event, "step_name", None)
                        or "Pipeline progress",
                        "status": getattr(event, "status", "running"),
                        "step_index": getattr(event, "step_index", None),
                        "total_steps": getattr(event, "total_steps", None),
                        "percent": getattr(event, "percent", None),
                        "message": getattr(event, "message", ""),
                        "detail": getattr(event, "detail", None),
                    }

                step = (
                    data.get("step")
                    or data.get("stage")
                    or data.get("step_name")
                    or data.get("message")
                    or "Pipeline progress"
                )
                percent = data.get("percent")
                if percent is None:
                    percent = 0

                self.emit(job_id, "progress", {
                    "status": data.get("status", "running"),
                    "step": step,
                    "stage": data.get("stage", step),
                    "step_index": data.get("step_index"),
                    "total_steps": data.get("total_steps"),
                    "percent": int(percent),
                    "message": data.get("message") or str(step),
                    "detail": data.get("detail"),
                    "item_index": data.get("item_index"),
                    "total_items": data.get("total_items"),
                })
            except Exception as exc:
                # Do not let a progress/UI reporting bug kill data conversion.
                self.emit(job_id, "progress", {
                    "status": "running",
                    "step": "Progress update skipped",
                    "percent": 0,
                    "message": f"A non-fatal progress update error was ignored: {exc!r}",
                })
        return callback

    def start_convert_job(
        self,
        input_paths: list[str | Path],
        *,
        output_dir: str | Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> JobRecord:
        record = self.create_job("convert", output_dir=output_dir)
        thread = threading.Thread(
            target=self._run_convert_job,
            args=(record.job_id, [str(Path(p)) for p in input_paths], options or {}),
            daemon=True,
        )
        thread.start()
        return record

    def _convert_one(self, pipeline: NeuroImportPipeline, job_id: str, input_path: Path, out_dir: Path, options: dict[str, Any]) -> dict[str, Any]:
        self.emit(job_id, "progress", {"status": "running", "step": f"Converting {input_path.name}", "percent": 5, "message": f"Converting {input_path.name}"})
        preprocess_cfg = None
        if options.get("preprocess"):
            preprocess_cfg = {
                "enabled": True,
                "demean": bool(options.get("demean", False)),
                "detrend": bool(options.get("detrend", False)),
                "normalization": options.get("normalization") or None,
            }
        window_cfg = None
        if options.get("make_windows"):
            window_cfg = {
                "enabled": True,
                "window_seconds": float(options.get("window_seconds") or 2.0),
                "step_seconds": float(options.get("step_seconds") or 1.0),
            }
        export_cfg = {
            "format": options.get("export_format", "npy"),
            "save_signal_csv": bool(options.get("save_signal_csv", False)),
            "csv_max_mb": float(options.get("csv_max_mb", 250.0)),
        }
        unit_cfg = {}
        for key in ("original_units", "target_units", "scale_factor", "offset"):
            if options.get(key) not in (None, ""):
                unit_cfg[key] = options[key]
        adapter_kwargs: dict[str, Any] = {}
        if options.get("sampling_rate") not in (None, ""):
            adapter_kwargs["sampling_rate"] = float(options["sampling_rate"])
        if options.get("signal_path"):
            adapter_kwargs["signal_path"] = options["signal_path"]
        if options.get("orientation") and options["orientation"] != "auto":
            adapter_kwargs["orientation"] = options["orientation"]

        result = pipeline.convert(
            input_path,
            output_dir=out_dir,
            include_aux=bool(options.get("include_aux", False)),
            preprocess_config=preprocess_cfg,
            window_config=window_cfg,
            export_config=export_cfg,
            unit_config=unit_cfg or None,
            mapping_path=options.get("mapping_path") or None,
            progress_callback=self._progress_callback(job_id),
            **adapter_kwargs,
        )
        return {
            "input_path": str(input_path),
            "output_dir": str(out_dir),
            "adapter": result.get("adapter"),
            "canonical_paths": result.get("canonical_paths", {}),
            "qc_paths": result.get("qc_paths", {}),
            "window_paths": result.get("window_paths", {}),
        }

    def _run_convert_job(self, job_id: str, input_paths: list[str], options: dict[str, Any]) -> None:
        out_root = Path(self.jobs[job_id].output_dir or self.workspace / "outputs" / job_id)
        out_root.mkdir(parents=True, exist_ok=True)
        pipeline = NeuroImportPipeline()
        results = []
        try:
            self.emit(job_id, "state", {"status": "running", "step": "Starting conversion", "percent": 1, "message": "Starting conversion."})
            for idx, raw_path in enumerate(input_paths):
                path = Path(raw_path)
                if path.is_dir():
                    files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES]
                else:
                    files = [path]
                for file_index, file_path in enumerate(files):
                    safe_name = file_path.stem.replace(" ", "_")[:80]
                    out_dir = out_root / f"{idx+1:03d}_{file_index+1:03d}_{safe_name}"
                    results.append(self._convert_one(pipeline, job_id, file_path, out_dir, options))
            manifest_path = out_root / "webapp_conversion_manifest.json"
            manifest_path.write_text(json.dumps({"results": results}, indent=2))
            self.emit(job_id, "state", {
                "status": "complete",
                "step": "Conversion complete",
                "percent": 100,
                "message": "Conversion complete.",
                "output_dir": str(out_root),
                "result": {"output_dir": str(out_root), "manifest": str(manifest_path), "converted": results},
            })
        except Exception as exc:
            self.emit(job_id, "state", {"status": "failed", "step": "Conversion failed", "percent": 100, "message": "Conversion failed.", "error": repr(exc), "output_dir": str(out_root)})

    def start_compare_job(
        self,
        group_a_dirs: list[str | Path],
        group_b_dirs: list[str | Path],
        *,
        output_dir: str | Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> JobRecord:
        record = self.create_job("compare", output_dir=output_dir)
        thread = threading.Thread(
            target=self._run_compare_job,
            args=(record.job_id, [str(Path(p)) for p in group_a_dirs], [str(Path(p)) for p in group_b_dirs], options or {}),
            daemon=True,
        )
        thread.start()
        return record

    def _run_compare_job(self, job_id: str, group_a_dirs: list[str], group_b_dirs: list[str], options: dict[str, Any]) -> None:
        out_root = Path(self.jobs[job_id].output_dir or self.workspace / "outputs" / job_id)
        out_root.mkdir(parents=True, exist_ok=True)
        try:
            self.emit(job_id, "state", {"status": "running", "step": "Extracting features", "percent": 10, "message": "Extracting feature summaries from Group A and Group B."})
            result = run_comparative_analysis(group_a_dirs, group_b_dirs, output_dir=out_root, comparison_name=options.get("comparison_name", "comparison"), sampling_rate=options.get("sampling_rate"))
            self.emit(job_id, "state", {"status": "complete", "step": "Comparative analysis complete", "percent": 100, "message": "Comparative analysis complete.", "output_dir": str(out_root), "result": result})
        except Exception as exc:
            self.emit(job_id, "state", {"status": "failed", "step": "Comparative analysis failed", "percent": 100, "message": "Comparative analysis failed.", "error": repr(exc), "output_dir": str(out_root)})

    def start_live_job(
        self,
        source: str | Path,
        *,
        channels_csv: str | Path | None = None,
        metadata_json: str | Path | None = None,
        fs: float | None = None,
        channel_profile: str = "auto",
        output_dir: str | Path | None = None,
    ) -> JobRecord:
        record = self.create_job("live", output_dir=output_dir)
        thread = threading.Thread(
            target=self._run_live_job,
            args=(record.job_id, str(Path(source)), str(channels_csv) if channels_csv else None, str(metadata_json) if metadata_json else None, fs, channel_profile),
            daemon=True,
        )
        thread.start()
        return record

    def _run_live_job(self, job_id: str, source: str, channels_csv: str | None, metadata_json: str | None, fs: float | None, channel_profile: str) -> None:
        try:
            self.emit(job_id, "state", {"status": "running", "step": "Starting live replay backend", "percent": 10, "message": "Launching variable-electrode live replay backend."})
            cmd = [sys.executable, "-m", "neuro_importer_live.run_variable_backend", "--source", source, "--channel-profile", channel_profile]
            if channels_csv:
                cmd += ["--channels-csv", channels_csv]
            if metadata_json:
                cmd += ["--metadata-json", metadata_json]
            if fs:
                cmd += ["--fs", str(fs)]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            with self.lock:
                self.live_processes[job_id] = [proc]
            self.emit(job_id, "state", {"status": "running", "step": "Live replay running", "percent": 100, "message": "Live replay backend is running. Open the raw and spectral browser views.", "result": {"raw_url": "http://127.0.0.1:8765", "spectral_url": "http://127.0.0.1:8766"}})
        except Exception as exc:
            self.emit(job_id, "state", {"status": "failed", "step": "Live replay failed", "percent": 100, "message": "Live replay failed.", "error": repr(exc)})


    def start_speedmouse_analyze_job(
        self,
        input_paths: list[str | Path],
        *,
        output_dir: str | Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> JobRecord:
        record = self.create_job("speedmouse_analyze", output_dir=output_dir)
        thread = threading.Thread(
            target=self._run_speedmouse_analyze_job,
            args=(record.job_id, [str(Path(p)) for p in input_paths], options or {}),
            daemon=True,
        )
        thread.start()
        return record

    def _run_speedmouse_analyze_job(self, job_id: str, input_paths: list[str], options: dict[str, Any]) -> None:
        out_root = Path(self.jobs[job_id].output_dir or self.workspace / "outputs" / job_id)
        converted_root = out_root / "converted"
        speedmouse_root = out_root / "speedmouse"
        converted_root.mkdir(parents=True, exist_ok=True)
        speedmouse_root.mkdir(parents=True, exist_ok=True)
        pipeline = NeuroImportPipeline()
        converted: list[dict[str, Any]] = []
        speedmouse_datasets: list[dict[str, Any]] = []
        try:
            self.emit(job_id, "state", {"status": "running", "step": "Analyze for SpeedMouse", "percent": 1, "message": "Starting SpeedMouse analysis pipeline."})
            files: list[Path] = []
            for raw_path in input_paths:
                path = Path(raw_path)
                if path.is_dir():
                    files.extend([p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES])
                else:
                    files.append(path)
            if not files:
                raise ValueError("No supported input files found for SpeedMouse analysis.")
            total = len(files)
            for i, file_path in enumerate(files):
                base_percent = int((i / total) * 70)
                safe_name = file_path.stem.replace(" ", "_")[:80]
                out_dir = converted_root / f"{i+1:03d}_{safe_name}"
                self.emit(job_id, "state", {"status": "running", "step": f"Convert {file_path.name}", "percent": max(3, base_percent), "message": f"Converting {file_path.name}."})
                conv = self._convert_one(pipeline, job_id, file_path, out_dir, options)
                converted.append(conv)
                sm_dir = speedmouse_root / f"{i+1:03d}_{safe_name}"
                self.emit(job_id, "state", {"status": "running", "step": f"Build SpeedMouse dataset {file_path.name}", "percent": min(95, base_percent + 70), "message": "Computing Welch/centroid/geometry arrays for SpeedMouse."})
                sm_paths = write_speedmouse_dataset(
                    out_dir,
                    sm_dir,
                    dataset_id=safe_name,
                    sampling_rate=options.get("sampling_rate") if options.get("sampling_rate") not in (None, "") else None,
                    max_analysis_samples=int(options.get("speedmouse_max_analysis_samples") or 240000),
                    max_windows=int(options.get("speedmouse_max_windows") or 600),
                )
                speedmouse_url = f"/speedmouse/?dataset=/api/jobs/{job_id}/speedmouse/data.json&backend_job={job_id}&backend=1&t={int(time.time())}"
                speedmouse_datasets.append({"input_path": str(file_path), "converted_dir": str(out_dir), **sm_paths, "speedmouse_url": speedmouse_url})
            manifest_path = out_root / "speedmouse_job_manifest.json"
            result = {
                "output_dir": str(out_root),
                "converted": converted,
                "speedmouse_datasets": speedmouse_datasets,
                "primary_speedmouse_url": speedmouse_datasets[0]["speedmouse_url"] if speedmouse_datasets else None,
            }
            manifest_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["manifest"] = str(manifest_path)
            self.emit(job_id, "state", {"status": "complete", "step": "SpeedMouse analysis complete", "percent": 100, "message": "SpeedMouse analysis complete.", "output_dir": str(out_root), "result": result})
        except Exception as exc:
            self.emit(job_id, "state", {"status": "failed", "step": "SpeedMouse analysis failed", "percent": 100, "message": "SpeedMouse analysis failed.", "error": repr(exc), "output_dir": str(out_root)})

    def start_speedmouse_from_converted_job(
        self,
        recording_dirs: list[str | Path],
        *,
        output_dir: str | Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> JobRecord:
        record = self.create_job("speedmouse_from_converted", output_dir=output_dir)
        thread = threading.Thread(
            target=self._run_speedmouse_from_converted_job,
            args=(record.job_id, [str(Path(p)) for p in recording_dirs], options or {}),
            daemon=True,
        )
        thread.start()
        return record

    def _run_speedmouse_from_converted_job(self, job_id: str, recording_dirs: list[str], options: dict[str, Any]) -> None:
        out_root = Path(self.jobs[job_id].output_dir or self.workspace / "outputs" / job_id)
        speedmouse_root = out_root / "speedmouse"
        speedmouse_root.mkdir(parents=True, exist_ok=True)
        datasets = []
        try:
            for i, rec in enumerate(recording_dirs):
                p = Path(rec)
                safe_name = p.name.replace(" ", "_")[:80]
                self.emit(job_id, "state", {"status": "running", "step": f"Build SpeedMouse dataset {safe_name}", "percent": int((i / max(1, len(recording_dirs))) * 90), "message": f"Building SpeedMouse data.json for {safe_name}."})
                sm_dir = speedmouse_root / f"{i+1:03d}_{safe_name}"
                paths = write_speedmouse_dataset(
                    p,
                    sm_dir,
                    dataset_id=safe_name,
                    sampling_rate=options.get("sampling_rate") if options.get("sampling_rate") not in (None, "") else None,
                    max_analysis_samples=int(options.get("speedmouse_max_analysis_samples") or 240000),
                    max_windows=int(options.get("speedmouse_max_windows") or 600),
                )
                datasets.append({"recording_dir": str(p), **paths, "speedmouse_url": f"/speedmouse/?dataset=/api/jobs/{job_id}/speedmouse/data.json&backend_job={job_id}&backend=1&t={int(time.time())}"})
            result = {"output_dir": str(out_root), "speedmouse_datasets": datasets, "primary_speedmouse_url": datasets[0]["speedmouse_url"] if datasets else None}
            (out_root / "speedmouse_from_converted_manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            self.emit(job_id, "state", {"status": "complete", "step": "SpeedMouse dataset complete", "percent": 100, "message": "SpeedMouse dataset complete.", "output_dir": str(out_root), "result": result})
        except Exception as exc:
            self.emit(job_id, "state", {"status": "failed", "step": "SpeedMouse dataset failed", "percent": 100, "message": "SpeedMouse dataset failed.", "error": repr(exc), "output_dir": str(out_root)})

    def start_speedmouse_compare_job(
        self,
        group_a_dirs: list[str | Path],
        group_b_dirs: list[str | Path],
        *,
        output_dir: str | Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> JobRecord:
        record = self.create_job("speedmouse_compare", output_dir=output_dir)
        thread = threading.Thread(
            target=self._run_speedmouse_compare_job,
            args=(record.job_id, [str(Path(p)) for p in group_a_dirs], [str(Path(p)) for p in group_b_dirs], options or {}),
            daemon=True,
        )
        thread.start()
        return record

    def _run_speedmouse_compare_job(self, job_id: str, group_a_dirs: list[str], group_b_dirs: list[str], options: dict[str, Any]) -> None:
        out_root = Path(self.jobs[job_id].output_dir or self.workspace / "outputs" / job_id)
        out_root.mkdir(parents=True, exist_ok=True)
        try:
            self.emit(job_id, "state", {"status": "running", "step": "Build SpeedMouse comparison pack", "percent": 5, "message": "Building SpeedMouse data.json files and comparison manifest."})
            result = build_speedmouse_comparison_pack(
                group_a_dirs,
                group_b_dirs,
                output_dir=out_root,
                comparison_name=options.get("comparison_name", "speedmouse_comparison"),
                sampling_rate=options.get("sampling_rate") if options.get("sampling_rate") not in (None, "") else None,
            )
            result["speedmouse_comparison_url"] = f"/speedmouse/?comparison={quote('/api/file?path=' + result['comparison_manifest'], safe='/?:=&%')}"
            self.emit(job_id, "state", {"status": "complete", "step": "SpeedMouse comparison complete", "percent": 100, "message": "SpeedMouse comparison complete.", "output_dir": str(out_root), "result": result})
        except Exception as exc:
            self.emit(job_id, "state", {"status": "failed", "step": "SpeedMouse comparison failed", "percent": 100, "message": "SpeedMouse comparison failed.", "error": repr(exc), "output_dir": str(out_root)})

    def stop_live_job(self, job_id: str) -> None:
        with self.lock:
            procs = self.live_processes.pop(job_id, [])
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        self.emit(job_id, "state", {"status": "stopped", "step": "Live replay stopped", "percent": 100, "message": "Live replay stopped."})
