"""One-click launcher: start the app server and open it in the browser.

Runs `uvicorn` as a subprocess bound to `app.config.SERVER_HOST`/`SERVER_PORT`
(loopback-only, matching the app's local-only design), polls `/health` until
the server responds, then opens the default browser to the dashboard. The
subprocess is terminated on Ctrl+C or any other exit from this script, so
there's never an orphaned server left running in the background.
"""

import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import FrameType

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SERVER_HOST, SERVER_PORT  # noqa: E402

READY_TIMEOUT_SECONDS = 15.0
READY_POLL_INTERVAL_SECONDS = 0.2


def _wait_until_ready(url: str, timeout: float, process: subprocess.Popen) -> bool:
    """Poll `url` until it responds successfully, `timeout` elapses, or `process` exits.

    Bailing out as soon as `process` exits (e.g. the port is already in use,
    or an import error on startup) avoids waiting out the full timeout on a
    server that has already crashed.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(READY_POLL_INTERVAL_SECONDS)
    return False


def main() -> None:
    """Start uvicorn, wait for readiness, open the browser, then wait on the server."""
    import webbrowser

    base_url = f"http://{SERVER_HOST}:{SERVER_PORT}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            SERVER_HOST,
            "--port",
            str(SERVER_PORT),
        ],
        cwd=Path(__file__).resolve().parent.parent,
    )

    def _shutdown(signum: int, frame: FrameType | None) -> None:
        process.terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        if _wait_until_ready(f"{base_url}/health", READY_TIMEOUT_SECONDS, process):
            print(f"Finance Tracker is running at {base_url}")
            webbrowser.open(base_url)
        elif process.poll() is not None:
            print(
                "uvicorn exited before the server became ready — "
                "check the output above for errors (e.g. the port may "
                "already be in use).",
                file=sys.stderr,
            )
        else:
            print(
                f"Server did not respond within {READY_TIMEOUT_SECONDS:.0f}s — "
                f"check the uvicorn output above for errors.",
                file=sys.stderr,
            )
        process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
