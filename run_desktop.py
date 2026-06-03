"""Desktop launcher: starts Flask in a thread and opens a frameless PyWebview window.

Run in dev:  python run_desktop.py
The window is frameless (no native title bar); the Vue app draws its own 44px
titlebar with window controls wired to the pywebview API.
"""

import socket
import threading
import time

import webview

from backend.app import app


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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
    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    # wait briefly for the server to come up
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

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
    webview.start()


if __name__ == "__main__":
    main()
