"""Locations for recordings, bundled assets, and per-user desktop data."""
import os
from pathlib import Path
import sys


def macos_bundle() -> Path | None:
    if sys.platform == "darwin":
        resources = Path(__file__).resolve().parent.parent
        bundle = resources.parent.parent
        if resources.name == "Resources" and resources.parent.name == "Contents" and bundle.suffix == ".app":
            return bundle
    return None


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False) or macos_bundle())


def application_directory() -> Path:
    bundle = macos_bundle()
    if bundle:
        return bundle.parent
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def runtime_directory() -> Path:
    override = os.environ.get("INTENSITY_APP_DATA")
    if override:
        return Path(override).expanduser().resolve()
    if macos_bundle():
        return Path.home() / "Library" / "Application Support" / "Intensity"
    if getattr(sys, "frozen", False):
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local / "Intensity"
    return application_directory() / ".cache"


def model_directory() -> Path:
    override = os.environ.get("CELLPOSE_LOCAL_MODELS_PATH")
    if override:
        return Path(override).expanduser().resolve()
    bundled = Path(__file__).resolve().parent / "models"
    if (bundled / "cpsam_v2").is_file():
        return bundled
    return runtime_directory() / "cellpose-models"
