from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .job_manager import JobManager, SUPPORTED_UPLOAD_SUFFIXES

PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / "static"
SPEEDMOUSE_DIR = PACKAGE_DIR / "speedmouse"
DEFAULT_WORKSPACE = Path(os.environ.get("NEURO_SIGNAL_APP_WORKSPACE", str(Path.home() / "neuro_signal_app_workspace")))
manager = JobManager(DEFAULT_WORKSPACE)

app = FastAPI(title="Neuro Signal App", version="0.9.1")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/speedmouse", StaticFiles(directory=SPEEDMOUSE_DIR, html=True), name="speedmouse")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text())




@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": "0.9.1", "workspace": str(manager.workspace), "speedmouse": "/speedmouse/"}


def _parse_json_form(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _save_uploads(job_id: str, files: list[UploadFile]) -> list[str]:
    upload_dir = manager.workspace / "uploads" / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for up in files:
        filename = Path(up.filename or "uploaded_file").name
        target = upload_dir / filename
        with target.open("wb") as f:
            shutil.copyfileobj(up.file, f)
        saved.append(str(target))
    return saved


@app.post("/api/jobs/convert-upload")
def convert_upload(
    files: list[UploadFile] = File(...),
    options_json: str = Form("{}"),
    output_dir: str | None = Form(None),
) -> dict[str, Any]:
    # Make job first so uploaded files are placed under the job id.
    record = manager.create_job("upload_prepare", output_dir=output_dir or None)
    saved = _save_uploads(record.job_id, files)
    # Reuse the job id by replacing the temporary record with a real conversion state.
    with manager.lock:
        manager.jobs.pop(record.job_id, None)
        manager.event_queues.pop(record.job_id, None)
    job = manager.start_convert_job(saved, output_dir=output_dir or None, options=_parse_json_form(options_json))
    # Keep uploaded files copied into new job folder too for traceability.
    return {"job_id": job.job_id, "status": job.status, "saved_files": saved, "output_dir": job.output_dir}


@app.post("/api/jobs/convert-paths")
def convert_paths(payload: dict[str, Any]) -> dict[str, Any]:
    paths = payload.get("paths") or []
    if not paths:
        return JSONResponse({"error": "No paths provided."}, status_code=400)  # type: ignore[return-value]
    job = manager.start_convert_job(paths, output_dir=payload.get("output_dir") or None, options=payload.get("options") or {})
    return {"job_id": job.job_id, "status": job.status, "output_dir": job.output_dir}


@app.post("/api/jobs/inspect-upload")
def inspect_upload(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    from neuro_importer.pipeline import NeuroImportPipeline
    temp_id = "inspect_" + os.urandom(4).hex()
    saved = _save_uploads(temp_id, files)
    pipe = NeuroImportPipeline()
    results = []
    for path in saved:
        try:
            results.append(pipe.inspect(path))
        except Exception as exc:
            results.append({"path": path, "error": repr(exc)})
    return {"results": results}


@app.post("/api/jobs/compare")
def compare(payload: dict[str, Any]) -> dict[str, Any]:
    group_a = payload.get("group_a") or []
    group_b = payload.get("group_b") or []
    if not group_a or not group_b:
        return JSONResponse({"error": "Provide group_a and group_b converted recording directories."}, status_code=400)  # type: ignore[return-value]
    job = manager.start_compare_job(group_a, group_b, output_dir=payload.get("output_dir") or None, options=payload.get("options") or {})
    return {"job_id": job.job_id, "status": job.status, "output_dir": job.output_dir}


@app.post("/api/jobs/live")
def live(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source")
    if not source:
        return JSONResponse({"error": "Provide source signal.npy path."}, status_code=400)  # type: ignore[return-value]
    job = manager.start_live_job(
        source,
        channels_csv=payload.get("channels_csv") or None,
        metadata_json=payload.get("metadata_json") or None,
        fs=float(payload["fs"]) if payload.get("fs") else None,
        channel_profile=payload.get("channel_profile") or "auto",
        output_dir=payload.get("output_dir") or None,
    )
    return {"job_id": job.job_id, "status": job.status, "output_dir": job.output_dir}


@app.post("/api/jobs/{job_id}/stop-live")
def stop_live(job_id: str) -> dict[str, Any]:
    manager.stop_live_job(job_id)
    return {"ok": True, "job_id": job_id}


@app.post("/api/jobs/analyze-speedmouse-upload")
def analyze_speedmouse_upload(
    files: list[UploadFile] = File(...),
    options_json: str = Form("{}"),
    output_dir: str | None = Form(None),
) -> dict[str, Any]:
    record = manager.create_job("speedmouse_upload_prepare", output_dir=output_dir or None)
    saved = _save_uploads(record.job_id, files)
    with manager.lock:
        manager.jobs.pop(record.job_id, None)
        manager.event_queues.pop(record.job_id, None)
    job = manager.start_speedmouse_analyze_job(saved, output_dir=output_dir or None, options=_parse_json_form(options_json))
    return {"job_id": job.job_id, "status": job.status, "saved_files": saved, "output_dir": job.output_dir}


@app.post("/api/jobs/analyze-speedmouse-paths")
def analyze_speedmouse_paths(payload: dict[str, Any]) -> dict[str, Any]:
    paths = payload.get("paths") or []
    if not paths:
        return JSONResponse({"error": "No paths provided."}, status_code=400)  # type: ignore[return-value]
    job = manager.start_speedmouse_analyze_job(paths, output_dir=payload.get("output_dir") or None, options=payload.get("options") or {})
    return {"job_id": job.job_id, "status": job.status, "output_dir": job.output_dir}


@app.post("/api/jobs/speedmouse-from-converted")
def speedmouse_from_converted(payload: dict[str, Any]) -> dict[str, Any]:
    recording_dirs = payload.get("recording_dirs") or []
    if not recording_dirs:
        return JSONResponse({"error": "Provide recording_dirs converted recording folders."}, status_code=400)  # type: ignore[return-value]
    job = manager.start_speedmouse_from_converted_job(recording_dirs, output_dir=payload.get("output_dir") or None, options=payload.get("options") or {})
    return {"job_id": job.job_id, "status": job.status, "output_dir": job.output_dir}


@app.post("/api/jobs/compare-speedmouse")
def compare_speedmouse(payload: dict[str, Any]) -> dict[str, Any]:
    group_a = payload.get("group_a") or []
    group_b = payload.get("group_b") or []
    if not group_a or not group_b:
        return JSONResponse({"error": "Provide group_a and group_b converted recording directories."}, status_code=400)  # type: ignore[return-value]
    job = manager.start_speedmouse_compare_job(group_a, group_b, output_dir=payload.get("output_dir") or None, options=payload.get("options") or {})
    return {"job_id": job.job_id, "status": job.status, "output_dir": job.output_dir}


@app.websocket("/ws/speedmouse/live")
async def speedmouse_live_ws(websocket: WebSocket) -> None:
    from neuro_importer_speedmouse.live_bridge import stream_speedmouse_samples
    await websocket.accept()
    qp = websocket.query_params
    source = qp.get("source")
    if not source:
        await websocket.send_json({"type": "error", "error": "Missing source query parameter."})
        await websocket.close()
        return
    try:
        await stream_speedmouse_samples(
            websocket,
            source=source,
            channels_csv=qp.get("channels_csv"),
            metadata_json=qp.get("metadata_json"),
            fs=float(qp["fs"]) if qp.get("fs") else None,
            chunk_samples=int(qp.get("chunk_samples") or 256),
            speed=float(qp.get("speed") or 1.0),
            loop=(qp.get("loop") or "false").lower() in {"1", "true", "yes"},
        )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "error": repr(exc)})
        finally:
            await websocket.close()




@app.get("/api/jobs/{job_id}/speedmouse/data.json")
def job_speedmouse_data_json(job_id: str) -> FileResponse:
    """Serve the primary SpeedMouse data.json for a completed job.

    This avoids nested /api/file?path=... query strings in SpeedMouse URLs and
    makes it unambiguous that SpeedMouse is loading the backend-generated
    dataset for this specific job, not its bundled demo data.
    """
    record = manager.get(job_id)
    if not record:
        return JSONResponse({"error": "Job not found."}, status_code=404)  # type: ignore[return-value]

    data_json: str | None = None
    result = record.result or {}
    datasets = result.get("speedmouse_datasets") or []
    if datasets and isinstance(datasets, list):
        first = datasets[0] or {}
        data_json = first.get("data_json")
    if not data_json and result.get("data_json"):
        data_json = result.get("data_json")
    if not data_json and record.output_dir:
        candidates = sorted(Path(record.output_dir).rglob("speedmouse/**/data.json"))
        if not candidates:
            candidates = sorted(Path(record.output_dir).rglob("data.json"))
        if candidates:
            data_json = str(candidates[0])

    if not data_json:
        return JSONResponse({"error": "No SpeedMouse data.json found for this job."}, status_code=404)  # type: ignore[return-value]
    p = Path(data_json).expanduser().resolve()
    if not p.exists():
        return JSONResponse({"error": f"SpeedMouse data.json path does not exist: {p}"}, status_code=404)  # type: ignore[return-value]
    return FileResponse(p, media_type="application/json")


@app.get("/api/jobs/{job_id}/speedmouse/manifest.json")
def job_speedmouse_manifest_json(job_id: str) -> JSONResponse:
    """Return a small manifest used by SpeedMouse to show provenance."""
    record = manager.get(job_id)
    if not record:
        return JSONResponse({"error": "Job not found."}, status_code=404)
    result = record.result or {}
    return JSONResponse({
        "job_id": job_id,
        "status": record.status,
        "output_dir": record.output_dir,
        "result": result,
    })

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    record = manager.get(job_id)
    if not record:
        return JSONResponse({"error": "Job not found."}, status_code=404)  # type: ignore[return-value]
    return record.__dict__


@app.get("/api/open-output")
def open_output(path: str) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return JSONResponse({"error": f"Path does not exist: {p}"}, status_code=404)  # type: ignore[return-value]
    if os.name == "posix":
        os.system(f'xdg-open "{p}" >/dev/null 2>&1 &')
    else:
        webbrowser.open(p.as_uri() if p.is_file() else str(p))
    return {"ok": True, "path": str(p)}


@app.get("/api/file")
def get_file(path: str) -> FileResponse:
    p = Path(path).expanduser().resolve()
    return FileResponse(p)


@app.websocket("/api/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    q = manager.subscribe(job_id)
    try:
        while True:
            try:
                event = await asyncio.to_thread(q.get, True, 0.5)
                await websocket.send_json(event)
            except queue.Empty:
                record = manager.get(job_id)
                if record and record.status in {"complete", "failed", "stopped"}:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "job_id": job_id,
                        "status": record.status,
                        "percent": record.progress_percent,
                        "step": record.current_step,
                        "output_dir": record.output_dir,
                        "result": record.result,
                    })
                    return
                await websocket.send_json({"type": "heartbeat", "job_id": job_id})
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(job_id, q)



@app.get("/live/raw")
def live_raw_view() -> FileResponse:
    from neuro_importer_live import __file__ as live_init
    return FileResponse(Path(live_init).parent / "raw_visualizer_variable.html")


@app.get("/live/spectral")
def live_spectral_view() -> FileResponse:
    from neuro_importer_live import __file__ as live_init
    return FileResponse(Path(live_init).parent / "spectral_visualizer_variable.html")


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Launch the local HTML Neuro Signal App.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args()

    global manager
    if args.workspace:
        manager = JobManager(args.workspace)
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    print(f"Neuro Signal App v0.8 running at {url}")
    print(f"Workspace: {manager.workspace}")
    uvicorn.run("neuro_signal_webapp.app_server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
