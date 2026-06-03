"""Desktop launcher: starts Flask in a thread and opens a frameless PyWebview window.

Run in dev:  python run_desktop.py
The window is frameless (no native title bar); the Vue app draws its own 44px
titlebar with window controls wired to the pywebview API.
"""

import ctypes
import os
import socket
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

# Win32 constants for native (OS-driven) title-bar dragging.
_WM_NCLBUTTONDOWN = 0x00A1
_HTCAPTION = 0x0002

# Ensure the repo root is importable even when Python runs in isolated mode
# (-I ignores cwd / PYTHONPATH). Harmless under PyInstaller (frozen) too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
        self._maximized = False

    def set_window(self, window):
        self._window = window

    def minimize(self):
        if self._window:
            self._window.minimize()

    def toggle_maximize(self):
        # A frameless window has no native caption, so we track the maximize
        # state ourselves and toggle maximize/restore. (The old code toggled
        # *fullscreen*, which hides the taskbar — not what a maximize button
        # should do.)
        if not self._window:
            return
        if self._maximized:
            self._window.restore()
            self._maximized = False
        else:
            self._window.maximize()
            self._maximized = True

    def close(self):
        if self._window:
            self._window.destroy()

    def _native_hwnd(self):
        """Resolve the top-level HWND of the pywebview (winforms) window."""
        try:
            from webview.platforms.winforms import BrowserView
            form = BrowserView.instances.get(self._window.uid)
            if form is not None:
                return int(form.Handle.ToInt32()), form
        except Exception:
            pass
        # Fallback: locate the window by its (unique) title.
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, self._window.title)
            if hwnd:
                return int(hwnd), None
        except Exception:
            pass
        return None, None

    def start_drag(self):
        """Hand the drag off to the OS window manager (native title-bar drag).

        The CSS ``.pywebview-drag-region`` / JS ``window.move`` approach proved
        unreliable (synthetic move events weren't treated as a continuous drag).
        The robust Win32 idiom is: ``ReleaseCapture()`` then post
        ``WM_NCLBUTTONDOWN`` with ``HTCAPTION`` so ``DefWindowProc`` runs the
        native modal move loop — identical to dragging a real title bar.

        ``ReleaseCapture`` + ``SendMessage`` must run on the GUI thread that
        owns the window (the WebView2 child holds the mouse capture there), so
        we marshal onto it via ``Control.Invoke`` when called from the js_api
        worker thread.
        """
        if not self._window:
            return
        hwnd, form = self._native_hwnd()
        if not hwnd:
            return

        def _do():
            user32 = ctypes.windll.user32
            user32.SendMessageW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            ]
            user32.ReleaseCapture()
            user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, _HTCAPTION, 0)

        try:
            if form is not None and bool(form.InvokeRequired):
                from System import Action
                form.Invoke(Action(_do))
            else:
                _do()
        except Exception:
            try:
                _do()
            except Exception:
                pass


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
