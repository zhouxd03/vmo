"""Desktop launcher: starts Flask in a thread and opens a frameless PyWebview window.

Run in dev:  python run_desktop.py
The window is frameless (no native title bar); the Vue app draws its own 44px
titlebar with window controls wired to the pywebview API.
"""

import socket
import threading
import time
import urllib.request

import webview

from backend.app import app
from backend.core.webview2 import ensure_runtime


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_health(port: int, timeout: float = 30.0) -> bool:
    """Poll /api/health until the backend is actually serving (not just the
    socket bound). Returns True once healthy, False on timeout."""
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def _serve(port: int) -> None:
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


class Api:
    """Exposed to the frontend via window.pywebview.api for window controls."""

    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def minimize(self):
        if self._window:
            self._window.minimize()

    def toggle_maximize(self):
        if self._window:
            self._window.toggle_fullscreen()

    def close(self):
        if self._window:
            self._window.destroy()


def main() -> None:
    # Make sure the Edge WebView2 runtime is available, otherwise pywebview
    # falls back to MSHTML (IE) and the modern Vue bundle renders blank.
    ensure_runtime()

    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    # Wait until the backend actually answers (not just the socket binds) so
    # the webview never navigates to a not-yet-serving URL (which would leave
    # a blank page with no retry).
    _wait_for_health(port)

    api = Api()
    window = webview.create_window(
        "连续性批量创作工具",
        f"http://127.0.0.1:{port}/",
        width=1360,
        height=860,
        min_size=(1024, 700),
        frameless=True,
        easy_drag=False,
        background_color="#0c0d0f",
        js_api=api,
    )
    api.set_window(window)
    # gui="edgechromium" forces the WebView2 backend (never MSHTML).
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
