import argparse
import json
import threading
import urllib.error
import urllib.request
import webbrowser

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Run the local intensity oscillation tracker.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=.5) as response:
            existing = json.load(response)
        if existing.get("app") == "intensity-tracker":
            if not args.no_browser:
                webbrowser.open(url)
            print(f"Intensity is already running at {url}")
            return
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        pass
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("tracker.server:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
