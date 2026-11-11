"""Native desktop controls and the bundled Chromium viewer; no .NET bridge."""
from __future__ import annotations

from concurrent.futures import Future
import logging
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QStandardPaths, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QFileDialog, QMainWindow
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView


class FilePicker(QObject):
    """Marshal the backend's file-picker requests onto Qt's main thread."""

    requested = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending: set[Future] = set()
        self.lock = threading.Lock()
        self.closed = False
        self.directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        self.requested.connect(self.show_dialog, Qt.ConnectionType.QueuedConnection)

    def choose(self) -> str | None:
        future = Future()
        with self.lock:
            if self.closed:
                return None
            self.pending.add(future)
        self.requested.emit(future)
        try:
            return future.result()
        finally:
            with self.lock:
                self.pending.discard(future)

    @Slot(object)
    def show_dialog(self, future):
        if future.done():
            return
        try:
            filename, _ = QFileDialog.getOpenFileName(
                self.window, "Open a fluorescence recording", self.directory,
                "TIFF recordings (*.tif *.tiff *.tid)")
            if filename:
                self.directory = str(Path(filename).parent)
            if not future.done():
                future.set_result(filename or None)
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)

    def shutdown(self):
        with self.lock:
            self.closed = True
            pending = tuple(self.pending)
        for future in pending:
            if not future.done():
                future.set_result(None)


class LocalPage(QWebEnginePage):
    def __init__(self, profile, origin: str, parent):
        super().__init__(profile, parent)
        self.origin = QUrl(origin)
        self.setBackgroundColor(QColor("#f5f7f8"))

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        # The app displays only its own loopback interface, including downloads.
        return (url.scheme(), url.host(), url.port()) == (
            self.origin.scheme(), self.origin.host(), self.origin.port())


class DesktopWindow(QMainWindow):
    def __init__(self, application, folder: Path, url: str):
        super().__init__()
        self.startup_error: Exception | None = None
        self.setWindowTitle("Intensity — Cell oscillation tracker")
        self.setMinimumSize(1000, 700)
        screen = application.primaryScreen().availableGeometry()
        self.resize(min(1440, screen.width()), min(960, screen.height() - 60))
        icon = Path(__file__).parent / "intensity.ico"
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))

        self.view = QWebEngineView(self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCentralWidget(self.view)
        self.profile = QWebEngineProfile("Intensity", application)
        self.profile.setCachePath(str(folder / "chromium" / "cache"))
        self.profile.setPersistentStoragePath(str(folder / "chromium" / "storage"))
        self.page = LocalPage(self.profile, url, self.view)
        self.view.setPage(self.page)
        self.picker = FilePicker(self)
        self.save_directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        self.profile.downloadRequested.connect(self.save_download)
        self.view.loadFinished.connect(self.page_loaded)
        self.page.renderProcessTerminated.connect(self.renderer_stopped)
        self.view.setUrl(QUrl(url))

    @Slot(bool)
    def page_loaded(self, success):
        if success:
            logging.info("Desktop interface loaded with bundled Qt WebEngine")
        else:
            self.startup_error = RuntimeError("The desktop viewer could not display the local interface.")
            logging.error("Local interface failed to load: %s", self.view.url().toString())
            self.close()

    def renderer_stopped(self, status, exit_code):
        self.startup_error = RuntimeError(f"The desktop viewer stopped unexpectedly (code {exit_code}).")
        logging.error("Qt WebEngine renderer stopped: %s, exit %s", status, exit_code)
        self.close()

    def save_download(self, request):
        suggested = Path(request.suggestedFileName()).name
        extension = Path(suggested).suffix.lower()
        file_filter = {".csv": "CSV files (*.csv)", ".json": "JSON files (*.json)"}.get(extension, "All files (*)")
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save analysis results", str(Path(self.save_directory) / suggested), file_filter)
        if not filename:
            request.cancel()
            return
        target = Path(filename)
        self.save_directory = str(target.parent)
        request.setDownloadDirectory(str(target.parent))
        request.setDownloadFileName(target.name)
        request.accept()

    def closeEvent(self, event):
        self.picker.shutdown()
        super().closeEvent(event)
