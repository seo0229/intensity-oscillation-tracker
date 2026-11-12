"""Assemble the Apple silicon app from pinned, prebuilt macOS binaries."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import tomllib
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen
import zipfile

from packaging.requirements import Requirement
from packaging.tags import compatible_tags, cpython_tags, mac_platforms
from packaging.utils import parse_wheel_filename

from . import __version__
from .paths import application_directory, model_directory

PYTHON_VERSION = "3.13.18"
PYTHON_RELEASE = "20261110"
RUNTIME = (
    f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_RELEASE}/"
    f"cpython-{PYTHON_VERSION}+{PYTHON_RELEASE}-aarch64-apple-darwin-install_only_stripped.tar.gz",
    "7a2384641584ab9984221fcb0fc9265df465922af71be71410529e2a9c3f0540",
)
LAUNCHER = (
    "https://files.pythonhosted.org/packages/be/d8/a04a88b182613370a262f674929f0e284ea04e1803f2d38fc5639fbec2d7/"
    "py2app-0.28.10-py3-none-any.whl",
    "bdb0cd2cbfa5160b5b11b8d612f6eb92d484a0282801e85b159f1930cd834714",
)
MODEL_SHA256 = "0f1cc3f7ecdd8a037a57c6c48d9d8921391be4cbce3fa9f13c3e3a2e1253c667"
CONTENTS = "Intensity.app/Contents"
RESOURCES = f"{CONTENTS}/Resources"
SITE = f"{RESOURCES}/lib/python3.13/site-packages"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def mac_wheels(root: Path) -> list[tuple[str, str]]:
    # uv resolves the dependency graph. Evaluate its exported markers for the
    # recipient, then choose the highest-ranked compatible file in uv.lock.
    requirements = subprocess.check_output([
        "uv", "export", "--frozen", "--no-dev", "--extra", "desktop", "--no-emit-project",
        "--no-annotate", "--no-header", "--no-hashes",
    ], cwd=root, text=True)
    with (root / "uv.lock").open("rb") as stream:
        packages = {(p["name"], p["version"]): p for p in tomllib.load(stream)["package"]}
    environment = dict(sys_platform="darwin", os_name="posix", platform_system="Darwin",
                       platform_machine="arm64", python_version="3.13", python_full_version=PYTHON_VERSION,
                       implementation_name="cpython", implementation_version=PYTHON_VERSION,
                       platform_python_implementation="CPython", extra="")
    platforms = list(mac_platforms(version=(14, 0), arch="arm64"))
    tags = [*cpython_tags((3, 13), ["cp313"], platforms), *compatible_tags((3, 13), "cp313", platforms)]
    rank = {tag: position for position, tag in enumerate(tags)}
    selected = []
    for line in requirements.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate(environment):
            continue
        specifier, = req.specifier
        choices = []
        for wheel in packages[req.name, specifier.version].get("wheels", []):
            filename = unquote(urlsplit(wheel["url"]).path.rsplit("/", 1)[1])
            compatible = parse_wheel_filename(filename)[3].intersection(rank)
            if compatible:
                choices.append((min(rank[tag] for tag in compatible), wheel["url"], wheel["hash"]))
        if not choices:
            raise RuntimeError(f"No macOS arm64 wheel for {req.name} {specifier.version}")
        _, url, checksum = min(choices)
        selected.append((url, checksum.removeprefix("sha256:")))
    return selected


def fetch(cache: Path, item: tuple[str, str]) -> Path:
    url, checksum = item
    target = cache / unquote(urlsplit(url).path.rsplit("/", 1)[1])
    if target.is_file() and digest(target) == checksum:
        return target
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
        temporary = Path(output.name)
    try:
        with urlopen(url, timeout=120) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        if digest(temporary) != checksum:
            raise ValueError(f"Checksum mismatch in {target.name}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def write_member(bundle, name, stream, mode, size):
    # ZIP's Unix attributes carry executable permissions and symbolic links
    # through a Windows build host to Finder's Archive Utility.
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    info.file_size = size
    with bundle.open(info, "w") as dest:
        shutil.copyfileobj(stream, dest, length=1024 * 1024)


def write_bytes(bundle, name, data, mode=stat.S_IFREG | 0o644):
    write_member(bundle, name, io.BytesIO(data), mode, len(data))


def write_file(bundle, name, path):
    with path.open("rb") as stream:
        write_member(bundle, name, stream, stat.S_IFREG | 0o644, path.stat().st_size)


def write_runtime(bundle, runtime, launcher):
    with zipfile.ZipFile(launcher) as wheel:
        write_bytes(bundle, f"{CONTENTS}/MacOS/Intensity", wheel.read("py2app/apptemplate/prebuilt/main-arm64"),
                    stat.S_IFREG | 0o755)
        license_name = next(n for n in wheel.namelist() if n.endswith("/licenses/LICENSE.txt"))
        write_bytes(bundle, f"{RESOURCES}/licenses/py2app.txt", wheel.read(license_name))
    with tarfile.open(runtime) as tar:
        for member in tar:
            if member.isdir():
                continue
            path = PurePosixPath(member.name).relative_to("python")
            if ".." in path.parts:
                raise ValueError(f"Unsafe runtime path: {path}")
            name = f"{RESOURCES}/{path.as_posix()}"
            if member.issym():
                write_bytes(bundle, name, member.linkname.encode(), stat.S_IFLNK | 0o777)
            elif member.isfile():
                with tar.extractfile(member) as stream:
                    write_member(bundle, name, stream, stat.S_IFREG | member.mode, member.size)
                # py2app uses a physical auxiliary interpreter to locate its
                # private PYTHONHOME. The original runtime layout is preserved.
                if path.as_posix() == "bin/python3.13":
                    with tar.extractfile(member) as stream:
                        write_member(bundle, f"{CONTENTS}/MacOS/python", stream, stat.S_IFREG | 0o755, member.size)
            else:
                raise ValueError(f"Unexpected runtime entry: {member.name}")
    write_bytes(bundle, f"{CONTENTS}/Frameworks/libpython3.13.dylib",
                b"../Resources/lib/libpython3.13.dylib", stat.S_IFLNK | 0o777)
    write_bytes(bundle, f"{CONTENTS}/lib", b"Resources/lib", stat.S_IFLNK | 0o777)


def write_wheels(bundle, wheels):
    for wheel_path in wheels:
        with zipfile.ZipFile(wheel_path) as wheel:
            for item in wheel.infolist():
                if item.is_dir():
                    continue
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError(f"Unsafe wheel path: {path}")
                if path.parts[0].endswith(".data"):
                    scheme, *relative = path.parts[1:]
                    # PEP 427's spread step, using Resources as Python's prefix.
                    prefix = {"data": RESOURCES, "purelib": SITE, "platlib": SITE,
                              "headers": f"{RESOURCES}/include", "scripts": f"{RESOURCES}/bin"}[scheme]
                    name = "/".join([prefix, *relative])
                else:
                    name = f"{SITE}/{path}"
                if name in bundle.NameToInfo:
                    previous = bundle.getinfo(name)
                    if (previous.CRC, previous.file_size) != (item.CRC, item.file_size):
                        raise ValueError(f"Conflicting wheel contents at {name}")
                    continue
                mode = item.external_attr >> 16
                if not stat.S_IFMT(mode):
                    mode = stat.S_IFREG | (mode or 0o644)
                with wheel.open(item) as stream:
                    write_member(bundle, name, stream, mode, item.file_size)


def write_app(bundle, root, weights, wheels):
    from .packaging.icon import desktop_icon

    info = dict(CFBundlePackageType="APPL", CFBundleName="Intensity", CFBundleDisplayName="Intensity",
                CFBundleExecutable="Intensity", CFBundleIdentifier="org.intensitytracker.desktop",
                CFBundleInfoDictionaryVersion="6.0", CFBundleIconFile="Intensity.icns",
                CFBundleVersion=__version__, CFBundleShortVersionString=__version__,
                LSMinimumSystemVersion="14.0", NSHighResolutionCapable=True, NSPrincipalClass="NSApplication",
                PyExecutableName="python", PyMainFileNames=["__boot__"],
                PyResourcePackages=["lib/python3.13/site-packages"],
                PyRuntimeLocations=["@executable_path/../Frameworks/libpython3.13.dylib"],
                LSEnvironment={"PYTHONNOUSERSITE": "1"})
    write_bytes(bundle, f"{CONTENTS}/Info.plist", plistlib.dumps(info))
    # On macOS Qt resolves a relative prefix from the .app's Contents directory.
    write_bytes(bundle, f"{RESOURCES}/qt.conf", b"[Paths]\nPrefix=Resources/lib/python3.13/site-packages/PySide6/Qt\n")
    icon = io.BytesIO()
    desktop_icon().save(icon, format="ICNS")
    write_bytes(bundle, f"{RESOURCES}/Intensity.icns", icon.getvalue())
    write_file(bundle, f"{RESOURCES}/__boot__.py", root / "tracker/packaging/macos_entry.py")
    for path in sorted((root / "tracker").glob("*.py")):
        if not path.name.startswith("build_"):
            write_file(bundle, f"{RESOURCES}/tracker/{path.name}", path)
    for path in sorted((root / "tracker/static").rglob("*")):
        if path.is_file():
            write_file(bundle, f"{RESOURCES}/{path.relative_to(root).as_posix()}", path)
    write_file(bundle, f"{RESOURCES}/tracker/models/cpsam_v2", weights)
    write_file(bundle, f"{RESOURCES}/licenses/Intensity.txt", root / "LICENSE")
    manifest = dict(version=__version__, python=PYTHON_VERSION, architecture="arm64", minimum_macos="14.0",
                    model="cpsam_v2", model_sha256=MODEL_SHA256, wheels=[p.name for p in wheels])
    write_bytes(bundle, f"{RESOURCES}/build-info.json", json.dumps(manifest, indent=2).encode())


def verify_arm64_target(bundle, path):
    # Wheel platform tags can advertise an older OS than the Mach-O inside.
    # Read the actual ARM slice and its LC_BUILD_VERSION deployment target.
    with bundle.open(path) as stream:
        magic = stream.read(4)
        if magic in (bytes.fromhex("cafebabe"), bytes.fromhex("cafebabf")):
            count, = struct.unpack(">I", stream.read(4))
            layout = ">IIIII" if magic == bytes.fromhex("cafebabe") else ">IIQQII"
            slices = [struct.unpack(layout, stream.read(struct.calcsize(layout))) for _ in range(count)]
            arm = next((item for item in slices if item[0] == 0x0100000c), None)
            if arm is None:
                raise ValueError(f"Missing Apple silicon slice in {path}")
            stream.seek(arm[2])
            magic = stream.read(4)
        if magic != bytes.fromhex("cffaedfe"):
            raise ValueError(f"Invalid Mach-O header in {path}")
        cpu, _, _, count, length, _, _ = struct.unpack("<7I", stream.read(28))
        if cpu != 0x0100000c:
            raise ValueError(f"Not an Apple silicon binary: {path}")
        commands = stream.read(length)
    offset = 0
    minimum = None
    for _ in range(count):
        kind, size = struct.unpack_from("<II", commands, offset)
        if kind == 0x32:  # LC_BUILD_VERSION
            minimum, = struct.unpack_from("<I", commands, offset + 12)
        elif kind == 0x24:  # LC_VERSION_MIN_MACOSX
            minimum, = struct.unpack_from("<I", commands, offset + 8)
        offset += size
    if minimum is None or minimum > (14 << 16):
        raise ValueError(f"Deployment target exceeds macOS 14.0 or is missing in {path}: {minimum!r}")


def verify_archive(archive):
    framework = f"{SITE}/PySide6/Qt/lib/QtWebEngineCore.framework"
    executables = [f"{CONTENTS}/MacOS/Intensity", f"{CONTENTS}/MacOS/python",
                   f"{framework}/Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess"]
    required = [f"{CONTENTS}/Info.plist", f"{RESOURCES}/__boot__.py", f"{RESOURCES}/qt.conf", f"{RESOURCES}/tracker/desktop.py",
                f"{RESOURCES}/tracker/static/index.html", f"{RESOURCES}/lib/python3.13/encodings/__init__.py",
                f"{RESOURCES}/lib/libpython3.13.dylib", f"{SITE}/PySide6/Qt/plugins/platforms/libqcocoa.dylib",
                f"{framework}/Resources/qtwebengine_resources.pak", f"{framework}/Resources/icudtl.dat",
                f"{framework}/Resources/qtwebengine_locales/en-US.pak", f"{SITE}/torch/lib/libtorch_cpu.dylib"]
    with zipfile.ZipFile(archive) as bundle:
        for path in required:
            bundle.getinfo(path)
        for path in executables:
            if not (bundle.getinfo(path).external_attr >> 16) & 0o111:
                raise ValueError(f"Missing executable permissions: {path}")
        for path in [*executables, f"{RESOURCES}/lib/libpython3.13.dylib", f"{SITE}/torch/lib/libtorch_cpu.dylib",
                     f"{SITE}/PySide6/QtCore.abi3.so", f"{SITE}/PySide6/QtWebEngineWidgets.abi3.so",
                     f"{framework}/Versions/A/QtWebEngineCore"]:
            verify_arm64_target(bundle, path)
        with bundle.open(f"{RESOURCES}/tracker/models/cpsam_v2") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA256:
                raise ValueError("Incorrect bundled Cellpose weights")
        bad = bundle.testzip()
        if bad:
            raise ValueError(f"Archive integrity check failed: {bad}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = application_directory()
    parser.add_argument("--dist-dir", type=Path, default=root / "dist" / f"macos-v{__version__}")
    parser.add_argument("--model", type=Path, default=model_directory() / "cpsam_v2")
    args = parser.parse_args()
    archive = args.dist_dir.resolve() / f"Intensity-macOS-Apple-Silicon-v{__version__}.zip"
    if archive.exists():
        parser.error(f"{archive} already exists. Choose another --dist-dir.")
    if not args.model.is_file() or digest(args.model) != MODEL_SHA256:
        parser.error("Provide the cpsam_v2 weights from this release using --model.")
    cache = root / ".cache" / "macos-inputs"
    cache.mkdir(parents=True, exist_ok=True)
    inputs = [RUNTIME, LAUNCHER, *mac_wheels(root)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        runtime, launcher, *wheels = pool.map(lambda item: fetch(cache, item), inputs)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="intensity-mac-", dir=archive.parent) as staging:
        compressed = Path(staging) / archive.name
        with zipfile.ZipFile(compressed, "x", allowZip64=True) as bundle:
            print("Adding the native launcher and Python runtime...", flush=True)
            write_runtime(bundle, runtime, launcher)
            print(f"Adding {len(wheels)} macOS wheels...", flush=True)
            write_wheels(bundle, wheels)
            print("Adding the tracker and Cellpose weights...", flush=True)
            write_app(bundle, root, args.model, wheels)
        print("Verifying the archive and bundled model...", flush=True)
        verify_archive(compressed)
        checksum = digest(compressed)
        # Reserve the filename, then publish the complete ZIP on the same volume.
        # This also works on removable drives and systems without hard links.
        with archive.open("xb"):
            pass
        try:
            compressed.replace(archive)
        except OSError:
            archive.unlink(missing_ok=True)
            raise
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    print(f"Verified Mac app ZIP: {archive} ({archive.stat().st_size / 1e9:.2f} GB)", flush=True)


if __name__ == "__main__":
    main()
