"""Detection and measurements. Display adjustments never enter this pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy import ndimage as ndi
from scipy.signal import find_peaks
from skimage import feature, measure, morphology, segmentation

from .recordings import Recording
from .cellpose_detector import segment_cells
from .metrics import summarize_checked


@dataclass
class Detection:
    labels: np.ndarray
    distance: np.ndarray
    rois: list[dict]
    outlines: list[list[list[float]]]
    metadata: dict = field(default_factory=dict)


def detect_regions(reference: np.ndarray, radius: int = 12, sensitivity: float = 0.5, margin: int = 2,
                   progress: Callable[[str], None] = lambda _: None) -> Detection:
    """Identify cells with Cellpose, then fit a fixed disk inside each mask."""
    labels, metadata = segment_cells(reference, sensitivity, progress)
    progress("Placing circles inside the Cellpose boundaries")
    return regions_from_labels(labels, radius, margin, metadata)


def detect_regions_classical(reference: np.ndarray, radius: int = 12, sensitivity: float = 0.5, margin: int = 2) -> Detection:
    """Previous threshold/watershed detector, retained only for comparisons."""
    image = np.asarray(reference, dtype=np.float32)
    if not np.isfinite(image).all():
        raise ValueError("The image contains non-finite pixel values.")
    if image.max() == image.min():
        return Detection(np.zeros(image.shape, np.int32), np.zeros(image.shape), [], [])
    low, high = np.percentile(image, [2, 99])
    normalized = np.clip((image - low) / max(high - low, 1e-6), 0, 1)
    smooth = ndi.gaussian_filter(normalized, 1.4)
    background = ndi.gaussian_filter(smooth, max(20, radius * 3))
    # A lower local cutoff includes dimmer cell interiors. The absolute floor
    # prevents admitting empty background when a field is sparse.
    cutoff = background * (1.10 - .38 * sensitivity)
    mask = (smooth > cutoff) & (smooth > (.055 - .035 * sensitivity))
    mask = morphology.closing(mask, morphology.disk(2))
    mask = ndi.binary_fill_holes(mask)
    mask = morphology.remove_small_objects(mask, max_size=int(np.pi * (radius + margin) ** 2))
    mask[:margin + 1] = False
    mask[-margin - 1:] = False
    mask[:, :margin + 1] = False
    mask[:, -margin - 1:] = False
    distance = ndi.distance_transform_edt(mask)
    seeds = feature.peak_local_max(ndi.gaussian_filter(distance, 1), min_distance=2 * radius + margin,
                                   threshold_abs=radius + margin, labels=mask, exclude_border=False)
    markers = np.zeros(mask.shape, np.int32)
    if len(seeds):
        markers[tuple(seeds.T)] = np.arange(1, len(seeds) + 1)
    labels = segmentation.watershed(-distance, markers, mask=mask).astype(np.int32)
    return regions_from_labels(labels, radius, margin, {"engine": "threshold_watershed"})


def regions_from_labels(labels: np.ndarray, radius: int = 12, margin: int = 2,
                        metadata: dict | None = None) -> Detection:
    """Find a centroid-nearest disk that fits within one instance boundary."""
    labels = np.asarray(labels)
    if labels.ndim != 2 or labels.dtype.kind not in "iu" or np.any(labels < 0):
        raise ValueError("Expected a 2D array of nonnegative integer region labels.")
    if radius < 1 or margin < 0:
        raise ValueError("Circle radius must be positive and margin nonnegative.")
    labels = labels.astype(np.int32)
    safe_distance = np.zeros(labels.shape, np.float32)
    rois, outlines = [], []
    regions = measure.regionprops(labels)
    for region in regions:
        miny, minx, maxy, maxx = region.bbox
        local = labels[miny:maxy, minx:maxx] == region.label
        local_dist = ndi.distance_transform_edt(np.pad(local, 1))[1:-1, 1:-1]
        safe_distance[miny:maxy, minx:maxx] = np.maximum(safe_distance[miny:maxy, minx:maxx], local_dist)
        valid = np.argwhere(local_dist > radius + margin)
        if not len(valid):
            continue
        centroid = np.array(region.centroid) - [miny, minx]
        cy, cx = valid[np.argmin(np.sum((valid - centroid) ** 2, axis=1))] + [miny, minx]
        rois.append(dict(id=0, x=int(cx), y=int(cy), radius=radius, included=True,
                         area=int(region.area), label=int(region.label), manual=False))
        contours = measure.find_contours(np.pad(local, 1), .5)
        if contours:
            contour = max(contours, key=len)
            contour = measure.approximate_polygon(contour, tolerance=1.5)
            outlines.append([[round(float(x + minx - 1), 1), round(float(y + miny - 1), 1)] for y, x in contour])
    rois.sort(key=lambda r: (r["y"] // max(radius * 4, 1), r["x"]))
    for number, roi in enumerate(rois, 1):
        roi["id"] = number
    details = dict(metadata or {}, candidate_regions=len(regions), circles_placed=len(rois),
                   regions_too_small=len(regions)-len(rois), radius_pixels=radius, margin_pixels=margin)
    return Detection(labels, safe_distance, rois, outlines, details)


def circle_pixels(roi: dict, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    x, y, radius = int(roi["x"]), int(roi["y"]), int(roi["radius"])
    yy, xx = np.mgrid[y-radius:y+radius+1, x-radius:x+radius+1]
    inside = (xx - x) ** 2 + (yy - y) ** 2 <= radius ** 2
    rows, cols = yy[inside], xx[inside]
    if rows.min() < 0 or cols.min() < 0 or rows.max() >= shape[0] or cols.max() >= shape[1]:
        raise ValueError("The whole circle must remain inside the image.")
    return rows, cols


def validate_roi(roi: dict, detection: Detection, others: list[dict], margin: int = 2) -> None:
    circle_pixels(roi, detection.labels.shape)
    x, y = int(roi["x"]), int(roi["y"])
    if detection.distance[y, x] <= roi["radius"] + margin:
        raise ValueError("The circle must fit inside a detected region with a 2-pixel margin. Try a nearby interior point or adjust detection.")
    for other in others:
        if other["id"] != roi["id"] and np.hypot(x-other["x"], y-other["y"]) <= roi["radius"]+other["radius"]:
            raise ValueError("Measurement circles cannot overlap. Move or remove the existing circle first.")


def extract_traces(recording: Recording, rois: list[dict], progress: Callable[[float, str], None] = lambda *_: None) -> np.ndarray:
    if not rois:
        return np.empty((0, recording.frames), dtype=np.float64)
    indices, owners = [], []
    for i, roi in enumerate(rois):
        rows, cols = circle_pixels(roi, (recording.height, recording.width))
        indices.append(rows * recording.width + cols)
        owners.append(np.full(len(rows), i, dtype=np.int32))
    counts = np.array([len(i) for i in indices])
    indices, owners = np.concatenate(indices), np.concatenate(owners)
    traces = np.empty((len(rois), recording.frames), dtype=np.float64)
    for frame, pixels in enumerate(recording.iter_frames()):
        values = pixels.ravel()[indices]
        if not np.isfinite(values).all():
            raise ValueError(f"Frame {frame + 1} contains non-finite measurements.")
        traces[:, frame] = np.bincount(owners, weights=values, minlength=len(rois)) / counts
        if frame % 10 == 0 or frame == recording.frames - 1:
            progress((frame + 1) / recording.frames, f"Measuring frame {frame + 1} of {recording.frames}")
    return traces


def summarize(rois: list[dict], traces: np.ndarray, interval: float | None, baseline_frames: int = 10,
              prominence_percent: float = 5.0, separation_frames: int = 5) -> dict:
    n = traces.shape[1]
    baseline_frames = max(1, min(baseline_frames, n))
    duration = (n - 1) * interval if interval is not None else None
    result, normalized = [], []
    for roi, trace in zip(rois, traces):
        baseline = float(np.mean(trace[:baseline_frames]))
        relative = (trace - baseline) / baseline if baseline > 1e-9 else None
        noise = float(np.median(np.abs(np.diff(trace) - np.median(np.diff(trace)))) / .67448975 / np.sqrt(2)) if n > 2 else 0
        prominence = max(abs(baseline) * prominence_percent / 100, noise * 3, 1e-9)
        # Peaks are found on the original trace. No smoothing, detrending,
        # bleaching correction, or per-frame normalization is hidden here.
        peaks, _ = find_peaks(trace, prominence=prominence, distance=separation_frames)
        gaps = np.diff(peaks)
        period = float(np.median(gaps) * interval) if len(peaks) >= 2 and interval is not None else None
        rate = len(peaks) * 60 / duration if duration and duration > 0 else None
        result.append(dict(**roi, raw=np.round(trace, 5).tolist(),
                           relative=np.round(relative, 7).tolist() if relative is not None else None,
                           baseline=baseline, mean=float(np.mean(trace)), peaks=peaks.tolist(),
                           peak_count=len(peaks), peak_rate_per_min=rate, median_interval_seconds=period,
                           prominence_used=prominence, relative_valid=relative is not None))
        normalized.append(relative)
    selected = [i for i, r in enumerate(rois) if r["included"]]
    relative_selected = [i for i in selected if normalized[i] is not None]

    def aggregate(arrays):
        if not len(arrays):
            return {"mean": [], "sd": []}
        values = np.asarray(arrays)
        return {"mean": np.round(values.mean(axis=0), 7).tolist(), "sd": np.round(values.std(axis=0), 7).tolist()}

    return dict(rois=result, metrics=summarize_checked(result), population=dict(raw=aggregate([traces[i] for i in selected]),
                relative=aggregate([normalized[i] for i in relative_selected]), included=len(selected),
                relative_included=len(relative_selected)),
                frames=n, interval=interval, duration=duration, baseline_frames=baseline_frames,
                prominence_percent=prominence_percent, separation_frames=separation_frames)
