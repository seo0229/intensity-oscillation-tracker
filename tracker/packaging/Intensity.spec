# Build with: uv run --extra desktop --extra build python -m tracker.build_windows
import os
from pathlib import Path
import tomllib
from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct, VSVersionInfo

root = Path(SPECPATH).resolve().parents[1]
assets = Path(os.environ["INTENSITY_BUILD_ASSETS"])
model = Path(os.environ["INTENSITY_BUNDLE_MODEL"])
version_string = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
version_tuple = tuple(map(int, version_string.split("."))) + (0,)
datas = [
    (str(root / "tracker" / "static"), "tracker/static"),
    (str(assets / "intensity.ico"), "tracker"),
    (str(model), "tracker/models"),
    (str(assets / "licenses"), "licenses"),
]
for distribution in ("cellpose", "torch", "torchvision", "PySide6", "PySide6-WebEngine"):
    datas += copy_metadata(distribution)

hiddenimports = collect_submodules("imagecodecs") + [
    "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on",
]
analysis = Analysis(
    [str(root / "tracker" / "packaging" / "desktop_entry.py")],
    pathex=[str(root)], binaries=[], datas=datas, hiddenimports=hiddenimports,
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "_tkinter",
              "pytest", "IPython", "jupyter", "notebook", "webview", "pythonnet", "clr", "clr_loader"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple, mask=0x3f,
                      flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("FileDescription", "Intensity - Cell oscillation tracker"),
        StringStruct("FileVersion", version_string), StringStruct("ProductVersion", version_string),
        StringStruct("ProductName", "Intensity"), StringStruct("OriginalFilename", "Intensity.exe"),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="Intensity",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(assets / "intensity.ico"), version=version)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="Intensity")
