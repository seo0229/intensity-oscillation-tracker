"""Entry script invoked by the native py2app launcher in Contents/MacOS."""
import os
from pathlib import Path
import sys

from tracker.desktop import main


if __name__ == "__main__":
    # Finder launches the app from an arbitrary directory. Point Qt at the
    # wheel's private plugins, helper and resource packs before creating a view.
    resources = Path(__file__).resolve().parent
    qt = resources / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages/PySide6/Qt"
    framework = qt / "lib/QtWebEngineCore.framework"
    os.environ["QT_PLUGIN_PATH"] = str(qt / "plugins")
    os.environ["QTWEBENGINEPROCESS_PATH"] = str(framework / "Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess")
    os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(framework / "Resources")
    os.environ["QTWEBENGINE_LOCALES_PATH"] = str(framework / "Resources/qtwebengine_locales")
    sys.argv[1:] = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
    raise SystemExit(main())
