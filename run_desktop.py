"""Desktop launcher: starts Flask in a thread and opens a frameless PyWebview window.

Run in dev:  python run_desktop.py
The window is frameless (no native title bar); the Vue app draws its own 44px
titlebar with window controls wired to the pywebview API.
"""

import ctypes
import logging
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

# Reuse the app's root logger so window events land in the in-memory log buffer
# surfaced by the「日志调试」tab.
_wlog = logging.getLogger("batch_studio.window")

# Win32 constants for native (OS-driven) title-bar dragging.
_WM_NCLBUTTONDOWN = 0x00A1
_HTCAPTION = 0x0002

# Hit-test codes for the eight resize directions (used by start_resize so a
# frameless window can still be stretched from its edges/corners).
_HT_EDGE = {
    "left": 10, "right": 11, "top": 12, "topleft": 13, "topright": 14,
    "bottom": 15, "bottomleft": 16, "bottomright": 17,
}
# Window-style bits needed to make a borderless form user-resizable.
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
_WM_NCCALCSIZE = 0x0083
_GWLP_WNDPROC = -4
# Keeps the subclassed WndProc + previous proc alive (prevent GC of the C
# callback, which would crash the message pump).
_WNDPROC_KEEPALIVE = {}

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

    def save_text_file(self, filename: str, text: str):
        """Write *text* to the user's Downloads folder.

        WebView2 silently drops blob/anchor downloads inside this frameless
        shell, so the「日志调试」export routes through here instead of a browser
        download. Writing straight to Downloads avoids a native modal dialog
        (which can't be driven reliably) while still giving the user a file to
        hand to a developer.
        """
        try:
            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            os.makedirs(downloads, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "") or "export.log"
            path = os.path.join(downloads, safe)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text or "")
            _wlog.info("文件已导出: %s (%d 字符)", path, len(text or ""))
            return {"ok": True, "path": path}
        except Exception as e:  # noqa: BLE001
            _wlog.warning("save_text_file 失败: %s", e)
            return {"ok": False, "error": str(e)}

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

    def start_resize(self, edge: str):
        """Begin a native resize-drag from *edge* (one of the eight directions).

        Mirrors :meth:`start_drag` but feeds ``WM_NCLBUTTONDOWN`` the matching
        ``HT*`` hit-test code so ``DefWindowProc`` runs the OS resize loop. The
        frontend places thin transparent handles on the window border and calls
        this on mousedown, so a frameless window can be stretched freely while
        the Vue layout reflows responsively (UI 不变形).
        """
        ht = _HT_EDGE.get(edge)
        if not ht or not self._window:
            _wlog.info("start_resize(%s) ignored (ht=%s window=%s)", edge, ht, bool(self._window))
            return
        hwnd, form = self._native_hwnd()
        if not hwnd:
            _wlog.warning("start_resize(%s): no native hwnd", edge)
            return

        def _do():
            user32 = ctypes.windll.user32
            user32.SendMessageW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            ]
            user32.ReleaseCapture()
            user32.SendMessageW(hwnd, _WM_NCLBUTTONDOWN, ht, 0)

        invoked = "direct"
        try:
            if form is not None and bool(form.InvokeRequired):
                from System import Action
                form.Invoke(Action(_do))
                invoked = "Invoke"
            else:
                _do()
        except Exception as e:  # noqa: BLE001
            invoked = f"fallback({e})"
            try:
                _do()
            except Exception:
                pass
        _wlog.info("start_resize(%s) ht=%d hwnd=%s via=%s", edge, ht, hwnd, invoked)

    def enable_resize(self):
        """Make the borderless form user-resizable without showing a frame.

        Adds ``WS_THICKFRAME`` (so the OS resize loop works) and subclasses the
        window proc to swallow ``WM_NCCALCSIZE``, which removes the non-client
        border Windows would otherwise paint — keeping the custom chrome look.
        Safe to call once after the window is shown.
        """
        hwnd, _form = self._native_hwnd()
        if not hwnd or hwnd in _WNDPROC_KEEPALIVE:
            return
        try:
            user32 = ctypes.windll.user32
            getp = user32.GetWindowLongPtrW if hasattr(user32, "GetWindowLongPtrW") else user32.GetWindowLongW
            setp = user32.SetWindowLongPtrW if hasattr(user32, "SetWindowLongPtrW") else user32.SetWindowLongW
            getp.restype = ctypes.c_void_p
            setp.restype = ctypes.c_void_p
            setp.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

            style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
            user32.SetWindowLongW(hwnd, _GWL_STYLE, style | _WS_THICKFRAME)

            # Pointer-sized message types. The default ctypes int is 32-bit, but
            # on 64-bit Windows lParam carries 64-bit pointers (e.g. the
            # NCCALCSIZE_PARAMS* for WM_NCCALCSIZE, MINMAXINFO* for
            # WM_GETMINMAXINFO). Passing those through CallWindowProcW without
            # the right argtypes raised "int too long to convert" on every such
            # message, so the subclassed proc crashed and the native resize loop
            # could never recompute the window bounds — the root cause of the
            # window not stretching.
            LRESULT = ctypes.c_ssize_t      # LONG_PTR
            WPARAM_T = ctypes.c_size_t      # UINT_PTR
            LPARAM_T = ctypes.c_ssize_t     # LONG_PTR
            user32.CallWindowProcW.restype = LRESULT
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT, WPARAM_T, LPARAM_T,
            ]

            WNDPROC = ctypes.WINFUNCTYPE(
                LRESULT, wintypes.HWND, wintypes.UINT, WPARAM_T, LPARAM_T,
            )
            old_proc = getp(hwnd, _GWLP_WNDPROC)

            def _proc(h, msg, wp, lp):
                # Swallow NC frame calc when resizing border is on → client area
                # fills the whole window, so no visible border is drawn.
                if msg == _WM_NCCALCSIZE and wp:
                    return 0
                return user32.CallWindowProcW(
                    ctypes.c_void_p(old_proc), h, msg, wp, lp)

            cb = WNDPROC(_proc)
            _WNDPROC_KEEPALIVE[hwnd] = (cb, old_proc)
            setp(hwnd, _GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))

            # Force a frame recalculation so the change takes effect immediately.
            SWP = 0x0001 | 0x0002 | 0x0004 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP)
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
    if not _wait_for_health(port):
        _wlog.warning("Backend health check timed out before opening desktop window")

    api = Api()
    start_route = os.environ.get("BATCH_STUDIO_START_ROUTE", "/worktable").strip() or "/worktable"
    if not start_route.startswith("/"):
        start_route = "/" + start_route
    start_url = f"http://127.0.0.1:{port}/?desktop_ts={int(time.time())}#{start_route}"
    window = webview.create_window(
        "连续性批量创作工具",
        start_url,
        width=1280,
        height=820,
        min_size=(860, 600),
        frameless=True,
        easy_drag=False,
        background_color="#0c0d0f",
        js_api=api,
    )
    api.set_window(window)

    # Once the native form exists, make it freely resizable (frameless windows
    # ship without resize grips by default). Done on `shown` so the HWND is real.
    def _on_shown():
        api.enable_resize()
    try:
        window.events.shown += _on_shown
    except Exception:
        pass

    # gui="edgechromium" forces the WebView2 backend (never MSHTML).
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
