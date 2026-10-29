"""Lazy, local Cellpose inference shared by all analysis sessions."""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
from pathlib import Path
import threading
from typing import Callable

import numpy as np

MODEL_NAME = "cpsam_v2"
_lock = threading.Lock()
_loaded = None


def cell_probability_threshold(sensitivity: float) -> float:
    if not np.isfinite(sensitivity) or not 0 <= sensitivity <= 1:
        raise ValueError("Detection sensitivity must be between 0 and 1.")
    # The midpoint reproduces the Cellpose defaults used in the dataset trial.
    # Higher sensitivity lowers the cell/background cutoff.
    return round(3 - 6 * sensitivity, 6)


def _load_model(progress: Callable[[str], None]):
    global _loaded
    if _loaded is not None:
        return _loaded
    cache = Path(__file__).resolve().parent.parent / ".cache"
    os.environ.setdefault("CELLPOSE_LOCAL_MODELS_PATH", str(cache / "cellpose-models"))
    os.environ.setdefault("TORCH_HOME", str(cache / "torch"))
    progress("Loading Cellpose")
    try:
        import torch
        from cellpose import models
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "Cellpose could not load. Run 'uv sync --locked' in the project folder, "
            "then restart the app."
        ) from exc
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    gpu = torch.cuda.is_available()
    model_path = Path(os.environ["CELLPOSE_LOCAL_MODELS_PATH"]) / MODEL_NAME
    cached = model_path.is_file()
    progress("Loading Cellpose on the GPU" if cached and gpu else
             "Loading Cellpose on the CPU (slower)" if cached else
             "Downloading Cellpose for first use (about 1.2 GB)")
    try:
        model = models.CellposeModel(gpu=gpu, pretrained_model=MODEL_NAME, use_bfloat16=gpu)
    except Exception as exc:
        if not cached:
            raise RuntimeError(
                "Cellpose's model could not be downloaded or loaded. Check the internet connection "
                "and free disk space, then analyze again. Image data stays on this computer."
            ) from exc
        raise RuntimeError(f"Cellpose's cached model could not load: {exc}") from exc
    weights = Path(model.pretrained_model)
    if weights.name != MODEL_NAME:
        raise RuntimeError("Cellpose loaded an unexpected model. Check the model installation.")
    with weights.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    metadata = dict(engine="cellpose", model=MODEL_NAME,
                    cellpose_version=importlib.metadata.version("cellpose"),
                    torch_version=importlib.metadata.version("torch"),
                    device=str(model.device),
                    device_name=torch.cuda.get_device_name(0) if gpu else "CPU",
                    weights_sha256=digest, precision="bfloat16" if gpu else "float32")
    _loaded = model, metadata
    return _loaded


def segment_cells(reference: np.ndarray, sensitivity: float = .5,
                  progress: Callable[[str], None] = lambda _: None) -> tuple[np.ndarray, dict]:
    """Segment a copy: Cellpose may normalize float32 arrays in place."""
    image = np.asarray(reference, dtype=np.float32)
    if image.ndim != 2 or not image.size or not np.isfinite(image).all():
        raise ValueError("Cellpose needs a finite, nonempty 2D grayscale reference.")
    threshold = cell_probability_threshold(sensitivity)
    settings = dict(engine="cellpose", model=MODEL_NAME, cellprob_threshold=threshold,
                    flow_threshold=.4, diameter=None, normalization_percentiles=[1, 99],
                    min_size=15, batch_size=4)
    if image.max() == image.min():
        return np.zeros(image.shape, np.int32), dict(settings, skipped="constant_reference")
    with _lock:
        model, metadata = _load_model(progress)
        progress("Identifying cells with Cellpose" if metadata["device"] != "cpu" else
                 "Identifying cells with Cellpose on the CPU (this may take longer)")
        masks, _, _ = model.eval(image.copy(), batch_size=4, diameter=None, normalize=True,
                                 flow_threshold=.4, cellprob_threshold=threshold,
                                 min_size=15, do_3D=False)
    masks = np.asarray(masks)
    if masks.shape != image.shape or masks.dtype.kind not in "iu" or np.any(masks < 0):
        raise RuntimeError("Cellpose returned invalid masks. Try analyzing the recording again.")
    return masks.astype(np.int32), dict(metadata, **settings)
