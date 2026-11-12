"""Build the portable Windows app from the locked project environment."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from .paths import application_directory, model_directory
from . import __version__


def prepare_assets(root: Path, model: Path) -> Path:
    from .packaging.icon import desktop_icon
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    (root / "build").mkdir(exist_ok=True)
    assets = Path(tempfile.mkdtemp(prefix="windows-assets-", dir=root / "build"))
    with model.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()

    desktop_icon().save(assets / "intensity.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])

    # Include notices for the runtime dependency closure, without development tools.
    notices = assets / "licenses"
    notices.mkdir(exist_ok=True)
    pending = ["intensity-oscillation-tracker", "PySide6"]
    versions = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in versions:
            continue
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for requirement in distribution.requires or []:
            req = Requirement(requirement)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                pending.append(req.name)
        for file in distribution.files or []:
            if any(word in str(file).lower() for word in ("license", "copying", "notice")):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = notices / name / Path(str(file)).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Preserve distinct notices that happen to share a basename.
                    if target.exists() and target.read_bytes() != source.read_bytes():
                        target = target.with_name(hashlib.sha256(str(file).encode()).hexdigest()[:8] + "-" + target.name)
                    shutil.copyfile(source, target)
    for candidate in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
        if candidate.is_file():
            shutil.copyfile(candidate, notices / "Python-LICENSE.txt")
            break
    (assets / "build-info.json").write_text(json.dumps(dict(
        app="Intensity", version=__version__, python=sys.version, desktop_viewer="Qt WebEngine",
        pythonnet_required=False, webview2_required=False,
        model="cpsam_v2", model_sha256=digest, dependencies=dict(sorted(versions.items()))
    ), indent=2), encoding="utf-8")
    (assets / "Read me.txt").write_text(
        "INTENSITY - CELL OSCILLATION TRACKER\n\n"
        f"Portable Windows release {__version__}\n\n"
        "Extract the entire ZIP, then double-click Intensity.exe inside the extracted Intensity folder.\n"
        "Keep the entire folder together; _internal is required.\n"
        "Requires 64-bit Windows 10/11. Python, Chromium, Qt, the analysis libraries, CUDA libraries,\n"
        "and Cellpose weights are included. No Python.NET, .NET, or WebView2 installation is needed.\n\n"
        "Use Open recording to choose a TIFF/TID. Keep companion TID/Experiment.json files beside\n"
        "their TIFF when present. Recordings are not bundled. A dataset folder beside Intensity.exe\n"
        "appears in the library, or set INTENSITY_DATASET to another folder.\n\n"
        "The app runs locally. It uses an NVIDIA GPU when available and otherwise the CPU.\n"
        "If GPU initialization or analysis fails, Cellpose retries on the CPU automatically.\n"
        "CPU analysis uses the same model and can take substantially longer.\n"
        "Export results before closing. Closing the window stops the analysis server.\n"
        "Logs and window storage are in %LOCALAPPDATA%\\Intensity.\n"
        "See README.md for measurement definitions and the existing metadata timing policy.\n",
        encoding="utf-8")
    return assets


def verify_package(output: Path):
    required = ["Intensity.exe", "_internal/tracker/models/cpsam_v2", "_internal/tracker/static/index.html",
                "_internal/PySide6/QtWebEngineProcess.exe", "_internal/PySide6/plugins/platforms/qwindows.dll",
                "_internal/PySide6/resources/qtwebengine_resources.pak"]
    for name in required:
        if not (output / name).is_file():
            raise RuntimeError(f"Incomplete desktop package: missing {name}")
    for path in output.rglob("*"):
        if path.name.lower() == "python.runtime.dll" or path.name.lower() in {"pythonnet", "clr_loader", "webview"}:
            raise RuntimeError(f"Obsolete desktop dependency unexpectedly bundled: {path}")


def package_zip(output: Path, archive: Path):
    """Write a standard ZIP64 archive and verify every file before handing it out."""
    output, archive = output.resolve(), archive.resolve()
    verify_package(output)
    if archive.exists():
        raise FileExistsError(f"Archive already exists: {archive}")
    if archive.is_relative_to(output):
        raise ValueError("Put the ZIP outside the app folder.")
    files = sorted(path for path in output.rglob("*") if path.is_file())
    archive.parent.mkdir(parents=True, exist_ok=True)
    print(f"Creating ZIP64 archive with {len(files)} files: {archive}", flush=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as bundle:
        for path in files:
            if path.stat().st_size > 100_000_000:
                print(f"Compressing {path.relative_to(output)}", flush=True)
            bundle.write(path, (Path("Intensity") / path.relative_to(output)).as_posix())
    print("Checking archive integrity...", flush=True)
    with zipfile.ZipFile(archive) as bundle:
        expected = {"Intensity/" + path.relative_to(output).as_posix() for path in files}
        if set(bundle.namelist()) != expected or bundle.testzip() is not None:
            raise RuntimeError("ZIP verification failed; do not distribute this archive.")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Verified ZIP: {archive} ({archive.stat().st_size / 1024**3:.2f} GiB)", flush=True)


def build_environment(assets: Path, model: Path) -> dict:
    environment = dict(os.environ, INTENSITY_BUNDLE_MODEL=str(model), INTENSITY_BUILD_ASSETS=str(assets))
    # DLL discovery must not pick up unrelated applications' ICU/OpenSSL/etc.
    # from the developer's PATH. Wheel libraries remain discoverable by hooks.
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(map(str, [Path(sys.executable).parent, Path(sys.base_prefix),
                                                   windows / "System32", windows]))
    for name in ("PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "QT_PLUGIN_PATH",
                 "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QTWEBENGINEPROCESS_PATH"):
        environment.pop(name, None)
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, help="Path to the cached cpsam_v2 weights")
    parser.add_argument("--dist-dir", type=Path, help="Parent directory for the new Intensity folder (default: dist)")
    parser.add_argument("--zip", action="store_true", help="Also create and verify a ZIP for sharing")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build the Windows executable on Windows.")
    root = application_directory()
    dist = (args.dist_dir or root / "dist").resolve()
    output = dist / "Intensity"
    if output.exists():
        parser.error(f"{output} already exists. Choose a new --dist-dir or move the old app folder first. "
                     "Existing app folders may contain recordings or exported results and are never replaced automatically.")
    model = (args.model or model_directory() / "cpsam_v2").resolve()
    if not model.is_file():
        parser.error("Cellpose weights are missing. Run an analysis once to cache cpsam_v2, or use --model.")
    assets = prepare_assets(root, model)
    environment = build_environment(assets, model)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(dist), "--workpath", str(root / "build" / "pyinstaller"),
                    str(root / "tracker" / "packaging" / "Intensity.spec")],
                   cwd=root, env=environment, check=True)
    for name in ("Read me.txt", "build-info.json"):
        shutil.copyfile(assets / name, output / name)
    for name in ("README.md", "LICENSE"):
        shutil.copyfile(root / name, output / name)
    verify_package(output)
    print(f"Portable Windows app: {output / 'Intensity.exe'}")
    if args.zip:
        package_zip(output, dist / f"Intensity-Windows-v{__version__}.zip")


if __name__ == "__main__":
    main()
