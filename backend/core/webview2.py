"""Ensure the Microsoft Edge WebView2 runtime is present.

pywebview renders the Vue UI through Edge Chromium (WebView2). When the
WebView2 runtime is missing, pywebview silently falls back to the legacy
MSHTML (IE) engine, which cannot run a modern ES-module bundle -> the window
shows a blank white page. To make the packaged app usable on a clean machine,
we detect the runtime at startup and, if absent, run the bundled evergreen
bootstrapper (per-user, no admin required) before opening the window.

Bootstrapper: desktop_assets/MicrosoftEdgeWebview2Setup.exe, bundled into the
PyInstaller dir (see desktop.spec). Safe no-op on non-Windows / when present.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Evergreen WebView2 Runtime AppID (same under HKLM 32-bit view and HKCU).
_RUNTIME_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_BOOTSTRAPPER = "MicrosoftEdgeWebview2Setup.exe"


def is_runtime_installed() -> bool:
    """True if the WebView2 runtime is registered for the machine or user."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return True

    probes = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\" + _RUNTIME_GUID),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + _RUNTIME_GUID),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + _RUNTIME_GUID),
    ]
    for hive, subkey in probes:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
                if pv and pv != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def _find_bootstrapper() -> Path | None:
    """Locate the bundled bootstrapper in frozen and dev layouts."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / _BOOTSTRAPPER)
        candidates.append(Path(meipass) / "desktop_assets" / _BOOTSTRAPPER)
    root = Path(__file__).resolve().parents[2]
    candidates.append(root / "desktop_assets" / _BOOTSTRAPPER)
    for c in candidates:
        if c.is_file():
            return c
    return None


def ensure_runtime(log=print) -> bool:
    """Install the WebView2 runtime if missing. Returns True if available
    afterwards. Never raises — failure just falls through to whatever engine
    pywebview can use."""
    if sys.platform != "win32":
        return True
    if is_runtime_installed():
        return True

    setup = _find_bootstrapper()
    if setup is None:
        log("[webview2] runtime missing and bootstrapper not bundled; "
            "the window may render blank. Install Edge WebView2 Runtime.")
        return False

    log(f"[webview2] runtime missing; installing via {setup.name} (per-user)...")
    try:
        # Per-user evergreen install — no admin prompt. CREATE_NO_WINDOW hides
        # the console flash.
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        subprocess.run(
            [str(setup), "/silent", "/install"],
            check=False,
            creationflags=flags,
            timeout=600,
        )
    except Exception as e:  # noqa: BLE001 - best effort, never block launch
        log(f"[webview2] install failed: {e!r}")
        return False

    ok = is_runtime_installed()
    log(f"[webview2] install finished; runtime present={ok}")
    return ok
