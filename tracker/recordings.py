"""Read ordinary monochrome TIFF stacks and the dataset's TIFF/TID pairs."""
from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image


@dataclass
class Recording:
    source: Path
    data: Path
    width: int
    height: int
    frames: int
    dtype: str
    intervals: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reference: np.ndarray | None = field(default=None, repr=False)
    display_range: tuple[float, float] | None = None

    @property
    def title(self) -> str:
        name = self.source.stem.lower()
        if name.startswith("huh7_torsinko"):
            return "HuH7 · torsinKO + ATP"
        if name.startswith("hela"):
            return "HeLa · Baseline" if "baseline" in str(self.source.parent).lower() else "HeLa · ATP"
        return re.sub(r"[_-]+", " ", self.source.stem)

    @property
    def timing_conflict(self) -> bool:
        values = list(self.intervals.values())
        return len(values) > 1 and not np.allclose(values, values[0], rtol=1e-3)

    @property
    def suggested_interval(self) -> float | None:
        if not self.intervals or self.timing_conflict:
            return None
        return next(iter(self.intervals.values()))

    def metadata(self) -> dict:
        return dict(title=self.title, source=str(self.source), data=str(self.data),
                    width=self.width, height=self.height, frames=self.frames,
                    dtype=self.dtype, bytes=self.data.stat().st_size,
                    intervals=self.intervals, timing_conflict=self.timing_conflict,
                    interval=self.suggested_interval, warnings=self.warnings)

    def frame(self, index: int) -> np.ndarray:
        if not 0 <= index < self.frames:
            raise ValueError("Frame is outside the recording.")
        with tifffile.TiffFile(self.data) as tif:
            return tif.pages[index].asarray()

    def iter_frames(self):
        with tifffile.TiffFile(self.data) as tif:
            for page in tif.pages:
                yield page.asarray()

    def prepare_reference(self) -> np.ndarray:
        if self.reference is None:
            indices = np.unique(np.linspace(0, self.frames - 1, min(12, self.frames), dtype=int))
            total = np.zeros((self.height, self.width), dtype=np.float32)
            with tifffile.TiffFile(self.data) as tif:
                for i in indices:
                    total += tif.pages[int(i)].asarray().astype(np.float32) / len(indices)
            self.reference = total
            low, high = np.percentile(total[::2, ::2], [1, 99.5])
            self.display_range = (float(low), float(max(high, low + 1)))
        return self.reference

    def preview(self, index: int = -1, gain: float = 1.0, thumbnail: bool = False) -> bytes:
        reference = self.prepare_reference()
        array = reference if index == -1 else self.frame(index)
        low, high = self.display_range
        pixels = np.clip((array.astype(np.float32) - low) * 255 * gain / (high - low), 0, 255).astype(np.uint8)
        image = Image.fromarray(pixels)
        if thumbnail:
            image.thumbnail((320, 180))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        return buffer.getvalue()


def open_recording(source: str | Path) -> Recording:
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() not in {".tif", ".tiff", ".tid"} or not source.is_file():
        raise ValueError("Choose an existing .tif, .tiff, or .tid file.")
    data = source
    intervals = {}
    warnings = []
    try:
        with tifffile.TiffFile(source) as tif:
            description = tif.pages[0].description or ""
        if "<OME" in description:
            root = ET.fromstring(description)
            pixels = [p for p in root.iter() if p.tag.rsplit("}", 1)[-1] == "Pixels"]
            # ThorCam includes a 64x64 placeholder Image before the acquisition.
            actual = max(pixels, key=lambda p: int(p.get("SizeX", 0)) * int(p.get("SizeY", 0)))
            if int(actual.get("SizeC", 1)) != 1 or int(actual.get("SizeZ", 1)) != 1:
                raise ValueError("This version supports one grayscale channel and one Z plane per recording.")
            interval = actual.get("TimeIncrement")
            if interval:
                scale = {"s": 1, "ms": .001, "µs": .000001, "us": .000001, "min": 60}.get(actual.get("TimeIncrementUnit", "s"))
                if scale is not None and float(interval) > 0:
                    intervals["TIFF metadata"] = float(interval) * scale
            filenames = {p.get("FileName") for p in actual.iter() if p.get("FileName")}
            other_files = {name for name in filenames if Path(name).name != source.name}
            if len(other_files) > 1:
                raise ValueError("This recording references multiple image files; this version supports a single stack.")
            if other_files:
                filename = next(iter(other_files))
                # References must be siblings, never arbitrary paths embedded in metadata.
                data = (source.parent / Path(filename.replace("\\", "/")).name).resolve()
                if not data.is_file():
                    raise ValueError(f"Missing image stack: {data.name}. Keep it beside the selected TIFF.")
    except (tifffile.TiffFileError, ET.ParseError) as exc:
        raise ValueError(f"Cannot read TIFF metadata: {exc}") from exc
    config_path = source.parent / "Experiment.json"
    requested_frames = None
    if config_path.is_file():
        try:
            settings = {s["Name"]: s["Setting"] for s in json.loads(config_path.read_text(encoding="utf-8-sig"))["Settings"]}
            capture = settings.get("CaptureConfigurations", {})
            interval = float(capture.get("IntervalMs", 0)) / 1000
            if interval > 0:
                intervals["Acquisition settings"] = interval
            requested_frames = capture.get("Frames")
        except (ValueError, KeyError, TypeError):
            warnings.append("Experiment.json could not be interpreted; verify the frame interval manually.")
    with tifffile.TiffFile(data) as tif:
        first = tif.pages[0]
        if len(first.shape) != 2 or first.samplesperpixel != 1:
            raise ValueError("This version supports grayscale TIFF pages. RGB or volumetric pages are not supported.")
        shape, dtype = first.shape, first.dtype
        if dtype.kind not in "uif":
            raise ValueError("Unsupported image pixel type.")
        if any(p.shape != shape or p.dtype != dtype for p in tif.pages):
            raise ValueError("All frames must have the same size and pixel type.")
        nframes = len(tif.pages)
    if requested_frames is not None and requested_frames != nframes:
        warnings.append(f"{nframes} frames are stored; the acquisition was configured for {requested_frames}. All stored frames will be used.")
    result = Recording(source, data, shape[1], shape[0], nframes, str(dtype), intervals, warnings)
    if result.timing_conflict:
        warnings.append("Frame intervals disagree. Confirm an interval to use seconds and report rates.")
    elif not intervals:
        warnings.append("No frame interval was found. Enter one to use seconds and report rates.")
    return result
