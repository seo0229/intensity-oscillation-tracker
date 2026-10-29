from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analysis import Detection, detect_regions, extract_traces, summarize, validate_roi
from .recordings import Recording, open_recording

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Intensity Oscillation Tracker", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
recordings: dict[str, Recording] = {}
sessions: dict[str, "Session"] = {}
catalog_lock = threading.RLock()


class Parameters(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    interval: float | None = Field(default=None, gt=0, le=3600)
    baseline_frames: int = Field(default=10, ge=1, le=1000000)
    prominence_percent: float = Field(default=5, ge=.01, le=1000)
    separation_frames: int = Field(default=5, ge=1, le=1000000)


class AnalyzeRequest(Parameters):
    recording_id: str
    detector: Literal["cellpose"] = "cellpose"
    radius: int = Field(default=12, ge=3, le=100)
    sensitivity: float = Field(default=.5, ge=0, le=1)


class OpenRequest(BaseModel):
    path: str


class EditRequest(BaseModel):
    action: Literal["move", "add", "delete", "include", "include_all"]
    roi_id: int | None = None
    x: int | None = None
    y: int | None = None
    included: bool | None = None


@dataclass
class Session:
    recording_id: str
    settings: AnalyzeRequest
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    progress: float = 0
    message: str = "Waiting for analysis"
    error: str | None = None
    started: float = field(default_factory=time.monotonic)
    elapsed: float = 0
    detection: Detection | None = None
    rois: list[dict] = field(default_factory=list)
    traces: np.ndarray | None = None
    result: dict | None = None
    next_roi_id: int = 1
    lock: threading.RLock = field(default_factory=threading.RLock)

    def refresh(self):
        self.result = summarize(self.rois, self.traces, **self.settings.model_dump(include=set(Parameters.model_fields)))

    def report(self, progress, message):
        with self.lock:
            self.progress, self.message = progress, message

    def snapshot(self):
        with self.lock:
            return dict(id=self.id, status=self.status, progress=self.progress, message=self.message,
                        error=self.error, elapsed=round(self.elapsed if self.status in {"done", "error"} else time.monotonic()-self.started, 1))


@app.middleware("http")
async def same_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Use the app from its local address."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(ValueError)
async def invalid_value(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def get_recording(key) -> Recording:
    if key not in recordings:
        raise HTTPException(404, "Recording is no longer open. Select it again.")
    return recordings[key]


def get_session(key, ready=False) -> Session:
    if key not in sessions:
        raise HTTPException(404, "Analysis is no longer available. Run it again.")
    session = sessions[key]
    if ready and session.status != "done":
        raise HTTPException(409, "Wait for the current analysis to finish.")
    return session


def register(path) -> dict:
    resolved = str(Path(path).expanduser().resolve())
    key = hashlib.sha256(resolved.encode()).hexdigest()[:16]
    with catalog_lock:
        if key not in recordings:
            recordings[key] = open_recording(resolved)
        return dict(id=key, **recordings[key].metadata())


@app.get("/api/recordings")
def list_recordings():
    folder = Path(os.environ.get("INTENSITY_DATASET", ROOT / "dataset"))
    items, errors = [], []
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in {".tif", ".tiff"}) if folder.is_dir() else []
    for path in paths:
        try:
            items.append(register(path))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return {"recordings": items, "errors": errors}


@app.get("/api/health")
def health():
    return {"app": "intensity-tracker", "version": "0.2.0", "detector": "cellpose"}


@app.post("/api/recordings/open")
def open_path(body: OpenRequest):
    return register(body.path)


@app.post("/api/browse")
def browse():
    script = "import tkinter as t; from tkinter import filedialog; import json; r=t.Tk(); r.withdraw(); r.attributes('-topmost',True); p=filedialog.askopenfilename(title='Open a fluorescence recording',filetypes=[('TIFF recordings','*.tif *.tiff *.tid')]); r.destroy(); print(json.dumps(p))"
    try:
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                   timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode:
            raise ValueError("The native file picker is unavailable. Paste the file path below instead.")
        path = json.loads(completed.stdout)
        return {"recording": register(path) if path else None}
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ValueError("File selection timed out. Try again or paste a path.") from exc


@app.get("/api/recordings/{key}/frame")
def frame(key: str, index: int = Query(-1, ge=-1), gain: float = Query(1, ge=.25, le=4), thumbnail: bool = False):
    if not np.isfinite(gain):
        raise ValueError("Display brightness must be finite.")
    return Response(get_recording(key).preview(index, gain, thumbnail), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


def run_analysis(session: Session):
    try:
        session.status = "running"
        recording = get_recording(session.recording_id)
        session.report(.03, "Building a reference from frames across the recording")
        reference = recording.prepare_reference()
        session.report(.14, "Preparing Cellpose detection")
        detection = detect_regions(reference, session.settings.radius, session.settings.sensitivity,
                                   progress=lambda message: session.report(.18, message))
        session.report(.3, f"Measuring {len(detection.rois)} fixed regions")
        traces = extract_traces(recording, detection.rois, lambda p, msg: session.report(.3 + p * .67, msg))
        with session.lock:
            session.detection, session.rois, session.traces = detection, detection.rois, traces
            session.next_roi_id = len(session.rois) + 1
            session.refresh()
            session.status, session.progress = "done", 1
            session.message = f"{len(session.rois)} regions measured"
    except Exception as exc:
        session.status, session.error, session.message = "error", str(exc), "Analysis could not finish"
    finally:
        session.elapsed = time.monotonic() - session.started


@app.post("/api/analyze")
def analyze(body: AnalyzeRequest):
    get_recording(body.recording_id)
    with catalog_lock:
        if sum(s.status in {"queued", "running"} for s in sessions.values()) >= 3:
            raise HTTPException(409, "Three recordings are already queued. Let one finish first.")
        # Keep memory bounded; raw video frames are never retained as a stack.
        completed = [s for s in sessions.values() if s.status in {"done", "error"}]
        for old in sorted(completed, key=lambda s: s.started)[:-5]:
            del sessions[old.id]
        session = Session(body.recording_id, body)
        sessions[session.id] = session
    pool.submit(run_analysis, session)
    return session.snapshot()


@app.get("/api/sessions/{key}")
def status(key: str):
    return get_session(key).snapshot()


@app.get("/api/sessions/{key}/result")
def result(key: str):
    session = get_session(key, ready=True)
    with session.lock:
        return dict(**session.result, recording=get_recording(session.recording_id).metadata(),
                    settings=session.settings.model_dump(), detection=session.detection.metadata,
                    outlines=session.detection.outlines)


@app.post("/api/sessions/{key}/settings")
def settings(key: str, body: Parameters):
    session = get_session(key, ready=True)
    with session.lock:
        session.settings = session.settings.model_copy(update=body.model_dump())
        session.refresh()
    return {"ok": True}


def run_edit(session: Session, rois: list[dict], index: int, candidate: dict):
    try:
        measured = extract_traces(get_recording(session.recording_id), [candidate], session.report)
        with session.lock:
            traces = session.traces.copy()
            if index == len(session.rois):
                traces = np.vstack([traces, measured])
            else:
                traces[index] = measured[0]
            session.rois, session.traces = rois, traces
            session.refresh()
            session.status, session.progress, session.message = "done", 1, "Region updated"
    except Exception as exc:
        with session.lock:
            # The previous successful measurements remain intact after an edit failure.
            session.status, session.error, session.message = "done", str(exc), "Edit failed; previous results retained"
    finally:
        session.elapsed = time.monotonic() - session.started


@app.post("/api/sessions/{key}/edit")
def edit(key: str, body: EditRequest):
    session = get_session(key, ready=True)
    with session.lock:
        rois = [r.copy() for r in session.rois]
        if body.action == "include_all":
            if body.included is None:
                raise ValueError("Choose whether to include all regions.")
            for roi in rois:
                roi["included"] = body.included
            session.rois = rois
            session.refresh()
            return session.snapshot()
        if body.action == "add":
            index = len(rois)
            candidate = dict(id=session.next_roi_id, radius=session.settings.radius, included=True, manual=True, area=0, label=0)
        else:
            index = next((i for i, r in enumerate(rois) if r["id"] == body.roi_id), None)
            if index is None:
                raise HTTPException(404, "Region not found.")
            candidate = rois[index]
        if body.action in {"move", "add"}:
            if body.x is None or body.y is None:
                raise ValueError("Choose a circle center.")
            candidate.update(x=body.x, y=body.y, manual=True)
            validate_roi(candidate, session.detection, rois)
            candidate["label"] = int(session.detection.labels[body.y, body.x])
            candidate["area"] = int(np.count_nonzero(session.detection.labels == candidate["label"]))
            if body.action == "add":
                rois.append(candidate)
                session.next_roi_id += 1
            session.status, session.progress, session.error = "running", 0, None
            session.message, session.started = "Measuring the updated circle", time.monotonic()
            pool.submit(run_edit, session, rois, index, candidate)
        elif body.action == "delete":
            del rois[index]
            session.rois, session.traces = rois, np.delete(session.traces, index, axis=0)
            session.refresh()
        else:
            if body.included is None:
                raise ValueError("Choose whether to include the region.")
            candidate["included"] = body.included
            session.rois = rois
            session.refresh()
    return session.snapshot()


@app.get("/api/sessions/{key}/export")
def export(key: str, kind: Literal["traces", "summary", "metrics", "session"] = "traces", mode: Literal["raw", "relative"] = "raw"):
    session = get_session(key, ready=True)
    with session.lock:
        data = session.result
        if kind == "session":
            payload = dict(version=2, recording=get_recording(session.recording_id).metadata(),
                           settings=session.settings.model_dump(), detection=session.detection.metadata, results=data,
                           notes="Fixed circular ROI mean intensities. Relative values use the first baseline_frames frames. No background or bleaching correction. Rates count detected peaks, not proven periodic oscillations.")
            return Response(json.dumps(payload, indent=2, allow_nan=False), media_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="analysis-session.json"'})
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        rois = data["rois"]
        if kind == "metrics":
            writer.writerow(["metric", "checked_regions"])
            for name in ["frames", "interval", "duration", "baseline_frames", "prominence_percent", "separation_frames"]:
                writer.writerow([name, data[name]])
            writer.writerows(data["metrics"].items())
        elif kind == "summary":
            fields = ["id", "x", "y", "radius", "included", "manual", "area", "baseline", "mean", "peak_count", "peak_rate_per_min", "median_interval_seconds", "prominence_used"]
            writer.writerow(fields)
            writer.writerows([[r.get(k) for k in fields] for r in rois])
        else:
            selected = [r for r in rois if r["included"] and r[mode] is not None]
            writer.writerow(["frame", "time_seconds"] + [f"roi_{r['id']}_{mode}" for r in selected] + [f"population_mean_{mode}"])
            average = data["population"][mode]["mean"]
            for i in range(data["frames"]):
                writer.writerow([i + 1, round(i * data["interval"], 7) if data["interval"] else ""] + [r[mode][i] for r in selected] + [average[i] if average else ""])
        return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}-{mode}.csv"'})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
