"""Locations for the source app and the portable Windows distribution."""
import os
from pathlib import Path
import sys


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def runtime_directory() -> Path:
    override = os.environ.get("INTENSITY_APP_DATA")
    if override:
        return Path(override).expanduser().resolve()
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
