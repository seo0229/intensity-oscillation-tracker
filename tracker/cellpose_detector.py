"""Lazy, local Cellpose inference shared by all analysis sessions."""
from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import logging
import os
from pathlib import Path
import threading
from typing import Callable

import numpy as np

from .paths import model_directory, runtime_directory

MODEL_NAME = "cpsam_v2"
_lock = threading.Lock()
_loaded = None
_logger = logging.getLogger(__name__)


def cell_probability_threshold(sensitivity: float) -> float:
    if not np.isfinite(sensitivity) or not 0 <= sensitivity <= 1:
        raise ValueError("Detection sensitivity must be between 0 and 1.")
    # The midpoint reproduces the Cellpose defaults used in the dataset trial.
    # Higher sensitivity lowers the cell/background cutoff.
    return round(3 - 6 * sensitivity, 6)


def _release_gpu_model():
    """Drop failed GPU allocations before loading fresh weights on the CPU."""
    global _loaded
    _loaded = None
    gc.collect()
    import torch
    try:
        torch.cuda.empty_cache()
    except (RuntimeError, OSError):
        # A broken CUDA context must not prevent the CPU retry.
        _logger.debug("Could not clear the CUDA cache", exc_info=True)


def _load_model(progress: Callable[[str], None], *, cpu_only: bool = False,
                fallback_reason: str | None = None):
    global _loaded
    if _loaded is not None:
        if not cpu_only or _loaded[1]["device"] == "cpu":
            return _loaded
        _release_gpu_model()
    cache = runtime_directory()
    os.environ.setdefault("CELLPOSE_LOCAL_MODELS_PATH", str(model_directory()))
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
    gpu = False
    if not cpu_only:
        try:
            gpu = torch.cuda.is_available()
        except (RuntimeError, OSError):
            fallback_reason = "gpu_detection_failed"
            _logger.warning("GPU detection failed; using Cellpose on the CPU", exc_info=True)
    model_path = Path(os.environ["CELLPOSE_LOCAL_MODELS_PATH"]) / MODEL_NAME
    cached = model_path.is_file()
    progress("Loading Cellpose on the GPU" if cached and gpu else
             "Loading Cellpose on the CPU (slower)" if cached else
             "Downloading Cellpose for first use (about 1.2 GB)")
    retry_cpu = False
    try:
        # An explicit device prevents Cellpose silently choosing a different
        # device while our precision and exported metadata still describe GPU use.
        model = models.CellposeModel(gpu=gpu, device=torch.device("cuda:0" if gpu else "cpu"),
                                    pretrained_model=MODEL_NAME, use_bfloat16=gpu)
    except Exception as exc:
        if gpu and isinstance(exc, (RuntimeError, OSError)):
            _logger.warning("Cellpose GPU initialization failed; retrying on the CPU", exc_info=True)
            retry_cpu = True
        elif not cached:
            raise RuntimeError(
                "Cellpose's model could not be downloaded or loaded. Check the internet connection "
                "and free disk space, then analyze again. Image data stays on this computer."
            ) from exc
        else:
            raise RuntimeError(f"Cellpose's cached model could not load: {exc}") from exc
    if retry_cpu:
        # Leave the exception handler first so its traceback releases any
        # partially constructed GPU network before the CPU model is created.
        progress("GPU unavailable; retrying Cellpose on the CPU (slower)")
        _release_gpu_model()
        return _load_model(progress, cpu_only=True, fallback_reason="gpu_initialization_failed")
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
    if fallback_reason:
        metadata["cpu_fallback"] = fallback_reason
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
        arguments = dict(batch_size=4, diameter=None, normalize=True, flow_threshold=.4,
                         cellprob_threshold=threshold, min_size=15, do_3D=False)
        retry_cpu = False
        try:
            masks, _, _ = model.eval(image.copy(), **arguments)
        except (RuntimeError, OSError):
            if not metadata["device"].startswith("cuda"):
                raise
            _logger.warning("Cellpose GPU inference failed; retrying on the CPU", exc_info=True)
            retry_cpu = True
        if retry_cpu:
            model = None
            _release_gpu_model()
            progress("GPU analysis failed; retrying Cellpose on the CPU (this may take longer)")
            model, metadata = _load_model(progress, cpu_only=True, fallback_reason="gpu_inference_failed")
            # The failed attempt may have normalized its input in place. Retry
            # the untouched reference and the same settings exactly once.
            masks, _, _ = model.eval(image.copy(), **arguments)
    masks = np.asarray(masks)
    if masks.shape != image.shape or masks.dtype.kind not in "iu" or np.any(masks < 0):
        raise RuntimeError("Cellpose returned invalid masks. Try analyzing the recording again.")
    return masks.astype(np.int32), dict(metadata, **settings)
