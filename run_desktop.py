"""Desktop launcher for vmo studio."""

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

_wlog = logging.getLogger("vmo_studio.window")

_WM_NCLBUTTONDOWN = 0x00A1
_HTCAPTION = 0x0002
_HT_EDGE = {
    "left": 10, "right": 11, "top": 12, "topleft": 13, "topright": 14,
    "bottom": 15, "bottomleft": 16, "bottomright": 17,
}
_GWL_STYLE = -16
_WS_THICKFRAME = 0x00040000
_WM_NCCALCSIZE = 0x0083
_GWLP_WNDPROC = -4
_WNDPROC_KEEPALIVE = {}

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
    """pywebview API used by the custom window chrome."""

    def __init__(self):
        self._window = None
        self._maximized = False

    def set_window(self, window):
        self._window = window

    def minimize(self):
        if self._window:
            self._window.minimize()

    def toggle_maximize(self):
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
        try:
            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            os.makedirs(downloads, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "") or "export.log"
            path = os.path.join(downloads, safe)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text or "")
            _wlog.info("file exported: %s (%d chars)", path, len(text or ""))
            return {"ok": True, "path": path}
        except Exception as e:  # noqa: BLE001
            _wlog.warning("save_text_file failed: %s", e)
            return {"ok": False, "error": str(e)}

    def choose_path(self, mode: str = "file"):
        if not self._window:
            return {"ok": False, "error": "窗口尚未就绪"}
        try:
            dialog_type = webview.FileDialog.FOLDER if mode == "folder" else webview.FileDialog.OPEN
            file_types = ()
            if mode != "folder":
                file_types = (
                    "账号配置包 (*.zip;*.json)",
                    "可执行文件 (*.exe)",
                    "所有文件 (*.*)",
                )
            selected = self._window.create_file_dialog(
                dialog_type,
                allow_multiple=False,
                file_types=file_types,
            )
            if not selected:
                return {"ok": True, "path": ""}
            path = selected[0] if isinstance(selected, (list, tuple)) else selected
            return {"ok": True, "path": str(path)}
        except Exception as e:  # noqa: BLE001
            _wlog.warning("choose_path(%s) failed: %s", mode, e)
            return {"ok": False, "error": str(e)}

    def _native_hwnd(self):
        try:
            from webview.platforms.winforms import BrowserView
            form = BrowserView.instances.get(self._window.uid)
            if form is not None:
                return int(form.Handle.ToInt32()), form
        except Exception:
            pass
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, self._window.title)
            if hwnd:
                return int(hwnd), None
        except Exception:
            pass
        return None, None

    def start_drag(self):
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
        ht = _HT_EDGE.get(edge)
        if not ht or not self._window:
            _wlog.info("start_resize(%s) ignored", edge)
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

    def enable_resize(self):
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

            lresult = ctypes.c_ssize_t
            wparam_t = ctypes.c_size_t
            lparam_t = ctypes.c_ssize_t
            user32.CallWindowProcW.restype = lresult
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wparam_t, lparam_t,
            ]

            wndproc = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wparam_t, lparam_t)
            old_proc = getp(hwnd, _GWLP_WNDPROC)

            def _proc(h, msg, wp, lp):
                if msg == _WM_NCCALCSIZE and wp:
                    return 0
                return user32.CallWindowProcW(ctypes.c_void_p(old_proc), h, msg, wp, lp)

            cb = wndproc(_proc)
            _WNDPROC_KEEPALIVE[hwnd] = (cb, old_proc)
            setp(hwnd, _GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))

            swp = 0x0001 | 0x0002 | 0x0004 | 0x0020
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, swp)
        except Exception:
            pass


def main() -> None:
    os.environ.setdefault("VMO_DEV_AUTH_BYPASS", "1")
    ensure_runtime()

    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()
    if not _wait_for_health(port):
        _wlog.warning("Backend health check timed out before opening desktop window")

    api = Api()
    start_route = os.environ.get("VMO_STUDIO_START_ROUTE", "/worktable").strip() or "/worktable"
    if not start_route.startswith("/"):
        start_route = "/" + start_route
    start_url = f"http://127.0.0.1:{port}/?desktop_ts={int(time.time())}&local_auth=1#{start_route}"
    window = webview.create_window(
        "vmo studio",
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

    def _on_shown():
        api.enable_resize()

    try:
        window.events.shown += _on_shown
    except Exception:
        pass

    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()
