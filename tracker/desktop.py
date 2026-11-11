"""Native Windows window around the unchanged local web application."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time

from .paths import application_directory, model_directory, runtime_directory
from . import __version__


class LocalBackend:
    """Own a loopback listener without competing with the browser app's port."""

    def __init__(self, app):
        import uvicorn

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=port, loop="asyncio", http="h11",
                                ws="none", log_config=None, access_log=False)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]},
                                       name="intensity-local-server", daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("The local analysis server could not start.")
            time.sleep(.05)

    def stop(self):
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        self.socket.close()


def configure_logging(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        stream = (folder / "console.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[RotatingFileHandler(folder / "desktop.log", maxBytes=2_000_000,
                                                      backupCount=2, encoding="utf-8")])


def verify_recording(path: str) -> dict:
    """Exercise the frozen numerical stack without requiring GUI automation."""
    import hashlib
    import numpy as np
    from .analysis import circle_pixels, detect_regions, extract_traces, summarize, validate_roi
    from .recordings import open_recording

    recording = open_recording(path)
    reference = recording.prepare_reference()
    digest = hashlib.sha256(reference.tobytes()).hexdigest()
    detection = detect_regions(reference)
    assert hashlib.sha256(reference.tobytes()).hexdigest() == digest
    for roi in detection.rois:
        validate_roi(roi, detection, detection.rois)
    traces = extract_traces(recording, detection.rois)
    for frame in sorted({0, recording.frames // 2, recording.frames - 1}):
        pixels = recording.frame(frame)
        for roi, trace in zip(detection.rois[::max(1, len(detection.rois) // 3)],
                              traces[::max(1, len(detection.rois) // 3)]):
            rows, cols = circle_pixels(roi, pixels.shape)
            np.testing.assert_allclose(trace[frame], pixels[rows, cols].mean(), rtol=1e-12)
    result = summarize(detection.rois, traces, recording.suggested_interval)
    assert len(recording.preview(0)) > 100
    return dict(ok=True, frozen=bool(getattr(sys, "frozen", False)),
                frames=recording.frames, regions=len(detection.rois),
                detection=detection.metadata, metrics=result["metrics"],
                model_directory=str(model_directory()), raw_reference_preserved=True)


def check_desktop() -> dict:
    """Check the packaged native modules without creating a window."""
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QLibraryInfo, qVersion
    from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
    from PySide6.QtWebEngineWidgets import QWebEngineView

    helpers = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibraryExecutablesPath))
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    if not (helpers / "QtWebEngineProcess.exe").is_file():
        raise RuntimeError("The bundled Chromium helper is missing. Extract the complete app folder.")
    if not (plugins / "platforms" / "qwindows.dll").is_file():
        raise RuntimeError("The bundled Windows display plugin is missing. Extract the complete app folder.")
    return dict(ok=True, frozen=bool(getattr(sys, "frozen", False)), app_version=__version__,
                viewer="Qt WebEngine", pyside_version=pyside_version, qt_version=qVersion(),
                chromium_version=qWebEngineChromiumVersion(), pythonnet_required=False,
                webview2_required=False, helper_directory=str(helpers), plugin_directory=str(plugins))


def run_window(folder: Path):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from .desktop_window import DesktopWindow
    from . import server

    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Intensity.CellOscillationTracker")
    application = QApplication([sys.argv[0]])
    application.setApplicationName("Intensity")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("Intensity")
    backend = LocalBackend(server.app)
    runtime = folder / f"runtime-{os.getpid()}.json"
    window = None
    try:
        backend.start()
        runtime.write_text(json.dumps(dict(pid=os.getpid(), url=backend.url,
                                           version=__version__, viewer="Qt WebEngine",
                                           frozen=bool(getattr(sys, "frozen", False)))), encoding="utf-8")
        window = DesktopWindow(application, folder, backend.url)
        server.file_picker = window.picker.choose
        logging.info("Starting Intensity at %s; application directory %s", backend.url, application_directory())
        window.show()
        application.exec()
        if window.startup_error is not None:
            raise window.startup_error
    finally:
        server.file_picker = None
        if window is not None:
            window.picker.shutdown()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            window.profile.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        backend.stop()
        server.pool.shutdown(wait=False, cancel_futures=True)
        runtime.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Intensity Windows desktop app")
    parser.add_argument("--self-test", metavar="RECORDING", help="Verify the packaged analysis engine on a recording")
    parser.add_argument("--check-desktop", action="store_true", help="Check bundled desktop libraries without opening a window")
    parser.add_argument("--report", type=Path, help="JSON report for --self-test or --check-desktop")
    args = parser.parse_args()
    folder = runtime_directory()
    configure_logging(folder)
    diagnostic = bool(args.self_test or args.check_desktop)
    exit_code = 0
    try:
        if diagnostic:
            if args.report is None:
                raise ValueError("Provide --report with --self-test or --check-desktop.")
            report = verify_recording(args.self_test) if args.self_test else check_desktop()
            args.report.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        else:
            run_window(folder)
    except Exception as exc:
        exit_code = 1
        logging.exception("Intensity stopped unexpectedly")
        if diagnostic and args.report:
            args.report.write_text(json.dumps(dict(ok=False, error=str(exc))), encoding="utf-8")
        elif sys.platform == "win32":
            message = (f"Intensity could not start.\n\n{exc}\n\n"
                       f"Details: {folder / 'desktop.log'}\n\n"
                       "Keep Intensity.exe with its complete _internal folder.")
            ctypes.windll.user32.MessageBoxW(0, message, "Intensity", 0x10)
        else:
            raise
    finally:
        logging.shutdown()
    # Closing the app also ends any in-flight analysis, rather than keeping
    # Python's executor threads and GPU allocations alive after the window closes.
    if getattr(sys, "frozen", False) and not diagnostic:
        os._exit(exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
