"""Local Doubao web account pool used as a video provider.

This is a local implementation of the browser-extension protocol used by the
Doubao page service. VMO owns the pool, task server, browser launch, and task
state; it does not call another Electron app's IPC/runtime.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import requests as http

from ..core.paths import BUNDLED_DIR, DATA_DIR
from ..core.store import read_json, write_json
from . import llm as llm_service

logger = logging.getLogger("batch_studio")

POOL_DIR = DATA_DIR / "doubao-pool"
POOL_FILE = POOL_DIR / "pool.json"
TASKS_FILE = POOL_DIR / "tasks.json"
LOCK_FILE = POOL_DIR / "launch-lock.json"
PLUGIN_INSTALL_FILE = POOL_DIR / "plugin-install.json"
EXPORT_DIR = POOL_DIR / "exports"
IMPORT_TMP_DIR = POOL_DIR / "import-tmp"


def _resource_path(*parts: str) -> Path:
    bundled = BUNDLED_DIR.joinpath("resources", *parts)
    if bundled.exists():
        return bundled
    return Path(__file__).resolve().parents[2].joinpath("resources", *parts)


def _source_extension_dir() -> Path:
    return _resource_path("doubao-extension", "vmo")


def _source_duration15_extension_dir() -> Path:
    return _resource_path("doubao-extension", "duration15")


def _source_watermark_extension_dir() -> Path:
    return _resource_path("doubao-extension", "watermark")


def _packaged_multi_open_browser() -> Path:
    return _resource_path("doubao-browser", "doubao-watermark-browser.exe")


def _packaged_browser_chrome() -> Path:
    return _resource_path("doubao-browser", "chrome_bin", "chrome.exe")


SOURCE_EXTENSION_DIR = _source_extension_dir()
SOURCE_DURATION15_EXTENSION_DIR = _source_duration15_extension_dir()
SOURCE_WATERMARK_EXTENSION_DIR = _source_watermark_extension_dir()
EXTENSION_DIR = SOURCE_EXTENSION_DIR
DURATION15_EXTENSION_DIR = SOURCE_DURATION15_EXTENSION_DIR
WATERMARK_EXTENSION_DIR = SOURCE_WATERMARK_EXTENSION_DIR
PACKAGED_MULTI_OPEN_BROWSER = _packaged_multi_open_browser()
PACKAGED_BROWSER_CHROME = _packaged_browser_chrome()
DEFAULT_SERVER_PORT = int(os.environ.get("DOUBAO_POOL_PORT", "56622") or "56622")
LOCK_TTL_MS = 7 * 24 * 60 * 60 * 1000
DOUBAO_FIXED_VIDEO_DURATION_SECONDS = 15
DOUBAO_VIDEO_DURATION_OPTIONS = (5, 10, 15)
DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA = 5
DOUBAO_CREDITS_PER_VIDEO = 2
DOUBAO_AUTOMATION_MODE_FULL = "full"
DOUBAO_AUTOMATION_MODE_SEMI = "semi"
DOUBAO_AUTOMATION_MODES = {DOUBAO_AUTOMATION_MODE_FULL, DOUBAO_AUTOMATION_MODE_SEMI}
DOUBAO_LOW_CREDIT_PATTERNS = (
    "额度不足", "剩余额度不足", "积分不足", "余额不足", "今日剩余0", "剩余0",
    "没有足够", "额度不够", "积分不够", "可用额度不足",
)

DOUBAO_MODELS = [
    {"label": "豆包网页视频 - 自动", "value": "doubao-web-video"},
]

DOUBAO_SITE_CN = "cn"
DOUBAO_SITE_INTL = "intl"
DOUBAO_SITE_DEFAULT = DOUBAO_SITE_CN
DOUBAO_SITE_CONFIGS = {
    DOUBAO_SITE_CN: {
        "label": "Doubao",
        "host": "doubao.com",
        "origin": "https://www.doubao.com",
        "idlePath": "/chat",
        "taskPath": "/chat/create-image",
        "profileSubdir": "",
        "downloadSubdir": "doubao-videos",
        "model": "doubao-web-video",
    },
    DOUBAO_SITE_INTL: {
        "label": "Dola",
        "host": "dola.com",
        "origin": "https://www.dola.com",
        "idlePath": "/chat/",
        "taskPath": "/chat/",
        "profileSubdir": "intl",
        "downloadSubdir": "dola-videos",
        "model": "dola-web-video",
    },
}
DOUBAO_INTL_MODELS = [
    {"label": "国际版豆包网页视频 - 自动", "value": "dola-web-video"},
]
DOUBAO_ALL_MODELS = DOUBAO_MODELS + DOUBAO_INTL_MODELS

DOUBAO_ACCOUNT_EXPORT_VERSION = 1
DOUBAO_PROFILE_COPY_NAMES = {
    "Local State",
    "Preferences",
    "Secure Preferences",
    "Network",
    "Local Storage",
    "IndexedDB",
    "Session Storage",
    "Service Worker",
    "Sessions",
    "WebStorage",
    "SharedStorage",
    "DIPS",
}
DOUBAO_PROFILE_SKIP_NAMES = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "ShaderCache",
    "Crashpad",
    "BrowserMetrics",
    "BrowserMetrics-spare.pma",
    "lockfile",
    "LOCK",
    "LOG",
    "LOG.old",
}
DOUBAO_CHROMIUM_PROFILE_NAMES = {"default", "guest profile", "system profile"}

_STATE_LOCK = threading.RLock()
_SERVER: Optional[ThreadingHTTPServer] = None
_SERVER_THREAD: Optional[threading.Thread] = None
_LOCK = {"token": "", "port": 0, "expiresAt": 0}
_PLUGIN_HEARTBEAT: dict[str, Any] = {}
_ACCOUNT_PLUGIN_HEARTBEATS: dict[str, dict[str, Any]] = {}
_BROWSER_NAV_LOCK = threading.RLock()
_TASK_PAGE_OPEN_REQUESTS: dict[str, int] = {}
_ACCOUNT_BROWSER_LAUNCH_REQUESTS: dict[str, int] = {}
_PENDING_TASK_PAGE_OPENS: dict[str, int] = {}
_READY_OPEN_REQUESTS: dict[str, int] = {}
_ACCOUNT_KEEPALIVE_INJECTS: dict[str, int] = {}
_ACCOUNT_KEEPALIVE_SWEEPS: dict[str, int] = {}
PLUGIN_HEARTBEAT_TTL_MS = 20_000
ACCOUNT_HEARTBEAT_RETENTION_MS = 6 * 60 * 60 * 1000
ACCOUNT_KEEPALIVE_INJECT_DEBOUNCE_MS = 15_000
ACCOUNT_KEEPALIVE_SWEEP_DEBOUNCE_MS = 45_000
ACCOUNT_PLUGIN_RECOVERY_STALE_MS = 35_000
ACCOUNT_BROWSER_REOPEN_STALE_MS = 3 * 60 * 1000
ACCOUNT_SOFT_ISSUE_CONFIRMATIONS = 3
ACCOUNT_SOFT_ISSUE_WINDOW_MS = 18 * 60 * 1000
ACCOUNT_SOFT_COOLDOWN_MS = 2 * 60 * 1000
TASK_HEARTBEAT_STALE_MS = 60_000
DOUBAO_ACTIVE_RESERVATION_TTL_MS = 35 * 60 * 1000
DOUBAO_IDLE_RESERVATION_STALE_MS = 2 * 60 * 1000
DOUBAO_ACTIVE_SIGNAL_STALE_MS = 8 * 60 * 1000
DOUBAO_DOWNLOAD_RESERVATION_STALE_MS = 10 * 60 * 1000
DOUBAO_READY_OPEN_DEBOUNCE_MS = 20_000
DOUBAO_TASK_PAGE_OPEN_DEBOUNCE_MS = 25_000
DOUBAO_AUTOMATION_RETRY_LIMIT = 3
DOUBAO_RESUME_DOWNLOAD_RETRY_LIMIT = 5
DOUBAO_LLM_RECOVERY_CACHE_TTL_MS = 45_000
DOUBAO_LLM_RECOVERY_TEXT_LIMIT = 4200
DOUBAO_LLM_RECOVERY_ALLOWED_ACTIONS = {
    "no_action",
    "wait_generation",
    "reply_continue",
    "activate_result",
    "refresh_result",
    "resubmit_prompt",
    "redirect_video_surface",
    "reply_correction",
    "bind_candidate",
    "fail_hard",
}
_LLM_DECISION_CACHE: dict[str, dict[str, Any]] = {}


def _use_packaged_multi_open_browser() -> bool:
    # Doubao runs in VMO's localized Chromium runtime by default. The original
    # exe is only a source bundle used for extraction/reference, never a runtime
    # target for the account pool.
    env = str(os.environ.get("DOUBAO_USE_PACKAGED_BROWSER", "")).strip().lower()
    if env in {"force", "activated", "1", "true", "yes", "on"}:
        return PACKAGED_BROWSER_CHROME.exists()
    if env in {"0", "false", "no", "off"}:
        return False
    try:
        settings = (_read_state().get("settings") or {})
        if "usePackagedBrowser" in settings:
            return bool(settings.get("usePackagedBrowser")) and PACKAGED_BROWSER_CHROME.exists()
    except Exception:
        pass
    return PACKAGED_BROWSER_CHROME.exists()


def _is_packaged_multi_open_browser_path(path: str) -> bool:
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
        return resolved == PACKAGED_BROWSER_CHROME.resolve()
    except Exception:
        normalized = str(path).strip().lower()
        return normalized == str(PACKAGED_BROWSER_CHROME).strip().lower()


def _is_source_browser_bundle_path(path: str) -> bool:
    if not path:
        return False
    try:
        return Path(path).resolve() == PACKAGED_MULTI_OPEN_BROWSER.resolve()
    except Exception:
        return str(path).strip().lower() == str(PACKAGED_MULTI_OPEN_BROWSER).strip().lower()


class DoubaoPoolError(Exception):
    pass


def _default_state() -> dict:
    return {
        "rootDir": str(POOL_DIR / "profiles"),
        "accounts": [],
        "settings": {
            "usePackagedBrowser": True,
            "automationMode": DOUBAO_AUTOMATION_MODE_FULL,
        },
    }


def _read_state() -> dict:
    state = read_json(POOL_FILE, _default_state())
    state.setdefault("rootDir", str(POOL_DIR / "profiles"))
    state.setdefault("accounts", [])
    state.setdefault("settings", {})
    state["settings"].setdefault("usePackagedBrowser", True)
    state["settings"]["automationMode"] = _normalize_automation_mode(state["settings"].get("automationMode"))
    return state


def _write_state(state: dict) -> dict:
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    write_json(POOL_FILE, state)
    return state


def update_settings(patch: dict) -> dict:
    with _STATE_LOCK:
        state = _read_state()
        settings = dict(state.get("settings") or {})
        if "usePackagedBrowser" in patch:
            settings["usePackagedBrowser"] = bool(patch.get("usePackagedBrowser"))
        if "automationMode" in patch:
            settings["automationMode"] = _normalize_automation_mode(patch.get("automationMode"))
        state["settings"] = settings
        _write_state(state)
    _write_plugin_install_record(changed=True)
    return list_pool()


def _read_tasks() -> dict:
    data = read_json(TASKS_FILE, {"tasks": []})
    data.setdefault("tasks", [])
    return data


def _write_tasks(data: dict) -> dict:
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    write_json(TASKS_FILE, data)
    return data


def _now_ms() -> int:
    return int(time.time() * 1000)


def normalize_site(value: Any = "") -> str:
    raw = str(value or "").strip().lower()
    if raw in {DOUBAO_SITE_INTL, "dola", "global", "international", "oversea", "overseas", "www.dola.com"}:
        return DOUBAO_SITE_INTL
    if raw in {DOUBAO_SITE_CN, "doubao", "china", "domestic", "www.doubao.com"}:
        return DOUBAO_SITE_CN
    if "dola" in raw:
        return DOUBAO_SITE_INTL
    return DOUBAO_SITE_DEFAULT


def _account_site(account: Optional[dict]) -> str:
    return normalize_site((account or {}).get("site") or (account or {}).get("doubaoSite") or DOUBAO_SITE_DEFAULT)


def _task_site(task: Optional[dict]) -> str:
    return normalize_site((task or {}).get("site") or (task or {}).get("doubaoSite") or DOUBAO_SITE_DEFAULT)


def _site_config(site: Any = "") -> dict:
    return DOUBAO_SITE_CONFIGS.get(normalize_site(site), DOUBAO_SITE_CONFIGS[DOUBAO_SITE_DEFAULT])


def _site_label(site: Any = "") -> str:
    return str(_site_config(site).get("label") or "Doubao")


def _site_host(site: Any = "") -> str:
    return str(_site_config(site).get("host") or "doubao.com")


def _account_matches_site(account: dict, site: Any = "") -> bool:
    return _account_site(account) == normalize_site(site)


def _account_heartbeat_key(account_id: str = "", site: Any = "") -> str:
    account_id = str(account_id or "").strip()
    if not account_id:
        return ""
    return f"{normalize_site(site)}:{account_id}"


def _heartbeat_site_from_payload(payload: dict, task_id: str = "") -> str:
    explicit = _payload_value(payload, "site", "doubaoSite", "dbvdSite")
    if explicit:
        return normalize_site(explicit)
    url = str((payload or {}).get("url") or "")
    if url and _url_matches_site(url, DOUBAO_SITE_INTL):
        return DOUBAO_SITE_INTL
    if url and _url_matches_site(url, DOUBAO_SITE_CN):
        return DOUBAO_SITE_CN
    if task_id:
        return _task_site(get_task(task_id))
    return DOUBAO_SITE_DEFAULT


def _account_heartbeat_record(account_id: str = "", site: Any = "") -> dict:
    account_id = str(account_id or "").strip()
    if not account_id:
        return {}
    heartbeat = _ACCOUNT_PLUGIN_HEARTBEATS.get(_account_heartbeat_key(account_id, site))
    if heartbeat:
        return heartbeat
    legacy = _ACCOUNT_PLUGIN_HEARTBEATS.get(account_id)
    if legacy and normalize_site((legacy or {}).get("site") or site) == normalize_site(site):
        return legacy
    return {}


def _site_page_url(site: Any, account_id: str = "", task_id: str = "") -> str:
    cfg = _site_config(site)
    origin = str(cfg.get("origin") or "").rstrip("/")
    path = str(cfg.get("taskPath") if task_id else cfg.get("idlePath") or "/chat")
    url = urllib.parse.urljoin(f"{origin}/", path.lstrip("/"))
    params: dict[str, str] = {}
    if task_id:
        params["dbvdTaskId"] = str(task_id)
    if account_id:
        params["dbvdAccountId"] = str(account_id)
    if params:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    return url


def _url_matches_site(url: Any, site: Any = "") -> bool:
    target = normalize_site(site)
    try:
        host = urllib.parse.urlparse(str(url or "")).hostname or ""
    except Exception:
        host = ""
    host = host.lower()
    if target == DOUBAO_SITE_INTL:
        return host == "dola.com" or host.endswith(".dola.com")
    return host == "doubao.com" or host.endswith(".doubao.com")


def _account_dir(state: dict, account: dict) -> Path:
    root = Path(state.get("rootDir") or (POOL_DIR / "profiles"))
    subdir = str(_site_config(_account_site(account)).get("profileSubdir") or "")
    if subdir:
        root = root / subdir
    return root / str(account["id"])


def _download_dir(account: dict) -> Path:
    raw = account.get("downloadDir")
    if raw:
        return Path(raw)
    subdir = str(_site_config(_account_site(account)).get("downloadSubdir") or "doubao-videos")
    return POOL_DIR / subdir / str(account["id"])


def _debug_port(account: dict) -> int:
    raw = f"{_account_site(account)}:{account.get('id') or ''}"
    digest = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()
    return 43000 + (int(digest[:6], 16) % 1000)


def _task_active_statuses() -> set[str]:
    return {"queued", "claimed", "prepared", "submitted", "downloading"}


def _task_terminal_statuses() -> set[str]:
    return {"downloaded", "completed", "failed", "cancelled", "manual"}


def _normalize_automation_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"semi", "semi-auto", "semiauto", "manual", "assist"}:
        return DOUBAO_AUTOMATION_MODE_SEMI
    if mode in {"full", "auto", "automatic"}:
        return DOUBAO_AUTOMATION_MODE_FULL
    return DOUBAO_AUTOMATION_MODE_FULL


def _state_automation_mode(state: dict) -> str:
    return _normalize_automation_mode((state.get("settings") or {}).get("automationMode"))


def _task_automation_mode(task: Optional[dict]) -> str:
    if not isinstance(task, dict):
        return DOUBAO_AUTOMATION_MODE_FULL
    return _normalize_automation_mode(
        task.get("doubaoAutomationMode")
        or task.get("automationMode")
        or task.get("automation_mode")
    )


def _is_semi_auto_task(task: Optional[dict]) -> bool:
    return _task_automation_mode(task) == DOUBAO_AUTOMATION_MODE_SEMI


def _account_capacity(account: dict) -> int:
    try:
        return max(0, int(account.get("quota") or 0))
    except Exception:
        return 0


def _account_local_quota(account: dict) -> int:
    try:
        return max(0, int(account.get("quota") or 0))
    except Exception:
        return 0


def _account_used(account: dict) -> int:
    try:
        return max(0, int(account.get("used") or 0))
    except Exception:
        return 0


def _clear_account_exhaustion(account: dict, *, clear_remote: bool = True) -> None:
    account.pop("lastExhaustedAt", None)
    account.pop("exhaustedReason", None)
    account.pop("accountHealthState", None)
    account.pop("accountIssueKind", None)
    account.pop("accountIssueReason", None)
    account.pop("accountIssueFirstAt", None)
    account.pop("accountIssueLastAt", None)
    account.pop("accountIssueCount", None)
    account.pop("accountIssueKeys", None)
    account.pop("accountCooldownUntil", None)
    if clear_remote:
        account.pop("remoteCreditRemaining", None)
        account.pop("remoteRemainingVideos", None)
        account.pop("remoteCreditCheckedAt", None)


def _account_cooldown_until(account: dict) -> int:
    try:
        return max(0, int(account.get("accountCooldownUntil") or 0))
    except Exception:
        return 0


def _account_in_cooldown(account: dict, now_ms: Optional[int] = None) -> bool:
    marker = now_ms or _now_ms()
    return bool(_account_cooldown_until(account) > marker)


def _account_issue_count(account: dict) -> int:
    try:
        return max(0, int(account.get("accountIssueCount") or 0))
    except Exception:
        return 0


def _account_confirmed_exhausted(account: dict) -> bool:
    return bool(
        account.get("exhaustedReason")
        and _account_remote_remaining(account) == 0
        and _account_issue_count(account) >= ACCOUNT_SOFT_ISSUE_CONFIRMATIONS
    )


def _account_login_required(account: dict) -> bool:
    return str(account.get("accountHealthState") or "") == "login-required"


def _account_health_snapshot(account: dict) -> dict:
    now = _now_ms()
    cooldown_until = _account_cooldown_until(account)
    issue_count = _account_issue_count(account)
    if _account_login_required(account):
        status = "login-required"
    elif _account_confirmed_exhausted(account):
        status = "exhausted"
    elif cooldown_until > now:
        status = "cooldown"
    elif str(account.get("accountHealthState") or "") == "suspect":
        status = "suspect"
    else:
        status = "ok"
    return {
        "accountHealthStatus": status,
        "accountCooldownUntil": cooldown_until,
        "accountCooldownRemainingMs": max(0, cooldown_until - now),
        "accountIssueKind": account.get("accountIssueKind") or "",
        "accountIssueReason": account.get("accountIssueReason") or account.get("exhaustedReason") or "",
        "accountIssueCount": issue_count,
        "accountIssueLastAt": int(account.get("accountIssueLastAt") or account.get("lastExhaustedAt") or 0),
    }


def _refresh_account_health_state(account: dict, now_ms: Optional[int] = None) -> bool:
    now = now_ms or _now_ms()
    changed = False
    last_issue = int(account.get("accountIssueLastAt") or 0)
    if last_issue and now - last_issue > ACCOUNT_SOFT_ISSUE_WINDOW_MS:
        for key in ("accountIssueFirstAt", "accountIssueLastAt", "accountIssueCount", "accountIssueKeys"):
            if key in account:
                account.pop(key, None)
                changed = True
        if str(account.get("accountHealthState") or "") in {"suspect", "cooldown"} and not _account_confirmed_exhausted(account):
            for key in ("accountHealthState", "accountIssueKind", "accountIssueReason", "accountCooldownUntil", "exhaustedReason"):
                account.pop(key, None)
            changed = True
    cooldown_until = _account_cooldown_until(account)
    if cooldown_until and cooldown_until <= now and str(account.get("accountHealthState") or "") == "cooldown":
        account["accountHealthState"] = "suspect" if _account_issue_count(account) else "ok"
        changed = True
    return changed


def _refresh_accounts_health_state(state: dict) -> bool:
    now = _now_ms()
    changed = False
    for account in state.get("accounts", []):
        changed = _refresh_account_health_state(account, now) or changed
    return changed


def _mark_account_issue_in_state(
    state: dict,
    account_id: str,
    reason: str = "",
    *,
    site: Any = None,
    kind: str = "low-credit",
    issue_key: str = "",
    hard: bool = False,
) -> bool:
    now = _now_ms()
    confirmed = False
    target_site = normalize_site(site) if site is not None else ""
    for account in state.get("accounts", []):
        if str(account.get("id") or "") != str(account_id or ""):
            continue
        if target_site and _account_site(account) != target_site:
            continue
        _refresh_account_health_state(account, now)
        keys = [str(item) for item in (account.get("accountIssueKeys") or []) if str(item)]
        if not issue_key:
            issue_key = f"{kind}:{now}"
        first_at = int(account.get("accountIssueFirstAt") or 0)
        last_at = int(account.get("accountIssueLastAt") or 0)
        count = _account_issue_count(account)
        if not first_at or not last_at or now - last_at > ACCOUNT_SOFT_ISSUE_WINDOW_MS:
            first_at = now
            count = 0
            keys = []
        if issue_key not in keys:
            count += 1
            keys.append(issue_key)
        keys = keys[-8:]
        confirmed = bool(hard or count >= ACCOUNT_SOFT_ISSUE_CONFIRMATIONS)
        account["accountHealthState"] = "exhausted" if confirmed else "cooldown"
        account["accountIssueKind"] = kind
        account["accountIssueReason"] = reason or kind
        account["accountIssueFirstAt"] = first_at
        account["accountIssueLastAt"] = now
        account["accountIssueCount"] = count
        account["accountIssueKeys"] = keys
        account["accountCooldownUntil"] = now + ACCOUNT_SOFT_COOLDOWN_MS
        account["remoteCreditCheckedAt"] = now
        if confirmed:
            account["remoteCreditRemaining"] = 0
            account["remoteRemainingVideos"] = 0
            account["lastExhaustedAt"] = now
            account["exhaustedReason"] = reason or "网页提示额度不足"
        else:
            account.pop("remoteRemainingVideos", None)
            account["exhaustedReason"] = f"临时冷却：{reason or kind}"
        account["updatedAt"] = now
        break
    return confirmed


def _account_remote_remaining(account: dict) -> Optional[int]:
    remote = account.get("remoteRemainingVideos")
    if remote in (None, ""):
        return None
    try:
        return max(0, int(remote))
    except Exception:
        return None


def _account_remote_credit_remaining(account: dict) -> Optional[float]:
    return _to_float(account.get("remoteCreditRemaining"))


def _task_recent_automation(task: dict, now_ms: Optional[int] = None) -> bool:
    now = now_ms or _now_ms()
    task_id = str((task or {}).get("id") or "")
    account_id = str((task or {}).get("accountId") or "")
    site = _task_site(task)
    heartbeat_at = int((task or {}).get("automationHeartbeatAt") or 0)
    if heartbeat_at and now - heartbeat_at <= TASK_HEARTBEAT_STALE_MS:
        return True
    candidates = []
    if account_id:
        candidates.append(_account_heartbeat_record(account_id, site))
    candidates.append(_PLUGIN_HEARTBEAT or {})
    for heartbeat in candidates:
        last_seen = int((heartbeat or {}).get("lastSeenAt") or 0)
        heartbeat_task_id = str((heartbeat or {}).get("taskId") or "")
        if last_seen and now - last_seen <= PLUGIN_HEARTBEAT_TTL_MS and task_id and heartbeat_task_id == task_id:
            return True
    return False


def _task_time_marker(task: dict) -> int:
    values = []
    for key in ("automationHeartbeatAt", "updatedAt", "createdAt"):
        try:
            value = int((task or {}).get(key) or 0)
        except Exception:
            value = 0
        if value:
            values.append(value)
    return max(values) if values else 0


def _task_reservation_expired(task: dict, *, now_ms: Optional[int] = None) -> bool:
    status = str((task or {}).get("status") or "")
    if status not in _task_active_statuses():
        return False
    now = now_ms or _now_ms()
    marker = _task_time_marker(task)
    if not marker:
        return False
    if _task_recent_automation(task, now):
        return False
    stale_for = now - marker
    if stale_for <= 0:
        return False
    if status == "queued":
        return stale_for > DOUBAO_IDLE_RESERVATION_STALE_MS
    if status in {"claimed", "prepared"}:
        if _task_has_submission_anchor(task) or _task_has_generation_signal(task):
            return stale_for > DOUBAO_ACTIVE_SIGNAL_STALE_MS
        return stale_for > DOUBAO_IDLE_RESERVATION_STALE_MS
    if status == "submitted":
        if _task_has_result_binding(task) or _task_has_submission_anchor(task) or _task_has_generation_signal(task):
            return stale_for > DOUBAO_ACTIVE_SIGNAL_STALE_MS
        return stale_for > DOUBAO_IDLE_RESERVATION_STALE_MS
    if status == "downloading":
        return stale_for > DOUBAO_DOWNLOAD_RESERVATION_STALE_MS
    created_at = int((task or {}).get("createdAt") or 0)
    return bool(created_at and now - created_at > DOUBAO_ACTIVE_RESERVATION_TTL_MS)


def _active_task_counts(
    tasks_data: Optional[dict] = None,
    *,
    ignore_task_id: str = "",
    site: Any = None,
) -> dict[str, int]:
    data = tasks_data if isinstance(tasks_data, dict) else _read_tasks()
    now = _now_ms()
    counts: dict[str, int] = {}
    target_site = normalize_site(site) if site is not None else ""
    for task in data.get("tasks", []):
        if ignore_task_id and str(task.get("id") or "") == str(ignore_task_id):
            continue
        if target_site and _task_site(task) != target_site:
            continue
        status = str(task.get("status") or "")
        account_id = str(task.get("accountId") or "")
        expired = _task_reservation_expired(task, now_ms=now)
        if account_id and status in _task_active_statuses() and not expired:
            counts[account_id] = counts.get(account_id, 0) + 1
    return counts


def _account_remaining_slots(account: dict, active_counts: Optional[dict[str, int]] = None) -> int:
    active = active_counts or {}
    account_id = str(account.get("id") or "")
    local_remaining = max(0, _account_capacity(account) - _account_used(account))
    remote_remaining = _account_remote_remaining(account)
    remote_credit = _account_remote_credit_remaining(account)
    if remote_remaining is None and remote_credit is not None and remote_credit > 0:
        remote_remaining = max(0, int(remote_credit // DOUBAO_CREDITS_PER_VIDEO))
    # If Doubao has just reported positive remote credits, do not let an old
    # local usage counter make the account look dead. Local quota remains the
    # fallback when the page has not reported a usable remote balance.
    if remote_remaining is not None:
        remaining = remote_remaining if remote_credit is not None and remote_credit > 0 else min(local_remaining, remote_remaining)
    else:
        remaining = local_remaining
    return max(0, remaining - int(active.get(account_id, 0) or 0))


def _account_has_slots(account: dict, active_counts: Optional[dict[str, int]] = None) -> bool:
    return bool(
        account.get("enabled", True)
        and not _account_login_required(account)
        and not _account_in_cooldown(account)
        and not _account_confirmed_exhausted(account)
        and _account_remaining_slots(account, active_counts) > 0
    )


def _pick_account_with_capacity(state: dict, *, exclude_ids: Optional[set[str]] = None,
                                site: str = DOUBAO_SITE_DEFAULT,
                                active_counts: Optional[dict[str, int]] = None) -> Optional[dict]:
    blocked = exclude_ids or set()
    site = normalize_site(site)
    _refresh_accounts_health_state(state)
    counts = active_counts if active_counts is not None else _active_task_counts(site=site)
    candidates = [
        account for account in state.get("accounts", [])
        if account.get("enabled", True)
        and _account_matches_site(account, site)
        and str(account.get("id") or "") not in blocked
        and _account_has_slots(account, counts)
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda a: (int(a.get("lastUsedAt") or 0), -_account_remaining_slots(a, counts)),
    )[0]


def _is_low_credit_signal(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    return any(marker in compact for marker in DOUBAO_LOW_CREDIT_PATTERNS)


def _is_login_required_signal(text: str) -> bool:
    return bool(re.search(r"(登录已过期|请先登录|重新登录|账号异常|未登录|login\s*required|sign\s*in)", str(text or ""), re.I))


def _has_doubao_completed_signal(text: Any) -> bool:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return bool(re.search(
        r"(?:你的视频生成好(?:啦|了)?(?!后|之后|以后)|"
        r"视频生成好(?:啦|了)?(?!后|之后|以后)|"
        r"生成好(?:啦|了)(?!后|之后|以后)|"
        r"生成完成(?!后|之后|以后)|"
        r"视频已生成(?!后|之后|以后)|"
        r"已生成视频(?!后|之后|以后)|"
        r"创作完成(?!后|之后|以后)|"
        r"下载视频|保存视频)",
        value,
        re.I,
    ))


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _is_soft_submit_error(text: str) -> bool:
    value = str(text or "")
    return bool(re.search(
        r"提示词未成功写入|页面未确认提交|提交前提示词为空|未找到豆包提示词输入框|"
        r"上传参考图后未找到|DOUBAO_FIXED_VIDEO_DURATION_SECONDS|is not defined|"
        r"生成超时|未检测到可下载视频|视频下载失败|自动回收失败|下载失败|未检测到结果",
        value,
        re.I,
    ))


def _task_has_generation_signal(task: Optional[dict]) -> bool:
    if not isinstance(task, dict):
        return False
    page_status = str(task.get("doubaoPageStatus") or "").strip()
    if page_status in {"generating", "queued-signal", "duration-confirm", "completed-signal"}:
        return True
    stage = str(task.get("stage") or "").strip()
    if stage in {
        "doubao-generating", "doubao-queued", "doubao-duration-confirm",
        "doubao-completed", "waiting-result",
    }:
        return True
    progress = _to_float(task.get("doubaoProgress"))
    if progress is not None and progress > 0 and page_status != "failed-signal":
        return True
    credit_cost = _to_float(task.get("doubaoCreditCost"))
    if credit_cost is not None and credit_cost > 0 and page_status != "failed-signal":
        return True
    credit_remaining = _to_float(task.get("doubaoCreditRemaining"))
    if credit_remaining is not None and credit_remaining > 0 and page_status != "failed-signal":
        return True
    return False


def _task_has_submission_anchor(task: Optional[dict]) -> bool:
    if not isinstance(task, dict):
        return False
    guard = task.get("resultGuard") or {}
    if isinstance(guard, dict):
        for key in ("submittedAt", "anchorTop", "anchorBottom", "maxMessageIdBefore", "promptFingerprint"):
            if str(guard.get(key) or "").strip():
                return True
    before_keys = task.get("beforeVideoKeys")
    return bool(isinstance(before_keys, list) and before_keys)


def _compact_text(value: Any, limit: int = DOUBAO_LLM_RECOVERY_TEXT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    head = max(800, limit // 4)
    tail = max(1200, limit - head - 32)
    return f"{text[:head]} ... {text[-tail:]}"


def _normalize_llm_decision(value: Any, *, source: str = "llm") -> dict:
    raw = value if isinstance(value, dict) else {}
    action = str(raw.get("action") or "").strip().lower().replace("-", "_")
    aliases = {
        "continue": "reply_continue",
        "confirm_continue": "reply_continue",
        "reply": "reply_continue",
        "wait": "wait_generation",
        "generating": "wait_generation",
        "completed": "activate_result",
        "download": "activate_result",
        "refresh": "refresh_result",
        "retry": "resubmit_prompt",
        "resubmit": "resubmit_prompt",
        "correct": "reply_correction",
        "correction": "reply_correction",
        "redirect": "redirect_video_surface",
        "video_surface": "redirect_video_surface",
        "bind": "bind_candidate",
        "select_candidate": "bind_candidate",
        "capture": "activate_result",
        "trigger_capture": "activate_result",
        "fail": "fail_hard",
        "error": "fail_hard",
    }
    action = aliases.get(action, action)
    if action not in DOUBAO_LLM_RECOVERY_ALLOWED_ACTIONS:
        action = "no_action"
    try:
        confidence = float(raw.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    retry_delay = raw.get("retryDelayMs")
    try:
        retry_delay = int(float(retry_delay))
    except (TypeError, ValueError):
        retry_delay = 0
    decision = {
        "action": action,
        "reply": _compact_text(raw.get("reply") or "", 220),
        "reason": _compact_text(raw.get("reason") or "", 360),
        "confidence": confidence,
        "retryDelayMs": max(0, min(120_000, retry_delay)),
        "source": source,
    }
    if raw.get("candidateIndex") not in (None, ""):
        try:
            decision["candidateIndex"] = max(0, int(float(raw.get("candidateIndex"))))
        except (TypeError, ValueError):
            pass
    if raw.get("candidateKey"):
        decision["candidateKey"] = str(raw.get("candidateKey") or "")[:260]
    if raw.get("candidateReason"):
        decision["candidateReason"] = _compact_text(raw.get("candidateReason") or "", 360)
    return decision


def _rule_doubao_llm_decision(payload: dict) -> dict:
    text = _compact_text(payload.get("text") or payload.get("sample") or "", DOUBAO_LLM_RECOVERY_TEXT_LIMIT)
    page_status_obj = payload.get("pageStatus") if isinstance(payload.get("pageStatus"), dict) else {}
    page_status = str(page_status_obj.get("status") or "").strip()
    stage = str(payload.get("stage") or page_status_obj.get("stage") or "").strip().lower()
    surface = page_status_obj.get("surface") if isinstance(page_status_obj.get("surface"), dict) else {}
    active_status = page_status in {"generating", "queued-signal", "duration-confirm", "completed-signal"}
    target_seconds = int(_to_float(payload.get("targetSeconds")) or DOUBAO_FIXED_VIDEO_DURATION_SECONDS)
    if re.search("(\u767b\u5f55\u5df2\u8fc7\u671f|\u8bf7\u5148\u767b\u5f55|\u91cd\u65b0\u767b\u5f55|\u8d26\u53f7\u5f02\u5e38|\u672a\u767b\u5f55|login required)", text, re.I):
        return _normalize_llm_decision({
            "action": "fail_hard",
            "reason": "\u8c46\u5305\u9875\u9762\u63d0\u793a\u767b\u5f55\u72b6\u6001\u5f02\u5e38\uff0c\u9700\u8981\u91cd\u65b0\u767b\u5f55\u5bf9\u5e94\u8d26\u53f7\u3002",
            "confidence": 0.96,
        }, source="rule")
    if _is_low_credit_signal(text) and not active_status:
        return _normalize_llm_decision({
            "action": "fail_hard",
            "reason": "\u8c46\u5305\u9875\u9762\u63d0\u793a\u989d\u5ea6\u4e0d\u8db3\uff0c\u4ea4\u7ed9\u8d26\u53f7\u6c60\u5207\u6362\u53ef\u7528\u8d26\u53f7\u3002",
            "confidence": 0.94,
        }, source="rule")
    if surface and not active_status and bool(surface.get("imageMode")):
        return _normalize_llm_decision({
            "action": "redirect_video_surface",
            "reason": "\u5f53\u524d\u8f93\u5165\u533a\u4f3c\u4e4e\u5904\u4e8e\u56fe\u50cf\u751f\u6210\u6a21\u5f0f\uff0c\u9700\u8981\u5148\u5207\u56de\u89c6\u9891\u751f\u6210\u6a21\u5f0f\u518d\u63d0\u4ea4\u3002",
            "confidence": 0.93,
        }, source="rule")
    if surface and not active_status and any(token in stage for token in ("submit", "prompt", "upload", "surface")):
        if surface.get("hasAnyInput") and not surface.get("hasVideoInput") and not surface.get("videoSurface"):
            return _normalize_llm_decision({
                "action": "redirect_video_surface",
                "reason": "\u5f53\u524d\u9875\u9762\u6ca1\u6709\u68c0\u6d4b\u5230\u53ef\u7528\u7684\u89c6\u9891\u751f\u6210\u8f93\u5165\u533a\uff0c\u5148\u5207\u56de\u89c6\u9891\u751f\u6210\u9762\u677f\u3002",
                "confidence": 0.84,
            }, source="rule")
    if re.search("(\u662f\u5426|\u8981\u4e0d\u8981|\u786e\u8ba4|\u786e\u5b9a|\u8c03\u6574\u4e3a|\u6539\u4e3a).{0,24}(\u7ee7\u7eed|\u751f\u6210)|\u7ee7\u7eed\u751f\u6210|continue generating", text, re.I):
        return _normalize_llm_decision({
            "action": "reply_continue",
            "reply": f"\u662f\uff0c\u751f\u6210{target_seconds}\u79d2\u89c6\u9891",
            "reason": "\u9875\u9762\u5728\u8be2\u95ee\u662f\u5426\u7ee7\u7eed\u751f\u6210\uff0c\u81ea\u52a8\u786e\u8ba4\u76ee\u6807\u65f6\u957f\u3002",
            "confidence": 0.97,
        }, source="rule")
    if _has_doubao_completed_signal(text):
        return _normalize_llm_decision({
            "action": "activate_result",
            "reason": "\u9875\u9762\u63d0\u793a\u89c6\u9891\u5df2\u5b8c\u6210\uff0c\u8fdb\u5165\u7ed3\u679c\u6fc0\u6d3b\u548c\u6293\u53d6\u3002",
            "confidence": 0.97,
        }, source="rule")
    if re.search("(\u7f51\u7edc\u5f02\u5e38|\u8bf7\u6c42\u5931\u8d25|\u670d\u52a1\u5f02\u5e38|\u7a0d\u540e\u91cd\u8bd5|\u9875\u9762\u672a\u66f4\u65b0|\u5361\u4f4f|\u5237\u65b0)", text):
        return _normalize_llm_decision({
            "action": "refresh_result",
            "reason": "\u9875\u9762\u7591\u4f3c\u5361\u4f4f\u6216\u8bf7\u6c42\u5f02\u5e38\uff0c\u5efa\u8bae\u5237\u65b0\u5f53\u524d\u4efb\u52a1\u9875\u540e\u7ee7\u7eed\u6293\u53d6\u3002",
            "confidence": 0.91,
            "retryDelayMs": 8000,
        }, source="rule")
    if not active_status and re.search("(\u751f\u6210\u56fe\u7247|\u56fe\u50cf\u6a21\u5f0f|Seedream|AI\\s*\u4fee\u56fe|\u63cf\u8ff0\u4f60\u60f3\u8981\u7684\u56fe\u7247)", text, re.I):
        return _normalize_llm_decision({
            "action": "redirect_video_surface",
            "reason": "\u9875\u9762\u6587\u672c\u66f4\u50cf\u56fe\u50cf\u751f\u6210\u6d41\u7a0b\uff0c\u5efa\u8bae\u5207\u56de\u89c6\u9891\u751f\u6210\u6a21\u5f0f\u3002",
            "confidence": 0.82,
        }, source="rule")
    if re.search("(\u4ee5\u4e0b\u662f|\u4e3a\u4f60\u751f\u6210|\u5efa\u8bae\u4f60).{0,60}(\u89c6\u9891\u63d0\u793a\u8bcd|\u5206\u955c|\u955c\u5934|\u8fd0\u955c)|(\u89c6\u9891\u63d0\u793a\u8bcd|\u5206\u955c\u811a\u672c|\u955c\u5934\u8bed\u8a00).{0,80}(\u53ef\u4ee5|\u5982\u4e0b)", text):
        return _normalize_llm_decision({
            "action": "reply_correction",
            "reply": f"\u4e0d\u8981\u8f93\u51fa\u63d0\u793a\u8bcd\u6216\u6587\u6848\uff0c\u8bf7\u76f4\u63a5\u751f\u6210{target_seconds}\u79d2\u89c6\u9891\u3002",
            "reason": "\u9875\u9762\u4f3c\u4e4e\u8f93\u51fa\u4e86\u63d0\u793a\u8bcd\u6216\u6587\u6848\uff0c\u800c\u4e0d\u662f\u5f00\u59cb\u751f\u6210\u89c6\u9891\u3002",
            "confidence": 0.86,
        }, source="rule")
    if re.search(r"(登录已过期|请先登录|重新登录|账号异常|未登录|login required)", text, re.I):
        return _normalize_llm_decision({
            "action": "fail_hard",
            "reason": "豆包页面提示登录状态异常，需要重新登录对应账号。",
            "confidence": 0.96,
        }, source="rule")
    if _is_low_credit_signal(text) and not active_status:
        return _normalize_llm_decision({
            "action": "fail_hard",
            "reason": "豆包页面提示额度不足，交给账号池切换可用账号。",
            "confidence": 0.94,
        }, source="rule")
    if re.search(r"(是否|要不要|确认|确定|调整为|改为).{0,24}(继续|生成)|继续生成|continue generating", text, re.I):
        return _normalize_llm_decision({
            "action": "reply_continue",
            "reply": f"是，生成{target_seconds}秒视频",
            "reason": "页面在询问是否继续生成，自动确认目标时长。",
            "confidence": 0.9,
        }, source="rule")
    if _has_doubao_completed_signal(text):
        return _normalize_llm_decision({
            "action": "activate_result",
            "reason": "页面提示视频已完成，进入结果激活和抓取。",
            "confidence": 0.9,
        }, source="rule")
    if page_status in {"generating", "queued-signal", "duration-confirm", "completed-signal"}:
        return _normalize_llm_decision({
            "action": "wait_generation" if page_status != "completed-signal" else "activate_result",
            "reason": "规则已识别到豆包仍在生成或已完成。",
            "confidence": 0.86,
        }, source="rule")
    if re.search(r"(网络异常|请求失败|服务异常|稍后重试|页面未更新|卡住|刷新)", text):
        return _normalize_llm_decision({
            "action": "refresh_result",
            "reason": "页面疑似卡住或请求异常，建议刷新当前任务页后继续抓取。",
            "confidence": 0.82,
            "retryDelayMs": 8000,
        }, source="rule")
    return _normalize_llm_decision({
        "action": "no_action",
        "reason": "规则未发现需要自动恢复的明确提示。",
        "confidence": 0.2,
    }, source="rule")


def _doubao_llm_decide(payload: dict) -> dict:
    payload = payload or {}
    text = _compact_text(payload.get("text") or payload.get("sample") or "")
    page_status = payload.get("pageStatus") if isinstance(payload.get("pageStatus"), dict) else {}
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    task_id = str(payload.get("taskId") or task.get("id") or "")
    stage = str(payload.get("stage") or page_status.get("stage") or task.get("stage") or "")
    target_seconds = int(_to_float(payload.get("targetSeconds") or task.get("doubaoTargetDuration") or task.get("targetDuration")) or DOUBAO_FIXED_VIDEO_DURATION_SECONDS)
    rule = _rule_doubao_llm_decision({**payload, "text": text, "targetSeconds": target_seconds})
    # High-confidence deterministic decisions are cheaper and safer than asking the model.
    if rule["action"] != "no_action" and rule["confidence"] >= 0.9:
        return {**rule, "enabled": True, "usedLlm": False}

    candidate_videos = payload.get("candidateVideos") if isinstance(payload.get("candidateVideos"), list) else []
    candidate_signature = []
    for item in candidate_videos[:8]:
        if not isinstance(item, dict):
            continue
        ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
        candidate_signature.append({
            "index": item.get("index"),
            "key": str(item.get("key") or "")[:180],
            "score": _to_float(item.get("score")) or 0,
            "task": str(ids.get("task") or "")[:80],
            "message": str(ids.get("message") or "")[:80],
            "video": str(ids.get("video") or "")[:80],
            "networkTask": str(ids.get("networkTask") or "")[:80],
            "networkTrusted": bool(item.get("networkTrusted")),
        })
    cache_basis = json.dumps({
        "taskId": task_id,
        "stage": stage,
        "status": page_status.get("status"),
        "text": text[-2400:],
        "targetSeconds": target_seconds,
        "decisionKey": str(payload.get("decisionKey") or "")[:260],
        "candidateSignature": candidate_signature,
    }, ensure_ascii=False, sort_keys=True)
    cache_key = hashlib.sha1(cache_basis.encode("utf-8", errors="ignore")).hexdigest()
    now = _now_ms()
    with _STATE_LOCK:
        cached = _LLM_DECISION_CACHE.get(cache_key)
        if cached and now - int(cached.get("at") or 0) < DOUBAO_LLM_RECOVERY_CACHE_TTL_MS:
            decision = dict(cached.get("decision") or {})
            decision["cached"] = True
            return decision

    prompt_preview = _compact_text(payload.get("prompt") or task.get("prompt") or "", 900)
    messages = [
        {
            "role": "system",
            "content": (
                "你是 VMO 豆包网页视频生产自动化的恢复判读器。"
                "你只能根据页面文本和任务状态选择一个白名单动作，返回严格 JSON，不能输出额外文字。"
                "不要索要或处理 cookie、token、账号密码；遇到登录/风控/额度不足/审核拒绝等不可自动恢复情况，选择 fail_hard。"
                "如果页面询问是否继续、是否调整为目标秒数继续生成，选择 reply_continue。"
                "如果仍在排队/生成/预计等待/进度中，选择 wait_generation。"
                "如果已生成/下载/保存视频，选择 activate_result。"
                "如果页面疑似卡住、结果未刷新但没有失败，选择 refresh_result。"
                "如果明确提示提交没有发生、需要重新发送，且没有生成中信号，选择 resubmit_prompt。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "allowedActions": sorted(DOUBAO_LLM_RECOVERY_ALLOWED_ACTIONS),
                "requiredJson": {
                    "action": "one of allowedActions",
                    "reply": "仅 reply_continue 时填写，默认 是，生成目标秒数视频",
                    "reason": "简短中文原因",
                    "confidence": "0-1",
                    "retryDelayMs": "可选，刷新/等待前建议延迟毫秒",
                },
                "task": {
                    "id": task_id,
                    "title": task.get("title") or task.get("shotNo") or task.get("shot_no") or "",
                    "status": task.get("status") or payload.get("taskStatus") or "",
                    "stage": stage,
                    "targetSeconds": target_seconds,
                    "accountId": task.get("accountId") or payload.get("accountId") or "",
                    "hasSubmissionAnchor": _task_has_submission_anchor(task),
                    "hasResultBinding": _task_has_result_binding(task),
                },
                "pageStatus": page_status,
                "ruleHint": rule,
                "promptPreview": prompt_preview,
                "pageTextTail": text,
            }, ensure_ascii=False),
        },
    ]
    messages[0]["content"] = (
        "You are the unattended co-pilot for VMO's Doubao web video automation. "
        "Return strict JSON only, with no markdown and no extra text. "
        "Choose exactly one whitelisted action from the user payload. "
        "Never request or handle cookies, tokens, passwords, or account secrets. "
        "Use fail_hard for login expiry, risk control, insufficient credit, moderation rejection, or other non-recoverable states. "
        "Use reply_continue when the page asks whether to continue or adjust to the target duration and continue. "
        "Use reply_correction when Doubao drifted into chat/explanation/prompt-writing instead of producing the requested video; include a short corrective reply telling it to directly generate the target-duration video and not output prompts. "
        "Use redirect_video_surface when the current page or input is not the video generation surface. "
        "Use wait_generation when the page is still queued, generating, waiting, or showing progress. "
        "Use activate_result when the page says the video is completed or offers download/save. "
        "Use bind_candidate when candidateVideos are provided and one candidate clearly belongs to the current task; include candidateIndex or candidateKey. "
        "Do not use bind_candidate when candidates are ambiguous; return no_action or the relevant recovery action instead. "
        "Use refresh_result when the page appears stale or the result has not refreshed and there is no hard failure. "
        "Use resubmit_prompt only when submission clearly did not happen and there is no active generation signal."
    )
    messages[1]["content"] = json.dumps({
        "allowedActions": sorted(DOUBAO_LLM_RECOVERY_ALLOWED_ACTIONS),
        "requiredJson": {
            "action": "one of allowedActions",
            "reply": "Only for reply_continue. Prefer: yes, generate targetSeconds seconds video.",
            "candidateIndex": "Only for bind_candidate, zero-based index in candidateVideos.",
            "candidateKey": "Only for bind_candidate, stable key from candidateVideos.",
            "reason": "Short reason, preferably Chinese.",
            "confidence": "0-1",
            "retryDelayMs": "Optional delay before refresh/wait.",
        },
        "task": {
            "id": task_id,
            "title": task.get("title") or task.get("shotNo") or task.get("shot_no") or "",
            "status": task.get("status") or payload.get("taskStatus") or "",
            "stage": stage,
            "targetSeconds": target_seconds,
            "accountId": task.get("accountId") or payload.get("accountId") or "",
            "hasSubmissionAnchor": _task_has_submission_anchor(task),
            "hasResultBinding": _task_has_result_binding(task),
        },
        "pageStatus": page_status,
        "ruleHint": rule,
        "candidateVideos": candidate_videos,
        "deterministicCandidate": payload.get("deterministicCandidate") or {},
        "promptPreview": prompt_preview,
        "pageTextTail": text,
    }, ensure_ascii=False)
    try:
        raw = llm_service.chat_json(messages, temperature=0, max_tokens=360, timeout=60)
        decision = _normalize_llm_decision(raw, source="llm")
        if decision["action"] == "reply_continue" and not decision["reply"]:
            decision["reply"] = f"是，生成{target_seconds}秒视频"
        if decision["action"] == "reply_continue" and not re.search("\u79d2|\u89c6\u9891|video", decision.get("reply") or "", re.I):
            decision["reply"] = f"\u662f\uff0c\u751f\u6210{target_seconds}\u79d2\u89c6\u9891"
        if decision["action"] == "reply_correction" and not decision["reply"]:
            decision["reply"] = f"\u8bf7\u76f4\u63a5\u751f\u6210{target_seconds}\u79d2\u89c6\u9891\uff0c\u4e0d\u8981\u8f93\u51fa\u63d0\u793a\u8bcd\u6216\u6587\u6848\u3002"
        if decision["confidence"] < 0.45 and rule["action"] != "no_action":
            decision = {**rule, "source": "rule-after-low-confidence-llm"}
        decision.update({"enabled": True, "usedLlm": decision.get("source") == "llm"})
    except Exception as e:  # noqa: BLE001
        logger.info("[DoubaoPool] LLM recovery decision fallback task=%s stage=%s error=%s", task_id, stage, e)
        decision = {**rule, "enabled": False, "usedLlm": False, "llmError": str(e)[:240]}

    with _STATE_LOCK:
        _LLM_DECISION_CACHE[cache_key] = {"at": now, "decision": dict(decision)}
        stale = [key for key, item in _LLM_DECISION_CACHE.items() if now - int(item.get("at") or 0) > DOUBAO_LLM_RECOVERY_CACHE_TTL_MS * 4]
        for key in stale[:100]:
            _LLM_DECISION_CACHE.pop(key, None)
    return decision


def _task_has_result_binding(task: Optional[dict]) -> bool:
    if not isinstance(task, dict):
        return False
    for key in (
        "downloadId", "downloadFilename", "assetUrl", "backupUrl",
        "doubaoInternalTaskId", "doubaoInternalMessageId", "doubaoInternalVideoId",
        "doubaoResultKey", "networkAnchorTaskId", "networkAnchorAccountId",
    ):
        if str(task.get(key) or "").strip():
            return True
    meta = task.get("doubaoResultMeta") or {}
    return bool(isinstance(meta, dict) and meta.get("resultKeys"))


def _task_account_should_lock(task: Optional[dict]) -> bool:
    if not isinstance(task, dict):
        return False
    status = str(task.get("status") or "")
    if status in {"prepared", "submitted", "downloading", "downloaded", "completed", "manual"}:
        return True
    return _task_has_submission_anchor(task) or _task_has_generation_signal(task) or _task_has_result_binding(task)


def _lock_task_account(task: dict, reason: str = "") -> None:
    account_id = str((task or {}).get("accountId") or "")
    if not account_id:
        return
    task.setdefault("initialAccountId", account_id)
    task.setdefault("accountIdOriginal", task.get("initialAccountId") or account_id)
    if not str(task.get("lockedAccountId") or "").strip():
        task["lockedAccountId"] = account_id
    task["accountSwitchLocked"] = True
    if not int(task.get("accountLockedAt") or 0):
        task["accountLockedAt"] = _now_ms()
    if reason:
        task["accountLockReason"] = reason


def _task_allows_account_reroute(task: Optional[dict]) -> bool:
    if not isinstance(task, dict):
        return False
    if bool(task.get("accountSwitchLocked")) or str(task.get("lockedAccountId") or "").strip():
        return False
    status = str(task.get("status") or "")
    if status in {"prepared", "submitted", "downloading", "downloaded", "completed", "cancelled", "manual"}:
        return False
    if _task_has_submission_anchor(task) or _task_has_generation_signal(task) or _task_has_result_binding(task):
        return False
    return status in {"queued", "claimed", "failed"}


def _remember_account_exhausted(
    account_id: str,
    reason: str = "",
    *,
    issue_key: str = "",
    site: Any = None,
) -> None:
    if not account_id:
        return
    if _is_login_required_signal(reason):
        _remember_account_login_required(account_id, reason, site=site)
        return
    state = _read_state()
    _mark_account_exhausted_in_state(state, account_id, reason or "low credit", issue_key=issue_key, site=site)
    _write_state(state)


def _mark_account_login_required_in_state(
    state: dict,
    account_id: str,
    reason: str = "",
    *,
    site: Any = None,
) -> bool:
    now = _now_ms()
    target_site = normalize_site(site) if site is not None else ""
    for account in state.get("accounts", []):
        if str(account.get("id") or "") != str(account_id or ""):
            continue
        if target_site and _account_site(account) != target_site:
            continue
        account["accountHealthState"] = "login-required"
        account["accountIssueKind"] = "login-required"
        account["accountIssueReason"] = reason or "豆包页面提示需要重新登录"
        account["accountIssueLastAt"] = now
        account["accountCooldownUntil"] = 0
        account.pop("exhaustedReason", None)
        account["updatedAt"] = now
        return True
    return False


def _remember_account_login_required(account_id: str, reason: str = "", *, site: Any = None) -> None:
    if not account_id:
        return
    state = _read_state()
    if _mark_account_login_required_in_state(state, account_id, reason, site=site):
        _write_state(state)


def _is_failed_page_signal(task: Optional[dict]) -> bool:
    return str((task or {}).get("doubaoPageStatus") or "").strip() == "failed-signal"


def _merged_task(task: dict, patch: dict) -> dict:
    merged = dict(task or {})
    merged.update(patch or {})
    return merged


def _should_preserve_generation_failure(task: dict, patch: dict) -> bool:
    if str((patch or {}).get("status") or "") != "failed":
        return False
    merged = _merged_task(task, patch)
    if not _task_has_generation_signal(merged):
        return False
    page_status = str(merged.get("doubaoPageStatus") or "").strip()
    credit_remaining = _to_float(merged.get("doubaoCreditRemaining"))
    text = " ".join(str(x or "") for x in (
        patch.get("error"), patch.get("doubaoFailureReason"), patch.get("doubaoPageMessage"),
        task.get("error"), task.get("doubaoFailureReason"), task.get("doubaoPageMessage"),
    ))
    if credit_remaining is not None and credit_remaining > 0 and _is_low_credit_signal(text):
        return True
    if page_status == "failed-signal" and not (_is_soft_submit_error(text) or _is_low_credit_signal(text)):
        return False
    return _is_soft_submit_error(text) or _is_low_credit_signal(text)


def _should_revive_failed_task(task: dict) -> bool:
    if str((task or {}).get("status") or "") != "failed":
        return False
    if not _task_has_generation_signal(task):
        return False
    text = " ".join(str(x or "") for x in (
        task.get("error"), task.get("doubaoFailureReason"), task.get("doubaoPageMessage"),
    ))
    return not text.strip() or _is_soft_submit_error(text) or _is_low_credit_signal(text)


def _should_skip_automation_retry(task: dict) -> bool:
    if not isinstance(task, dict):
        return False
    if str(task.get("rerouteSkippedReason") or "").strip():
        return True
    if str(task.get("stage") or "") in {"account-exhausted", "account-reroute-blocked"}:
        return True
    text = " ".join(str(x or "") for x in (
        task.get("error"), task.get("doubaoFailureReason"), task.get("doubaoPageMessage"),
    ))
    return _is_low_credit_signal(text)


def _public_account(state: dict, account: dict, active_counts: Optional[dict[str, int]] = None) -> dict:
    account_id = str(account.get("id") or "")
    site = _account_site(account)
    videos_dir = _download_dir(account)
    videos = []
    if videos_dir.exists():
        for file in sorted(videos_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                stat = file.stat()
                videos.append({
                    "id": file.stem,
                    "name": file.name,
                    "path": str(file),
                    "size": stat.st_size,
                    "createdAt": int(stat.st_mtime * 1000),
                })
            except Exception:
                continue
    quota = _account_local_quota(account)
    effective_quota = _account_capacity(account)
    used = _account_used(account)
    active = int((active_counts or {}).get(account_id, 0) or 0)
    heartbeat = _heartbeat_snapshot(account_id, site=site)
    health = _account_health_snapshot(account)
    available_slots = _account_remaining_slots(account, active_counts)
    if not account.get("enabled", True) or health.get("accountHealthStatus") in {"cooldown", "exhausted", "login-required"}:
        available_slots = 0
    return {
        **account,
        "site": site,
        "siteLabel": _site_label(site),
        "siteHost": _site_host(site),
        "remaining": max(0, quota - used),
        "capacity": effective_quota,
        "available": available_slots,
        "active": active,
        "videoCount": len(videos),
        "downloadDir": str(videos_dir),
        "profileDir": str(_account_dir(state, account)),
        "debugPort": _debug_port(account),
        "extensionBinding": {
            "mode": "auto-load",
            "extensionDir": str(EXTENSION_DIR),
            "extraExtensionDirs": [str(path) for path in _browser_extension_dirs()[1:]],
            "duration15ExtensionDir": str(DURATION15_EXTENSION_DIR),
            "watermarkExtensionDir": str(WATERMARK_EXTENSION_DIR),
            "profileDir": str(_account_dir(state, account)),
            "debugPort": _debug_port(account),
            "site": site,
            "siteHost": _site_host(site),
        },
        "videos": videos,
        **_account_keepalive_status(account),
        **health,
        **heartbeat,
    }


def _find_browser() -> str:
    env = os.environ.get("DOUBAO_BROWSER_PATH", "").strip()
    candidates: list[str] = []
    use_packaged = _use_packaged_multi_open_browser()
    if use_packaged and PACKAGED_BROWSER_CHROME.exists():
        candidates.append(str(PACKAGED_BROWSER_CHROME))
    if env and not _is_source_browser_bundle_path(env):
        candidates.append(env)
    which_names = [
        "chrome",
        "chrome.exe",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
        "msedge.exe",
        "microsoft-edge",
        "brave",
        "brave-browser",
        "brave.exe",
    ]
    for name in which_names:
        found = shutil.which(name)
        if found:
            candidates.append(found)
    if sys.platform.startswith("win"):
        env_dirs = [
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        win_relatives = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
            r"Chromium\Application\chrome.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for base in env_dirs:
            for relative in win_relatives:
                if base:
                    candidates.append(str(Path(base) / relative))
    elif sys.platform == "darwin":
        home = str(Path.home())
        candidates.extend([
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            f"{home}/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"{home}/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            f"{home}/Applications/Chromium.app/Contents/MacOS/Chromium",
            f"{home}/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ])
    else:
        candidates.extend([
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
            "/usr/bin/brave-browser",
            "/snap/bin/chromium",
            "/snap/bin/brave",
        ])
    seen: set[str] = set()
    for item in candidates:
        if not item:
            continue
        path = str(Path(os.path.expandvars(os.path.expanduser(item))))
        key = path.lower() if sys.platform.startswith("win") else path
        if key in seen:
            continue
        seen.add(key)
        if Path(path).exists():
            return path
    return ""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ensure_all_extensions() -> None:
    missing = []
    if _use_packaged_multi_open_browser() and not PACKAGED_BROWSER_CHROME.exists():
        missing.append(str(PACKAGED_BROWSER_CHROME))
    if not _extension_manifest_ready(EXTENSION_DIR):
        missing.append(str(EXTENSION_DIR))
    if not _extension_manifest_ready(DURATION15_EXTENSION_DIR):
        missing.append(str(DURATION15_EXTENSION_DIR))
    if not _extension_manifest_ready(WATERMARK_EXTENSION_DIR):
        missing.append(str(WATERMARK_EXTENSION_DIR))
    for path in (EXTENSION_DIR / "duration15.js", EXTENSION_DIR / "network.js", EXTENSION_DIR / "sw.js", EXTENSION_DIR / "page.js"):
        if not path.exists():
            missing.append(str(path))
    if missing:
        raise DoubaoPoolError("豆包生产能力资源缺失: " + "；".join(missing))


def _extension_manifest_ready(extension_dir: Path) -> bool:
    return bool(extension_dir.exists() and (extension_dir / "manifest.json").exists())


def _installed_extension_dirs_ready() -> bool:
    browser_ready = (not _use_packaged_multi_open_browser()) or PACKAGED_BROWSER_CHROME.exists()
    return bool(
        browser_ready
        and _extension_manifest_ready(EXTENSION_DIR)
        and (EXTENSION_DIR / "duration15.js").exists()
        and _extension_manifest_ready(DURATION15_EXTENSION_DIR)
        and _extension_manifest_ready(WATERMARK_EXTENSION_DIR)
    )


def _installed_sources_current() -> bool:
    return _installed_extension_dirs_ready()


def _plugin_install_record() -> dict:
    return read_json(PLUGIN_INSTALL_FILE, {})


def _extension_version(path: Path) -> str:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        return str(manifest.get("version") or "").strip()
    except Exception:
        return ""


def _vmo_extension_version() -> str:
    return _extension_version(EXTENSION_DIR)


def _watermark_extension_version() -> str:
    return _extension_version(WATERMARK_EXTENSION_DIR)


def _runtime_extension_version_stale(account_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> bool:
    expected = _vmo_extension_version()
    heartbeat = _account_heartbeat_record(account_id, site) if account_id else _PLUGIN_HEARTBEAT
    heartbeat = heartbeat or {}
    runtime = str(heartbeat.get("version") or "").strip()
    extension_id = str(heartbeat.get("extensionId") or "").strip()
    if runtime.startswith("devtools-") or extension_id.startswith("devtools-"):
        return False
    if not expected or not runtime:
        return False
    last_seen = int(heartbeat.get("lastSeenAt") or 0)
    connected = bool(last_seen and _now_ms() - last_seen <= PLUGIN_HEARTBEAT_TTL_MS)
    return bool(connected and runtime != expected)


def _write_plugin_install_record(changed: bool = False) -> None:
    packaged_enabled = _use_packaged_multi_open_browser()
    record = {
        "installedAt": int((_plugin_install_record() or {}).get("installedAt") or _now_ms()),
        "updatedAt": _now_ms(),
        "version": "doubao-pool-v6-watermark-bridge",
        "vmoExtensionVersion": _vmo_extension_version(),
        "watermarkExtensionVersion": _watermark_extension_version(),
        "durationSeconds": DOUBAO_FIXED_VIDEO_DURATION_SECONDS,
        "durationOptions": list(DOUBAO_VIDEO_DURATION_OPTIONS),
        "durationMode": "adaptive",
        "extensionDir": str(EXTENSION_DIR),
        "duration15ExtensionDir": str(DURATION15_EXTENSION_DIR),
        "watermarkExtensionDir": str(WATERMARK_EXTENSION_DIR),
        "browserIsolation": "localized-chromium-dedicated-profile" if packaged_enabled else "system-browser-dedicated-profile",
        "packagedBrowserChrome": str(PACKAGED_BROWSER_CHROME) if PACKAGED_BROWSER_CHROME.exists() else "",
        "sourceBrowserBundle": str(PACKAGED_MULTI_OPEN_BROWSER) if PACKAGED_MULTI_OPEN_BROWSER.exists() else "",
        "packagedChromiumEnabled": packaged_enabled,
        "multiOpenBrowser": "",
        "multiOpenBrowserEnabled": False,
        "changed": bool(changed),
    }
    write_json(PLUGIN_INSTALL_FILE, record)


def _plugin_install_record_current(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    return bool(
        record.get("version") == "doubao-pool-v6-watermark-bridge"
        and record.get("extensionDir") == str(EXTENSION_DIR)
        and record.get("duration15ExtensionDir") == str(DURATION15_EXTENSION_DIR)
        and record.get("watermarkExtensionDir") == str(WATERMARK_EXTENSION_DIR)
        and record.get("packagedBrowserChrome") == (str(PACKAGED_BROWSER_CHROME) if PACKAGED_BROWSER_CHROME.exists() else "")
        and record.get("sourceBrowserBundle") == (str(PACKAGED_MULTI_OPEN_BROWSER) if PACKAGED_MULTI_OPEN_BROWSER.exists() else "")
        and record.get("vmoExtensionVersion") == _vmo_extension_version()
        and record.get("watermarkExtensionVersion") == _watermark_extension_version()
        and bool(record.get("packagedChromiumEnabled")) == _use_packaged_multi_open_browser()
    )


def _plugin_status_items() -> list[dict[str, Any]]:
    vmo_ready = _extension_manifest_ready(EXTENSION_DIR)
    duration15_ready = _extension_manifest_ready(DURATION15_EXTENSION_DIR)
    vmo_duration_bridge_ready = (EXTENSION_DIR / "duration15.js").exists()
    watermark_ready = _extension_manifest_ready(WATERMARK_EXTENSION_DIR)
    packaged_browser_enabled = _use_packaged_multi_open_browser()
    packaged_browser_ready = PACKAGED_BROWSER_CHROME.exists()
    duration_runtime = _duration_capability_snapshot()
    duration_verified = bool(duration_runtime.get("durationVerified"))
    duration_status = "verified" if duration_verified else "awaiting-page-verification"
    native_ready = bool(vmo_ready and vmo_duration_bridge_ready and duration15_ready and watermark_ready)
    return [
        {
            "id": "vmo-doubao-native",
            "name": "VMO 豆包生产桥接",
            "kind": "chrome-extension",
            "role": "automation-bridge",
            "installed": native_ready,
            "enabled": native_ready,
            "required": True,
            "path": str(EXTENSION_DIR),
            "sourcePath": str(SOURCE_EXTENSION_DIR),
            "capabilities": ["automation", "network-capture", "download-recovery"],
            "diagnostic": "VMO 负责任务领取、自动提交、轮询、抓取、下载和状态回传。",
        },
        {
            "id": "doubao-packaged-browser",
            "name": "豆包本地化 Chromium 内核",
            "kind": "localized-browser-runtime",
            "role": "browser-runtime",
            "installed": packaged_browser_ready,
            "enabled": packaged_browser_enabled,
            "required": packaged_browser_enabled,
            "path": str(PACKAGED_BROWSER_CHROME) if packaged_browser_ready else "",
            "sourcePath": str(PACKAGED_MULTI_OPEN_BROWSER) if PACKAGED_MULTI_OPEN_BROWSER.exists() else "",
            "capabilities": ["dedicated-profile", "devtools-bridge", "unpacked-extensions"],
            "diagnostic": "VMO 直接启动解包后的 Chromium 内核，并传入账号环境、调试端口和插件目录。",
        },
        {
            "id": "doubao-duration-15s",
            "name": "15 秒档位扩展",
            "kind": "chrome-extension",
            "role": "duration-15s",
            "installed": duration15_ready,
            "enabled": duration15_ready,
            "required": True,
            "path": str(DURATION15_EXTENSION_DIR),
            "sourcePath": str(SOURCE_DURATION15_EXTENSION_DIR),
        },
        {
            "id": "doubao-watermark-download",
            "name": "去水印插件",
            "kind": "chrome-extension",
            "role": "watermark-download",
            "installed": watermark_ready,
            "enabled": watermark_ready,
            "required": True,
            "path": str(WATERMARK_EXTENSION_DIR),
            "sourcePath": str(SOURCE_WATERMARK_EXTENSION_DIR),
            "diagnostic": "VMO 在网络捕获、页面解析、候选合并和下载前统一优先使用豆包无水印视频地址。",
        },
        {
            "id": "doubao-duration-adaptive",
            "name": "豆包分镜时长档位",
            "kind": "runtime-check",
            "role": "adaptive-duration",
            "installed": True,
            "enabled": True,
            "verified": duration_verified,
            "runtimeVerified": duration_verified,
            "status": duration_status,
            "options": duration_runtime.get("durationOptions") or [],
            "diagnostic": duration_runtime.get("durationMessage") or "按分镜时长自动选择 5/10/15 秒，等待豆包页面验证时长控件。",
            "required": True,
        },
    ]


def _plugin_install_status() -> dict:
    items = _plugin_status_items()
    required_items = [item for item in items if item.get("required", True)]
    installed = all(bool(item.get("installed")) for item in required_items)
    enabled = all(bool(item.get("enabled")) for item in required_items)
    record = _plugin_install_record()
    source_current = _installed_sources_current()
    return {
        "installed": installed,
        "enabled": enabled,
        "sourceCurrent": source_current,
        "items": items,
        "installRecord": record if isinstance(record, dict) else {},
        "installFile": str(PLUGIN_INSTALL_FILE),
        "durationSeconds": DOUBAO_FIXED_VIDEO_DURATION_SECONDS,
        "durationOptions": list(DOUBAO_VIDEO_DURATION_OPTIONS),
        "durationMode": "adaptive",
    }


def _install_builtin_plugins(force: bool = False) -> dict:
    before = _plugin_install_status()
    sources_current = _installed_sources_current()
    if before.get("installed") and not force and sources_current:
        record = before.get("installRecord") or {}
        record_changed = False
        if not _plugin_install_record_current(record):
            _write_plugin_install_record(changed=True)
            before = _plugin_install_status()
            record_changed = True
        return {"ok": True, "changed": record_changed, **before}
    _ensure_all_extensions()
    changed = not before.get("installed") or force or not sources_current
    _write_plugin_install_record(changed=changed)
    after = _plugin_install_status()
    return {"ok": bool(after.get("installed")), "changed": changed, **after}


def _browser_extension_dirs() -> list[Path]:
    dirs: list[Path] = []
    if _extension_manifest_ready(EXTENSION_DIR):
        dirs.append(EXTENSION_DIR)
    if _extension_manifest_ready(DURATION15_EXTENSION_DIR):
        dirs.append(DURATION15_EXTENSION_DIR)
    if _extension_manifest_ready(WATERMARK_EXTENSION_DIR):
        dirs.append(WATERMARK_EXTENSION_DIR)
    return dirs


def _browser_extension_arg() -> str:
    return ",".join(str(path) for path in _browser_extension_dirs())


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", int(port))) != 0


def _server_healthy(port: int, token: str = "") -> bool:
    if not port:
        return False
    try:
        lock = _load_launch_lock()
        auth_token = token or str(lock.get("token") or "")
        if not auth_token:
            return False
        url = f"http://127.0.0.1:{int(port)}/__dbvd_auth?token={urllib.parse.quote(auth_token)}&rid=probe"
        response = http.get(url, timeout=0.8)
        data = response.json() if response.ok else {}
        return bool(response.ok and data.get("ok"))
    except Exception:
        return False


def _load_launch_lock() -> dict:
    for path in (LOCK_FILE, EXTENSION_DIR / "launch-lock.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                _LOCK.update({
                    "version": int(payload.get("version") or 1),
                    "token": str(payload.get("token") or ""),
                    "port": int(payload.get("port") or 0),
                    "expiresAt": int(payload.get("expiresAt") or 0),
                })
                return dict(_LOCK)
        except Exception:
            continue
    return dict(_LOCK)


def _preferred_server_port() -> int:
    persisted = _load_launch_lock()
    port = int(persisted.get("port") or DEFAULT_SERVER_PORT)
    if port > 0 and _port_available(port):
        return port
    if DEFAULT_SERVER_PORT > 0 and DEFAULT_SERVER_PORT != port and _port_available(DEFAULT_SERVER_PORT):
        return DEFAULT_SERVER_PORT
    return _free_port()


def _write_launch_lock(port: int) -> dict:
    old = _load_launch_lock()
    token = str(old.get("token") or secrets.token_urlsafe(32))
    expires_at = _now_ms() + LOCK_TTL_MS
    payload = {"version": 1, "token": token, "port": port, "expiresAt": expires_at}
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    EXTENSION_DIR.mkdir(parents=True, exist_ok=True)
    (EXTENSION_DIR / "launch-lock.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _LOCK.update(payload)
    return payload


def _payload_value(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _tracking_value_from_url(url: str, name: str) -> str:
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        query = urllib.parse.parse_qs(parsed.query)
        value = (query.get(name) or [""])[0]
        if value:
            return urllib.parse.unquote(str(value))
        match = re.search(rf"{re.escape(name)}=([^&]+)", parsed.fragment or "")
        return urllib.parse.unquote(match.group(1)) if match else ""
    except Exception:
        return ""


def _heartbeat_task_id(payload: dict) -> str:
    task_id = _payload_value(payload, "taskId", "currentTaskId", "dbvdTaskId")
    return task_id or _tracking_value_from_url(str(payload.get("url") or ""), "dbvdTaskId")


def _task_account_id(task_id: str) -> str:
    if not task_id:
        return ""
    try:
        for task in _read_tasks().get("tasks", []) or []:
            if str(task.get("id") or "") != str(task_id):
                continue
            return str(
                task.get("currentAccountId")
                or task.get("accountId")
                or task.get("lockedAccountId")
                or task.get("initialAccountId")
                or ""
            ).strip()
    except Exception:
        return ""
    return ""


def _heartbeat_account_id(payload: dict) -> str:
    account_id = _payload_value(payload, "accountId", "currentAccountId", "dbvdAccountId")
    if account_id:
        return account_id
    account_id = _tracking_value_from_url(str(payload.get("url") or ""), "dbvdAccountId")
    if account_id:
        return account_id
    return _task_account_id(_heartbeat_task_id(payload))


def _prune_account_heartbeats(now: Optional[int] = None) -> None:
    marker = now or _now_ms()
    stale = [
        key for key, heartbeat in _ACCOUNT_PLUGIN_HEARTBEATS.items()
        if marker - int((heartbeat or {}).get("lastSeenAt") or 0) > ACCOUNT_HEARTBEAT_RETENTION_MS
    ]
    for key in stale:
        _ACCOUNT_PLUGIN_HEARTBEATS.pop(key, None)


def _duration_capability_snapshot(heartbeat_override: Optional[dict] = None) -> dict:
    heartbeat = dict(_PLUGIN_HEARTBEAT if heartbeat_override is None else heartbeat_override)
    last_seen = int(heartbeat.get("lastSeenAt") or 0)
    connected = bool(last_seen and _now_ms() - last_seen <= PLUGIN_HEARTBEAT_TTL_MS)
    raw = heartbeat.get("durationCapability")
    capability = raw if connected and isinstance(raw, dict) else {}
    options = [
        str(item).strip()
        for item in (capability.get("options") or capability.get("visibleLabels") or [])
        if str(item).strip()
    ]
    available_seconds = []
    for item in capability.get("availableSeconds") or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value in DOUBAO_VIDEO_DURATION_OPTIONS and value not in available_seconds:
            available_seconds.append(value)
    for label in options:
        for match in re.finditer(r"(^|[^0-9])(\d{1,2})\s*(?:s|秒)([^0-9]|$)", label, re.IGNORECASE):
            value = int(match.group(2))
            if value in DOUBAO_VIDEO_DURATION_OPTIONS and value not in available_seconds:
                available_seconds.append(value)
    available_seconds.sort()
    required_seconds = int(capability.get("requiredSeconds") or DOUBAO_FIXED_VIDEO_DURATION_SECONDS)
    has_target = bool(capability.get("hasTarget") or capability.get("selectedTarget") or required_seconds in available_seconds)
    verified = bool(connected and has_target)
    if verified:
        mode = "/".join(str(item) for item in (available_seconds or [required_seconds]))
        message = f"豆包页面已检测到 {mode} 秒档位"
    elif connected and capability:
        message = str(capability.get("message") or f"当前豆包页面未检测到 {required_seconds} 秒选项").strip()
    else:
        message = f"账号浏览器尚未验证 {DOUBAO_FIXED_VIDEO_DURATION_SECONDS} 秒上限选项"
    return {
        "durationCapability": capability,
        "durationVerified": verified,
        "durationOptions": options,
        "durationAvailableSeconds": available_seconds,
        "durationMessage": message,
        "durationSeconds": DOUBAO_FIXED_VIDEO_DURATION_SECONDS,
        "durationMode": "adaptive",
    }


def _heartbeat_snapshot(account_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> dict:
    key = str(account_id or "").strip()
    site = normalize_site(site)
    if key:
        heartbeat = dict(_account_heartbeat_record(key, site))
    else:
        site_records = [
            item for item in _ACCOUNT_PLUGIN_HEARTBEATS.values()
            if normalize_site((item or {}).get("site")) == site
        ]
        if site_records:
            heartbeat = dict(max(site_records, key=lambda item: int((item or {}).get("lastSeenAt") or 0)))
        elif normalize_site((_PLUGIN_HEARTBEAT or {}).get("site")) == site:
            heartbeat = dict(_PLUGIN_HEARTBEAT)
        else:
            heartbeat = {}
    last_seen = int(heartbeat.get("lastSeenAt") or 0)
    connected = bool(last_seen and _now_ms() - last_seen <= PLUGIN_HEARTBEAT_TTL_MS)
    return {
        "pluginConnected": connected,
        "pluginLastSeenAt": last_seen,
        "pluginSite": normalize_site(heartbeat.get("site") or site),
        "pluginActiveUrl": heartbeat.get("url") or "",
        "pluginExtensionId": heartbeat.get("extensionId") or "",
        "pluginVersion": heartbeat.get("version") or "",
        "pluginUserAgent": heartbeat.get("userAgent") or "",
        "pluginAccountId": heartbeat.get("accountId") or "",
        "pluginAccountKey": heartbeat.get("accountKey") or "",
        "pluginTaskId": heartbeat.get("taskId") or "",
        **_duration_capability_snapshot(heartbeat),
    }


def _record_plugin_heartbeat(payload: dict) -> dict:
    with _STATE_LOCK:
        now = _now_ms()
        duration_capability = payload.get("durationCapability")
        if not isinstance(duration_capability, dict):
            duration_capability = {}
        task_id = _heartbeat_task_id(payload)
        site = _heartbeat_site_from_payload(payload, task_id)
        account_id = _heartbeat_account_id(payload)
        account_key = _account_heartbeat_key(account_id, site)
        record = {
            "lastSeenAt": now,
            "site": site,
            "url": str(payload.get("url") or ""),
            "title": str(payload.get("title") or ""),
            "extensionId": str(payload.get("extensionId") or ""),
            "version": str(payload.get("version") or ""),
            "userAgent": str(payload.get("userAgent") or ""),
            "accountId": account_id,
            "accountKey": account_key,
            "taskId": task_id,
            "durationCapability": duration_capability,
        }
        _PLUGIN_HEARTBEAT.update(record)
        if account_key:
            _ACCOUNT_PLUGIN_HEARTBEATS[account_key] = dict(record)
        _prune_account_heartbeats(now)
        return _heartbeat_snapshot(account_id, site=site)


def _websocket_read_frame(sock: socket.socket) -> str:
    header = sock.recv(2)
    if len(header) < 2:
        raise DoubaoPoolError("DevTools websocket closed")
    b1, b2 = header
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    mask = sock.recv(4) if (b2 & 0x80) else b""
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise DoubaoPoolError("DevTools websocket frame truncated")
        payload += chunk
    if mask:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    opcode = b1 & 0x0F
    if opcode == 8:
        raise DoubaoPoolError("DevTools websocket closed")
    return payload.decode("utf-8", errors="replace")


def _websocket_send_frame(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    mask = secrets.token_bytes(4)
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.extend([0x80 | 126, *struct.pack("!H", length)])
    else:
        header.extend([0x80 | 127, *struct.pack("!Q", length)])
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _devtools_eval(ws_url: str, expression: str, *, timeout: float = 2.5) -> dict:
    return _devtools_send(ws_url, "Runtime.evaluate", {
        "expression": expression,
        "awaitPromise": True,
        "returnByValue": True,
    }, timeout=timeout)


def _devtools_send(ws_url: str, method: str, params: Optional[dict] = None, *, timeout: float = 2.5) -> dict:
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 80)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: http://127.0.0.1\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise DoubaoPoolError(response.decode("utf-8", errors="replace").splitlines()[0])
        payload = {
            "id": 1,
            "method": method,
            "params": params or {},
        }
        _websocket_send_frame(sock, json.dumps(payload, ensure_ascii=False))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(_websocket_read_frame(sock))
            if msg.get("id") == 1:
                return msg
    raise DoubaoPoolError("DevTools evaluation timeout")


def _extract_eval_value(message: dict) -> Any:
    result = ((message.get("result") or {}).get("result") or {})
    if "value" in result:
        return result.get("value")
    if result.get("type") == "undefined":
        return None
    return result


def _bridge_script(lock: dict, account_id: str = "") -> str:
    payload = {
        "port": int(lock.get("port") or 0),
        "token": str(lock.get("token") or ""),
        "expiresAt": int(lock.get("expiresAt") or 0),
        "accountId": str(account_id or ""),
        "version": "devtools-bridge",
    }
    return f"""
(() => {{
  const cfg = {json.dumps(payload, ensure_ascii=False)};
  const DOUBAO_FIXED_VIDEO_DURATION_SECONDS = {DOUBAO_FIXED_VIDEO_DURATION_SECONDS};
  const DOUBAO_VIDEO_DURATION_OPTIONS = [5, 10, 15];
  try {{
    if (cfg.accountId) sessionStorage.setItem('dbvdAccountId', cfg.accountId);
  }} catch {{}}
  if (window.__vmoDoubaoBridgeReady) return {{ ok: true, already: true, href: location.href, accountId: cfg.accountId || '' }};
  window.__vmoDoubaoBridgeReady = true;
  function durationVisible(el) {{
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.01;
  }}
  function durationText(el) {{
    if (!el) return '';
    return [
      el.getAttribute && el.getAttribute('aria-label'),
      el.getAttribute && el.getAttribute('title'),
      el.innerText,
      el.textContent,
    ].map(value => String(value || '').replace(/\\s+/g, ' ').trim()).filter(Boolean).sort((a, b) => a.length - b.length)[0] || '';
  }}
  function inspectDoubaoDurationCapability() {{
    const required = DOUBAO_FIXED_VIDEO_DURATION_SECONDS;
    const targetPattern = new RegExp('(^|[^0-9])' + required + '\\\\s*(?:s|秒)([^0-9]|$)', 'i');
    const nodes = [...document.querySelectorAll('button,[role="button"],[role="option"],[aria-label],[title],li,div,span')]
      .filter(durationVisible)
      .map(el => durationText(el))
      .filter(Boolean)
      .filter(text => /时长|duration|\\b(?:5|10|15)\\s*(?:s|秒)\\b|(?:5|10|15)\\s*秒/i.test(text));
    const labels = [...new Set(nodes)].slice(0, 30);
    const availableSeconds = [...new Set(labels.flatMap(text => {{
      const seconds = [];
      const pattern = /(^|[^0-9])(\\d{{1,2}})\\s*(?:s|秒)([^0-9]|$)/gi;
      let match = null;
      while ((match = pattern.exec(String(text || '')))) {{
        const number = Number(match[2]);
        if (DOUBAO_VIDEO_DURATION_OPTIONS.includes(number)) seconds.push(number);
      }}
      return seconds;
    }}))].sort((a, b) => a - b);
    const hasTarget = labels.some(text => targetPattern.test(text)) || availableSeconds.includes(required);
    return {{
      requiredSeconds: required,
      availableSeconds,
      hasTarget,
      selectedTarget: hasTarget && labels.some(text => targetPattern.test(text) && /选中|已选|selected|checked/i.test(text)),
      options: labels,
      message: hasTarget ? '豆包页面已检测到 ' + required + ' 秒选项' : '当前豆包页面未检测到 ' + required + ' 秒选项',
    }};
  }}
  function readAssigned(name) {{
    try {{
      const url = new URL(location.href);
      const fromUrl = url.searchParams.get(name) || (url.hash.match(new RegExp(name + '=([^&]+)')) || [])[1] || '';
      if (fromUrl) {{
        const value = decodeURIComponent(fromUrl);
        sessionStorage.setItem(name, value);
        return value;
      }}
      return sessionStorage.getItem(name) || '';
    }} catch {{
      try {{ return sessionStorage.getItem(name) || ''; }} catch {{ return ''; }}
    }}
  }}
  async function heartbeat() {{
    const accountId = cfg.accountId || readAssigned('dbvdAccountId');
    const taskId = readAssigned('dbvdTaskId');
    const body = {{
      url: location.href,
      title: document.title,
      userAgent: navigator.userAgent,
      extensionId: 'devtools-bridge',
      version: cfg.version,
      accountId,
      taskId,
      durationCapability: inspectDoubaoDurationCapability(),
    }};
    const url = `http://127.0.0.1:${{cfg.port}}/__dbvd_heartbeat?token=${{encodeURIComponent(cfg.token)}}&rid=devtools-bridge&version=${{encodeURIComponent(cfg.version)}}`;
    const response = await fetch(url, {{
      method: 'POST',
      cache: 'no-store',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(body),
    }});
    return await response.json();
  }}
  heartbeat().catch(() => {{}});
  window.__vmoDoubaoHeartbeatTimer = setInterval(() => heartbeat().catch(() => {{}}), 5000);
  return {{ ok: true, injected: true, href: location.href }};
}})()
"""


def _automation_bridge_script(lock: dict, task_id: str = "", account_id: str = "") -> str:
    try:
        core_script = (SOURCE_EXTENSION_DIR / "core.js").read_text(encoding="utf-8")
    except Exception:
        core_script = ""
    try:
        network_script = (SOURCE_EXTENSION_DIR / "network.js").read_text(encoding="utf-8")
    except Exception:
        network_script = ""
    try:
        watermark_script = (SOURCE_EXTENSION_DIR / "watermark-extractor.js").read_text(encoding="utf-8")
    except Exception:
        watermark_script = ""
    payload = {
        "port": int(lock.get("port") or 0),
        "token": str(lock.get("token") or ""),
        "taskId": str(task_id or ""),
        "accountId": str(account_id or ""),
        "version": "devtools-automation",
        "coreScript": core_script,
        "networkScript": network_script,
        "watermarkScript": watermark_script,
    }
    return f"""
(() => {{
  const cfg = {json.dumps(payload, ensure_ascii=False)};
  const automationBuildId = 'devtools-automation-r16-semi-auto-mode';
  const previous = window.__vmoDoubaoAutomationWorker;
  if (previous && previous.buildId !== automationBuildId) {{
    try {{ previous.running = false; }} catch (_) {{}}
  }}
  if (previous && previous.running && previous.buildId === automationBuildId && Date.now() - Number(previous.lastTick || 0) < 12000) {{
    previous.desiredTaskId = cfg.taskId || previous.desiredTaskId || '';
    previous.accountId = cfg.accountId || previous.accountId || '';
    return {{ ok: true, already: true, taskId: previous.desiredTaskId || '', href: location.href }};
  }}
  const worker = {{
    running: true,
    desiredTaskId: cfg.taskId || '',
    accountId: cfg.accountId || '',
    version: cfg.version,
    buildId: automationBuildId,
    startedAt: Date.now(),
    lastTick: Date.now(),
    activeTaskId: '',
    lastDoubaoStatusSyncAt: 0,
    lastLlmDecisionAt: 0,
    lastLlmDecisionKey: '',
    lastLlmDecision: null,
    lastWatermarkDiagnostic: null,
  }};
  window.__vmoDoubaoAutomationWorker = worker;
  window.__vmoDoubaoAutomationRunning = true;
  window.__vmoDoubaoAutomationTaskId = cfg.taskId || '';
  ensureNetworkCaptureScript();
  ensureWatermarkExtractorScript();
  window.addEventListener('message', (event) => {{
    const data = event.data;
    if (data && data.source === 'page-service-channel' && data.type === 'network-candidates-updated') {{
      const taskId = getAssignedTaskId();
      if (taskId) {{
        updateTask({{ id: taskId, stage: 'network-result-captured', doubaoAutomationDiagnostic: {{ reason: 'network-result-captured', at: Date.now(), count: Array.isArray(data.videos) ? data.videos.length : 0 }} }}).catch(() => {{}});
      }}
    }}
  }});
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const enc = encodeURIComponent;
  const base = `http://127.0.0.1:${{cfg.port}}`;
  const auth = `token=${{enc(cfg.token)}}&rid=devtools-automation`;
  const DOUBAO_FIXED_VIDEO_DURATION_SECONDS = {DOUBAO_FIXED_VIDEO_DURATION_SECONDS};
  const DOUBAO_VIDEO_DURATION_OPTIONS = [5, 10, 15];
  const DOUBAO_REQUIRE_NO_WATERMARK_DOWNLOAD = true;
  const DOUBAO_STATUS_SYNC_INTERVAL_MS = 2500;
  const VMO_IDLE_TASK_MESSAGE = '等待 VMO 豆包任务';
  const VMO_LOCAL_BRIDGE_WAIT_MESSAGE = '等待 VMO 本地服务重连，任务会自动恢复';
  const VMO_TASK_COMPLETED_MESSAGE = '豆包任务已完成';
  const DOUBAO_HARD_FAILURE_PATTERN = /(?:出于肖像保护考虑|肖像保护[^。；;\\n]*(?:考虑|真实人脸|人脸素材|参考)|(?:暂不支持|不支持)[^。；;\\n]*(?:真实人脸|人脸素材|人物肖像)[^。；;\\n]*(?:参考|上传)?|(?:换(?:一)?张参考图|更换参考图)[^。；;\\n]*(?:文生视频)?)[^。；;\\n]*/i;
  const DOUBAO_STATUS_KEYWORDS = {{
    creditCost: ['将消耗', '预计消耗', '本次消耗', '消耗', '扣除', '使用额度', '视频生成额度', 'credits', 'credit'],
    creditRemaining: ['今日剩余', '当前剩余', '剩余额度', '剩余积分', '账户余额', '余额', '可用额度'],
    progress: ['进度', '完成度', '已完成', '生成进度', '当前进度', '%'],
    queued: ['排队中', '队列中', '等待生成', '预计等待', '任务已创建', '已提交'],
    generating: ['正在为您生成', '正在生成', '生成中', '创作中', '视频生成中'],
    completed: ['视频生成好啦', '视频生成好了', '生成好啦', '生成好了', '生成完成', '视频已生成', '已生成视频', '创作完成', '下载视频', '保存视频'],
    failed: ['视频生成失败', '生成失败', '生成异常', '无法生成', '任务失败', '请求失败', '额度不足', '积分不足', '余额不足', '审核未通过', '内容不符合', '肖像保护', '真实人脸素材', '人脸素材作为参考', '换张参考图', '网络异常', '服务异常', '超时', '繁忙', '稍后重试', '登录已过期'],
  }};
  function readDurationNumber(value) {{
    if (value === null || value === undefined || value === '') return 0;
    if (typeof value === 'number') return Number.isFinite(value) && value > 0 ? value : 0;
    const match = String(value).match(/\\d+(?:\\.\\d+)?/);
    const number = match ? Number(match[0]) : 0;
    return Number.isFinite(number) && number > 0 ? number : 0;
  }}
  function selectDoubaoDuration(seconds) {{
    const value = readDurationNumber(seconds);
    if (!value) return DOUBAO_FIXED_VIDEO_DURATION_SECONDS;
    if (value > 10) return 15;
    if (value > 5) return 10;
    return 5;
  }}
  function promptDurationSeconds(prompt = '') {{
    const labelled = String(prompt || '').match(/(?:总时长|视频时长|生成时长|时长|duration)\\s*[:：]?\\s*(\\d+(?:\\.\\d+)?)\\s*(?:秒|s|sec|seconds?)?/i);
    return labelled ? readDurationNumber(labelled[1]) : 0;
  }}
  function taskSourceDurationSeconds(task = {{}}) {{
    for (const key of ['sourceDuration', 'requestedDuration', 'storyboardDuration', 'shotDuration', 'originalDuration', 'duration']) {{
      const value = readDurationNumber(task && task[key]);
      if (value) return value;
    }}
    return promptDurationSeconds(task && task.prompt || '');
  }}
  function taskTargetDurationSeconds(task = {{}}) {{
    for (const key of ['doubaoTargetDuration', 'targetDuration', 'selectedDuration']) {{
      const value = Math.round(readDurationNumber(task && task[key]));
      if (DOUBAO_VIDEO_DURATION_OPTIONS.includes(value)) return value;
    }}
    return selectDoubaoDuration(taskSourceDurationSeconds(task));
  }}
  function durationConfirmReply(task = null) {{
    return `是，生成${{taskTargetDurationSeconds(task || {{}})}}秒视频`;
  }}
  const DOUBAO_STATUS_PATTERNS = {{
    progress: [
      /(?:生成进度|完成进度|当前进度|进度|完成度|已完成)\\s*[:：]?\\s*(\\d{{1,3}})\\s*%/i,
      /(\\d{{1,3}})\\s*%\\s*(?:完成|生成|进度)/i,
    ],
    wait: [
      /(?:预计等待|预计用时|预计耗时|预计还需|还需等待|剩余时间|排队预计|等待|预计)\\s*[:：]?\\s*([0-9.]+\\s*(?:小时|时|分钟|分|秒|min|mins?|s|h|hrs?))/i,
      /(?:约|大约)\\s*([0-9.]+\\s*(?:小时|时|分钟|分|秒|min|mins?|s|h|hrs?))/i,
    ],
    creditCost: [
      /(?:将消耗|预计消耗|本次消耗|消耗|扣除|将扣除|使用|花费)\\s*[:：]?\\s*([0-9.]+)\\s*(?:个|点|次)?\\s*(?:视频生成)?(?:额度|积分|点数|credits?|credit)/i,
      /([0-9.]+)\\s*(?:个|点|次)?\\s*(?:视频生成)?(?:额度|积分|点数|credits?)\\s*(?:将被消耗|已消耗|消耗|扣除)/i,
    ],
    creditRemaining: [
      /(?:今日剩余|当前剩余|剩余额度|剩余积分|账户余额|余额|剩余|可用)\\s*[:：]?\\s*([0-9.]+)\\s*(?:个|点|次)?\\s*(?:视频生成)?(?:额度|积分|点数|credits?|credit)/i,
      /([0-9.]+)\\s*(?:个|点|次)?\\s*(?:视频生成)?(?:额度|积分|点数|credits?)\\s*(?:剩余|可用)/i,
    ],
    model: [
      /(?:使用|本次使用|模型)\\s*[:：]?\\s*([^，。,.；;]{{2,50}}?(?:模型|Seedance[^，。,.；;]{{0,30}}))\\s*(?:生成|，|,|。|$)/i,
      /(Seedance\\s*[^，。,.；;]{{0,40}}(?:模型)?)/i,
    ],
    failed: [
      DOUBAO_HARD_FAILURE_PATTERN,
      /(?:视频生成失败|生成失败|生成异常|无法生成|任务失败|请求失败|提交失败|下载失败|生成额度未扣除|额度未扣除|未扣除|额度不足|剩余额度不足|积分不足|余额不足|内容不符合|审核未通过|违规|敏感|风控|网络异常|服务异常|服务器异常|超时|繁忙|请稍后重试|稍后再试|出了点问题|遇到问题|登录已过期|请先登录|重新登录)[^。；;\\n]*/i,
    ],
    completed: [
      /(?:你的视频生成好(?:啦|了)?(?!后|之后|以后)|视频生成好(?:啦|了)?(?!后|之后|以后)|生成好(?:啦|了)(?!后|之后|以后)|生成完成(?!后|之后|以后)|视频已生成(?!后|之后|以后)|已生成视频(?!后|之后|以后)|创作完成(?!后|之后|以后)|下载视频|保存视频)/i,
    ],
    generating: [
      /(?:正在为您生成|正在生成|生成中|创作中|视频生成中|排队中|队列中|等待生成|预计等待)/i,
    ],
  }};
  const CN_MORE = '\\u66f4\\u591a';
  const CN_VIDEO = '\\u89c6\\u9891';
  const CN_VIDEO_GENERATION = '\\u89c6\\u9891\\u751f\\u6210';
  const CN_ADD_PHOTO_VIDEO_HINT = '\\u6dfb\\u52a0\\u7167\\u7247\\uff0c\\u63cf\\u8ff0\\u4f60\\u60f3\\u751f\\u6210\\u7684\\u89c6\\u9891';
  const CN_DESCRIBE_VIDEO_HINT = '\\u63cf\\u8ff0\\u4f60\\u60f3\\u8981\\u7684\\u89c6\\u9891';

  async function api(path, options = {{}}) {{
    const join = path.includes('?') ? '&' : '?';
    const res = await fetch(`${{base}}${{path}}${{join}}${{auth}}`, {{
      cache: 'no-store',
      ...options,
      headers: options.body ? {{ 'Content-Type': 'application/json', ...(options.headers || {{}}) }} : options.headers,
    }});
    const text = await res.text();
    let data = null;
    try {{ data = text ? JSON.parse(text) : null; }} catch {{ data = null; }}
    if (!res.ok || !data || !data.ok) throw new Error((data && data.error) || `HTTP ${{res.status}}`);
    return data;
  }}

  async function claimTask() {{
    const taskId = getAssignedTaskId();
    if (!taskId) return null;
    const accountId = getAssignedAccountId();
    const query = [
      taskId ? `taskId=${{enc(taskId)}}` : '',
      accountId ? `accountId=${{enc(accountId)}}` : '',
    ].filter(Boolean).join('&');
    const path = `/__dbvd_task${{query ? `?${{query}}` : ''}}`;
    return (await api(path)).task || null;
  }}

  async function getTaskStatus(taskId) {{
    if (!taskId) return null;
    return (await api(`/__dbvd_task_status?taskId=${{enc(taskId)}}`)).task || null;
  }}

  async function updateTask(patch) {{
    const next = {{ ...(patch || {{}}) }};
    if (next.id && !next.automationRunnerId) next.automationRunnerId = 'devtools-automation';
    if (next.id && !next.accountId) {{
      const accountId = getAssignedAccountId();
      if (accountId) next.accountId = accountId;
    }}
    return (await api('/__dbvd_task', {{ method: 'POST', body: JSON.stringify(next) }})).task || null;
  }}

  async function downloadTaskVideo(task, video) {{
    return (await api('/__dbvd_download', {{ method: 'POST', body: JSON.stringify({{ id: task.id, video }}) }})).task || null;
  }}

  async function devtoolsClick(element, taskId = '') {{
    if (!(element instanceof HTMLElement)) return false;
    if (typeof isLikelyConversationTitleOrHistoryTarget === 'function' && isLikelyConversationTitleOrHistoryTarget(element)) return false;
    const rect = element.getBoundingClientRect();
    const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
    const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
    const hit = document.elementFromPoint(x, y);
    const target = typeof clickableTargetFor === 'function' ? clickableTargetFor(hit || element) : (hit || element);
    if (!target) return false;
    if (typeof isLikelyConversationTitleOrHistoryTarget === 'function' && isLikelyConversationTitleOrHistoryTarget(target)) return false;
    try {{
      await api('/__dbvd_click', {{
        method: 'POST',
        body: JSON.stringify({{ taskId: taskId || getAssignedTaskId(), accountId: getAssignedAccountId(), x, y }}),
      }});
      return true;
    }} catch (error) {{
      return false;
    }}
  }}

  async function devtoolsRefreshTaskPage(taskId = '', reason = '') {{
    try {{
      await api('/__dbvd_refresh', {{
        method: 'POST',
        body: JSON.stringify({{ taskId: taskId || getAssignedTaskId(), accountId: getAssignedAccountId(), reason }}),
      }});
      return true;
    }} catch {{
      return false;
    }}
  }}

  function llmRecoveryEnabled(task = null) {{
    return !(task && (task.doubaoLlmRecovery === false || task.decisionLlm === false));
  }}

  function compactDecisionText(value, limit = 4200) {{
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text.length <= limit) return text;
    const head = Math.max(800, Math.floor(limit / 4));
    const tail = Math.max(1200, limit - head - 32);
    return `${{text.slice(0, head)}} ... ${{text.slice(-tail)}}`;
  }}

  function decisionHash(value) {{
    let hash = 2166136261;
    const text = String(value || '');
    for (let i = 0; i < text.length; i++) {{
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }}
    return (hash >>> 0).toString(16);
  }}

  function collectAutomationSurfaceState() {{
    let anyInput = null;
    let videoInput = null;
    let videoSurface = false;
    let imageMode = false;
    let titleModal = false;
    let inputContext = '';
    try {{
      anyInput = findAnyPromptInput() || findPromptInput();
    }} catch {{}}
    try {{
      videoInput = findVideoPromptInput();
    }} catch {{}}
    try {{
      videoSurface = Boolean(isVideoSurface());
    }} catch {{}}
    try {{
      imageMode = Boolean(anyInput && isImageModePromptInput(anyInput));
    }} catch {{}}
    try {{
      titleModal = Boolean(isConversationTitleEditModalOpen());
    }} catch {{}}
    try {{
      inputContext = anyInput ? elementTextContext(anyInput).slice(0, 500) : '';
    }} catch {{}}
    return {{
      href: location.href,
      videoSurface,
      hasVideoInput: Boolean(videoInput),
      hasAnyInput: Boolean(anyInput),
      imageMode,
      titleModal,
      inputContext,
    }};
  }}

  async function requestLlmAutomationDecision(task, stage = '', pageStatus = null, extra = {{}}) {{
    if (!llmRecoveryEnabled(task)) return null;
    const text = compactDecisionText(statusSourceTextForTask(task, 4200), 4200);
    const surface = collectAutomationSurfaceState();
    const mergedPageStatus = {{ ...(pageStatus || {{}}), surface }};
    const key = decisionHash(JSON.stringify({{
      taskId: task && task.id || getAssignedTaskId() || '',
      stage,
      status: mergedPageStatus.status || '',
      text: text.slice(-1800),
      targetSeconds: taskTargetDurationSeconds(task || {{}}),
      decisionKey: extra && extra.decisionKey || '',
      surface: {{
        videoSurface: surface.videoSurface,
        hasVideoInput: surface.hasVideoInput,
        imageMode: surface.imageMode,
        href: surface.href,
      }},
    }}));
    const now = Date.now();
    const hasCandidateContext = Array.isArray(extra && extra.candidateVideos) && extra.candidateVideos.length > 0;
    const minDecisionInterval = hasCandidateContext ? 2500 : 18000;
    if (worker.lastLlmDecision && key === worker.lastLlmDecisionKey && now - Number(worker.lastLlmDecisionAt || 0) < 45000) return worker.lastLlmDecision;
    if (now - Number(worker.lastLlmDecisionAt || 0) < minDecisionInterval && key !== worker.lastLlmDecisionKey) return null;
    worker.lastLlmDecisionAt = now;
    worker.lastLlmDecisionKey = key;
    try {{
      const data = await api('/__dbvd_llm_decide', {{
        method: 'POST',
        body: JSON.stringify({{
          taskId: task && task.id || getAssignedTaskId() || '',
          accountId: task && (task.accountId || task.currentAccountId) || getAssignedAccountId() || '',
          stage,
          taskStatus: task && task.status || '',
          targetSeconds: taskTargetDurationSeconds(task || {{}}),
          prompt: task && task.prompt || '',
          pageStatus: mergedPageStatus,
          candidateVideos: Array.isArray(extra && extra.candidateVideos) ? extra.candidateVideos : [],
          deterministicCandidate: extra && extra.deterministicCandidate || {{}},
          task: task ? {{
            id: task.id,
            title: task.title || '',
            status: task.status || '',
            stage: task.stage || '',
            accountId: task.accountId || task.currentAccountId || '',
            prompt: task.prompt || '',
            doubaoTargetDuration: task.doubaoTargetDuration || task.targetDuration || task.duration || '',
            resultGuard: task.resultGuard || {{}},
            beforeVideoKeys: task.beforeVideoKeys || [],
            doubaoResultKey: task.doubaoResultKey || '',
            assetUrl: task.assetUrl || '',
            backupUrl: task.backupUrl || '',
          }} : {{}},
          text,
          ...extra,
        }}),
      }});
      const decision = data && data.decision || null;
      worker.lastLlmDecision = decision;
      if (decision && task && task.id) {{
        await updateTask({{
          id: task.id,
          doubaoLlmDecision: decision,
          doubaoLlmDecisionAt: Date.now(),
          doubaoLlmAction: decision.action || '',
          doubaoAutomationDiagnostic: {{
            reason: 'llm-recovery-decision',
            at: Date.now(),
            action: decision.action || '',
            source: decision.source || '',
            usedLlm: Boolean(decision.usedLlm),
            confidence: decision.confidence || 0,
            message: decision.reason || '',
          }},
        }}).catch(() => {{}});
      }}
      return decision;
    }} catch {{
      return null;
    }}
  }}

  async function sendAutomationReply(task, reply, stage = 'llm-reply-continue') {{
    const text = String(reply || durationConfirmReply(task)).trim();
    if (!text) return false;
    const input = findAnyPromptInput() || findPromptInput();
    if (!input) throw new Error('豆包页面需要回复，但未找到可写输入框');
    toast(`自动回复：${{text}}`);
    await setPrompt(input, text, {{ allowChatReply: true }});
    await waitForPromptValue(input, text, {{ allowChatReply: true }});
    await submitDurationConfirmReply(input);
    await sleep(1200);
    return true;
  }}

  async function applyLlmAutomationDecision(task, decision, context = {{}}) {{
    if (!decision || !decision.action || decision.action === 'no_action') return '';
    const action = String(decision.action || '');
    const reason = String(decision.reason || '');
    const pageStatus = context.pageStatus || null;
    if (action === 'wait_generation') {{
      if (reason) toast(reason);
      return '';
    }}
    if (action === 'reply_continue') {{
      await sendAutomationReply(task, decision.reply || durationConfirmReply(task), 'llm-reply-continue');
      return 'reply';
    }}
    if (action === 'reply_correction') {{
      const reply = decision.reply || `请直接生成${{taskTargetDurationSeconds(task || {{}})}}秒视频，不要输出提示词或文案。`;
      await sendAutomationReply(task, reply, 'llm-reply-correction');
      return 'reply';
    }}
    if (action === 'redirect_video_surface') {{
      toast(reason || 'LLM 判断当前不在视频生成流程，正在切回视频生成页');
      await ensureVideoSurface();
      await assertVideoSurfaceForSubmit(task || {{ id: getAssignedTaskId() }}, 'llm-redirect-video-surface');
      return 'redirect';
    }}
    if (action === 'activate_result') {{
      await updateTask({{ id: task.id, status: 'submitted', stage: 'llm-result-detected', doubaoPageMessage: reason || 'LLM 判断页面已有视频结果，正在抓取' }}).catch(() => {{}});
      toast(reason || 'LLM 判断页面已有视频结果，正在抓取');
      await activateLatestResultCard();
      return 'activate';
    }}
    if (action === 'refresh_result') {{
      const delay = Number(decision.retryDelayMs || 0);
      await updateTask({{ id: task.id, status: 'submitted', stage: 'llm-refresh-result-page', doubaoRefreshReason: 'llm-recovery', doubaoPageMessage: reason || 'LLM 建议刷新页面恢复抓取' }}).catch(() => {{}});
      toast(reason || 'LLM 建议刷新页面恢复抓取');
      if (delay) await sleep(Math.min(delay, 120000));
      if (!(await devtoolsRefreshTaskPage(task.id, 'llm-recovery'))) location.reload();
      await sleep(8000);
      return 'refresh';
    }}
    if (action === 'resubmit_prompt') {{
      if (hasActiveDoubaoGenerationSignal(pageStatus)) return '';
      await updateTask({{ id: task.id, status: 'submitted', stage: 'llm-resubmit-prompt', doubaoPageMessage: reason || 'LLM 建议重新提交提示词' }}).catch(() => {{}});
      await resubmitGenerationTask(task, Number(context.beforeFailureCount || 0));
      return 'resubmit';
    }}
    if (action === 'fail_hard') {{
      throw new Error(reason || 'LLM 判断豆包页面当前状态不可自动恢复');
    }}
    return '';
  }}

  async function reportAutomationDiagnostic(taskId, reason, extra = {{}}) {{
    const id = taskId || getAssignedTaskId();
    if (!id) return null;
    try {{
      return await updateTask({{
        id,
        stage: extra.stage || 'automation-diagnostic',
        doubaoAutomationDiagnostic: {{
          reason: String(reason || ''),
          at: Date.now(),
          ...extra,
        }},
      }});
    }} catch {{
      return null;
    }}
  }}

  async function heartbeat() {{
    await api('/__dbvd_heartbeat', {{
      method: 'POST',
      body: JSON.stringify({{
        url: location.href,
        title: document.title,
        extensionId: 'devtools-automation',
        version: cfg.version,
        userAgent: navigator.userAgent,
        accountId: getAssignedAccountId(),
        taskId: getAssignedTaskId(),
        durationCapability: inspectDoubaoDurationCapability(),
      }}),
    }}).catch(() => {{}});
  }}

  function toast(text) {{
    let root = document.getElementById('vmo-doubao-devtools-status');
    if (!root) {{
      root = document.createElement('div');
      root.id = 'vmo-doubao-devtools-status';
      root.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483647;max-width:360px;padding:12px 14px;border-radius:8px;background:#111827;color:#fff;font:13px/1.5 system-ui;box-shadow:0 12px 32px rgba(0,0,0,.28)';
      document.documentElement.appendChild(root);
    }}
    root.textContent = text;
  }}

  function getAssignedTaskId() {{
    try {{
      const url = new URL(location.href);
      const fromUrl = url.searchParams.get('dbvdTaskId') || (url.hash.match(/dbvdTaskId=([^&]+)/) || [])[1] || '';
      if (fromUrl) {{
        const value = decodeURIComponent(fromUrl);
        sessionStorage.setItem('dbvdTaskId', value);
        worker.desiredTaskId = value;
        return value;
      }}
    }} catch {{}}
    const stored = sessionStorage.getItem('dbvdTaskId') || '';
    return worker.desiredTaskId || stored || cfg.taskId || '';
  }}

  function getAssignedAccountId() {{
    try {{
      const url = new URL(location.href);
      const fromUrl = url.searchParams.get('dbvdAccountId') || (url.hash.match(/dbvdAccountId=([^&]+)/) || [])[1] || '';
      if (fromUrl) {{
        const value = decodeURIComponent(fromUrl);
        sessionStorage.setItem('dbvdAccountId', value);
        worker.accountId = value;
        return value;
      }}
    }} catch {{}}
    return worker.accountId || sessionStorage.getItem('dbvdAccountId') || cfg.accountId || '';
  }}

  async function assertActive(task, stage = '') {{
    if (!task || !task.id) return task;
    const latest = await getTaskStatus(task.id);
    if (latest && latest.status === 'cancelled') throw new Error(latest.error || '任务已停止');
    const runnerAccountId = getAssignedAccountId();
    if (latest && runnerAccountId && latest.accountId && String(latest.accountId) !== String(runnerAccountId)) {{
      throw new Error('任务已切换到其它豆包账号');
    }}
    try {{ await updateTask({{ id: task.id, automationHeartbeatAt: Date.now(), stage }}); }} catch {{}}
    return latest || task;
  }}

  async function syncDoubaoPageStatus(task, stage = '') {{
    if (!task || !task.id) return null;
    const now = Date.now();
    if (now - Number(worker.lastDoubaoStatusSyncAt || 0) < DOUBAO_STATUS_SYNC_INTERVAL_MS) return null;
    worker.lastDoubaoStatusSyncAt = now;
    const pageStatus = parseDoubaoPageStatus(task);
    try {{
      await updateTask({{
        id: task.id,
        automationHeartbeatAt: now,
        stage: pageStatus.stage || stage,
        doubaoPageStatus: pageStatus.status,
        doubaoPageMessage: pageStatus.message,
        doubaoProgress: pageStatus.progress,
        doubaoCreditCost: pageStatus.creditCost,
        doubaoCreditRemaining: pageStatus.creditRemaining,
        doubaoEstimatedWait: pageStatus.estimatedWait,
        doubaoModel: pageStatus.model,
        doubaoFailureReason: pageStatus.failureReason,
        doubaoSignals: pageStatus.signals,
        doubaoLastText: pageStatus.sample,
      }});
    }} catch {{}}
    if (pageStatus.message) toast(pageStatus.message);
    return pageStatus;
  }}

  function hasActiveDoubaoGenerationSignal(pageStatus) {{
    if (!pageStatus) return false;
    if (['generating', 'queued-signal', 'duration-confirm', 'completed-signal'].includes(pageStatus.status)) return true;
    if (pageStatus.progress && pageStatus.progress > 0) return true;
    if (pageStatus.creditCost && pageStatus.creditCost > 0) return true;
    return false;
  }}

  async function preserveActiveGenerationOnError(task, message, stage = 'waiting-result') {{
    if (!task || !task.id) return false;
    if (getHardGenerationFailureMessage() || DOUBAO_HARD_FAILURE_PATTERN.test(String(message || ''))) return false;
    let pageStatus = null;
    try {{
      pageStatus = parseDoubaoPageStatus(task);
    }} catch {{}}
    if (!hasActiveDoubaoGenerationSignal(pageStatus)) return false;
    await updateTask({{
      id: task.id,
      status: 'submitted',
      error: '',
      automationHeartbeatAt: Date.now(),
      stage: pageStatus.stage || stage,
      doubaoPageStatus: pageStatus.status,
      doubaoPageMessage: pageStatus.message || String(message || ''),
      doubaoProgress: pageStatus.progress,
      doubaoCreditCost: pageStatus.creditCost,
      doubaoCreditRemaining: pageStatus.creditRemaining,
      doubaoEstimatedWait: pageStatus.estimatedWait,
      doubaoModel: pageStatus.model,
      doubaoFailureReason: pageStatus.failureReason,
      doubaoSignals: pageStatus.signals,
      doubaoLastText: pageStatus.sample,
    }}).catch(() => {{}});
    if (pageStatus.message) toast(pageStatus.message);
    return true;
  }}

  function isLocalBridgeDisconnect(error) {{
    const message = String(error && error.message || error || '');
    return /Failed to fetch|NetworkError|Load failed|ERR_CONNECTION|ECONNREFUSED|fetch/i.test(message);
  }}

  function parseDoubaoPageStatus(task = null) {{
    const raw = statusSourceTextForTask(task);
    const text = raw.replace(/\\s+/g, ' ').trim();
    const normalized = normalizeStatusText(text);
    const sourceText = normalized.slice(-3200);
    const progressMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.progress);
    const waitMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.wait);
    const costMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.creditCost);
    const remainMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.creditRemaining);
    const modelMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.model);
    const failureSourceText = currentFailureSourceText(task, sourceText);
    const failedMatch = firstStatusMatch(failureSourceText, DOUBAO_STATUS_PATTERNS.failed);
    const completedMatch = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.completed);
    const completedKeywordIndex = latestCompletedStatusIndex(sourceText);
    const guard = task && task.resultGuard || {{}};
    const completed = (guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore)
      ? detectGenerationCompletedNotice(task)
      : Boolean(hasCompletedStatusSignal(sourceText) || completedMatch);
    const failedIndex = failedMatch ? failedMatch.index : -1;
    const failureCompletedIndex = latestCompletedStatusIndex(failureSourceText);
    const completedIndex = Math.max(completedMatch ? completedMatch.index : -1, completedKeywordIndex);
    const confirming = detectDurationAdjustQuestion(task);
    const queued = hasAnyStatusKeyword(sourceText, DOUBAO_STATUS_KEYWORDS.queued);
    const generating = firstStatusMatch(sourceText, DOUBAO_STATUS_PATTERNS.generating) || hasAnyStatusKeyword(sourceText, DOUBAO_STATUS_KEYWORDS.generating) || queued;
    const progressNumber = progressMatch ? firstNumber(progressMatch) : null;
    const progress = progressNumber !== null ? Math.max(0, Math.min(100, progressNumber)) : (completed ? 95 : (generating ? 35 : 0));
    const creditCost = costMatch ? firstNumber(costMatch) : null;
    const creditRemaining = remainMatch ? firstNumber(remainMatch) : null;
    const estimatedWait = waitMatch ? cleanStatusValue(waitMatch[1]) : '';
    const model = modelMatch ? cleanStatusValue(modelMatch[1]) : '';
    let status = 'idle';
    let stage = '';
    let message = '';
    if (failedMatch && failedIndex >= failureCompletedIndex) {{
      status = 'failed-signal';
      stage = 'doubao-failure-signal';
      message = cleanStatusValue(failedMatch[0]) || '豆包提示生成失败';
    }} else if (completed) {{
      status = 'completed-signal';
      stage = 'doubao-completed';
      message = '豆包已提示视频生成完成，正在抓取结果';
    }} else if (confirming) {{
      status = 'duration-confirm';
      stage = 'doubao-duration-confirm';
      message = '豆包要求确认 ' + taskTargetDurationSeconds(task || {{}}) + ' 秒生成，正在自动回复';
    }} else if (generating) {{
      status = queued && !hasAnyStatusKeyword(sourceText, DOUBAO_STATUS_KEYWORDS.generating) ? 'queued-signal' : 'generating';
      stage = status === 'queued-signal' ? 'doubao-queued' : 'doubao-generating';
      message = buildDoubaoGeneratingMessage({{ creditCost, creditRemaining, estimatedWait, model, progress, queued: status === 'queued-signal' }});
    }}
    const signals = {{
      queued: Boolean(queued),
      generating: Boolean(generating),
      completed: Boolean(completed),
      failed: Boolean(failedMatch),
      confirming: Boolean(confirming),
      creditCost: Boolean(costMatch),
      creditRemaining: Boolean(remainMatch),
      estimatedWait: Boolean(waitMatch),
      progress: Boolean(progressMatch),
      model: Boolean(modelMatch),
    }};
    return {{
      status,
      stage,
      message,
      progress,
      creditCost,
      creditRemaining,
      estimatedWait,
      model,
      failureReason: failedMatch ? cleanStatusValue(failedMatch[0]) : '',
      signals,
      sample: sourceText.slice(-360),
    }};
  }}

  function statusSourceTextForTask(task = null, limit = 3200) {{
    const pageText = getPageTextWithoutPanel();
    const guard = task && task.resultGuard || {{}};
    const hasGuard = Boolean(guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore || (guard.promptProbe || []).length);
    if (!hasGuard) return pageText.slice(-limit);
    const parts = [];
    const add = (value) => {{
      const text = normalizeStatusText(value);
      if (text && !parts.includes(text)) parts.push(text);
    }};
    for (const video of extractVisibleResultVideos()) {{
      if (scoreTaskVideoCandidate(video, new Set(), guard) > -Infinity) add(video.cardText || '');
    }}
    const probes = Array.isArray(guard.promptProbe) ? guard.promptProbe : [];
    const normalizedPage = normalizeStatusText(pageText);
    let latestProbeIndex = -1;
    for (const probe of probes) {{
      const key = normalizeStatusText(probe).slice(0, 80);
      if (!key || key.length < 12) continue;
      latestProbeIndex = Math.max(latestProbeIndex, normalizedPage.lastIndexOf(key));
    }}
    if (latestProbeIndex >= 0) add(normalizedPage.slice(latestProbeIndex, latestProbeIndex + limit));
    const anchorBottom = Number(guard.anchorBottom || 0);
    if (anchorBottom) {{
      const nearTexts = [...document.querySelectorAll('article, [data-testid], [role="article"], [class*="message"], [class*="chat"], [class*="card"], [class*="result"], video')]
        .filter((node) => node instanceof HTMLElement && node.offsetParent !== null && !node.closest('#vmo-doubao-devtools-status'))
        .map((node) => {{
          const root = closestResultCard(node) || node;
          const rect = root.getBoundingClientRect?.();
          const text = String(root.innerText || root.textContent || '').replace(/\s+/g, ' ').trim();
          return {{ rect, text }};
        }})
        .filter((item) => item.rect && item.text && item.rect.bottom >= anchorBottom - 180)
        .sort((a, b) => a.rect.top - b.rect.top)
        .slice(-8)
        .map((item) => item.text);
      nearTexts.forEach(add);
    }}
    add(pageText.slice(-Math.min(limit, 1200)));
    return parts.join(' ').slice(-limit);
  }}

  function currentFailureSourceText(task = null, fallbackText = '') {{
    const guard = task && task.resultGuard || {{}};
    const hasGuard = Boolean(guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore || (guard.promptProbe || []).length);
    if (!hasGuard) return fallbackText || getPageTextWithoutPanel();
    const parts = [];
    const add = (value) => {{
      const text = normalizeStatusText(value);
      if (text && !parts.includes(text)) parts.push(text);
    }};
    for (const video of extractVisibleResultVideos()) {{
      if (scoreTaskVideoCandidate(video, new Set(), guard) > -Infinity) add(video.cardText || '');
    }}
    const source = statusSourceTextForTask(task, 2400);
    const promptEchoLike = Array.isArray(guard.promptProbe) && guard.promptProbe.some((probe) => {{
      const key = normalizeStatusText(probe).slice(0, 80);
      return key && key.length >= 12 && source.includes(key);
    }});
    if (!promptEchoLike) add(source);
    return parts.join(' ') || '';
  }}

  function normalizeStatusText(value) {{
    return String(value || '')
      .replace(/\\u00a0/g, ' ')
      .replace(/[，,]\\s*/g, '，')
      .replace(/\\s+/g, ' ')
      .trim();
  }}

  function completedStatusMatches(text) {{
    const value = normalizeStatusText(text);
    const patterns = [
      /你的视频生成好(?:啦|了)?(?!后|之后|以后)/gi,
      /视频生成好(?:啦|了)?(?!后|之后|以后)/gi,
      /生成好(?:啦|了)(?!后|之后|以后)/gi,
      /生成完成(?!后|之后|以后)/gi,
      /视频已生成(?!后|之后|以后)/gi,
      /已生成视频(?!后|之后|以后)/gi,
      /创作完成(?!后|之后|以后)/gi,
      /下载视频/gi,
      /保存视频/gi,
    ];
    const matches = [];
    for (const pattern of patterns) {{
      pattern.lastIndex = 0;
      let match = null;
      while ((match = pattern.exec(value))) {{
        matches.push({{ index: match.index, text: match[0] }});
      }}
    }}
    return matches.sort((a, b) => a.index - b.index);
  }}

  function hasCompletedStatusSignal(text) {{
    return completedStatusMatches(text).length > 0;
  }}

  function latestCompletedStatusIndex(text) {{
    const matches = completedStatusMatches(text);
    return matches.length ? matches[matches.length - 1].index : -1;
  }}

  function firstStatusMatch(text, patterns) {{
    for (const pattern of patterns || []) {{
      const match = String(text || '').match(pattern);
      if (match) return match;
    }}
    return null;
  }}

  function hasAnyStatusKeyword(text, keywords) {{
    const value = String(text || '').toLowerCase();
    return (keywords || []).some((item) => value.includes(String(item || '').toLowerCase()));
  }}

  function lastStatusKeywordIndex(text, keywords) {{
    const value = String(text || '').toLowerCase();
    let latest = -1;
    for (const item of keywords || []) {{
      const index = value.lastIndexOf(String(item || '').toLowerCase());
      if (index > latest) latest = index;
    }}
    return latest;
  }}

  function firstNumber(match) {{
    for (const item of Array.from(match || []).slice(1)) {{
      if (item === undefined || item === null || item === '') continue;
      const value = Number(String(item).match(/[0-9.]+/)?.[0] || NaN);
      if (Number.isFinite(value)) return value;
    }}
    return null;
  }}

  function cleanStatusValue(value) {{
    return String(value || '').replace(/\\s+/g, '').replace(/[。；;，,]+$/g, '').trim();
  }}

  function buildDoubaoGeneratingMessage(info = {{}}) {{
    const parts = [info.queued ? '豆包排队中' : '豆包正在生成'];
    if (info.model) parts.push(info.model);
    if (info.creditCost !== null && info.creditCost !== undefined) parts.push(`消耗${{info.creditCost}}额度`);
    if (info.creditRemaining !== null && info.creditRemaining !== undefined) parts.push(`剩余${{info.creditRemaining}}额度`);
    if (info.estimatedWait) parts.push(`预计${{info.estimatedWait}}`);
    if (info.progress && info.progress > 0 && info.progress < 95) parts.push(`${{info.progress}}%`);
    return parts.join(' · ');
  }}

  function visible(el) {{
    if (!(el instanceof HTMLElement)) return false;
    if (el.closest('#vmo-doubao-devtools-status')) return false;
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') <= 0.01) return false;
    return true;
  }}

  function textOf(el) {{
    if (!el) return '';
    const values = [
      el.getAttribute && el.getAttribute('aria-label'),
      el.getAttribute && el.getAttribute('title'),
      el.innerText,
      el.textContent,
    ].map(value => String(value || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
    return values.sort((a, b) => a.length - b.length)[0] || '';
  }}

  function compactText(value) {{
    return String(value || '').replace(/\\s+/g, '');
  }}

  function isMoreText(value) {{
    const compact = compactText(value);
    return compact === CN_MORE || compact === '更多' || /^More$/i.test(compact);
  }}

  function isVideoGenerationText(value) {{
    const text = String(value || '').trim();
    const compact = compactText(text);
    return compact === CN_VIDEO_GENERATION || compact === '视频生成' || /(^|\\s)视频生成(\\s|$)|Video\\s*Generation/i.test(text);
  }}

  function durationTargetPattern(seconds = DOUBAO_FIXED_VIDEO_DURATION_SECONDS) {{
    const value = String(Number(seconds) || DOUBAO_FIXED_VIDEO_DURATION_SECONDS);
    return new RegExp('(^|[^0-9])' + value + '\\s*(?:s|秒)([^0-9]|$)', 'i');
  }}

  function isDurationText(value) {{
    return /时长|duration|(^|[^0-9])(?:5|10|15)\\s*(?:s|秒)([^0-9]|$)/i.test(String(value || ''));
  }}

  function durationSecondsInText(value) {{
    const seconds = new Set();
    const pattern = /(^|[^0-9])(\\d{{1,2}})\\s*(?:s|秒)([^0-9]|$)/gi;
    let match = null;
    while ((match = pattern.exec(String(value || '')))) {{
      const number = Number(match[2]);
      if (DOUBAO_VIDEO_DURATION_OPTIONS.includes(number)) seconds.add(number);
    }}
    return [...seconds];
  }}

  function durationControlText(el) {{
    const values = [
      el && el.getAttribute && el.getAttribute('aria-label'),
      el && el.getAttribute && el.getAttribute('title'),
      el && el.innerText,
      el && el.textContent,
    ].map(value => String(value || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
    return values.sort((a, b) => a.length - b.length)[0] || '';
  }}

  function durationElementSnapshot(el) {{
    if (!(el instanceof HTMLElement)) return null;
    const rect = el.getBoundingClientRect();
    return {{
      tag: el.tagName,
      role: el.getAttribute('role') || '',
      text: durationControlText(el).slice(0, 120),
      ariaLabel: String(el.getAttribute('aria-label') || '').slice(0, 120),
      title: String(el.getAttribute('title') || '').slice(0, 120),
      className: String(el.className || '').slice(0, 120),
      rect: rectSnapshot(rect),
    }};
  }}

  function durationCandidates(seconds = DOUBAO_FIXED_VIDEO_DURATION_SECONDS) {{
    const input = findPromptInput() || findAnyPromptInput();
    const inputRect = input && input.getBoundingClientRect();
    const composerRect = input && composerRectFor(input);
    const pattern = durationTargetPattern(seconds);
    const candidates = [...document.querySelectorAll('button,[role="button"],[role="option"],[aria-label],[title],li,div,span')]
      .filter(visible)
      .map(el => clickableTargetFor(el) || el)
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => visible(el) && !isLikelyConversationTitleOrHistoryTarget(el))
      .map(el => {{
        const text = durationControlText(el);
        const rect = el.getBoundingClientRect();
        const center = rectCenter(rect);
        let score = 0;
        if (!isDurationText(text)) score -= 2000;
        if (/时长|duration/i.test(text)) score += 1200;
        if (pattern.test(text)) score += 6000;
        if (/(?:^|[^0-9])10\\s*(?:s|秒)(?:[^0-9]|$)/i.test(text)) score += 500;
        if (/(?:^|[^0-9])5\\s*(?:s|秒)(?:[^0-9]|$)/i.test(text)) score += 250;
        if (/选中|已选|selected|checked/i.test(text + ' ' + (el.getAttribute('aria-selected') || '') + ' ' + (el.getAttribute('aria-checked') || ''))) score += 400;
        if (inputRect) {{
          if (center.y < inputRect.top - 140 || center.y > inputRect.bottom + 180) score -= 1600;
          if (center.x < inputRect.left - 120 || center.x > inputRect.right + 180) score -= 900;
        }}
        if (composerRect) {{
          if (center.y < composerRect.top - 80 || center.y > composerRect.bottom + 140) score -= 1000;
          if (center.x < composerRect.left - 100 || center.x > composerRect.right + 160) score -= 700;
        }}
        score -= Math.max(0, rect.width * rect.height - 24000) / 800;
        return {{ el, text, score }};
      }})
      .filter(entry => entry.score > -500 && isDurationText(entry.text))
      .sort((a, b) => b.score - a.score);
    return candidates;
  }}

  function inspectDoubaoDurationCapability(taskOrSeconds = null) {{
    const requiredSeconds = typeof taskOrSeconds === 'number' ? selectDoubaoDuration(taskOrSeconds) : taskTargetDurationSeconds(taskOrSeconds || {{}});
    const pattern = durationTargetPattern(requiredSeconds);
    const candidates = durationCandidates(requiredSeconds);
    const labels = [...new Set(candidates.map(entry => entry.text).filter(Boolean))].slice(0, 30);
    const availableSeconds = [...new Set(labels.flatMap(durationSecondsInText))].sort((a, b) => a - b);
    const target = candidates.find(entry => pattern.test(entry.text));
    const hasTarget = Boolean(target) || availableSeconds.includes(requiredSeconds);
    return {{
      requiredSeconds,
      availableSeconds,
      hasTarget,
      selectedTarget: Boolean(target && /选中|已选|selected|checked|true/i.test(target.text + ' ' + (target.el.getAttribute('aria-selected') || '') + ' ' + (target.el.getAttribute('aria-checked') || ''))),
      options: labels,
      target: target ? durationElementSnapshot(target.el) : null,
      message: hasTarget ? '豆包页面已检测到 ' + requiredSeconds + ' 秒选项' : '当前豆包页面未检测到 ' + requiredSeconds + ' 秒选项',
    }};
  }}

  async function ensureFixedDurationSelected(task = null) {{
    const requiredSeconds = taskTargetDurationSeconds(task || {{}});
    const pattern = durationTargetPattern(requiredSeconds);
    const before = inspectDoubaoDurationCapability(task || requiredSeconds);
    let candidates = durationCandidates(requiredSeconds);
    let visibleSeconds = [...new Set(candidates.flatMap(entry => durationSecondsInText(entry.text)))];
    if (visibleSeconds.length === 1 && visibleSeconds[0] === requiredSeconds) {{
      await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'duration-already-selected', {{
        stage: 'duration-already-selected',
        requiredSeconds,
        before,
      }});
      return before;
    }}
    let target = candidates.find(entry => pattern.test(entry.text));
    if (!target) {{
      const opener = candidates[0];
      if (opener) {{
        if (!(await devtoolsClick(opener.el, task && task.id || getAssignedTaskId()))) clickElement(opener.el);
        await sleep(500);
        candidates = durationCandidates(requiredSeconds);
        visibleSeconds = [...new Set(candidates.flatMap(entry => durationSecondsInText(entry.text)))];
        if (visibleSeconds.length === 1 && visibleSeconds[0] === requiredSeconds) {{
          const after = inspectDoubaoDurationCapability(task || requiredSeconds);
          await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'duration-already-selected', {{
            stage: 'duration-already-selected',
            requiredSeconds,
            before,
            after,
          }});
          return after;
        }}
        target = candidates.find(entry => pattern.test(entry.text));
      }}
    }}
    if (!target) {{
      const after = inspectDoubaoDurationCapability(task || requiredSeconds);
      await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'fixed-duration-not-available', {{
        stage: 'fixed-duration-not-available',
        requiredSeconds,
        before,
        after,
        pageTextTail: getPageTextWithoutPanel().replace(/\\s+/g, ' ').slice(-500),
      }});
      throw new Error('豆包目标 ' + requiredSeconds + ' 秒未在页面生效：当前持续时间控件未出现对应选项，请确认页面处于视频生成模式');
    }}
    if (!(await devtoolsClick(target.el, task && task.id || getAssignedTaskId()))) clickElement(target.el);
    await sleep(350);
    const after = inspectDoubaoDurationCapability(task || requiredSeconds);
    await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'fixed-duration-selected', {{
      stage: 'fixed-duration-selected',
      requiredSeconds,
      before,
      after,
    }});
    return after;
  }}

  function elementTextContext(el) {{
    if (!el) return '';
    const parts = [
      el.getAttribute && el.getAttribute('placeholder'),
      el.getAttribute && el.getAttribute('aria-label'),
      el.getAttribute && el.getAttribute('data-placeholder'),
      el.getAttribute && el.getAttribute('title'),
      el.innerText,
      el.textContent,
    ];
    let node = el.parentElement;
    for (let i = 0; node && i < 4; i++, node = node.parentElement) {{
      parts.push(node.getAttribute && node.getAttribute('aria-label'));
      parts.push(node.innerText || node.textContent || '');
    }}
    return parts.map(value => String(value || '').replace(/\\s+/g, ' ').trim()).filter(Boolean).join(' ');
  }}

  function isInsideConversationTitleDialog(el) {{
    let node = el;
    for (let i = 0; node && i < 8; i++, node = node.parentElement) {{
      if (!(node instanceof HTMLElement)) break;
      const text = String(node.innerText || node.textContent || '');
      const role = String(node.getAttribute('role') || '').toLowerCase();
      if ((role === 'dialog' || /modal|dialog|popover/i.test(String(node.className || ''))) && /编辑对话名称|对话名称|conversation name|rename/i.test(text)) return true;
    }}
    return false;
  }}

  function findConversationTitleDialog() {{
    return [...document.querySelectorAll('[role="dialog"],[aria-modal="true"],div,section')]
      .filter(el => el instanceof HTMLElement && visible(el) && !el.closest('#vmo-doubao-devtools-status'))
      .find(el => {{
        const rect = el.getBoundingClientRect();
        if (rect.width < 180 || rect.height < 80) return false;
        const text = String(el.innerText || el.textContent || '');
        return /编辑对话名称|对话名称|conversation name|rename/i.test(text);
      }}) || null;
  }}

  function isConversationTitleEditModalOpen() {{
    return Boolean(findConversationTitleDialog());
  }}

  function isAllowedAutomationNavigationText(text) {{
    const compact = compactText(text);
    return compact === 'AI创作'
      || compact === CN_MORE
      || compact === '更多'
      || compact === CN_VIDEO
      || compact === '视频'
      || compact === CN_VIDEO_GENERATION
      || compact === '视频生成'
      || compact === '根据图片生成视频'
      || compact === '制作特定动作视频'
      || /添加照片|参考图|首帧|尾帧|上传|图片生成视频|动作视频|Create|Video\\s*Generation/i.test(String(text || ''));
  }}

  function isLikelyConversationTitleOrHistoryTarget(el) {{
    if (!(el instanceof HTMLElement) || el.closest('#vmo-doubao-devtools-status')) return false;
    if (isInsideConversationTitleDialog(el)) return false;
    const rect = el.getBoundingClientRect();
    const text = textOf(el);
    const raw = elementTextContext(el);
    if (/编辑对话名称|对话名称|conversation name|rename/i.test(raw)) return true;
    const center = rectCenter(rect);
    const inTopTitleBar = rect.top < Math.max(112, window.innerHeight * 0.12)
      && center.x > Math.max(360, window.innerWidth * 0.24)
      && center.x < window.innerWidth - 220
      && rect.width < Math.min(520, window.innerWidth * 0.48);
    if (inTopTitleBar) return true;
    if (isAllowedAutomationNavigationText(text)) return false;
    const inLeftHistoryList = rect.left < Math.max(420, window.innerWidth * 0.34)
      && rect.top > Math.max(260, window.innerHeight * 0.20)
      && rect.height <= 96
      && /历史对话|主对话|生成视频|\\d+秒|短视频|对话|chat/i.test(raw);
    return inLeftHistoryList;
  }}

  async function closeConversationTitleModal() {{
    const dialog = findConversationTitleDialog();
    if (!dialog) return false;
    const button = [...dialog.querySelectorAll('button,[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .map(el => nearestClickable(el) || el)
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .find(el => /取消|关闭|Close|Cancel/i.test(textOf(el)));
    if (button) {{
      clickElement(button);
      await sleep(300);
    }} else {{
      document.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }}));
      document.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }}));
      await sleep(300);
    }}
    return !isConversationTitleEditModalOpen();
  }}

  function isPromptInputRejected(el) {{
    if (!(el instanceof HTMLElement) || el.closest('#vmo-doubao-devtools-status')) return true;
    if (isInsideConversationTitleDialog(el)) return true;
    const text = elementTextContext(el);
    if (/编辑对话名称|对话名称|标题|名称|rename|conversation name/i.test(text)) return true;
    if (/搜索|历史|评论|comment|search/i.test(text)) return true;
    return false;
  }}

  function isImageModePromptInput(el) {{
    const text = elementTextContext(el);
    const hasVideo = /描述你想(?:要|生成)的视频|添加照片[^。；;]*视频|生成视频|Seedance|首帧|尾帧|时长|分辨率/i.test(text);
    const hasImage = /描述你想要的图片|生成图片|Seedream|AI\\s*抠图|擦除|图像模式/i.test(text);
    return hasImage && !hasVideo;
  }}

  function isVideoPromptInput(el) {{
    if (!(el instanceof HTMLElement) || isPromptInputRejected(el)) return false;
    if (isImageModePromptInput(el)) return false;
    const text = elementTextContext(el);
    return /描述你想(?:要|生成)的视频|添加照片[^。；;]*视频|生成视频|Seedance|首帧|尾帧|时长|分辨率|视频模式/i.test(text);
  }}

  function findVideoPromptInput() {{
    const candidates = [...document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]')]
      .filter(el => visible(el) && isVideoPromptInput(el));
    return candidates.map(el => {{
      const rect = el.getBoundingClientRect();
      let score = rect.width * rect.height / 1000;
      const text = elementTextContext(el);
      if (/描述你想(?:要|生成)的视频|添加照片[^。；;]*视频/i.test(text)) score += 18000;
      if (/Seedance|时长|分辨率|首帧|尾帧/i.test(text)) score += 8000;
      if (rect.bottom > window.innerHeight * 0.35) score += 1000;
      return {{ el, score }};
    }}).sort((a, b) => b.score - a.score)[0]?.el || null;
  }}

  function findAnyPromptInput() {{
    const candidates = [...document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]')]
      .filter(el => visible(el) && !isPromptInputRejected(el));
    return candidates.sort((a, b) => {{
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (br.width * br.height + br.bottom) - (ar.width * ar.height + ar.bottom);
    }})[0] || null;
  }}

  function scoreComposerVideoModeTab(el, inputRect, composerRect, source = null) {{
    if (!(el instanceof HTMLElement)) return -Infinity;
    if (isLikelyConversationTitleOrHistoryTarget(el) || isLikelyConversationTitleOrHistoryTarget(source)) return -Infinity;
    const rect = el.getBoundingClientRect();
    if (rect.width < 24 || rect.height < 18 || rect.width > 120 || rect.height > 72) return -Infinity;
    const targetText = compactText(textOf(el));
    const sourceText = compactText(textOf(source || el));
    if (targetText !== CN_VIDEO && targetText !== '视频' && sourceText !== CN_VIDEO && sourceText !== '视频') return -Infinity;
    const center = rectCenter(rect);
    if (inputRect) {{
      if (center.x < inputRect.left - 80 || center.x > inputRect.right + 140) return -Infinity;
      if (center.y < inputRect.top - 40 || center.y > inputRect.bottom + 170) return -Infinity;
    }} else if (rect.top < window.innerHeight * 0.35) {{
      return -Infinity;
    }}
    if (composerRect) {{
      if (center.x < composerRect.left - 40 || center.x > composerRect.right + 40) return -Infinity;
      if (center.y < composerRect.top - 20 || center.y > composerRect.bottom + 40) return -Infinity;
    }}
    let score = 12000;
    if (inputRect) score += 1200 - Math.min(1200, Math.abs(center.y - (inputRect.bottom + 38)));
    if (composerRect) score += 700 - Math.min(700, Math.abs(center.x - (composerRect.left + composerRect.width * 0.18)));
    score -= rect.width * rect.height / 500;
    return score;
  }}

  function findComposerVideoModeTab(input = null) {{
    const inputNode = input || findAnyPromptInput() || findVideoPromptInput();
    const inputRect = inputNode ? inputNode.getBoundingClientRect() : null;
    const composerRect = inputNode ? composerRectFor(inputNode) : null;
    const scored = [...document.querySelectorAll('button,[role="button"],[role="tab"],[aria-label],[title],div,span')]
      .filter(visible)
      .map(source => {{
        const target = clickableTargetFor(source) || source;
        const targetScore = scoreComposerVideoModeTab(target, inputRect, composerRect, source);
        const sourceScore = scoreComposerVideoModeTab(source, inputRect, composerRect, source);
        return {{ el: targetScore >= sourceScore ? target : source, source, score: Math.max(targetScore, sourceScore) }};
      }})
      .filter((entry, index, list) => entry.el instanceof HTMLElement && list.findIndex(other => other.el === entry.el) === index)
      .filter(entry => Number.isFinite(entry.score))
      .sort((a, b) => b.score - a.score);
    return scored[0]?.el || null;
  }}

  async function ensureVideoModeSelected() {{
    await closeConversationTitleModal();
    if (isVideoSurface()) return true;
    const tab = findComposerVideoModeTab();
    if (!tab) return false;
    clickElement(tab);
    for (let i = 0; i < 8; i++) {{
      await sleep(500);
      if (isVideoSurface()) return true;
    }}
    return false;
  }}

  async function assertVideoSurfaceForSubmit(task = null, stage = 'submit') {{
    await closeConversationTitleModal();
    const input = findVideoPromptInput();
    if (isVideoSurface() && input && !isConversationTitleEditModalOpen()) return input;
    const anyInput = findAnyPromptInput() || findPromptInput();
    await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'video-surface-not-ready', {{
      stage,
      href: location.href,
      imageMode: Boolean(anyInput && isImageModePromptInput(anyInput)),
      titleModal: isConversationTitleEditModalOpen(),
      inputContext: elementTextContext(anyInput).slice(0, 500),
      submitDebug: submitDebugInfo(anyInput),
      pageTextTail: getPageTextWithoutPanel().replace(/\\s+/g, ' ').slice(-500),
    }});
    throw new Error('不在 AI 创作视频页：当前未切到“视频”模式，已阻止误提交');
  }}

  function rectCenter(rect) {{
    return {{ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }};
  }}

  function nearestClickable(el) {{
    let node = el;
    for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {{
      if (!(node instanceof HTMLElement) || node === document.body || node === document.documentElement) break;
      if (!visible(node)) continue;
      const role = String(node.getAttribute('role') || '').toLowerCase();
      const tag = node.tagName.toLowerCase();
      const style = getComputedStyle(node);
      if (tag === 'button' || tag === 'a' || tag === 'label' || role === 'button' || role === 'menuitem' || role === 'tab') return node;
      if (node.hasAttribute('tabindex') && style.cursor === 'pointer') return node;
      if (typeof node.onclick === 'function' || style.cursor === 'pointer') return node;
    }}
    return null;
  }}

  function clickableTargetFor(el) {{
    if (!(el instanceof Element)) return null;
    const base = el instanceof HTMLElement ? el : el.parentElement;
    if (!(base instanceof HTMLElement)) return null;
    const rect = base.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    const hitBase = hit instanceof HTMLElement ? hit : hit?.parentElement;
    const target = nearestClickable(hitBase) || nearestClickable(base) || base;
    return isLikelyConversationTitleOrHistoryTarget(target) ? null : target;
  }}

  function findComposerRoot(input) {{
    if (!input) return null;
    const inputRect = input.getBoundingClientRect();
    const choices = [];
    let node = input;
    for (let depth = 0; node && depth < 9; depth++, node = node.parentElement) {{
      if (!(node instanceof HTMLElement) || node === document.body || node === document.documentElement) continue;
      const rect = node.getBoundingClientRect();
      if (rect.width < Math.max(260, window.innerWidth * 0.24)) continue;
      if (rect.height < Math.max(36, inputRect.height)) continue;
      if (rect.height > Math.min(420, window.innerHeight * 0.58)) continue;
      if (rect.bottom < window.innerHeight * 0.42) continue;
      if (inputRect.left < rect.left - 8 || inputRect.right > rect.right + 8) continue;
      const rawText = String(node.innerText || node.textContent || '');
      let score = 0;
      score += Math.min(rect.width, 1100) / 16;
      score += rect.bottom > window.innerHeight * 0.66 ? 240 : 0;
      score += Math.max(0, 260 - Math.abs(rect.bottom - inputRect.bottom));
      score -= Math.max(0, rect.height - 240);
      if (/热点|历史对话|下载电脑版/.test(rawText) && rawText.length > 120) score -= 500;
      choices.push({{ node, score }});
    }}
    choices.sort((a, b) => b.score - a.score);
    return choices[0]?.node || null;
  }}

  function composerRectFor(input) {{
    const inputRect = input && input.getBoundingClientRect();
    const root = input && findComposerRoot(input);
    if (root) return root.getBoundingClientRect();
    if (!inputRect) return null;
    const left = Math.max(0, inputRect.left - 48);
    const right = Math.min(window.innerWidth, Math.max(inputRect.right + 360, inputRect.right));
    const top = Math.max(0, inputRect.top - 130);
    const bottom = Math.min(window.innerHeight, inputRect.bottom + 170);
    return {{ left, right, top, bottom, width: right - left, height: bottom - top }};
  }}

  function rectLabel(el) {{
    if (!el) return 'none';
    const rect = el.getBoundingClientRect();
    return `${{Math.round(rect.left)}},${{Math.round(rect.top)}},${{Math.round(rect.width)}}x${{Math.round(rect.height)}}:${{textOf(el).slice(0, 16)}}`;
  }}

  function scoreBottomMore(el, inputRect, composerRect) {{
    if (!(el instanceof HTMLElement) || isLikelyConversationTitleOrHistoryTarget(el)) return -Infinity;
    const rect = el.getBoundingClientRect();
    const center = rectCenter(rect);
    if (rect.width < 18 || rect.height < 18 || rect.width > 180 || rect.height > 88) return -Infinity;
    if (rect.top < window.innerHeight * 0.36) return -Infinity;
    if (inputRect) {{
      if (center.x < inputRect.left - 90) return -Infinity;
      if (rect.bottom < inputRect.top - 130 || rect.top > inputRect.bottom + 190) return -Infinity;
    }}
    if (composerRect) {{
      if (center.x < composerRect.left - 72 || center.x > composerRect.right + 72) return -Infinity;
      if (center.y < composerRect.top - 100 || center.y > composerRect.bottom + 100) return -Infinity;
    }}
    let score = 1000;
    score += center.x / 3;
    score += center.y / 8;
    if (composerRect) score += 900 - Math.min(900, Math.abs(center.y - composerRect.bottom + 34));
    if (inputRect) score += 700 - Math.min(700, Math.abs(center.y - inputRect.bottom));
    score -= (rect.width * rect.height) / 180;
    return score;
  }}

  function scoreDirectVideoEntry(target, source, inputRect, composerRect) {{
    if (!(target instanceof HTMLElement) || isLikelyConversationTitleOrHistoryTarget(target) || isLikelyConversationTitleOrHistoryTarget(source)) return -Infinity;
    const rect = target.getBoundingClientRect();
    const center = rectCenter(rect);
    const sourceCompact = compactText(textOf(source || target));
    const targetCompact = compactText(textOf(target));
    const xSlack = Math.max(96, Math.min(320, window.innerWidth * 0.18));
    const ySlack = Math.max(96, Math.min(240, window.innerHeight * 0.18));
    const toolbarSlack = Math.max(90, Math.min(220, (composerRect?.height || 120) * 1.8));
    if (sourceCompact !== CN_VIDEO_GENERATION && sourceCompact !== '视频生成' && targetCompact !== CN_VIDEO_GENERATION && targetCompact !== '视频生成') return -Infinity;
    if (rect.width < 36 || rect.height < 20 || rect.width > 180 || rect.height > 88) return -Infinity;
    if (rect.top < window.innerHeight * 0.35) return -Infinity;
    if (inputRect) {{
      if (center.x < inputRect.left - xSlack || center.x > inputRect.right + xSlack) return -Infinity;
      if (center.y < inputRect.top - ySlack || center.y > inputRect.bottom + ySlack) return -Infinity;
    }}
    if (composerRect) {{
      if (center.x < composerRect.left - 72 || center.x > composerRect.right + 72) return -Infinity;
      if (center.y < composerRect.bottom - toolbarSlack || center.y > composerRect.bottom + Math.max(72, window.innerHeight * 0.08)) return -Infinity;
    }}
    let score = 10000;
    if (sourceCompact === CN_VIDEO_GENERATION || sourceCompact === '视频生成') score += 6000;
    if (targetCompact === CN_VIDEO_GENERATION || targetCompact === '视频生成') score += 3000;
    if (inputRect) score += 1200 - Math.min(1200, Math.abs(center.y - (inputRect.bottom + 38)));
    if (composerRect) score += 1200 - Math.min(1200, Math.abs(center.y - (composerRect.bottom - 34)));
    score -= (rect.width * rect.height) / 900;
    return score;
  }}

  function clickElement(el) {{
    if (!el) return false;
    if (isLikelyConversationTitleOrHistoryTarget(el)) return false;
    el.scrollIntoView?.({{ block: 'center', inline: 'center' }});
    const rect = el.getBoundingClientRect();
    const init = {{ bubbles: true, cancelable: true, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 }};
    const target = clickableTargetFor(el);
    if (!target) return false;
    if (isLikelyConversationTitleOrHistoryTarget(target)) return false;
    target.focus?.();
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
      const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
      target.dispatchEvent(new Ctor(type, init));
    }}
    try {{ target.click?.(); }} catch {{}}
    if (target !== el) {{
      try {{ el.click?.(); }} catch {{}}
    }}
    return true;
  }}

  function findPromptInput() {{
    return findVideoPromptInput();
  }}

  function isVideoSurface() {{
    const txt = String(document.body?.innerText || '');
    const hasVideoWorkspace = txt.includes(CN_ADD_PHOTO_VIDEO_HINT)
      || txt.includes(CN_DESCRIBE_VIDEO_HINT)
      || /Seedance/i.test(txt)
      || (txt.includes(CN_VIDEO_GENERATION) && /添加照片|描述你想|生成视频|参考图|首帧|比例|分辨率|时长/i.test(txt));
    return hasVideoWorkspace && !!findVideoPromptInput();
  }}

  function findBottomMore() {{
    const input = findPromptInput();
    const inputRect = input && input.getBoundingClientRect();
    const composerRect = composerRectFor(input);
    const scored = [...document.querySelectorAll('button,[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .filter(el => isMoreText(textOf(el)))
      .map(el => {{
        const target = clickableTargetFor(el);
        return {{ el: target, source: el, score: scoreBottomMore(target, inputRect, composerRect) }};
      }})
      .filter(item => Number.isFinite(item.score))
      .filter((item, index, list) => list.findIndex(other => other.el === item.el) === index)
      .sort((a, b) => b.score - a.score);
    return scored[0]?.el || null;
  }}

  function findDirectVideoEntry() {{
    const input = findPromptInput();
    const inputRect = input && input.getBoundingClientRect();
    const composerRect = composerRectFor(input);
    const scored = [...document.querySelectorAll('button,[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .filter(el => {{
        const txt = textOf(el);
        const compact = compactText(txt);
        return txt && txt.length <= 36 && (compact === CN_VIDEO_GENERATION || compact === '视频生成');
      }})
      .map(el => {{
        const target = clickableTargetFor(el);
        return {{ el: target, source: el, score: scoreDirectVideoEntry(target, el, inputRect, composerRect) }};
      }})
      .filter(item => Number.isFinite(item.score))
      .filter((item, index, list) => list.findIndex(other => other.el === item.el) === index)
      .sort((a, b) => b.score - a.score);
    return scored[0]?.el || null;
  }}

  function findCreationHubEntry() {{
    const candidates = [...document.querySelectorAll('a,button,[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .filter(el => {{
        const txt = textOf(el);
        const compact = compactText(txt);
        const href = String(el.href || el.getAttribute?.('href') || '');
        if (href.includes('/chat/create-image')) return true;
        return compact === 'AI创作' || /AI\\s*创作|Create/i.test(txt);
      }})
      .map(el => clickableTargetFor(el))
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => !isLikelyConversationTitleOrHistoryTarget(el))
      .filter(el => {{
        const rect = el.getBoundingClientRect();
        return rect.width >= 36 && rect.height >= 20 && rect.left < Math.max(420, window.innerWidth * 0.52);
      }})
      .sort((a, b) => {{
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        const ah = String(a.href || a.getAttribute?.('href') || '');
        const bh = String(b.href || b.getAttribute?.('href') || '');
        const as = (ah.includes('/chat/create-image') ? 10000 : 0) - ar.left - ar.top / 3;
        const bs = (bh.includes('/chat/create-image') ? 10000 : 0) - br.left - br.top / 3;
        return bs - as;
      }});
    return candidates[0] || null;
  }}

  function findCreationVideoTab() {{
    const pageText = String(document.body?.innerText || '');
    if (!/AI\\s*创作|我的创作|Seedream|参考图|Previous slide|Next slide/i.test(pageText)) return null;
    const candidates = [...document.querySelectorAll('button,[role="tab"],[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .filter(el => {{
        const txt = textOf(el);
        const compact = compactText(txt);
        if (compact !== CN_VIDEO && compact !== '视频') return false;
        const rect = el.getBoundingClientRect();
        return rect.width >= 28 && rect.height >= 18
          && rect.top > window.innerHeight * 0.08
          && rect.top < window.innerHeight * 0.55
          && rect.left > Math.min(220, window.innerWidth * 0.18);
      }})
      .map(el => clickableTargetFor(el))
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => !isLikelyConversationTitleOrHistoryTarget(el))
      .sort((a, b) => {{
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        const as = ar.left + ar.top - (String(a.getAttribute?.('role') || '').toLowerCase() === 'tab' ? 5000 : 0);
        const bs = br.left + br.top - (String(b.getAttribute?.('role') || '').toLowerCase() === 'tab' ? 5000 : 0);
        return as - bs;
      }});
    return candidates[0] || null;
  }}

  function findSidebarVideoTemplateEntry() {{
    const candidates = [...document.querySelectorAll('a,button,[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .filter(el => {{
        const txt = textOf(el);
        const compact = compactText(txt);
        return compact === '根据图片生成视频' || compact === '制作特定动作视频' || /图片生成视频|动作视频|视频生成/i.test(txt);
      }})
      .map(el => clickableTargetFor(el))
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => !isLikelyConversationTitleOrHistoryTarget(el))
      .filter(el => {{
        const rect = el.getBoundingClientRect();
        return rect.width >= 36 && rect.height >= 20 && rect.left < Math.max(420, window.innerWidth * 0.52);
      }})
      .sort((a, b) => {{
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        const at = compactText(textOf(a));
        const bt = compactText(textOf(b));
        const as = (at === '根据图片生成视频' ? 5000 : 0) - ar.top - ar.left / 4;
        const bs = (bt === '根据图片生成视频' ? 5000 : 0) - br.top - br.left / 4;
        return bs - as;
      }});
    return candidates[0] || null;
  }}

  function findSidebarMoreEntry() {{
    const candidates = [...document.querySelectorAll('button,[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .filter(el => isMoreText(textOf(el)))
      .map(el => clickableTargetFor(el))
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => !isLikelyConversationTitleOrHistoryTarget(el))
      .filter(el => {{
        const rect = el.getBoundingClientRect();
        return rect.width >= 36 && rect.height >= 20
          && rect.left < Math.max(360, window.innerWidth * 0.42)
          && rect.top > 48
          && rect.top < window.innerHeight * 0.55;
      }})
      .sort((a, b) => {{
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (ar.left + ar.top / 4) - (br.left + br.top / 4);
      }});
    return candidates[0] || null;
  }}

  function isDolaHost() {{
    return /(^|\.)dola\.com$/i.test(location.hostname || '');
  }}

  function siteCreationPath() {{
    return isDolaHost() ? '/chat/' : '/chat/create-image';
  }}

  async function enterCreationHub() {{
    if (isDolaHost() && /^\\/chat\\/?$/.test(location.pathname || '')) return true;
    if (!isDolaHost() && /\\/chat\\/create-image/.test(location.pathname || '')) return true;
    const hub = findCreationHubEntry();
    if (hub) {{
      clickElement(hub);
      await sleep(2200);
      return true;
    }}
    try {{
      const next = new URL(siteCreationPath(), location.origin);
      location.href = next.toString();
      await sleep(2600);
      return true;
    }} catch {{
      return false;
    }}
  }}

  function findVideoEntry(anchorRect = null) {{
    const candidates = [...document.querySelectorAll('button,[role="button"],[role="menuitem"],[aria-label],[title],li,div,span')].filter(visible).filter(el => {{
      const txt = textOf(el);
      if (!txt || txt.length > 72) return false;
      if (!isVideoGenerationText(txt)) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width < 36 || rect.height < 20) return false;
      if (!anchorRect) return true;
      const center = rectCenter(rect);
      return center.x >= anchorRect.left - 300
        && center.x <= anchorRect.right + 430
        && center.y >= anchorRect.top - 560
        && center.y <= anchorRect.bottom + 260;
    }}).map(el => clickableTargetFor(el)).filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index).filter(el => !isLikelyConversationTitleOrHistoryTarget(el));
    return candidates.sort((a, b) => {{
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      const at = textOf(a);
      const bt = textOf(b);
      const targetX = anchorRect ? anchorRect.left + Math.min(anchorRect.width, 96) : window.innerWidth;
      const targetY = anchorRect ? anchorRect.top - 120 : window.innerHeight;
      const ac = rectCenter(ar);
      const bc = rectCenter(br);
      const as = (compactText(at) === '视频生成' ? 12000 : 0) - Math.hypot(ac.x - targetX, ac.y - targetY) - (ar.width * ar.height / 8000);
      const bs = (compactText(bt) === '视频生成' ? 12000 : 0) - Math.hypot(bc.x - targetX, bc.y - targetY) - (br.width * br.height / 8000);
      return bs - as;
    }})[0] || null;
  }}

  function collectMoreDiagnostics() {{
    return [...document.querySelectorAll('button,[role="button"],[aria-label],[title],div,span')]
      .filter(visible)
      .filter(el => isMoreText(textOf(el)))
      .slice(0, 10)
      .map(rectLabel)
      .join('|') || 'none';
  }}

  function collectVideoDiagnostics() {{
    return [...document.querySelectorAll('button,[role="button"],[role="menuitem"],[aria-label],[title],li,div,span')]
      .filter(visible)
      .filter(el => isVideoGenerationText(textOf(el)))
      .slice(0, 10)
      .map(rectLabel)
      .join('|') || 'none';
  }}

  async function ensureVideoSurface() {{
    await closeConversationTitleModal();
    if (isVideoSurface()) return;
    if (await ensureVideoModeSelected()) return;
    let creationVideo = findCreationVideoTab();
    if (creationVideo) {{
      clickElement(creationVideo);
      await sleep(1800);
      if (isVideoSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }}
    let direct = findDirectVideoEntry();
    if (direct) {{
      clickElement(direct);
      await sleep(1800);
      if (isVideoSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }}
    await enterCreationHub();
    if (isVideoSurface()) return;
    if (await ensureVideoModeSelected()) return;
    creationVideo = findCreationVideoTab();
    if (creationVideo) {{
      clickElement(creationVideo);
      await sleep(1800);
      if (isVideoSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }}
    direct = findDirectVideoEntry();
    if (direct) {{
      clickElement(direct);
      await sleep(1800);
      if (isVideoSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }}
    let sidebarTemplate = findSidebarVideoTemplateEntry();
    if (sidebarTemplate) {{
      clickElement(sidebarTemplate);
      await sleep(2200);
      if (isVideoSurface()) return;
      if (await ensureVideoModeSelected()) return;
    }}
    let sidebarMore = findSidebarMoreEntry();
    if (sidebarMore) {{
      clickElement(sidebarMore);
      await sleep(800);
      sidebarTemplate = findSidebarVideoTemplateEntry();
      if (sidebarTemplate) {{
        clickElement(sidebarTemplate);
        await sleep(2200);
        if (isVideoSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }}
    }}
    let more = findBottomMore();
    if (more) {{
      const moreRect = more.getBoundingClientRect();
      clickElement(more);
      await sleep(700);
      const video = findVideoEntry(moreRect);
      if (video) {{
        clickElement(video);
        await sleep(1800);
        if (await ensureVideoModeSelected()) return;
      }}
    }}
    for (let i = 0; i < 16; i++) {{
      if (isVideoSurface()) return;
      if (await ensureVideoModeSelected()) return;
      direct = findDirectVideoEntry();
      if (direct) {{
        clickElement(direct);
        await sleep(1200);
        if (isVideoSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }}
      creationVideo = findCreationVideoTab();
      if (creationVideo) {{
        clickElement(creationVideo);
        await sleep(1200);
        if (isVideoSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }}
      sidebarTemplate = findSidebarVideoTemplateEntry();
      if (sidebarTemplate) {{
        clickElement(sidebarTemplate);
        await sleep(1600);
        if (isVideoSurface()) return;
        if (await ensureVideoModeSelected()) return;
      }}
      sidebarMore = findSidebarMoreEntry();
      if (sidebarMore && i % 4 === 1) {{
        clickElement(sidebarMore);
        await sleep(500);
      }}
      more = findBottomMore();
      const moreRect = more && more.getBoundingClientRect();
      if (more && i % 3 === 0) {{
        clickElement(more);
        await sleep(500);
      }}
      const video = findVideoEntry(moreRect);
      if (video) {{
        clickElement(video);
        await sleep(1200);
        if (await ensureVideoModeSelected()) return;
      }}
      await sleep(500);
    }}
    const selectedMore = rectLabel(findBottomMore());
    const selectedDirect = rectLabel(findDirectVideoEntry());
    const selectedCreationVideo = rectLabel(findCreationVideoTab());
    const selectedSidebarMore = rectLabel(findSidebarMoreEntry());
    const selectedSidebarTemplate = rectLabel(findSidebarVideoTemplateEntry());
    const moreCount = [...document.querySelectorAll('button,[role="button"],[aria-label],[title],div,span')].filter(el => visible(el) && isMoreText(textOf(el))).length;
    const videoCount = [...document.querySelectorAll('button,[role="button"],[role="menuitem"],[aria-label],[title],li,div,span')].filter(el => visible(el) && isVideoGenerationText(textOf(el))).length;
    const sample = String(document.body?.innerText || '').replace(/\\s+/g, ' ').slice(0, 240);
    throw new Error(`未进入豆包主对话的视频生成模式：more=${{moreCount}} video=${{videoCount}} selectedDirect=${{selectedDirect}} selectedCreationVideo=${{selectedCreationVideo}} selectedSidebarMore=${{selectedSidebarMore}} selectedSidebarTemplate=${{selectedSidebarTemplate}} selectedMore=${{selectedMore}} moreList=${{collectMoreDiagnostics()}} videoList=${{collectVideoDiagnostics()}} page=${{sample}}`);
  }}

  function imagePaths(task) {{
    const paths = Array.isArray(task.imagePaths) ? task.imagePaths : [];
    return (paths.length ? paths : (task.imagePath ? [task.imagePath] : [])).slice(0, 9).filter(Boolean);
  }}

  async function getFile(task, index) {{
    const res = await fetch(`${{base}}/__dbvd_file?${{auth}}&taskId=${{enc(task.id)}}&index=${{enc(index)}}`, {{ cache: 'no-store' }});
    if (!res.ok) throw new Error(`参考图 ${{index + 1}} 读取失败 HTTP ${{res.status}}`);
    const blob = await res.blob();
    const name = String(imagePaths(task)[index] || `ref_${{index + 1}}.png`).split(/[\\\\/]/).pop() || `ref_${{index + 1}}.png`;
    return new File([blob], name, {{ type: blob.type || 'image/png' }});
  }}

  function findFileInput() {{
    const inputs = [...document.querySelectorAll('input[type="file"]')];
    return inputs.find(input => /image|png|jpe?g|webp/i.test(String(input.accept || ''))) || inputs[0] || null;
  }}

  function setFiles(input, files) {{
    const dt = new DataTransfer();
    files.forEach(file => dt.items.add(file));
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'files')?.set;
    if (setter) setter.call(input, dt.files);
    else input.files = dt.files;
    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }}

  function findUploadDropTarget(promptInput) {{
    const upload = [...document.querySelectorAll('button,[role="button"],label,div,span')].filter(visible).find(el => /上传|图片|参考图|首帧|添加|附件|add|upload|image/i.test(textOf(el)));
    if (upload) return upload;
    let node = promptInput || findPromptInput();
    for (let i = 0; node && i < 5; i++, node = node.parentElement) {{
      if (node instanceof HTMLElement) return node;
    }}
    return document.body;
  }}

  function dispatchImageDrop(target, files) {{
    const dt = new DataTransfer();
    files.forEach(file => dt.items.add(file));
    const candidates = [...new Set([target, findPromptInput(), document.body, document.documentElement].filter(Boolean))];
    for (const item of candidates) {{
      try {{
        item.focus?.();
        for (const type of ['dragenter', 'dragover', 'drop']) {{
          item.dispatchEvent(new DragEvent(type, {{ bubbles: true, cancelable: true, dataTransfer: dt }}));
        }}
        item.dispatchEvent(new ClipboardEvent('paste', {{ bubbles: true, cancelable: true, clipboardData: dt }}));
      }} catch {{}}
    }}
  }}

  async function waitForReferenceImageAttached(expectedCount) {{
    for (let i = 0; i < 40; i++) {{
      const text = String(document.body?.innerText || '');
      if (/重新上传|删除图片|添加照片|已上传|上传成功|图片上传/i.test(text)) return true;
      const media = [...document.querySelectorAll('img,canvas,video')].filter(item => {{
        if (!(item instanceof HTMLElement) || item.closest('#vmo-doubao-devtools-status')) return false;
        const rect = item.getBoundingClientRect();
        return rect.width >= 40 && rect.height >= 40 && rect.top >= 0 && rect.left >= 0;
      }});
      if (media.length >= Math.min(1, expectedCount || 1)) return true;
      await sleep(300);
    }}
    throw new Error('参考图上传后未在豆包页面检测到缩略图');
  }}

  async function uploadImages(task) {{
    await assertActive(task, 'uploading');
    const paths = imagePaths(task);
    if (!paths.length) return;
    let input = findFileInput();
    if (!input) {{
      const upload = [...document.querySelectorAll('button,[role="button"],label,div,span')].filter(visible).find(el => /上传|图片|参考图|首帧|添加/i.test(textOf(el)));
      if (upload) clickElement(upload);
      await sleep(1000);
      input = findFileInput();
    }}
    if (!input) throw new Error('未找到豆包图片上传控件');
    const files = [];
    for (let i = 0; i < paths.length; i++) files.push(await getFile(task, i));
    try {{
      setFiles(input, files);
    }} catch (error) {{
      dispatchImageDrop(findUploadDropTarget(findPromptInput()), files);
    }}
    toast(`已提交参考图 ${{files.length}} 张`);
    await sleep(1200);
    await waitForReferenceImageAttached(files.length);
    await assertActive(task, 'uploaded');
  }}

  function getEditableText(input) {{
    if (!input) return '';
    const clone = input.cloneNode(true);
    try {{
      clone.querySelectorAll('[data-slate-placeholder="true"],[data-slate-zero-width]').forEach(node => node.remove());
    }} catch {{}}
    return String(clone.innerText || clone.textContent || '').replace(/\\uFEFF/g, '').replace(/\\r\\n?/g, '\\n').trim();
  }}

  function getPromptInputText(input) {{
    return input && 'value' in input ? String(input.value || '') : getEditableText(input);
  }}

  function selectEditableContents(input) {{
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(input);
    selection.removeAllRanges();
    selection.addRange(range);
  }}

  async function clearPromptTarget(input) {{
    if (!input) return;
    input.focus?.();
    if ('value' in input) {{
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set;
      if (setter) setter.call(input, '');
      else input.value = '';
      dispatchTextInputEvents(input, '');
      await sleep(60);
      return;
    }}
    selectEditableContents(input);
    try {{
      input.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, cancelable: true, inputType: 'deleteContentBackward', data: null }}));
    }} catch {{}}
    try {{ document.execCommand('delete'); }} catch {{}}
    await sleep(60);
    if (normalizePromptCompare(getEditableText(input))) {{
      input.textContent = '';
    }}
    dispatchTextInputEvents(input, '');
    await sleep(60);
  }}

  function dispatchTextInputEvents(target, text = '') {{
    try {{ target.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, cancelable: true, inputType: 'insertReplacementText', data: null }})); }} catch {{}}
    try {{ target.dispatchEvent(new InputEvent('input', {{ bubbles: true, cancelable: true, inputType: 'insertReplacementText', data: null }})); }} catch {{}}
    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
    target.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Process', code: 'Unidentified', bubbles: true }}));
    target.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Process', code: 'Unidentified', bubbles: true }}));
  }}

  function promptLooksWritten(input, expected) {{
    const current = promptObservationText(input);
    const target = normalizePromptCompare(expected);
    if (!current || !target) return false;
    if (current === target || current.includes(target)) return true;
    if (target.includes(current) && current.length >= Math.min(120, target.length)) return true;
    const compactCurrent = compactPromptCompare(current);
    const compactTarget = compactPromptCompare(target);
    if (compactCurrent && compactTarget) {{
      if (compactCurrent === compactTarget || compactCurrent.includes(compactTarget)) return true;
      if (compactTarget.includes(compactCurrent) && compactCurrent.length >= Math.min(60, compactTarget.length)) return true;
      const important = buildPromptProbeSegments(compactTarget, 36).filter(Boolean);
      if (important.length && important.filter(part => compactCurrent.includes(part)).length >= Math.min(2, important.length)) return true;
      const durationOk = DOUBAO_VIDEO_DURATION_OPTIONS.some((seconds) => {{
        const pattern = new RegExp('时长?' + seconds + '秒|' + seconds + '秒');
        return pattern.test(compactCurrent) && pattern.test(compactTarget);
      }});
      const ratioOk = !/画面比例|比例|169|16[:：]?9/.test(compactTarget) || /画面比例|比例|169|16[:：]?9/.test(compactCurrent);
      const head = compactTarget.slice(0, Math.min(48, compactTarget.length));
      if (durationOk && ratioOk && head.length >= 18 && compactCurrent.includes(head)) return true;
    }}
    const probes = buildPromptProbeSegments(target);
    if (!probes.length) return false;
    return probes.filter(part => current.includes(part)).length >= Math.min(2, probes.length);
  }}

  function promptObservationText(input) {{
    const parts = [];
    const add = (value) => {{
      const text = normalizePromptCompare(value);
      if (text && !parts.includes(text)) parts.push(text);
    }};
    add(getPromptInputText(input));
    const active = document.activeElement;
    if (active && active instanceof HTMLElement) add(getPromptInputText(active));
    const found = findPromptInput();
    if (found && found !== input) add(getPromptInputText(found));
    const root = findComposerRoot(input || found);
    if (root) add(root.innerText || root.textContent || '');
    return normalizePromptCompare(parts.join('\\n'));
  }}

  function buildPromptProbeSegments(target, size = 56) {{
    const text = normalizePromptCompare(target);
    const parts = [];
    const len = text.length;
    for (const start of [0, Math.floor(len * 0.35), Math.floor(len * 0.7)]) {{
      const part = text.slice(start, start + size).trim();
      if (part.length >= Math.min(18, size) && !parts.includes(part)) parts.push(part);
    }}
    return parts;
  }}

  function compactPromptCompare(value) {{
    return normalizePromptCompare(value)
      .replace(/[，,。.;；:：、"'“”‘’`~!！?？()\\[\\]{{}}<>《》【】\\-_/\\\\|]+/g, '')
      .replace(/\\s+/g, '')
      .toLowerCase();
  }}

  function promptDebugInfo(input, expected = '') {{
    const current = promptObservationText(input);
    return {{
      expectedLength: normalizePromptCompare(expected).length,
      observedLength: current.length,
      expectedCompactLength: compactPromptCompare(expected).length,
      observedCompactLength: compactPromptCompare(current).length,
      observedPreview: current.slice(0, 300),
      activeTag: document.activeElement && document.activeElement.tagName || '',
      inputTag: input && input.tagName || '',
      inputRole: input && input.getAttribute && input.getAttribute('role') || '',
      inputEditable: Boolean(input && input.isContentEditable),
    }};
  }}

  function insertTextBySelection(input, text) {{
    input.focus?.();
    if (!input.isContentEditable && input.getAttribute('role') !== 'textbox') return false;
    try {{
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
      return document.execCommand('insertText', false, text);
    }} catch {{
      return false;
    }}
  }}

  async function waitForWritablePromptInput(previous = null, options = {{}}) {{
    const allowChatReply = Boolean(options && options.allowChatReply);
    const nextPromptInput = () => (allowChatReply ? findAnyPromptInput() : findPromptInput());
    let last = previous;
    for (let i = 0; i < 30; i++) {{
      const input = nextPromptInput() || last;
      const allowedInput = allowChatReply ? !isPromptInputRejected(input) : isVideoPromptInput(input);
      if (input && input instanceof HTMLElement && input.offsetParent !== null && !input.closest('#vmo-doubao-devtools-status') && allowedInput) {{
        input.scrollIntoView?.({{ block: 'center', inline: 'center' }});
        input.focus?.();
        await sleep(100);
        if (document.activeElement === input || input.contains(document.activeElement) || 'value' in input || input.isContentEditable || input.getAttribute('role') === 'textbox') {{
          return input;
        }}
        last = input;
      }}
      await sleep(150);
    }}
    return nextPromptInput() || last;
  }}

  async function ensurePromptReadyForSubmit(input, expected) {{
    let target = await waitForWritablePromptInput(input);
    if (!promptLooksWritten(target, expected)) {{
      await setPrompt(target, expected);
      target = await waitForWritablePromptInput(target);
      await waitForPromptValue(target, expected);
    }}
    dispatchTextInputEvents(target, expected);
    await sleep(180);
    if (!promptLooksWritten(target, expected)) throw new Error('提交前提示词为空，已阻止只发送图片');
    return target;
  }}

  async function setPrompt(input, text, options = {{}}) {{
    const finalText = String(text || '');
    const allowChatReply = Boolean(options && options.allowChatReply);
    const nextPromptInput = () => (allowChatReply ? findAnyPromptInput() : findPromptInput());
    let target = await waitForWritablePromptInput(input, options);
    if (!target) throw new Error('未找到豆包提示词输入框');
    target.scrollIntoView?.({{ block: 'center', inline: 'center' }});
    target.focus();
    await sleep(80);
    await clearPromptTarget(target);
    if ('value' in target) {{
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(target), 'value')?.set;
      if (setter) setter.call(target, finalText);
      else target.value = finalText;
      dispatchTextInputEvents(target, finalText);
      await sleep(180);
      if (promptLooksWritten(target, finalText)) return;
    }}

    try {{
      const dt = new DataTransfer();
      dt.setData('text/plain', finalText);
      target.dispatchEvent(new ClipboardEvent('paste', {{ bubbles: true, cancelable: true, clipboardData: dt }}));
    }} catch {{}}
    await sleep(180);

    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {{
      target.focus();
      await clearPromptTarget(target);
      document.execCommand('insertText', false, finalText);
      await sleep(180);
    }}

    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {{
      target.focus();
      await clearPromptTarget(target);
      try {{
        target.dispatchEvent(new InputEvent('beforeinput', {{ bubbles: true, cancelable: true, inputType: 'insertText', data: finalText }}));
      }} catch {{}}
      document.execCommand('insertText', false, finalText);
      await sleep(180);
    }}

    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {{
      await clearPromptTarget(target);
      target.focus();
      insertTextBySelection(target, finalText);
      await sleep(180);
    }}
    dispatchTextInputEvents(target, finalText);
    let parent = target.parentElement;
    for (let i = 0; parent && i < 5; i++, parent = parent.parentElement) {{
      dispatchTextInputEvents(parent, finalText);
    }}
    target = nextPromptInput() || target;
    if (!promptLooksWritten(target, finalText)) {{
      await reportAutomationDiagnostic(getAssignedTaskId(), 'prompt-write-failed', {{
        stage: 'prompt-write-failed',
        expectedPromptPreview: normalizePromptCompare(finalText).slice(0, 240),
        observedPromptPreview: promptObservationText(target).slice(0, 240),
        promptDebug: promptDebugInfo(target, finalText),
      }});
      throw new Error('提示词未成功写入豆包输入框');
    }}
  }}

  async function waitForPromptValue(input, expected, options = {{}}) {{
    const allowChatReply = Boolean(options && options.allowChatReply);
    for (let i = 0; i < 30; i++) {{
      input = (allowChatReply ? findAnyPromptInput() : findPromptInput()) || input;
      if (promptLooksWritten(input, expected)) return true;
      await sleep(150);
    }}
    await reportAutomationDiagnostic(getAssignedTaskId(), 'prompt-wait-timeout', {{
      stage: 'prompt-wait-timeout',
      expectedPromptPreview: normalizePromptCompare(expected).slice(0, 240),
      observedPromptPreview: promptObservationText(input).slice(0, 240),
      promptDebug: promptDebugInfo(input, expected),
    }});
    throw new Error('提示词未成功写入豆包输入框');
  }}

  function normalizePromptCompare(value) {{
    return String(value || '')
      .replace(/<br\\s*\\/?>/gi, '\\n')
      .replace(/<\\/p>\\s*<p[^>]*>/gi, '\\n')
      .replace(/<\\/?(?:p|div)[^>]*>/gi, '\\n')
      .replace(/&nbsp;/gi, ' ')
      .replace(/&amp;/gi, '&')
      .replace(/&lt;/gi, '<')
      .replace(/&gt;/gi, '>')
      .replace(/\\uFEFF/g, '')
      .replace(/\\u00a0/g, ' ')
      .replace(/\\r\\n?/g, '\\n')
      .replace(/[：:]\\s*/g, '：')
      .replace(/\\s+/g, ' ')
      .trim();
  }}

  function findGenerateButton(input) {{
    if (!input || !isVideoPromptInput(input) || !isVideoSurface()) return null;
    const inputRect = input.getBoundingClientRect();
    const composerRect = composerRectFor(input);
    const buttons = [...document.querySelectorAll('button,[role="button"]')].filter(visible).filter(el => !el.closest('#vmo-doubao-devtools-status'));
    return buttons.find(el => {{
      if (el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled')) return false;
      if (isLikelyConversationTitleOrHistoryTarget(el) || !isElementWithinComposer(el, inputRect, composerRect, 96)) return false;
      const txt = textOf(el);
      return /生成视频|生成|发送|提交|Create|Generate|Send/i.test(txt) && !/更多|上传|图片|参考图|Seedance/i.test(txt);
    }}) || buttons.filter(el => {{
      const rect = el.getBoundingClientRect();
      const ir = input.getBoundingClientRect();
      if (isLikelyConversationTitleOrHistoryTarget(el) || !isElementWithinComposer(el, inputRect, composerRect, 96)) return false;
      return rect.width >= 28 && rect.height >= 28 && rect.left >= ir.left && rect.top >= ir.top - 100 && rect.bottom <= ir.bottom + 100;
    }}).sort((a, b) => {{
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (br.right + br.bottom) - (ar.right + ar.bottom);
    }})[0] || null;
  }}

  async function submit(input, expectedPrompt = '') {{
    input = await assertVideoSurfaceForSubmit({{ id: getAssignedTaskId() }}, 'submit-legacy');
    if (expectedPrompt) input = await ensurePromptReadyForSubmit(input, expectedPrompt);
    const btn = findGenerateButton(input);
    if (btn) {{
      if (!(await devtoolsClick(btn))) clickElement(btn);
    }}
    else {{
      submitPromptByKeyboard(input);
    }}
    await sleep(800);
  }}

  async function waitForGenerateButton(input = null) {{
    for (let i = 0; i < 14; i++) {{
      const button = findGenerateButton(input);
      if (button) return button;
      await sleep(250);
    }}
    return null;
  }}

  function findGenerateButton(input = null) {{
    const inputNode = input || findPromptInput();
    const inputRect = inputNode ? inputNode.getBoundingClientRect() : null;
    const composerRect = inputNode ? composerRectFor(inputNode) : null;
    if (!inputNode || !inputRect || !composerRect || !isVideoPromptInput(inputNode) || !isVideoSurface()) return null;
    const controls = [...document.querySelectorAll('button,[role="button"],[aria-label],[title],label,div,span,svg')]
      .filter(el => visible(el) && !el.closest('#vmo-doubao-devtools-status'));
    const candidates = controls
      .map(el => clickableTargetFor(el) || el)
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => visible(el) && !el.closest('#vmo-doubao-devtools-status') && !isLikelyConversationTitleOrHistoryTarget(el) && el.getAttribute('aria-disabled') !== 'true' && !el.hasAttribute('disabled'));
    const textButton = candidates.find(el => {{
      const text = String(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
      const rect = el.getBoundingClientRect();
      if (!isElementWithinComposer(el, inputRect, composerRect, 96)) return false;
      if (!text || text.length > 32) return false;
      if (isNonSubmitControlText(text)) return false;
      return /\\u751f\\u6210\\u89c6\\u9891|\\u751f\\u6210|\\u53d1\\u9001|\\u63d0\\u4ea4|Create|Generate|Send/i.test(text);
    }});
    if (textButton) return textButton;
    const visual = candidates
      .filter(el => isLikelySubmitControl(el, inputRect, composerRect))
      .sort((a, b) => scoreSubmitControl(b, inputRect, composerRect) - scoreSubmitControl(a, inputRect, composerRect));
    if (visual[0]) return visual[0];
    return findElementAtSubmitHotspot(inputRect, composerRect);
  }}

  function isNonSubmitControlText(text) {{
    return /\\u5173\\u95ed|\\u4ee5\\u540e\\u518d\\u8bf4|Refresh|Save|\\u4fdd\\u5b58|\\u53c2\\u8003\\u56fe|\\u6bd4\\u4f8b|\\u66f4\\u591a|Seedance|\\u4e0a\\u4f20|\\u56fe\\u7247|\\u9996\\u5e27|\\u9644\\u4ef6|图像|图片|视频$|AI\\s*抠图|擦除|推荐|下载豆包电脑版|add|upload|image|microphone|voice/i.test(String(text || ''));
  }}

  function isLikelySubmitControl(el, inputRect, composerRect) {{
    if (!(el instanceof HTMLElement)) return false;
    if (!inputRect || !composerRect || !isElementWithinComposer(el, inputRect, composerRect, 96)) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 24 || rect.height < 24 || rect.width > 96 || rect.height > 96) return false;
    const text = textOf(el);
    if (isNonSubmitControlText(text)) return false;
    const className = String(el.className || '');
    const submitClassHint = /send|submit|generate|creation|btn|button|highlight/i.test(className);
    const center = rectCenter(rect);
    if (inputRect) {{
      if (center.x < inputRect.left + inputRect.width * 0.42 || center.x > inputRect.right + 90) return false;
      if (center.y < inputRect.top - 95 || center.y > inputRect.bottom + 125) return false;
    }}
    if (composerRect) {{
      if (center.x < composerRect.left + composerRect.width * 0.50 || center.x > composerRect.right + 90) return false;
      if (center.y < composerRect.top - 20 || center.y > composerRect.bottom + 70) return false;
    }}
    return scoreSubmitControl(el, inputRect, composerRect) > (submitClassHint ? -250 : 0);
  }}

  function scoreSubmitControl(el, inputRect, composerRect) {{
    if (!isElementWithinComposer(el, inputRect, composerRect, 96)) return -Infinity;
    const rect = el.getBoundingClientRect();
    const center = rectCenter(rect);
    const style = getComputedStyle(el);
    const colorHint = `${{style.backgroundColor}} ${{style.color}} ${{style.borderColor}}`;
    const classHint = String(el.className || '');
    const attrHint = `${{el.getAttribute('aria-label') || ''}} ${{el.getAttribute('title') || ''}}`;
    let score = 0;
    if (/send-btn-wrapper|send|submit|generate|creation|highlight|primary/i.test(`${{classHint}} ${{attrHint}}`)) score += 1200;
    if (/rgb\\(0,\\s*102,\\s*255\\)|rgb\\(22,\\s*119,\\s*255\\)|rgb\\(24,\\s*144,\\s*255\\)|rgb\\(0,\\s*122,\\s*255\\)|#1677ff|#06f/i.test(colorHint)) score += 900;
    if (rect.width >= 34 && rect.height >= 34 && Math.abs(rect.width - rect.height) <= 20) score += 420;
    if (inputRect) {{
      score += Math.max(0, 700 - Math.abs(center.x - (inputRect.right - 28)) * 3);
      score += Math.max(0, 500 - Math.abs(center.y - (inputRect.bottom - 26)) * 4);
      if (center.x > inputRect.left + inputRect.width * 0.75) score += 300;
    }}
    if (composerRect) {{
      score += Math.max(0, 650 - Math.abs(center.x - (composerRect.right - 34)) * 3);
      score += Math.max(0, 520 - Math.abs(center.y - (composerRect.bottom - 34)) * 4);
    }}
    score -= Math.max(0, rect.width * rect.height - 3600) / 12;
    return score;
  }}

  function submitDebugInfo(input = null, expectedPrompt = '') {{
    const freshInput = findPromptInput() || input;
    const inputRect = freshInput ? freshInput.getBoundingClientRect() : null;
    const composerRect = freshInput ? composerRectFor(freshInput) : null;
    const button = findGenerateButton(freshInput);
    const candidates = describeSubmitCandidates(freshInput).slice(0, 8);
    return {{
      href: location.href,
      inputTextPreview: promptObservationText(freshInput).slice(0, 260),
      expectedPromptPreview: normalizePromptCompare(expectedPrompt).slice(0, 260),
      promptLooksWritten: expectedPrompt ? promptLooksWritten(freshInput, expectedPrompt) : undefined,
      inputRect: inputRect ? rectSnapshot(inputRect) : null,
      composerRect: composerRect ? rectSnapshot(composerRect) : null,
      selectedButton: button ? describeElement(button, freshInput) : null,
      candidates,
      pageTextTail: getPageTextWithoutPanel().replace(/\\s+/g, ' ').slice(-500),
    }};
  }}

  function describeSubmitCandidates(input = null) {{
    const inputNode = input || findPromptInput();
    const inputRect = inputNode ? inputNode.getBoundingClientRect() : null;
    const composerRect = inputNode ? composerRectFor(inputNode) : null;
    const controls = [...document.querySelectorAll('button,[role="button"],[aria-label],[title],label,div,span,svg')]
      .filter(el => visible(el) && !el.closest('#vmo-doubao-devtools-status'));
    return controls
      .map(el => clickableTargetFor(el) || el)
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => visible(el) && !el.closest('#vmo-doubao-devtools-status') && el.getAttribute('aria-disabled') !== 'true' && !el.hasAttribute('disabled'))
      .filter(el => !isLikelyConversationTitleOrHistoryTarget(el))
      .filter(el => isElementWithinComposer(el, inputRect, composerRect, 120))
      .map(el => ({{ el, score: scoreSubmitControl(el, inputRect, composerRect), rect: el.getBoundingClientRect() }}))
      .filter(entry => entry.score > -300 && entry.rect.width >= 16 && entry.rect.height >= 16)
      .sort((a, b) => b.score - a.score)
      .map(entry => describeElement(entry.el, inputNode, entry.score));
  }}

  function describeElement(el, input = null, score = undefined) {{
    const rect = el.getBoundingClientRect();
    return {{
      tag: el.tagName,
      role: el.getAttribute('role') || '',
      text: textOf(el).slice(0, 80),
      className: String(el.className || '').slice(0, 160),
      ariaLabel: String(el.getAttribute('aria-label') || '').slice(0, 80),
      title: String(el.getAttribute('title') || '').slice(0, 80),
      rect: rectSnapshot(rect),
      score,
      distanceToInputRight: input ? Math.round(rectCenter(rect).x - input.getBoundingClientRect().right) : null,
    }};
  }}

  function rectSnapshot(rect) {{
    return {{
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      right: Math.round(rect.right),
      bottom: Math.round(rect.bottom),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    }};
  }}

  function findElementAtSubmitHotspot(inputRect, composerRect) {{
    const rect = composerRect || inputRect;
    if (!rect || !inputRect || !composerRect || !isVideoSurface()) return null;
    const points = [
      [rect.right - 34, rect.bottom - 34],
      [rect.right - 48, rect.bottom - 42],
      [rect.right - 28, rect.bottom - 58],
    ];
    for (const [x, y] of points) {{
      const hit = document.elementFromPoint(Math.max(0, Math.min(window.innerWidth - 1, x)), Math.max(0, Math.min(window.innerHeight - 1, y)));
      const target = clickableTargetFor(hit);
      if (target instanceof HTMLElement && visible(target) && !target.closest('#vmo-doubao-devtools-status') && isElementWithinComposer(target, inputRect, composerRect, 64) && !isNonSubmitControlText(textOf(target))) return target;
    }}
    return null;
  }}

  function isElementWithinComposer(el, inputRect, composerRect, slack = 72) {{
    if (!(el instanceof HTMLElement) || !inputRect || !composerRect) return false;
    if (el.closest('#vmo-doubao-devtools-status') || isInsideConversationTitleDialog(el)) return false;
    const rect = el.getBoundingClientRect();
    const center = rectCenter(rect);
    if (center.x < composerRect.left - slack || center.x > composerRect.right + slack) return false;
    if (center.y < composerRect.top - Math.min(80, slack) || center.y > composerRect.bottom + slack) return false;
    if (center.y < window.innerHeight * 0.30) return false;
    if (rect.width > Math.max(180, composerRect.width * 0.42) || rect.height > 120) return false;
    return true;
  }}

  function submitPromptByKeyboard(input) {{
    if (!isVideoSurface()) return false;
    input = findPromptInput() || input;
    if (!input || !isVideoPromptInput(input)) return false;
    input?.focus?.();
    for (const eventInit of [
      {{ key: 'Enter', code: 'Enter', ctrlKey: true }},
      {{ key: 'Enter', code: 'Enter', metaKey: true }},
      {{ key: 'Enter', code: 'Enter' }},
    ]) {{
      input?.dispatchEvent(new KeyboardEvent('keydown', {{ ...eventInit, bubbles: true, cancelable: true }}));
      input?.dispatchEvent(new KeyboardEvent('keyup', {{ ...eventInit, bubbles: true, cancelable: true }}));
    }}
    return true;
  }}

  function getPageTextWithoutPanel() {{
    const clone = document.body?.cloneNode(true);
    if (!clone) return '';
    const panel = clone.querySelector('#vmo-doubao-devtools-status');
    if (panel) panel.remove();
    return String(clone.innerText || clone.textContent || '');
  }}

  function isSubmissionLikelyAccepted(input) {{
    const freshInput = findPromptInput() || input;
    const current = normalizePromptCompare(getPromptInputText(freshInput));
    const pageText = getPageTextWithoutPanel();
    const accepted = /\\u6b63\\u5728\\u4e3a\\u60a8\\u751f\\u6210|\\u751f\\u6210\\u4e2d|\\u6b63\\u5728\\u751f\\u6210|\\u6392\\u961f\\u4e2d|\\u961f\\u5217\\u4e2d|\\u7b49\\u5f85\\u751f\\u6210|\\u4efb\\u52a1\\u5df2\\u521b\\u5efa|\\u5df2\\u63d0\\u4ea4|\\u521b\\u4f5c\\u4e2d|\\u89c6\\u9891\\u751f\\u6210\\u5931\\u8d25|\\u751f\\u6210\\u5931\\u8d25|\\u989d\\u5ea6\\u672a\\u6263\\u9664|\\u53d6\\u6d88\\u751f\\u6210|\\u505c\\u6b62\\u751f\\u6210/i.test(pageText);
    if (!accepted) return false;
    if (!current) return true;
    const button = findGenerateButton(freshInput);
    return !button || button.getAttribute('aria-disabled') === 'true' || button.hasAttribute('disabled');
  }}

  async function waitForSubmissionAccepted(input, beforeFailureCount = 0, task = null) {{
    let durationAdjustAnswered = false;
    for (let i = 0; i < 16; i++) {{
      await failOnLowerDurationCap(task || {{ id: getAssignedTaskId() }});
      if (detectDurationAdjustQuestion(task)) {{
        if (!durationAdjustAnswered) {{
          durationAdjustAnswered = true;
          await answerDurationAdjustQuestion(task);
        }}
        return true;
      }}
      if (getGenerationFailureAfter(beforeFailureCount, task)) return true;
      if (isSubmissionLikelyAccepted(input)) return true;
      const button = i % 4 === 2 ? await waitForGenerateButton(input) : null;
      if (button && isVideoSurface() && !(await devtoolsClick(button))) clickElement(button);
      await sleep(500);
    }}
    await reportAutomationDiagnostic(getAssignedTaskId(), 'submit-confirm-timeout', {{
      stage: 'submit-confirm-timeout',
      submitDebug: submitDebugInfo(input),
      pageStatus: parseDoubaoPageStatus(getAssignedTaskId() ? {{ id: getAssignedTaskId() }} : null),
    }});
    const fallbackTask = task || {{ id: getAssignedTaskId(), status: 'prepared' }};
    const pageStatus = parseDoubaoPageStatus(fallbackTask);
    const decision = await requestLlmAutomationDecision(fallbackTask, 'submit-confirm-timeout', pageStatus, {{
      beforeFailureCount,
      submitDebug: submitDebugInfo(input),
    }});
    const applied = await applyLlmAutomationDecision(fallbackTask, decision, {{ pageStatus, beforeFailureCount }});
    if (applied) return true;
    throw new Error('璞嗗寘椤甸潰鏈‘璁ゆ彁浜わ紝璇锋鏌モ€滆棰戠敓鎴愨€濇寜閽槸鍚﹀彲鐐瑰嚮');
  }}

  async function submit(input, expectedPrompt = '') {{
    input = await assertVideoSurfaceForSubmit({{ id: getAssignedTaskId() }}, 'submit');
    if (expectedPrompt) input = await ensurePromptReadyForSubmit(input, expectedPrompt);
    for (let i = 0; i < 5; i++) {{
      const btn = await waitForGenerateButton(input);
      if (btn) {{
        if (!(await devtoolsClick(btn))) clickElement(btn);
        await sleep(700);
      }}
      if (isSubmissionLikelyAccepted(input)) return true;
      submitPromptByKeyboard(input);
      await sleep(700);
      if (isSubmissionLikelyAccepted(input)) return true;
    }}
    await reportAutomationDiagnostic(getAssignedTaskId(), 'submit-not-accepted', {{
      stage: 'submit-not-accepted',
      submitDebug: submitDebugInfo(input, expectedPrompt),
      pageStatus: parseDoubaoPageStatus(getAssignedTaskId() ? {{ id: getAssignedTaskId() }} : null),
    }});
    return false;
  }}

  function ensureCoreScript() {{
    const id = 'vmo-doubao-devtools-core';
    if (!cfg.coreScript) return null;
    const existing = document.getElementById(id);
    if (existing) return existing;
    const script = document.createElement('script');
    script.id = id;
    script.textContent = cfg.coreScript;
    script.dataset.loaded = 'true';
    (document.head || document.documentElement).appendChild(script);
    return script;
  }}

  function ensureNetworkCaptureScript() {{
    const id = 'vmo-doubao-devtools-network';
    if (!cfg.networkScript) return null;
    const existing = document.getElementById(id);
    if (existing) return existing;
    const script = document.createElement('script');
    script.id = id;
    script.textContent = cfg.networkScript;
    script.dataset.loaded = 'true';
    (document.head || document.documentElement).appendChild(script);
    return script;
  }}

  function ensureWatermarkExtractorScript() {{
    const id = 'vmo-doubao-devtools-watermark-extractor';
    if (!cfg.watermarkScript) return null;
    const existing = document.getElementById(id);
    if (existing) return existing;
    const script = document.createElement('script');
    script.id = id;
    script.textContent = cfg.watermarkScript;
    script.dataset.loaded = 'true';
    (document.head || document.documentElement).appendChild(script);
    return script;
  }}

  async function waitForNetworkCaptureReady() {{
    ensureNetworkCaptureScript();
    await sleep(50);
  }}

  async function requestNetworkCandidates(timeoutMs = 700, task = null) {{
    await waitForNetworkCaptureReady();
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    const guard = task && task.resultGuard || {{}};
    return await new Promise((resolve) => {{
      const timer = setTimeout(() => {{
        window.removeEventListener('message', listener);
        resolve([]);
      }}, timeoutMs);
      const listener = (event) => {{
        const data = event.data;
        if (data && data.source === 'page-service-channel' && data.type === 'network-candidates-result' && data.requestId === requestId) {{
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          resolve(Array.isArray(data.videos) ? data.videos : []);
        }}
      }};
      window.addEventListener('message', listener);
      window.postMessage({{
        source: 'page-service-channel',
        type: 'network-candidates-request',
        requestId,
        taskId: task ? task.id || getAssignedTaskId() || '' : '',
        accountId: task ? (task.accountId || task.currentAccountId) || getAssignedAccountId() || '' : '',
        submittedAt: Number(guard && guard.submittedAt || 0),
      }}, '*');
    }});
  }}

  async function resetNetworkCaptureForTask(task, resultGuard = {{}}) {{
    await waitForNetworkCaptureReady().catch(() => {{}});
    window.postMessage({{
      source: 'page-service-channel',
      type: 'network-capture-reset',
      taskId: task && task.id || getAssignedTaskId() || '',
      accountId: task && (task.accountId || task.currentAccountId) || getAssignedAccountId() || '',
      submittedAt: Number(resultGuard && resultGuard.submittedAt || Date.now()),
      promptFingerprint: String(resultGuard && resultGuard.promptFingerprint || ''),
      promptProbe: Array.isArray(resultGuard && resultGuard.promptProbe) ? resultGuard.promptProbe : [],
    }}, '*');
    await sleep(30);
  }}

  async function waitForCoreScriptReady() {{
    ensureCoreScript();
    await sleep(50);
  }}

  async function requestExtract(task = null) {{
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    const networkVideos = await requestNetworkCandidates(900, task).catch(() => []);
    const domVideos = extractVisibleResultVideos();
    await waitForCoreScriptReady();
    return new Promise((resolve, reject) => {{
      const timer = setTimeout(() => {{
        window.removeEventListener('message', listener);
        const fallbackVideos = extractVisibleResultVideos();
        const mergedVideos = mergeExtractedVideos(networkVideos, domVideos, fallbackVideos);
        if (mergedVideos.length) {{
          resolve(mergedVideos);
          return;
        }}
        fallbackVideos.length ? resolve(fallbackVideos) : reject(new Error('读取豆包结果超时'));
      }}, 12000);
      const listener = (event) => {{
        const data = event.data;
        if (data && data.source === 'page-service-channel' && data.type === 'extract-result' && data.requestId === requestId) {{
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          if (data.ok) {{
            const videos = Array.isArray(data.videos) ? data.videos : [];
            resolve(mergeExtractedVideos(networkVideos, domVideos, videos, extractVisibleResultVideos()));
            return;
          }} else {{
            const fallbackVideos = extractVisibleResultVideos();
            const mergedVideos = mergeExtractedVideos(networkVideos, domVideos, fallbackVideos);
            if (mergedVideos.length) {{
              resolve(mergedVideos);
              return;
            }}
            fallbackVideos.length ? resolve(fallbackVideos) : reject(new Error(data.error || 'Page parse failed'));
          }}
        }}
      }};
      window.addEventListener('message', listener);
      window.postMessage({{ source: 'page-service-channel', type: 'extract', requestId }}, '*');
    }});
  }}

  async function waitForWatermarkExtractorReady() {{
    ensureWatermarkExtractorScript();
    await sleep(50);
  }}

  function watermarkDiagnostic(reason, patch = {{}}) {{
    const diagnostic = {{
      reason,
      at: Date.now(),
      scriptLoaded: document.getElementById('vmo-doubao-devtools-watermark-extractor')?.dataset?.loaded || '',
      hasRouterData: Boolean(window.__MODERN_ROUTER_DATA),
      ...patch,
    }};
    worker.lastWatermarkDiagnostic = diagnostic;
    return diagnostic;
  }}

  async function requestWatermarkCandidates(timeoutMs = 14000, options = {{}}) {{
    const diagnosticOnly = Boolean(options && options.diagnostic);
    const ready = await waitForWatermarkExtractorReady().catch((error) => {{
      watermarkDiagnostic('inject-error', {{ error: String(error && error.message || error) }});
      return false;
    }});
    if (ready === false && diagnosticOnly) {{
      return {{ videos: [], diagnostic: worker.lastWatermarkDiagnostic || watermarkDiagnostic('inject-error') }};
    }}
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    const target = watermarkRequestTarget(options && options.target);
    return await new Promise((resolve) => {{
      const timer = setTimeout(() => {{
        window.removeEventListener('message', listener);
        const diagnostic = watermarkDiagnostic('timeout', {{ timeoutMs }});
        resolve(diagnosticOnly ? {{ videos: [], diagnostic }} : []);
      }}, timeoutMs);
      const listener = (event) => {{
        const data = event.data;
        if (data && data.source === 'doubao-video-download-extractor' && data.type === 'extract-result' && data.requestId === requestId) {{
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          const videos = data.ok && Array.isArray(data.videos) ? data.videos : [];
          const normalizedVideos = videos.map((video) => normalizeExtractedVideoCandidate(video)).filter(Boolean);
          const noWatermarkCount = normalizedVideos.filter((video) => isWatermarkResolvedCandidate(video)).length;
          const diagnostic = watermarkDiagnostic(data.ok ? (noWatermarkCount ? 'resolved' : 'no-candidate') : 'extract-error', {{
            ok: Boolean(data.ok),
            count: videos.length,
            noWatermarkCount,
            error: data.ok ? '' : String(data.error || ''),
            firstSource: String(videos[0] && videos[0].source || ''),
            firstWatermarkSource: String(videos[0] && videos[0].doubaoWatermarkSource || ''),
            extractorReason: String(data.reason || data.diagnostic && data.diagnostic.reason || ''),
          }});
          resolve(diagnosticOnly ? {{ videos: normalizedVideos, diagnostic }} : normalizedVideos);
        }}
      }};
      window.addEventListener('message', listener);
      window.postMessage({{ source: 'doubao-video-download-extractor', type: 'extract', requestId, target }}, '*');
    }});
  }}

  async function requestCoreExtract(timeoutMs = 15000) {{
    await waitForCoreScriptReady();
    const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
    return await new Promise((resolve) => {{
      const timer = setTimeout(() => {{
        window.removeEventListener('message', listener);
        resolve([]);
      }}, timeoutMs);
      const listener = (event) => {{
        const data = event.data;
        if (data && data.source === 'page-service-channel' && data.type === 'extract-result' && data.requestId === requestId) {{
          window.removeEventListener('message', listener);
          clearTimeout(timer);
          resolve(data.ok && Array.isArray(data.videos) ? data.videos : []);
        }}
      }};
      window.addEventListener('message', listener);
      window.postMessage({{ source: 'page-service-channel', type: 'extract', requestId }}, '*');
    }});
  }}

  async function requestExtract(task = null) {{
    const networkVideos = await requestNetworkCandidates(900, task).catch(() => []);
    const domVideos = extractVisibleResultVideos();
    const [coreVideos, watermarkVideos] = await Promise.all([
      requestCoreExtract(15000).catch(() => []),
      requestWatermarkCandidates(14000).catch(() => []),
    ]);
    const fallbackVideos = extractVisibleResultVideos();
    const mergedVideos = mergeExtractedVideos(networkVideos, domVideos, coreVideos, watermarkVideos, fallbackVideos);
    if (mergedVideos.length) return mergedVideos;
    if (fallbackVideos.length) return fallbackVideos;
    throw new Error('extract timeout');
  }}

  function extractVisibleResultVideos() {{
    const doneText = getPageTextWithoutPanel();
    const hasDoneMarker = hasCompletedStatusSignal(doneText);
    const candidates = [];
    const add = (node, url, source, requireUrl = true) => {{
      const cleanUrl = normalizeVideoUrl(url);
      if (requireUrl && !cleanUrl) return;
      const root = closestResultCard(node);
      const rect = (root || node).getBoundingClientRect?.() || node.getBoundingClientRect?.();
      const cardText = String((root || node).innerText || (root || node).textContent || '').replace(/\s+/g, ' ').trim();
      const seedVid = cleanUrl ? extractVideoId(cleanUrl) : '';
      const internal = extractDoubaoInternalIds(root || node, {{ assetUrl: cleanUrl, backupUrl: cleanUrl, vid: seedVid }});
      const assetUrl = cleanUrl || internal.assetUrl || internal.backupUrl || '';
      const backupUrl = internal.backupUrl || (internal.assetUrl && internal.assetUrl !== assetUrl ? internal.assetUrl : '') || assetUrl;
      const fallbackVid = assetUrl ? extractVideoId(assetUrl) : seedVid;
      const candidate = {{
        vid: internal.doubaoInternalVideoId || fallbackVid,
        messageId: internal.doubaoInternalMessageId || extractMessageId(root || node) || '',
        assetUrl,
        backupUrl,
        title: document.title || 'doubao-video',
        width: Math.round(rect?.width || 0),
        height: Math.round(rect?.height || 0),
        rectTop: Math.round(rect?.top || 0),
        rectBottom: Math.round(rect?.bottom || 0),
        extractedAt: Date.now(),
        cardText: cardText.slice(0, 500),
        source,
        score: scoreResultCandidate(root || node, hasDoneMarker),
        doubaoInternalTaskId: internal.doubaoInternalTaskId || '',
        doubaoInternalMessageId: internal.doubaoInternalMessageId || '',
        doubaoInternalVideoId: internal.doubaoInternalVideoId || '',
        doubaoResultMeta: internal.doubaoResultMeta || {{}},
      }};
      candidate.doubaoResultKey = internal.doubaoResultKey || buildDoubaoResultKey(candidate);
      candidate.doubaoResultMeta = normalizeDoubaoResultMeta({{
        ...(candidate.doubaoResultMeta || {{}}),
        resultKeys: videoIdentityKeys(candidate),
        source,
      }});
      candidates.push(candidate);
    }};
    const addMarker = (node, source) => add(node, '', source, false);
    for (const video of [...document.querySelectorAll('video')]) {{
      if (!isResultMediaVisible(video)) continue;
      addMarker(video, 'dom-video-marker');
      add(video, video.currentSrc || video.src || video.getAttribute('src') || video.getAttribute('data-src') || '', 'dom-video');
      for (const source of [...video.querySelectorAll('source')]) {{
        add(video, source.src || source.getAttribute('src') || '', 'dom-video-source');
      }}
    }}
    for (const link of [...document.querySelectorAll('a[href]')]) {{
      if (!isResultMediaVisible(link)) continue;
      const href = link.href || link.getAttribute('href') || '';
      if (isLikelyDirectVideoUrl(href)) add(link, href, 'dom-link');
    }}
    for (const item of [...document.querySelectorAll('[src],[data-src],[data-url],[data-video-url],[data-download-url]')]) {{
      if (!isResultMediaVisible(item)) continue;
      for (const name of ['src', 'data-src', 'data-url', 'data-video-url', 'data-download-url']) {{
        const value = item.getAttribute(name) || '';
        if (isLikelyDirectVideoUrl(value)) add(item, value, `dom-${{name}}`);
      }}
    }}
    const performanceAnchor = findLatestCompletedResultCard();
    if (performanceAnchor) {{
      for (const url of readPerformanceVideoUrls()) {{
        add(performanceAnchor, url, 'performance-resource');
      }}
    }}
    return [...new Map(candidates
      .filter((item) => (item.assetUrl || item.messageId || item.vid || item.doubaoResultKey) && item.score > -1000)
      .sort((a, b) => b.score - a.score)
      .map((item) => [videoKey(item), item])).values()];
  }}

  function readPerformanceVideoUrls() {{
    try {{
      return [...new Set(performance.getEntriesByType('resource')
        .slice(-1000)
        .map((entry) => String(entry.name || ''))
        .filter((url) => isLikelyVideoResourceUrl(url)))].slice(-60);
    }} catch (error) {{
      return [];
    }}
  }}

  function findLatestCompletedResultCard() {{
    const candidates = [...document.querySelectorAll('div,section,article,li')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest('#vmo-doubao-devtools-status'))
      .filter((item) => hasCompletedStatusSignal(String(item.innerText || item.textContent || '')))
      .map((item) => {{
        const rect = item.getBoundingClientRect();
        return {{ item, rect, score: rect.top + rect.height + Math.min(rect.width * rect.height / 5000, 800) }};
      }})
      .filter((entry) => entry.rect.width >= 180 && entry.rect.height >= 80)
      .sort((a, b) => b.score - a.score);
    return candidates[0]?.item || null;
  }}

  async function activateLatestResultCard() {{
    const card = findLatestCompletedResultCard();
    if (!card) return false;
    card.scrollIntoView?.({{ block: 'center', inline: 'center' }});
    await sleep(500);
    const target = [...card.querySelectorAll('video,button,[role="button"],[aria-label],[title],img,canvas,div,span')]
      .filter((item) => item instanceof HTMLElement && item.offsetParent !== null && !item.closest('#vmo-doubao-devtools-status'))
      .map((item) => {{
        const rect = item.getBoundingClientRect();
        const text = String(item.innerText || item.textContent || item.getAttribute('aria-label') || item.getAttribute('title') || '');
        let score = rect.width * rect.height / 1000;
        if (/播放|play|预览|查看|下载|保存/i.test(text)) score += 2000;
        if (item.tagName === 'VIDEO') score += 1600;
        if (rect.width >= 160 && rect.height >= 90) score += 800;
        return {{ item, score }};
      }})
      .sort((a, b) => b.score - a.score)[0]?.item;
    if (target) {{
      try {{
        if (target.tagName === 'VIDEO') {{
          target.muted = true;
          await target.play?.().catch(() => {{}});
        }} else {{
          clickElement(target);
        }}
      }} catch (error) {{}}
    }}
    await sleep(1200);
    return true;
  }}

  function mergeExtractedVideos(...groups) {{
    const merged = [];
    const index = new Map();
    const keysFor = (item) => videoIdentityKeys(item);
    for (const rawItem of groups.flat().filter(Boolean)) {{
      const item = normalizeExtractedVideoCandidate(rawItem);
      const keys = keysFor(item);
      let target = keys.map((key) => index.get(key)).find(Boolean);
      if (!target) {{
        target = {{ ...item, extractionIndex: merged.length }};
        merged.push(target);
      }} else {{
        mergeVideoCandidate(target, item);
      }}
      keysFor(target).forEach((key) => index.set(key, target));
      keys.forEach((key) => index.set(key, target));
    }}
    return merged.filter((item) => hasUsableVideoUrl(item) && videoKey(item));
  }}

  function watermarkRequestTarget(video) {{
    if (!video || typeof video !== 'object') return null;
    return {{
      vid: video.vid || video.doubaoInternalVideoId || '',
      messageId: video.messageId || video.doubaoInternalMessageId || '',
      doubaoInternalTaskId: video.doubaoInternalTaskId || '',
      doubaoInternalMessageId: video.doubaoInternalMessageId || video.messageId || '',
      doubaoInternalVideoId: video.doubaoInternalVideoId || video.vid || '',
      doubaoResultKey: video.doubaoResultKey || '',
      networkAnchorTaskId: video.networkAnchorTaskId || '',
      networkAnchorAccountId: video.networkAnchorAccountId || '',
      networkAnchorSubmittedAt: Number(video.networkAnchorSubmittedAt || 0),
      assetUrl: video.assetUrl || '',
      backupUrl: video.backupUrl || '',
      noWatermarkUrl: video.noWatermarkUrl || '',
      doubaoResultMeta: video.doubaoResultMeta || {{}},
    }};
  }}

  function isTrustedWatermarkSource(video) {{
    if (!video || typeof video !== 'object') return false;
    const marker = String(video.doubaoWatermarkSource || video.watermarkSource || video.source || '');
    if (/derived|dom-|performance|network-capture|native/i.test(marker)) return false;
    if (/(?:samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api)/i.test(marker)) return true;
    const metaSource = String(video.doubaoResultMeta && (video.doubaoResultMeta.watermarkSource || video.doubaoResultMeta.source) || '');
    return /(?:samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api)/i.test(metaSource);
  }}

  function trustedNoWatermarkUrl(video) {{
    if (!video || typeof video !== 'object') return '';
    const explicit = video.noWatermarkUrl || video.no_watermark_url || '';
    const url = normalizeNoWatermarkVideoUrl(explicit);
    if (!url || !isTrustedWatermarkSource(video)) return '';
    return url;
  }}

  function isWatermarkResolvedCandidate(video) {{
    return Boolean(trustedNoWatermarkUrl(video));
  }}

  function normalizeExtractedVideoCandidate(item) {{
    if (!item || typeof item !== 'object') return item;
    const next = {{ ...item }};
    const sourceText = String(next.source || '');
    if (!next.doubaoWatermarkSource && /samantha[-_/]?video[-_/]?get[-_]?play[-_]?info/i.test(sourceText)) next.doubaoWatermarkSource = 'samantha-video-get-play-info';
    if (!next.doubaoWatermarkSource && /samantha[-_/]?media[-_/]?get[-_]?play[-_]?info|get[-_]?play[-_]?info/i.test(sourceText)) next.doubaoWatermarkSource = 'samantha-media-get-play-info';
    if (!next.doubaoWatermarkSource && /alice[-_/]?share[-_]?save/i.test(sourceText)) next.doubaoWatermarkSource = 'alice-share-save';
    if (!next.doubaoWatermarkSource && /creativity[-_/]?share[-_]?info/i.test(sourceText)) next.doubaoWatermarkSource = 'creativity-share-info';
    if (!next.doubaoWatermarkSource && /mget-play-info/i.test(sourceText)) next.doubaoWatermarkSource = 'mget-play-info';
    if (!next.doubaoWatermarkSource && /generation-task-list/i.test(sourceText)) next.doubaoWatermarkSource = 'generation-task-list';
    if (!next.doubaoWatermarkSource && /get-media-info/i.test(sourceText)) next.doubaoWatermarkSource = 'get-media-info';
    if (!next.doubaoWatermarkSource && /share-info/i.test(sourceText)) next.doubaoWatermarkSource = 'share-info';
    if (!next.doubaoWatermarkSource && /watermark.?extract|no.?watermark/i.test(sourceText)) next.doubaoWatermarkSource = 'watermark-extractor';
    const trustedWatermarkSource = isTrustedWatermarkSource(next);
    const explicitNoWatermarkUrl = next.noWatermarkUrl || next.no_watermark_url || '';
    const noWatermarkUrl = normalizeNoWatermarkVideoUrl(explicitNoWatermarkUrl || (trustedWatermarkSource ? next.playUrl || next.mainUrl || '' : ''));
    if (noWatermarkUrl) {{
      next.noWatermarkUrl = noWatermarkUrl;
      const currentAssetUrl = normalizeVideoUrl(next.assetUrl || '');
      if (!currentAssetUrl || currentAssetUrl !== noWatermarkUrl) {{
        if (next.assetUrl && next.assetUrl !== noWatermarkUrl && !next.backupUrl) next.backupUrl = next.assetUrl;
        next.assetUrl = noWatermarkUrl;
      }}
    }}
    if (next.assetUrl) {{
      const assetUrl = normalizeVideoUrl(next.assetUrl);
      next.assetUrl = isLikelyVideoResourceUrl(assetUrl) ? assetUrl : '';
    }}
    if (next.backupUrl) {{
      const backupUrl = normalizeVideoUrl(next.backupUrl);
      next.backupUrl = isLikelyVideoResourceUrl(backupUrl) ? backupUrl : '';
    }}
    if (isWatermarkResolvedCandidate(next)) {{
      next.doubaoWatermarkResolved = true;
      next.doubaoWatermarkSource = next.doubaoWatermarkSource || 'watermark-extractor';
      next.source = String(next.source || 'watermark-extractor');
    }}
    return next;
  }}

  function mergeVideoCandidate(target, item) {{
    for (const key of [
      'vid', 'messageId', 'assetUrl', 'backupUrl', 'noWatermarkUrl', 'title', 'cardText',
      'doubaoInternalTaskId', 'doubaoInternalMessageId', 'doubaoInternalVideoId', 'doubaoResultKey',
      'doubaoWatermarkSource', 'doubaoWatermarkDiagnostic',
      'networkAnchorTaskId', 'networkAnchorAccountId',
    ]) {{
      if (!target[key] && item[key]) target[key] = item[key];
    }}
    if (item.doubaoWatermarkResolved) target.doubaoWatermarkResolved = true;
    if (item.networkAnchorTrusted) target.networkAnchorTrusted = true;
    if (item.networkPromptMatched) target.networkPromptMatched = true;
    for (const key of ['width', 'height', 'rectTop', 'rectBottom', 'extractedAt', 'networkAnchorSubmittedAt']) {{
      if (!Number.isFinite(Number(target[key])) || Number(target[key]) === 0) {{
        if (Number.isFinite(Number(item[key])) && Number(item[key]) !== 0) target[key] = item[key];
      }}
    }}
    target.score = Math.max(Number(target.score || 0), Number(item.score || 0));
    if (item.source && !String(target.source || '').includes(item.source)) {{
      target.source = [target.source, item.source].filter(Boolean).join('+');
    }}
    preferVideoUrl(target, item.assetUrl || '');
    preferVideoUrl(target, item.backupUrl || '');
    preferVideoUrl(target, item.noWatermarkUrl || '');
    target.doubaoResultMeta = normalizeDoubaoResultMeta(target.doubaoResultMeta, item.doubaoResultMeta, {{
      resultKeys: videoIdentityKeys(target),
      source: target.source || item.source || '',
      watermarkSource: target.doubaoWatermarkSource || item.doubaoWatermarkSource || '',
    }});
  }}

  function hasUsableVideoUrl(video) {{
    return Boolean(video && (
      isLikelyVideoResourceUrl(video.assetUrl)
      || isLikelyVideoResourceUrl(video.backupUrl)
      || Boolean(normalizeNoWatermarkVideoUrl(video.noWatermarkUrl || video.no_watermark_url || ''))
    ));
  }}

  function normalizeVideoUrl(value) {{
    let url = String(value || '').trim();
    if (!url || url.startsWith('blob:') || url.startsWith('data:')) return '';
    url = url.replace(/\\\\u0026/g, '&').replace(/&amp;/g, '&').replace(/\\\\\\//g, '/');
    if (/^https%3A%2F%2F/i.test(url)) {{
      try {{ url = decodeURIComponent(url); }} catch (error) {{}}
    }}
    try {{
      return new URL(url, location.href).toString();
    }} catch (error) {{
      return '';
    }}
  }}

  function normalizeNoWatermarkVideoUrl(value) {{
    const url = toNoWatermarkVideoUrl(value);
    if (!url || !isNoWatermarkVideoUrl(url) || isExplicitDoubaoWatermarkUrl(url) || !isLikelyVideoResourceUrl(url)) return '';
    return url;
  }}

  function normalizeDoubaoNoWatermarkUrl(value) {{
    return normalizeNoWatermarkVideoUrl(value);
  }}

  function isExplicitDoubaoWatermarkUrl(value) {{
    const text = String(value || '');
    return /video_gen_watermark(?:_[a-z0-9]+)*/i.test(text)
      || /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|logo_type=(?!video_gen_no_watermark)[^&#]*watermark)/i.test(text);
  }}

  function isBlockedWatermarkVariantUrl(value) {{
    return /(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|(?:[?&]logo_type=(?!video_gen_no_watermark)[^&#]*watermark))/i.test(String(value || ''));
  }}

  function isNoWatermarkVideoUrl(value) {{
    const text = String(value || '');
    return /(?:[?&]lr=video_gen_no_watermark|video_gen_no_watermark)/i.test(text)
      || isDolaCleanVideoUrl(text);
  }}

  function isDolaCleanVideoUrl(value) {{
    const url = normalizeVideoUrl(value);
    if (!url || isExplicitDoubaoWatermarkUrl(url)) return false;
    try {{
      const parsed = new URL(url);
      const host = parsed.hostname.replace(/^www\./i, '').toLowerCase();
      const hostOk = host === 'dola.com' || host.endsWith('.dola.com') || host === 'byteintlapi.com' || host.endsWith('.byteintlapi.com');
      if (!hostOk) return false;
      const lr = String(parsed.searchParams.get('lr') || '');
      const logoType = String(parsed.searchParams.get('logo_type') || '');
      const markerOk = /^cici_ai$/i.test(lr) || /^cici_ai$/i.test(logoType) || /(?:[?&](?:lr|logo_type)=cici_ai)(?:[&#]|$)/i.test(url);
      const videoOk = /\/video(?:\/|_|-)|mime_type=video|download=true|\.mp4(?:$|[?#])|\/fplay\/|tos-mya|tos-[^/?#]+/i.test(`${{parsed.pathname}}${{parsed.search}}`);
      return markerOk && videoOk;
    }} catch (error) {{
      return false;
    }}
  }}

  function toNoWatermarkVideoUrl(value) {{
    const url = normalizeVideoUrl(value);
    if (!url || isBlockedWatermarkVariantUrl(url)) return '';
    return url.replace(/video_gen_watermark(?:_[a-z0-9]+)*/ig, 'video_gen_no_watermark');
  }}

  function preferVideoUrl(target, value) {{
    const url = normalizeVideoUrl(value);
    if (!url || !target || !isLikelyVideoResourceUrl(url)) return;
    if (!target.assetUrl) {{
      target.assetUrl = url;
      return;
    }}
    if (target.assetUrl === url || target.backupUrl === url) return;
    if (isNoWatermarkVideoUrl(url) && !isNoWatermarkVideoUrl(target.assetUrl)) {{
      if (target.assetUrl && !target.backupUrl) target.backupUrl = target.assetUrl;
      target.assetUrl = url;
      return;
    }}
    if (!target.backupUrl || (isNoWatermarkVideoUrl(url) && !isNoWatermarkVideoUrl(target.backupUrl))) {{
      target.backupUrl = url;
    }}
  }}

  function isLikelyDirectVideoUrl(value) {{
    const url = normalizeVideoUrl(value);
    if (!url) return false;
    if (/\.(?:png|jpe?g|webp|gif|avif|heic|heif)(?:~|$|[?#])/i.test(url)) return false;
    if (isExplicitDoubaoWatermarkUrl(url)) return false;
    if (/image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])/i.test(url) && !/video/i.test(url)) return false;
    return isLikelyVideoResourceUrl(url);
  }}

  function isLikelyHtmlDocumentUrl(value) {{
    const url = normalizeVideoUrl(value);
    if (!url) return false;
    if (/\.(?:html?|shtml)(?:$|[?#])/i.test(url)) return true;
    try {{
      const parsed = new URL(url);
      const host = parsed.hostname.replace(/^www\./i, '').toLowerCase();
      const path = parsed.pathname.replace(/\/+$/g, '') || '/';
      const hasEncodedMedia = /(?:[?&](?:download|media_url|play_url|main_url|backup_url)=https?%3A)/i.test(parsed.search);
      if ((host === 'doubao.com' || host === 'dola.com') && !hasEncodedMedia) {{
        if (path === '/' || /^\/(?:chat|download|login|auth|home|app)(?:\/|$)/i.test(path)) return true;
      }}
    }} catch (error) {{}}
    return false;
  }}

  function isLikelyVideoResourceUrl(value) {{
    const url = normalizeVideoUrl(value);
    if (!url || isLikelyHtmlDocumentUrl(url)) return false;
    try {{
      const parsed = new URL(url);
      if (/\/(?:api|samantha|alice|im|service|biz)\//i.test(parsed.pathname)
        && !/\.(?:mp4|mov|webm)(?:$|[?#])/i.test(parsed.pathname)
        && !/(?:[?&](?:download|media_url|play_url|main_url|backup_url)=https?%3A)/i.test(parsed.search)) {{
        return false;
      }}
    }} catch (error) {{
      return false;
    }}
    if (isExplicitDoubaoWatermarkUrl(url)) return false;
    if (/\.(?:png|jpe?g|webp|gif|avif|svg|heic|heif)(?:~|$|[?#])/i.test(url)) return false;
    if (/image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])/i.test(url) && !/video|play|media/i.test(url)) return false;
    return /\.(?:mp4|mov|webm|m4v)(?:$|[?#])|(?:[?&](?:vid|video_id|videoId|item_id|itemId|media_id|play_id)=)|\/video(?:[_/-]|$)|video_generation|play_?url|main_?url|download_?url|backup_?url|media_?url|tos-[^/?#]+\/obj\/[^?#]*(?:video|media|mp4)|byteimg\.com\/[^?#]*(?:video|tos|obj|mp4)|bytevod|voddos|vod-|video\.?tos/i.test(url);
  }}

  function extractVideoId(url) {{
    const match = String(url || '').match(/(?:vid=|video_id=|videoId=|item_id=|itemId=|media_id=|mediaId=|play_id=|playId=|\/)(v0[a-zA-Z0-9_-]{{6,}}|[a-f0-9]{{16,}}|[0-9]{{8,}})/i);
    return match ? match[1] : hashString(url);
  }}

  function hashString(value) {{
    let hash = 2166136261;
    const text = String(value || '');
    for (let i = 0; i < text.length; i++) {{
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }}
    return `dom-${{(hash >>> 0).toString(16)}}`;
  }}

  function extractDoubaoInternalIds(node, seed = {{}}) {{
    const result = {{
      doubaoInternalTaskId: '',
      doubaoInternalMessageId: '',
      doubaoInternalVideoId: '',
      doubaoResultKey: '',
      assetUrl: normalizeVideoUrl(seed.assetUrl || ''),
      backupUrl: normalizeVideoUrl(seed.backupUrl || ''),
      doubaoResultMeta: {{ sourceKeys: [], resultKeys: [] }},
    }};
    const add = (kind, value, sourceKey = '', source = '') => addInternalId(result, kind, value, sourceKey, source);
    add('video', seed.vid || extractVideoIdFromAnyUrl(seed.assetUrl || seed.backupUrl || ''), 'seed.vid', 'seed');
    add('message', seed.messageId || '', 'seed.messageId', 'seed');
    add('task', seed.taskId || '', 'seed.taskId', 'seed');
    let current = node;
    for (let i = 0; current && i < 9; i++, current = current.parentElement) {{
      const attrs = current.getAttributeNames ? current.getAttributeNames() : [];
      for (const name of attrs) {{
        const kind = classifyDoubaoIdKey(name);
        if (!kind) continue;
        add(kind, current.getAttribute(name) || '', name, 'dom');
      }}
      collectReactInternalIds(current, result);
    }}
    result.doubaoResultKey = buildDoubaoResultKey(result);
    result.doubaoResultMeta = normalizeDoubaoResultMeta({{
      ...(result.doubaoResultMeta || {{}}),
      resultKeys: videoIdentityKeys(result),
    }});
    return result;
  }}

  function collectReactInternalIds(node, result) {{
    if (!node || !Object.getOwnPropertyNames) return;
    let names = [];
    try {{
      names = Object.getOwnPropertyNames(node).filter((name) => /__react|__fiber|__vue|__svelte/i.test(name)).slice(0, 8);
    }} catch (error) {{
      return;
    }}
    for (const name of names) {{
      try {{
        collectInternalIdsFromObject(node[name], result, 0, `prop:${{name}}`);
      }} catch (error) {{}}
    }}
  }}

  function collectInternalIdsFromObject(value, result, depth = 0, source = 'object', seen = null, budget = null) {{
    if (!value || typeof value !== 'object' || depth > 5) return;
    const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
    const nextBudget = budget || {{ count: 0 }};
    if (++nextBudget.count > 2500) return;
    if (nextSeen) {{
      if (nextSeen.has(value)) return;
      nextSeen.add(value);
    }}
    if (Array.isArray(value)) {{
      const list = value.length > 80 ? value.slice(-80) : value;
      list.forEach((item, index) => collectInternalIdsFromObject(item, result, depth + 1, `${{source}}[${{index}}]`, nextSeen, nextBudget));
      return;
    }}
    for (const [key, item] of Object.entries(value).slice(-500)) {{
      if (isSensitiveIdKey(key)) continue;
      const kind = classifyDoubaoIdKey(key);
      if (kind && (typeof item === 'string' || typeof item === 'number')) {{
        addInternalId(result, kind, item, key, source);
      }}
      if (typeof item === 'string' && isMediaUrlKey(key) && isLikelyVideoResourceUrl(item)) {{
        addInternalMediaUrl(result, item, key, source);
      }}
      if (item && typeof item === 'object' && depth < 4) {{
        collectInternalIdsFromObject(item, result, depth + 1, `${{source}}.${{key}}`, nextSeen, nextBudget);
      }}
    }}
  }}

  function addInternalId(target, kind, value, sourceKey = '', source = '') {{
    const id = normalizeDoubaoInternalId(value, kind);
    if (!id) return false;
    const field = kind === 'task'
      ? 'doubaoInternalTaskId'
      : kind === 'message'
        ? 'doubaoInternalMessageId'
        : 'doubaoInternalVideoId';
    if (!target[field]) target[field] = id;
    const key = normalizeIdentityKey(kind, id);
    target.doubaoResultMeta = normalizeDoubaoResultMeta(target.doubaoResultMeta, {{
      sourceKeys: [`${{key}}@${{String(source || sourceKey || 'unknown').slice(0, 80)}}`],
      resultKeys: [key],
    }});
    return true;
  }}

  function classifyDoubaoIdKey(key) {{
    const name = String(key || '').replace(/[^a-zA-Z0-9]+/g, '_').toLowerCase();
    if (!name || isSensitiveIdKey(name)) return '';
    if (/(^|_)(task|job|generation|aigc|generate)(_|$)/.test(name) && /id|vid|item|task|job/.test(name)) return 'task';
    if (/(^|_)(message|msg|conversation|chat|dialog)(_|$)/.test(name) && /id|message|conversation|chat/.test(name)) return 'message';
    if (/(^|_)(vid|video|media|item)(_|$)/.test(name) && /id|vid|video|media|item/.test(name)) return 'video';
    if (/^(vid|video_id|videoid|item_id|itemid|media_id|mediaid|play_id|playid)$/.test(name)) return 'video';
    if (/^(message_id|messageid|msg_id|msgid|conversation_id|conversationid|chat_id|chatid|dialog_id|dialogid)$/.test(name)) return 'message';
    if (/^(task_id|taskid|job_id|jobid|generation_id|generationid|aigc_id|aigcid)$/.test(name)) return 'task';
    return '';
  }}

  function isSensitiveIdKey(key) {{
    return /token|cookie|secret|auth|csrf|passport|session|credential|password|web_id|device_id|user_id|uid|open_id/i.test(String(key || ''));
  }}

  function normalizeDoubaoInternalId(value, kind = '') {{
    const text = String(value ?? '').trim();
    if (!text || text.length > 500) return '';
    const fromUrl = extractIdFromUrlText(text, kind);
    if (fromUrl) return fromUrl;
    const safe = text.match(/^(v0[a-zA-Z0-9_-]{{6,}}|[a-f0-9]{{16,}}|[0-9]{{8,}}|[a-zA-Z0-9][a-zA-Z0-9_-]{{7,119}})$/i);
    if (safe) return safe[1].slice(0, 120);
    const embedded = text.match(/(?:^|[^\w-])(v0[a-zA-Z0-9_-]{{6,}}|[a-f0-9]{{16,}}|[0-9]{{8,}})(?:$|[^\w-])/i);
    return embedded ? embedded[1].slice(0, 120) : '';
  }}

  function extractIdFromUrlText(value, kind = '') {{
    const text = String(value || '').trim();
    if (!text) return '';
    const names = kind === 'task'
      ? ['task_id', 'taskId', 'job_id', 'jobId', 'generation_id', 'generationId', 'aigc_id', 'aigcId', 'id']
      : kind === 'message'
        ? ['message_id', 'messageId', 'msg_id', 'msgId', 'conversation_id', 'conversationId', 'chat_id', 'chatId', 'dialog_id', 'dialogId', 'id']
        : ['vid', 'video_id', 'videoId', 'item_id', 'itemId', 'media_id', 'mediaId', 'play_id', 'playId', 'id'];
    try {{
      if (/^https?:|^\/\//i.test(text)) {{
        const url = new URL(text, location.href);
        for (const name of names) {{
          const item = normalizeDoubaoInternalId(url.searchParams.get(name) || '', kind);
          if (item) return item;
        }}
      }}
    }} catch (error) {{}}
    for (const name of names) {{
      const pattern = new RegExp(`(?:[?&#/]|^)${{name}}=([^&#/?]+)`, 'i');
      const match = text.match(pattern);
      if (match) {{
        const item = normalizeDoubaoInternalId(decodeURIComponent(match[1] || ''), kind);
        if (item) return item;
      }}
    }}
    return '';
  }}

  function extractVideoIdFromAnyUrl(value) {{
    const url = normalizeVideoUrl(value);
    return url ? extractVideoId(url) : '';
  }}

  function normalizeIdentityKey(prefix, value) {{
    const text = String(value || '').trim();
    if (!text) return '';
    if (/^(result|task|message|video|vid|url):/i.test(text)) return text;
    return `${{prefix}}:${{text}}`;
  }}

  function buildDoubaoResultKey(video) {{
    const keys = videoIdentityKeys(video).filter((key) => /^(task|video|message|vid|url):/i.test(key));
    return keys[0] || '';
  }}

  function videoIdentityKeys(video) {{
    if (!video) return [];
    const keys = [];
    const add = (prefix, value) => {{
      const key = normalizeIdentityKey(prefix, value);
      if (key && !keys.includes(key)) keys.push(key);
    }};
    add('task', video.doubaoInternalTaskId || '');
    add('video', video.doubaoInternalVideoId || '');
    add('message', video.doubaoInternalMessageId || '');
    add('vid', video.vid || '');
    add('message', video.messageId || '');
    add('url', video.assetUrl || '');
    add('url', video.backupUrl || '');
    add('url', video.noWatermarkUrl || '');
    add('result', video.doubaoResultKey || '');
    return keys;
  }}

  function legacyVideoKeys(video) {{
    return [
      video && video.vid,
      video && video.messageId,
      video && video.assetUrl,
      video && video.backupUrl,
      video && video.noWatermarkUrl,
    ].filter(Boolean).map((item) => String(item));
  }}

  function normalizeDoubaoResultMeta(...items) {{
    const meta = {{}};
    const addArray = (key, value, limit = 40) => {{
      const list = Array.isArray(value) ? value : (value ? [value] : []);
      const current = Array.isArray(meta[key]) ? meta[key] : [];
      for (const item of list) {{
        const text = String(item || '').slice(0, 240);
        if (text && !current.includes(text)) current.push(text);
      }}
      meta[key] = current.slice(-limit);
    }};
    for (const item of items.filter(Boolean)) {{
      if (item.source) meta.source = String(item.source).slice(0, 120);
      if (item.watermarkSource) meta.watermarkSource = String(item.watermarkSource).slice(0, 80);
      if (item.extractionIndex !== undefined) meta.extractionIndex = Number(item.extractionIndex) || 0;
      if (item.score !== undefined) meta.score = Number(item.score) || 0;
      addArray('sourceKeys', item.sourceKeys);
      addArray('resultKeys', item.resultKeys);
    }}
    return meta;
  }}

  function readKnownDoubaoResultKeys() {{
    const keys = new Set();
    const addKnown = (kind, value) => {{
      const id = normalizeDoubaoInternalId(value, kind);
      const key = normalizeIdentityKey(kind, id);
      if (key) keys.add(key);
    }};
    const scanObject = (value, depth = 0, seen = null, budget = {{ count: 0 }}) => {{
      if (!value || typeof value !== 'object' || depth > 6 || ++budget.count > 5000) return;
      const nextSeen = seen || (typeof WeakSet !== 'undefined' ? new WeakSet() : null);
      if (nextSeen) {{
        if (nextSeen.has(value)) return;
        nextSeen.add(value);
      }}
      if (Array.isArray(value)) {{
        const list = value.length > 160 ? value.slice(-160) : value;
        list.forEach((item) => scanObject(item, depth + 1, nextSeen, budget));
        return;
      }}
      for (const [key, item] of Object.entries(value).slice(-800)) {{
        if (isSensitiveIdKey(key)) continue;
        const kind = classifyDoubaoIdKey(key);
        if (kind && (typeof item === 'string' || typeof item === 'number')) addKnown(kind, item);
        if (item && typeof item === 'object' && depth < 4) scanObject(item, depth + 1, nextSeen, budget);
      }}
    }};
    try {{ scanObject(globalThis.__MODERN_ROUTER_DATA); }} catch (error) {{}}
    for (const node of Array.from(document.querySelectorAll('*')).slice(-2500)) {{
      const attrs = node.getAttributeNames ? node.getAttributeNames() : [];
      for (const name of attrs) {{
        const kind = classifyDoubaoIdKey(name);
        if (kind) addKnown(kind, node.getAttribute(name) || '');
      }}
    }}
    try {{
      for (const video of extractVisibleResultVideos()) videoIdentityKeys(video).forEach((key) => keys.add(key));
    }} catch (error) {{}}
    return [...keys].slice(-1000);
  }}

  function knownKeySetFromGuard(guard = {{}}) {{
    const keys = new Set();
    const add = (item) => {{
      if (!item) return;
      if (Array.isArray(item)) {{
        item.forEach(add);
        return;
      }}
      if (typeof item === 'object') {{
        Object.values(item).forEach(add);
        return;
      }}
      const text = String(item || '').trim();
      if (!text) return;
      if (/^(result|task|message|video|vid|url):/i.test(text)) keys.add(text);
      else keys.add(text);
    }};
    add(guard.knownResultKeysBefore);
    add(guard.knownInternalIdsBefore);
    add(guard.beforeVideoKeys);
    return keys;
  }}

  function exactGuardKeySet(guard = {{}}) {{
    const keys = new Set();
    const add = (prefix, value) => {{
      const key = normalizeIdentityKey(prefix, value);
      if (key) keys.add(key);
    }};
    if (guard.doubaoResultKey) keys.add(String(guard.doubaoResultKey));
    add('task', guard.doubaoInternalTaskId);
    add('message', guard.doubaoInternalMessageId);
    add('video', guard.doubaoInternalVideoId);
    const meta = guard.doubaoResultMeta || {{}};
    if (Array.isArray(meta.resultKeys)) meta.resultKeys.forEach((key) => keys.add(String(key)));
    return keys;
  }}

  function isKnownVideoCandidate(video, beforeKeys, guard = {{}}) {{
    const identities = videoIdentityKeys(video);
    const legacy = legacyVideoKeys(video);
    const known = knownKeySetFromGuard(guard);
    for (const key of [...identities, ...legacy]) {{
      if ((beforeKeys && beforeKeys.has && beforeKeys.has(key)) || known.has(key)) return true;
    }}
    return false;
  }}

  function buildSelectedVideoPatch(video) {{
    const meta = normalizeDoubaoResultMeta(video && video.doubaoResultMeta, {{
      resultKeys: videoIdentityKeys(video),
      source: video && video.source || '',
      score: video && video.score,
      extractionIndex: video && video.extractionIndex,
      watermarkSource: video && video.doubaoWatermarkSource || '',
    }});
    return {{
      doubaoInternalTaskId: video && video.doubaoInternalTaskId || '',
      doubaoInternalMessageId: video && (video.doubaoInternalMessageId || video.messageId) || '',
      doubaoInternalVideoId: video && (video.doubaoInternalVideoId || video.vid) || '',
      doubaoResultKey: videoKey(video),
      networkAnchorTaskId: video && video.networkAnchorTaskId || '',
      networkAnchorAccountId: video && video.networkAnchorAccountId || '',
      networkAnchorSubmittedAt: Number(video && video.networkAnchorSubmittedAt || 0),
      networkAnchorTrusted: Boolean(video && video.networkAnchorTrusted),
      networkPromptMatched: Boolean(video && video.networkPromptMatched),
      noWatermarkUrl: video && video.noWatermarkUrl || '',
      doubaoWatermarkSource: video && video.doubaoWatermarkSource || '',
      doubaoWatermarkResolved: Boolean(video && isWatermarkResolvedCandidate(video)),
      doubaoWatermarkDiagnostic: video && video.doubaoWatermarkDiagnostic || worker.lastWatermarkDiagnostic || null,
      doubaoResultMeta: meta,
    }};
  }}

  function shareVideoIdentity(a, b) {{
    const aKeys = new Set(videoIdentityKeys(a).filter((key) => !/^url:/i.test(key)));
    const bKeys = videoIdentityKeys(b).filter((key) => !/^url:/i.test(key));
    if (bKeys.some((key) => aKeys.has(key))) return true;
    const aLegacy = new Set(legacyVideoKeys(a).filter((key) => !/^https?:/i.test(key)));
    return legacyVideoKeys(b).some((key) => key && !/^https?:/i.test(key) && aLegacy.has(key));
  }}

  function hasStrongWatermarkTarget(video) {{
    if (!video || typeof video !== 'object') return false;
    return [
      video.vid,
      video.doubaoInternalVideoId,
      video.messageId,
      video.doubaoInternalMessageId,
      video.doubaoInternalTaskId,
      video.networkAnchorTaskId,
      video.doubaoResultKey,
    ].some((value) => String(value || '').trim().length >= 6);
  }}

  function pickResolvedWatermarkMatch(selected, videos, allowLooseSingle = false) {{
    const resolved = (Array.isArray(videos) ? videos : []).filter((item) => isWatermarkResolvedCandidate(item));
    if (!resolved.length) return null;
    return resolved.find((item) => shareVideoIdentity(selected, item))
      || (resolved.length === 1 && (allowLooseSingle || !hasStrongWatermarkTarget(selected)) ? resolved[0] : null);
  }}

  async function resolveNoWatermarkForVideo(video, task = null, attempts = 2) {{
    let selected = normalizeExtractedVideoCandidate(video || {{}});
    if (!selected || isWatermarkResolvedCandidate(selected)) return selected;
    let diagnostic = watermarkDiagnostic('not-started');
    for (let index = 1; index <= attempts; index += 1) {{
      if (index > 1) await sleep(1200);
      const plans = [
        {{ label: 'targeted', target: selected, timeoutMs: index === 1 ? 16000 : 22000, allowLooseSingle: true }},
      ];
      if (index > 1 && (!task || !hasStrongWatermarkTarget(selected))) {{
        plans.push({{ label: 'page-scan', target: null, timeoutMs: 22000, allowLooseSingle: !task }});
      }}
      let videos = [];
      for (const plan of plans) {{
        const result = await requestWatermarkCandidates(plan.timeoutMs, {{ diagnostic: true, target: plan.target }}).catch((error) => ({{
          videos: [],
          diagnostic: watermarkDiagnostic('request-error', {{ error: String(error && error.message || error), plan: plan.label }}),
        }}));
        const planVideos = mergeExtractedVideos(Array.isArray(result.videos) ? result.videos : []);
        videos = mergeExtractedVideos(videos, planVideos);
        diagnostic = {{
          ...(result.diagnostic || worker.lastWatermarkDiagnostic || diagnostic || {{}}),
          plan: plan.label,
          count: planVideos.length || (result.diagnostic && result.diagnostic.count) || 0,
        }};
        const matched = pickResolvedWatermarkMatch(selected, planVideos, plan.allowLooseSingle);
        if (matched) {{
          mergeVideoCandidate(selected, matched);
          selected.doubaoWatermarkDiagnostic = watermarkDiagnostic('resolved', {{
            attempt: index,
            plan: plan.label,
            count: planVideos.length,
            matchedKey: videoKey(matched),
          }});
          return selected;
        }}
      }}
      if (task && task.id) {{
        await updateTask({{
          id: task.id,
          status: 'submitted',
          stage: 'watermark-resolving',
          doubaoWatermarkDiagnostic: {{
            ...(diagnostic || {{}}),
            reason: diagnostic && diagnostic.reason || 'no-candidate',
            attempt: index,
            count: videos.length,
          }},
        }}).catch(() => {{}});
      }}
    }}
    selected.doubaoWatermarkDiagnostic = {{
      ...(diagnostic || watermarkDiagnostic('no-candidate')),
      reason: diagnostic && diagnostic.reason || 'no-candidate',
      selectedKey: videoKey(selected),
    }};
    return selected;
  }}

  function extractMessageId(node) {{
    let current = node;
    for (let i = 0; current && i < 6; i++, current = current.parentElement) {{
      const attrs = current.getAttributeNames ? current.getAttributeNames() : [];
      for (const name of attrs) {{
        const value = current.getAttribute(name) || '';
        if (/message|conversation|chat|item/i.test(name) && value) return value.slice(0, 120);
      }}
    }}
    return '';
  }}

  function closestResultCard(node) {{
    let current = node;
    for (let i = 0; current && i < 8; i++, current = current.parentElement) {{
      if (!(current instanceof HTMLElement)) break;
      const text = String(current.innerText || current.textContent || '');
      if (hasCompletedStatusSignal(text) || /下载|保存|AI生成|播放/i.test(text)) return current;
      const rect = current.getBoundingClientRect();
      if (rect.width >= 160 && rect.height >= 120) return current;
    }}
    return node;
  }}

  function scoreResultCandidate(node, hasDoneMarker) {{
    if (!node || !(node instanceof HTMLElement)) return hasDoneMarker ? 100 : 0;
    const rect = node.getBoundingClientRect();
    const text = String(node.innerText || node.textContent || '');
    let score = 0;
    if (hasDoneMarker) score += 500;
    if (hasCompletedStatusSignal(text) || /下载|保存|AI生成|播放/i.test(text)) score += 600;
    score += Math.min(rect.width * rect.height / 1000, 600);
    score += rect.top > 0 ? 80 : 0;
    score += rect.left > window.innerWidth * 0.25 ? 80 : 0;
    return score;
  }}

  function isResultMediaVisible(item) {{
    if (!(item instanceof HTMLElement)) return false;
    if (item.closest('#vmo-doubao-devtools-status')) return false;
    const rect = item.getBoundingClientRect();
    if (!rect || rect.width < 24 || rect.height < 24) return false;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight + 600 || rect.left > window.innerWidth) return false;
    const style = getComputedStyle(item);
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.01;
  }}

  async function readCurrentVideoKeys() {{
    try {{
      const videos = await requestExtract();
      return new Set((Array.isArray(videos) ? videos : []).flatMap((video) => [...videoIdentityKeys(video), ...legacyVideoKeys(video)]).filter(Boolean));
    }} catch {{
      return new Set();
    }}
  }}

  function captureSubmissionAnchor(input, promptText = '', beforeKeys = null) {{
    const freshInput = findPromptInput() || input;
    const inputRect = freshInput ? freshInput.getBoundingClientRect() : null;
    const composerRect = freshInput ? composerRectFor(freshInput) : null;
    const rect = composerRect || inputRect;
    const knownResultKeysBefore = readKnownDoubaoResultKeys();
    if (beforeKeys && beforeKeys.forEach) beforeKeys.forEach((key) => {{
      const text = String(key || '').trim();
      if (text && !knownResultKeysBefore.includes(text)) knownResultKeysBefore.push(text);
    }});
    return {{
      submittedAt: Date.now(),
      anchorTop: Math.round(rect ? rect.top : 0),
      anchorBottom: Math.round(rect ? rect.bottom : 0),
      viewportHeight: Math.round(window.innerHeight || 0),
      maxMessageIdBefore: readKnownMaxMessageId(),
      knownResultKeysBefore: knownResultKeysBefore.slice(-1000),
      knownInternalIdsBefore: knownResultKeysBefore.slice(-1000),
      promptFingerprint: promptFingerprint(promptText),
      promptProbe: buildNetworkPromptProbe(promptText),
      href: location.href,
    }};
  }}

  function readKnownMaxMessageId() {{
    let maxId = '';
    const seen = typeof WeakSet !== 'undefined' ? new WeakSet() : null;
    const visitValue = (value, depth = 0) => {{
      if (depth > 8 || value === null || value === undefined) return;
      if (typeof value === 'string' || typeof value === 'number') {{
        maxId = maxNumericMessageId(maxId, value);
        return;
      }}
      if (typeof value !== 'object') return;
      if (seen) {{
        if (seen.has(value)) return;
        seen.add(value);
      }}
      if (Array.isArray(value)) {{
        value.slice(-300).forEach((item) => visitValue(item, depth + 1));
        return;
      }}
      for (const [key, item] of Object.entries(value).slice(-600)) {{
        if (/message.?id|item.?id|conversation.?id|chat.?id|id$/i.test(key)) visitValue(item, depth + 1);
        else if (depth < 3 && item && typeof item === 'object') visitValue(item, depth + 1);
      }}
    }};
    visitValue(globalThis.__MODERN_ROUTER_DATA);
    for (const node of Array.from(document.querySelectorAll('*')).slice(-2000)) {{
      const attrs = node.getAttributeNames ? node.getAttributeNames() : [];
      for (const name of attrs) {{
        if (/message|conversation|chat|item/i.test(name)) {{
          maxId = maxNumericMessageId(maxId, node.getAttribute(name) || '');
        }}
      }}
    }}
    return maxId;
  }}

  function maxNumericMessageId(left, right) {{
    const a = normalizeNumericMessageId(left);
    const b = normalizeNumericMessageId(right);
    if (!a) return b;
    if (!b) return a;
    return compareNumericMessageIds(a, b) >= 0 ? a : b;
  }}

  function normalizeNumericMessageId(value) {{
    const match = String(value || '').match(/\\d{{8,}}/);
    return match ? match[0].replace(/^0+/, '') || '0' : '';
  }}

  function compareNumericMessageIds(left, right) {{
    const a = normalizeNumericMessageId(left);
    const b = normalizeNumericMessageId(right);
    if (!a && !b) return 0;
    if (!a) return -1;
    if (!b) return 1;
    if (a.length !== b.length) return a.length > b.length ? 1 : -1;
    return a === b ? 0 : (a > b ? 1 : -1);
  }}

  function promptFingerprint(value) {{
    const text = normalizePromptCompare(value).slice(0, 240);
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) {{
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }}
    return (hash >>> 0).toString(16);
  }}

  function buildNetworkPromptProbe(value) {{
    const text = normalizePromptCompare(value);
    if (!text) return [];
    const probes = [];
    const add = (item) => {{
      const part = String(item || '').replace(/\s+/g, ' ').trim();
      if (part.length >= 12 && !probes.includes(part)) probes.push(part.slice(0, 220));
    }};
    add(text.slice(0, 180));
    add(text.slice(Math.max(0, text.length - 180)));
    if (text.length > 360) {{
      const middle = Math.max(0, Math.floor(text.length / 2) - 90);
      add(text.slice(middle, middle + 180));
    }}
    for (const match of text.match(/[\u4e00-\u9fa5A-Za-z0-9][^。；;.!?！？]{{24,160}}/g) || []) {{
      add(match);
      if (probes.length >= 6) break;
    }}
    return probes.slice(0, 6);
  }}

  function videoKey(video) {{
    const keys = videoIdentityKeys(video);
    if (keys.length) return keys[0];
    return String(video && (video.vid || video.messageId || video.assetUrl || video.backupUrl) || '');
  }}

  function videoStableKey(video) {{
    if (!video) return '';
    const idKey = videoKey(video);
    const url = normalizeVideoUrl(video.assetUrl || video.backupUrl || '');
    return [idKey, url].filter(Boolean).join('|');
  }}

  function pickNewVideo(videos, beforeKeys, task = null) {{
    if (!Array.isArray(videos) || !videos.length) return null;
    const guard = task && task.resultGuard || {{}};
    const sorted = rankVideoCandidates(videos, beforeKeys, task);
    return sorted.find(({{ video }}) => {{
      const key = videoKey(video);
      return key && !isKnownVideoCandidate(video, beforeKeys, guard);
    }})?.video || (beforeKeys.size === 0 ? sorted[0]?.video || null : null);
  }}

  function rankVideoCandidates(videos, beforeKeys, task = null) {{
    if (!Array.isArray(videos) || !videos.length) return [];
    const guard = task && task.resultGuard || {{}};
    return [...videos]
      .map((video) => ({{ video, score: scoreTaskVideoCandidate(video, beforeKeys, guard) }}))
      .filter((entry) => entry.score > -Infinity && hasUsableVideoUrl(entry.video))
      .sort((a, b) => b.score - a.score);
  }}

  function summarizeVideoCandidatesForLlm(entries, task = null) {{
    return entries.slice(0, 8).map((entry, index) => {{
      const video = entry.video || entry;
      return {{
        index,
        key: videoStableKey(video) || videoKey(video) || '',
        resultKey: video.doubaoResultKey || '',
        score: Number(entry.score || video.score || 0),
        source: String(video.source || '').slice(0, 120),
        cardText: String(video.cardText || '').replace(/\s+/g, ' ').slice(0, 500),
        ids: {{
          task: video.doubaoInternalTaskId || '',
          message: video.doubaoInternalMessageId || video.messageId || '',
          video: video.doubaoInternalVideoId || video.vid || '',
          networkTask: video.networkAnchorTaskId || '',
          networkAccount: video.networkAnchorAccountId || '',
        }},
        hasUrl: Boolean(video.assetUrl || video.backupUrl),
        networkTrusted: Boolean(video.networkAnchorTrusted || video.networkPromptMatched),
        extractedAt: Number(video.extractedAt || 0),
        taskPromptHint: task && task.prompt ? String(task.prompt).slice(0, 240) : '',
      }};
    }});
  }}

  function candidateFromLlmDecision(decision, entries) {{
    if (!decision || decision.action !== 'bind_candidate' || !Array.isArray(entries)) return null;
    if (Number(decision.confidence || 0) < 0.55) return null;
    if (decision.candidateIndex !== undefined) {{
      const entry = entries[Number(decision.candidateIndex)];
      if (entry && entry.video) return entry.video;
    }}
    const key = String(decision.candidateKey || '');
    if (key) {{
      const entry = entries.find((item) => {{
        const video = item.video || item;
        return key === (videoStableKey(video) || '') || key === (videoKey(video) || '') || key === String(video.doubaoResultKey || '');
      }});
      if (entry && entry.video) return entry.video;
    }}
    return null;
  }}

  async function pickNewVideoWithLlm(videos, beforeKeys, task = null, pageStatus = null, context = {{}}) {{
    const ranked = rankVideoCandidates(videos, beforeKeys, task);
    const deterministic = pickNewVideo(videos, beforeKeys, task);
    const top = ranked[0];
    const second = ranked[1];
    const topStrong = top && (
      top.score >= 5000
      || Boolean(top.video && (top.video.networkAnchorTrusted || top.video.networkPromptMatched))
      || (second ? top.score - second.score > 1800 : top.score > 1200)
    );
    if (deterministic && topStrong) return deterministic;
    if (!ranked.length) return deterministic;
    const candidateVideos = summarizeVideoCandidatesForLlm(ranked, task);
    const decision = await requestLlmAutomationDecision(task, context.stage || 'candidate-binding', pageStatus, {{
      candidateVideos,
      deterministicCandidate: deterministic ? summarizeVideoCandidatesForLlm([{{ video: deterministic, score: top && top.video === deterministic ? top.score : 0 }}], task)[0] : {{}},
      decisionKey: `candidates:${{candidateVideos.map((item) => `${{item.index}}:${{item.key}}:${{item.score}}`).join('|')}}`,
      beforeFailureCount: context.beforeFailureCount || 0,
      completedSeenAt: context.completedSeenAt || 0,
    }});
    const selected = candidateFromLlmDecision(decision, ranked);
    if (selected) {{
      await updateTask({{
        id: task.id,
        status: 'submitted',
        stage: 'llm-candidate-bound',
        doubaoLlmDecision: decision,
        doubaoLlmDecisionAt: Date.now(),
        doubaoLlmAction: 'bind_candidate',
        doubaoPageMessage: decision.candidateReason || decision.reason || 'LLM 已辅助绑定视频候选',
      }}).catch(() => {{}});
      return selected;
    }}
    if (decision && decision.action && decision.action !== 'bind_candidate') {{
      const applied = await applyLlmAutomationDecision(task, decision, {{ pageStatus, beforeFailureCount: context.beforeFailureCount || 0 }});
      if (applied) return null;
    }}
    return deterministic;
  }}

  function scoreTaskVideoCandidate(video, beforeKeys, guard = {{}}) {{
    const key = videoKey(video);
    if (!key || isKnownVideoCandidate(video, beforeKeys, guard)) return -Infinity;
    let score = Number(video.score || 0);
    const guardTaskId = String(guard.vmoTaskId || guard.taskId || '');
    const networkTaskId = String(video.networkAnchorTaskId || '');
    const networkTrusted = Boolean(video.networkAnchorTrusted || video.networkPromptMatched);
    if (guardTaskId && networkTaskId && guardTaskId !== networkTaskId) return -Infinity;
    if (guardTaskId && networkTaskId && guardTaskId === networkTaskId) score += networkTrusted ? 5200 : 1200;
    const identities = videoIdentityKeys(video);
    const exactGuardKeys = exactGuardKeySet(guard);
    if (exactGuardKeys.size && identities.some((item) => exactGuardKeys.has(item))) score += 10000;
    if (networkTrusted) score += 2400;
    if (identities.some((item) => /^(task|message|video|result):/i.test(item))) score += 1800;
    const rectTop = Number(video.rectTop);
    const rectBottom = Number(video.rectBottom);
    const anchorTop = Number(guard.anchorTop || 0);
    const anchorBottom = Number(guard.anchorBottom || 0);
    const submittedAt = Number(guard.submittedAt || 0);
    const networkSubmittedAt = Number(video.networkAnchorSubmittedAt || 0);
    const extractedAt = Number(video.extractedAt || 0);
    const hasRect = Number.isFinite(rectTop) && Number.isFinite(rectBottom) && (rectTop !== 0 || rectBottom !== 0);
    const messageId = normalizeNumericMessageId(video.messageId || '');
    const maxBeforeMessageId = normalizeNumericMessageId(guard.maxMessageIdBefore || '');
    const sourceText = String(video.source || '');
    const watermarkResolved = isWatermarkResolvedCandidate(video);
    if (submittedAt && networkSubmittedAt && networkSubmittedAt < submittedAt - 2500) return -Infinity;
    if (submittedAt && extractedAt && extractedAt < submittedAt - 2500) return -Infinity;
    if (maxBeforeMessageId && messageId && compareNumericMessageIds(messageId, maxBeforeMessageId) <= 0) return -Infinity;
    const hasStrongInternalId = Boolean(video.doubaoInternalTaskId || video.doubaoInternalMessageId || video.doubaoInternalVideoId || video.vid || video.messageId);
    if (anchorBottom && !hasRect && !messageId && !(networkTrusted && hasStrongInternalId) && !watermarkResolved) return -Infinity;
    if (/network|performance/i.test(sourceText) && !hasRect && !networkTrusted && !hasStrongInternalId) score -= 1800;
    if (watermarkResolved) score += 3600;
    if (hasRect && anchorBottom) {{
      if (rectBottom < anchorTop - 40) return -Infinity;
      if (rectTop >= anchorTop - 40) score += 1200;
      if (rectTop >= anchorBottom - 160) score += 900;
      score += Math.max(0, 800 - Math.abs(rectTop - anchorBottom));
    }}
    if (submittedAt && extractedAt >= submittedAt) score += 250;
    const cardText = String(video.cardText || '');
    if (/\\u4f60\\u7684\\u89c6\\u9891\\u751f\\u6210\\u597d|\\u89c6\\u9891\\u751f\\u6210\\u597d|\\u751f\\u6210\\u597d\\u4e86|\\u751f\\u6210\\u5b8c\\u6210|\\u4e0b\\u8f7d\\u89c6\\u9891|\\u4fdd\\u5b58\\u89c6\\u9891|(?:10|15)\\s*\\u79d2|Seedance/i.test(cardText)) score += 500;
    if (String(video.messageId || '')) score += 50;
    if (String(video.doubaoInternalTaskId || '')) score += 250;
    if (String(video.doubaoInternalMessageId || '')) score += 180;
    if (String(video.doubaoInternalVideoId || '')) score += 220;
    return score;
  }}

  async function waitAndDownloadGeneratedVideo(task, beforeKeys, beforeFailureCount = 0) {{
    const deadline = Date.now() + 30 * 60 * 1000;
    let lastError = null;
    let observedFailureCount = beforeFailureCount || 0;
    let durationAdjustAnswered = false;
    let completedSeenAt = 0;
    let lastRefreshAt = 0;
    let lastActivationAt = 0;
    let stableCandidateKey = '';
    let stableCandidateSeen = 0;
    let downloadAttempts = 0;
    const submittedAt = Number(task?.resultGuard?.submittedAt || 0) || Date.now();
    while (Date.now() < deadline) {{
      await sleep(completedSeenAt ? 2000 : 8000);
      await assertActive(task, 'waiting-result');
      const pageStatus = await syncDoubaoPageStatus(task, 'waiting-result') || parseDoubaoPageStatus(task);
      await failOnLowerDurationCap(task);
      if (detectDurationAdjustQuestion(task)) {{
        if (!durationAdjustAnswered) {{
          durationAdjustAnswered = true;
          await answerDurationAdjustQuestion(task);
        }}
        continue;
      }}
      const llmDecision = await requestLlmAutomationDecision(task, 'waiting-result', pageStatus, {{
        beforeFailureCount: observedFailureCount,
        completedSeenAt,
        lastError: lastError && lastError.message || '',
        downloadAttempts,
      }});
      const llmApplied = await applyLlmAutomationDecision(task, llmDecision, {{
        pageStatus,
        beforeFailureCount: observedFailureCount,
        completedSeenAt,
      }});
      if (llmApplied) {{
        if (llmApplied === 'activate') completedSeenAt ||= Date.now();
        if (llmApplied === 'refresh') lastRefreshAt = Date.now();
        continue;
      }}
      if ((pageStatus && pageStatus.status === 'completed-signal' && detectGenerationCompletedNotice(task)) || detectGenerationCompletedNotice(task)) {{
        completedSeenAt ||= Date.now();
        const now = Date.now();
        await updateTask({{ id: task.id, status: 'submitted', stage: 'completed-card-detected', doubaoProgress: 95, doubaoPageStatus: 'completed-signal', doubaoPageMessage: '豆包已提示视频生成完成，正在抓取结果' }}).catch(() => {{}});
        if (now - lastActivationAt > 10000) {{
          lastActivationAt = now;
          await activateLatestResultCard();
        }}
      }}
      const failure = getGenerationFailureAfter(observedFailureCount, task);
      if (failure) {{
        if (!failure.hard && hasActiveDoubaoGenerationSignal(pageStatus)) {{
          observedFailureCount = failure.count;
          continue;
        }}
        observedFailureCount = failure.count;
        throw new Error(failure.message || '豆包视频生成失败');
      }}
      let videos = [];
      try {{
        videos = await requestExtract(task);
      }} catch (error) {{
        lastError = error;
        continue;
      }}
      let nextVideo = await pickNewVideoWithLlm(videos, beforeKeys, task, pageStatus, {{
        stage: 'candidate-binding',
        beforeFailureCount: observedFailureCount,
        completedSeenAt,
      }});
      if (!nextVideo && completedSeenAt) {{
        await activateLatestResultCard();
        try {{
          videos = await requestExtract(task);
          nextVideo = await pickNewVideoWithLlm(videos, beforeKeys, task, pageStatus, {{
            stage: 'candidate-binding-after-activate',
            beforeFailureCount: observedFailureCount,
            completedSeenAt,
          }});
        }} catch (error) {{
          lastError = error;
        }}
      }}
      if (!nextVideo) {{
        const now = Date.now();
        const shouldRefreshAfterCompleted = completedSeenAt && now - completedSeenAt > 30000;
        const shouldRefreshActive = hasActiveDoubaoGenerationSignal(pageStatus) && now - submittedAt > 90_000;
        if ((shouldRefreshAfterCompleted || shouldRefreshActive) && now - lastRefreshAt > 45_000) {{
          lastRefreshAt = now;
          const reason = shouldRefreshAfterCompleted ? 'completed-without-result' : 'active-generation-stale';
          toast(shouldRefreshAfterCompleted ? '已检测到豆包完成，刷新页面重新读取结果' : '豆包生成页长时间未更新，刷新当前任务页读取结果');
          await updateTask({{ id: task.id, stage: 'refreshing-result-page', doubaoRefreshReason: reason, doubaoLastRefreshAt: now }}).catch(() => {{}});
          if (!(await devtoolsRefreshTaskPage(task.id, reason))) {{
            location.reload();
          }}
          await sleep(8000);
        }}
        continue;
      }}
      const nextKey = videoStableKey(nextVideo);
      if (nextKey && nextKey === stableCandidateKey) stableCandidateSeen += 1;
      else {{
        stableCandidateKey = nextKey;
        stableCandidateSeen = 1;
      }}
      if (completedSeenAt && stableCandidateSeen < 2) {{
        await updateTask({{ id: task.id, status: 'submitted', stage: 'result-candidate-confirming', doubaoResultKey: nextKey || '', assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '', backupUrl: nextVideo.backupUrl || '' }}).catch(() => {{}});
        await sleep(2500);
        continue;
      }}
      await assertActive(task, 'downloading');
      nextVideo = await resolveNoWatermarkForVideo(nextVideo, task, 2);
      let selectedPatch = buildSelectedVideoPatch(nextVideo);
      if (DOUBAO_REQUIRE_NO_WATERMARK_DOWNLOAD && !isWatermarkResolvedCandidate(nextVideo)) {{
        await updateTask({{
          id: task.id,
          status: 'submitted',
          stage: 'watermark-unresolved',
          doubaoPageMessage: '已抓到视频，但无水印解析未成功，暂不下载带水印版本',
          assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '',
          backupUrl: nextVideo.backupUrl || '',
          ...selectedPatch,
        }}).catch(() => {{}});
        lastError = new Error(selectedPatch.doubaoWatermarkDiagnostic?.error || '无水印解析未成功');
        stableCandidateKey = '';
        stableCandidateSeen = 0;
        await activateLatestResultCard();
        await sleep(5000);
        continue;
      }}
      await updateTask({{ id: task.id, status: 'downloading', assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '', backupUrl: nextVideo.backupUrl || '', ...selectedPatch }});
      try {{
        const downloadVideoPayload = {{ ...nextVideo, requireNoWatermark: DOUBAO_REQUIRE_NO_WATERMARK_DOWNLOAD }};
        const downloaded = await downloadTaskVideo(task, downloadVideoPayload);
        selectedPatch = buildSelectedVideoPatch(nextVideo);
        toast(`????${{task.title || task.id}}`);
        return downloaded || task;
      }} catch (error) {{
        lastError = error;
        downloadAttempts += 1;
        await updateTask({{ id: task.id, status: 'submitted', stage: 'download-retry-waiting', error: '', doubaoDownloadError: String(error && error.message || error), assetUrl: nextVideo.assetUrl || nextVideo.backupUrl || '', backupUrl: nextVideo.backupUrl || '', ...selectedPatch }}).catch(() => {{}});
        if (downloadAttempts >= 4) throw error;
        stableCandidateKey = '';
        stableCandidateSeen = 0;
        await activateLatestResultCard();
        if (downloadAttempts >= 2 && Date.now() - lastRefreshAt > 45000) {{
          lastRefreshAt = Date.now();
          if (!(await devtoolsRefreshTaskPage(task.id, 'download-url-retry'))) location.reload();
          await sleep(8000);
        }} else {{
          await sleep(5000);
        }}
      }}
    }}
    throw new Error(lastError && lastError.message ? lastError.message : '豆包视频生成超时，未检测到可下载视频');
  }}

  function getGenerationFailureAfter(previousCount, task = null) {{
    const hardFailure = getHardGenerationFailureMessage(currentFailureSourceText(task));
    if (hardFailure) return {{ count: Math.max(previousCount + 1, countGenerationFailures(task)), message: hardFailure, hard: true }};
    const count = countGenerationFailures(task);
    if (count <= previousCount) return null;
    return {{ count, message: getGenerationFailureMessage(task) || '豆包视频生成失败' }};
  }}

  function countGenerationFailures(task = null) {{
    const text = currentFailureSourceText(task, getPageTextWithoutPanel());
    return (text.match(/视频生成失败|生成失败|生成异常|无法生成|任务失败|请求失败|额度不足|积分不足|余额不足|生成额度未扣除|额度未扣除|未扣除|内容不符合|审核未通过|网络异常|服务异常|请稍后重试|稍后再试|出于肖像保护考虑|肖像保护|真实人脸素材|人脸素材作为参考|换张参考图/gi) || []).length;
  }}

  function getGenerationFailureMessage(task = null) {{
    const text = currentFailureSourceText(task, getPageTextWithoutPanel()).replace(/\\s+/g, ' ');
    const hardFailure = getHardGenerationFailureMessage(text);
    if (hardFailure) return hardFailure;
    const match = text.match(/(?:视频生成失败|生成失败|生成异常|无法生成|任务失败|请求失败|额度不足|积分不足|余额不足|生成额度未扣除|额度未扣除|未扣除|内容不符合|审核未通过|网络异常|服务异常|请稍后重试|稍后再试)[^。；;\\n]*/i);
    return match ? match[0].trim() || '豆包视频生成失败' : '';
  }}

  function getHardGenerationFailureMessage(text = String(document.body?.innerText || '')) {{
    const match = String(text || '').match(DOUBAO_HARD_FAILURE_PATTERN);
    return match ? match[0].trim() || '豆包拒绝当前素材/提示词' : '';
  }}

  function buildPromptText(task) {{
    const prompt = forcePromptDuration(String(task.prompt || '').trim(), taskTargetDurationSeconds(task));
    const parts = [prompt];
    if (task.ratio && !/画面比例|比例\\s*[:：]|aspect\\s*ratio/i.test(prompt)) parts.push(`画面比例：${{task.ratio}}`);
    return parts.filter(Boolean).join('\\n');
  }}

  function forcePromptDuration(value, seconds = DOUBAO_FIXED_VIDEO_DURATION_SECONDS) {{
    const text = String(value || '').replace(/\\r\\n?/g, '\\n').trim();
    const durationField = /((?:总时长|视频时长|生成时长|时长|duration)\\s*[:：]?\\s*)\\d+(?:\\.\\d+)?\\s*(?:秒|s|sec|seconds?)?/gi;
    const hasDurationField = (line) => /(?:总时长|视频时长|生成时长|时长|duration)\\s*[:：]?\\s*\\d+(?:\\.\\d+)?\\s*(?:秒|s|sec|seconds?)?/i.test(line);
    const skipLine = (line) => /时间轴|^\\s*(?:\\d+(?:\\.\\d+)?\\s*[-~至到]\\s*\\d+(?:\\.\\d+)?\\s*秒|[【\\[]?(?:动作过程|镜头动态|运镜\\/景别)[】\\]]?)/.test(line);
    let rewritten = false;
    const lines = (text ? text.split('\\n') : []).map((line) => {{
      if (!hasDurationField(line) || skipLine(line)) return line;
      rewritten = true;
      return line.replace(durationField, (_, label) => `${{label}}${{seconds}}秒`);
    }});
    if (!rewritten) lines.push(`时长：${{seconds}}秒`);
    return lines.join('\\n').replace(/\\n{{3,}}/g, '\\n\\n').trim();
  }}

  function detectDurationAdjustQuestion(task = null) {{
    const text = String(document.body?.innerText || '');
    const seconds = String(taskTargetDurationSeconds(task || {{}})).replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
    return new RegExp('不支持生成超过\\\\s*' + seconds + '\\\\s*秒的视频[\\\\s\\\\S]{{0,80}}调整为\\\\s*' + seconds + '\\\\s*秒继续生成|超过\\\\s*' + seconds + '\\\\s*秒[\\\\s\\\\S]{{0,80}}' + seconds + '\\\\s*秒继续生成|调整为\\\\s*' + seconds + '\\\\s*秒继续生成', 'i').test(text);
  }}

  function detectLowerDurationCapQuestion() {{
    const text = String(document.body?.innerText || '');
    const patterns = [
      /不支持生成超过\\s*(\\d{{1,2}})\\s*秒的视频[\\s\\S]{{0,100}}调整为\\s*(\\d{{1,2}})\\s*秒继续生成/i,
      /超过\\s*(\\d{{1,2}})\\s*秒[\\s\\S]{{0,100}}(\\d{{1,2}})\\s*秒继续生成/i,
      /调整为\\s*(\\d{{1,2}})\\s*秒继续生成/i,
    ];
    for (const pattern of patterns) {{
      const match = text.match(pattern);
      if (!match) continue;
      const values = match.slice(1).map(value => Number(value)).filter(value => Number.isFinite(value) && value > 0);
      const cap = Math.min(...values);
      if (Number.isFinite(cap) && cap < DOUBAO_FIXED_VIDEO_DURATION_SECONDS) return cap;
    }}
    return 0;
  }}

  async function failOnLowerDurationCap(task = null) {{
    const requiredSeconds = taskTargetDurationSeconds(task || {{}});
    const cap = detectLowerDurationCapQuestion();
    if (!cap) return false;
    if (cap >= requiredSeconds) return false;
    const message = '豆包页面提示只能调整为 ' + cap + ' 秒继续生成，低于当前目标 ' + requiredSeconds + ' 秒';
    await reportAutomationDiagnostic(task && task.id || getAssignedTaskId(), 'duration-cap-lower-than-required', {{
      stage: 'duration-cap-lower-than-required',
      capSeconds: cap,
      requiredSeconds,
      durationCapability: inspectDoubaoDurationCapability(task || requiredSeconds),
    }});
    throw new Error(message);
  }}

  function detectGenerationCompletedNotice(task = null) {{
    const guard = task && task.resultGuard || {{}};
    if (guard.submittedAt || guard.anchorBottom || guard.maxMessageIdBefore) {{
      return extractVisibleResultVideos().some((video) => {{
        if (!hasCompletedStatusSignal(String(video.cardText || ''))) return false;
        return scoreTaskVideoCandidate(video, new Set(), guard) > -Infinity;
      }});
    }}
    return hasCompletedStatusSignal(String(document.body?.innerText || ''));
  }}

  async function answerDurationAdjustQuestion(task = null) {{
    const input = findAnyPromptInput() || findPromptInput();
    const reply = durationConfirmReply(task);
    const seconds = taskTargetDurationSeconds(task || {{}});
    if (!input) throw new Error('豆包要求确认 ' + seconds + ' 秒生成，但未找到回复输入框');
    toast('已自动确认：生成' + seconds + '秒视频');
    await setPrompt(input, reply, {{ allowChatReply: true }});
    await waitForPromptValue(input, reply, {{ allowChatReply: true }});
    await submitDurationConfirmReply(input);
    await sleep(1200);
  }}

  async function submitDurationConfirmReply(input) {{
    input = findAnyPromptInput() || input;
    if (!input) return false;
    const rect = input.getBoundingClientRect();
    const composerRect = composerRectFor(input);
    const buttons = [...document.querySelectorAll('button,[role="button"],[aria-label],[title],label,div,span,svg')]
      .filter(el => visible(el) && !el.closest('#vmo-doubao-devtools-status'))
      .map(el => clickableTargetFor(el) || el)
      .filter((el, index, list) => el instanceof HTMLElement && list.indexOf(el) === index)
      .filter(el => visible(el) && !isLikelyConversationTitleOrHistoryTarget(el) && el.getAttribute('aria-disabled') !== 'true' && !el.hasAttribute('disabled'))
      .filter(el => isElementWithinComposer(el, rect, composerRect, 120) && !isNonSubmitControlText(textOf(el)))
      .sort((a, b) => scoreSubmitControl(b, rect, composerRect) - scoreSubmitControl(a, rect, composerRect));
    if (buttons[0]) {{
      if (!(await devtoolsClick(buttons[0]))) clickElement(buttons[0]);
      return true;
    }}
    input.focus?.();
    input.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }}));
    input.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }}));
    return true;
  }}

  function addInternalMediaUrl(target, value, sourceKey = '', source = '') {{
    const url = normalizeVideoUrl(value);
    if (!url) return false;
    if (!target.assetUrl) target.assetUrl = url;
    else if (!target.backupUrl && target.assetUrl !== url) target.backupUrl = url;
    target.doubaoResultMeta = normalizeDoubaoResultMeta(target.doubaoResultMeta, {{
      sourceKeys: [`url:${{url}}@${{String(source || sourceKey || 'unknown').slice(0, 80)}}`],
      resultKeys: [normalizeIdentityKey('url', url)],
    }});
    return true;
  }}

  function isMediaUrlKey(key) {{
    return /url|uri|src|play|main|backup|download|media|video|item/i.test(String(key || ''));
  }}

  function isSemiAutoTask(task) {{
    const mode = String(task && (task.doubaoAutomationMode || task.automationMode || task.automation_mode) || '').trim().toLowerCase();
    return ['semi', 'semi-auto', 'semiauto', 'manual', 'assist'].includes(mode);
  }}

  async function processTask(task) {{
    worker.activeTaskId = task.id;
    toast(`已领取：${{task.title || task.id}}`);
    task = await assertActive(task, 'claimed');
    const status = String(task.status || '');
    let beforeKeys = new Set(Array.isArray(task.beforeVideoKeys) ? task.beforeVideoKeys : []);
    let beforeFailureCount = countGenerationFailures(task);
    if (isSemiAutoTask(task) && ['submitted', 'downloading'].includes(status)) {{
      const message = '半自动模式：当前任务已交给用户手动处理，VMO 不再自动轮询或下载';
      await updateTask({{ id: task.id, status: 'manual', stage: 'semi-auto-ready', doubaoPageMessage: message, automationHeartbeatAt: Date.now() }}).catch(() => {{}});
      toast(message);
      return {{ ok: true, taskId: task.id, href: location.href, semiAuto: true }};
    }}
    if (['submitted', 'downloading'].includes(status)) {{
      toast(`继续回收：${{task.title || task.id}}`);
      await waitAndDownloadGeneratedVideo(task, beforeKeys, beforeFailureCount);
      return {{ ok: true, taskId: task.id, href: location.href, resumed: true }};
    }}
    if (!['submitted', 'downloading'].includes(status)) {{
      await ensureVideoSurface();
      await assertVideoSurfaceForSubmit(task, 'surface-ready');
      await syncDoubaoPageStatus(task, 'surface-ready');
      let input = findPromptInput();
      if (!input) throw new Error('未找到豆包提示词输入框');
      beforeKeys = await readCurrentVideoKeys();
      beforeFailureCount = 0;
      await uploadImages(task);
      await assertVideoSurfaceForSubmit(task, 'after-upload');
      input = await waitForWritablePromptInput(input);
      if (!input) throw new Error('上传参考图后未找到豆包提示词输入框');
      const promptText = buildPromptText(task);
      await assertVideoSurfaceForSubmit(task, 'before-prompt');
      await setPrompt(input, promptText);
      input = await waitForWritablePromptInput(input);
      await waitForPromptValue(input, promptText);
      input = await ensurePromptReadyForSubmit(input, promptText);
      const resultGuard = captureSubmissionAnchor(input, promptText, beforeKeys);
      resultGuard.vmoTaskId = task.id;
      await resetNetworkCaptureForTask(task, resultGuard);
      await assertActive(task, 'prepared');
      await updateTask({{ id: task.id, status: 'prepared', beforeVideoKeys: [...beforeKeys], resultGuard }});
      if (isSemiAutoTask(task)) {{
        const message = '半自动模式：已上传参考图并填入提示词，请手动设置时长、比例并点击生成';
        await updateTask({{
          id: task.id,
          status: 'manual',
          stage: 'semi-auto-ready',
          beforeVideoKeys: [...beforeKeys],
          resultGuard,
          doubaoPageMessage: message,
          automationHeartbeatAt: Date.now(),
        }});
        toast(message);
        worker.activeTaskId = '';
        return {{ ok: true, taskId: task.id, href: location.href, semiAuto: true }};
      }}
      toast(`已填入：${{task.title || task.id}}，正在点击生成`);
      await assertVideoSurfaceForSubmit(task, 'before-submit');
      await ensureFixedDurationSelected(task);
      await submit(input, promptText);
      await waitForSubmissionAccepted(input, beforeFailureCount, task);
      await assertActive(task, 'submitted');
      await syncDoubaoPageStatus(task, 'submitted');
      await updateTask({{ id: task.id, status: 'submitted', beforeVideoKeys: [...beforeKeys], resultGuard }});
      task.resultGuard = resultGuard;
      toast(`已提交：${{task.title || task.id}}`);
    }}
    await waitAndDownloadGeneratedVideo(task, beforeKeys, beforeFailureCount);
    return {{ ok: true, taskId: task.id, href: location.href }};
  }}

  async function workerLoop() {{
    while (worker.running) {{
      worker.lastTick = Date.now();
      try {{
        if (worker.activeTaskId) {{
          const latest = await getTaskStatus(worker.activeTaskId).catch(() => null);
          if (latest && !['downloaded', 'completed', 'failed', 'cancelled', 'manual'].includes(String(latest.status || ''))) {{
            await sleep(2500);
            continue;
          }}
          worker.activeTaskId = '';
        }}
        await heartbeat();
        const task = await claimTask();
        if (!task) {{
          toast(VMO_IDLE_TASK_MESSAGE);
          await sleep(2500);
          continue;
          toast('等待 VMO 豆包任务');
          await sleep(2500);
          continue;
        }}
        await processTask(task);
      }} catch (error) {{
        console.error('[VMO Doubao automation]', error);
        const message = String(error && error.message || error);
        if (/任务已切换到其它豆包账号/.test(message)) {{
          toast('任务已切换到其它豆包账号');
          await sleep(1500);
          continue;
        }}
        if (isLocalBridgeDisconnect(error)) {{
          const id = worker.activeTaskId || getAssignedTaskId();
          if (!id) {{
            worker.activeTaskId = '';
            toast(VMO_IDLE_TASK_MESSAGE);
            await sleep(2500);
            continue;
          }}
          const latest = id ? await getTaskStatus(id).catch(() => null) : null;
          if (!latest || ['downloaded', 'completed', 'failed', 'cancelled', 'manual'].includes(String(latest.status || ''))) {{
            worker.activeTaskId = '';
            toast(latest && ['downloaded', 'completed'].includes(String(latest.status || '')) ? VMO_TASK_COMPLETED_MESSAGE : VMO_LOCAL_BRIDGE_WAIT_MESSAGE);
            await sleep(2500);
            continue;
          }}
        }}
        toast(`VMO 自动化失败：${{message}}`);
        const id = worker.activeTaskId || getAssignedTaskId();
        try {{
          const latest = id ? await getTaskStatus(id) : null;
          if (id && (!latest || latest.status !== 'cancelled')) {{
            const preserved = await preserveActiveGenerationOnError(latest || {{ id }}, message, 'waiting-result');
            if (preserved) worker.activeTaskId = '';
            else await updateTask({{ id, status: 'failed', error: message }});
          }}
        }} catch {{}}
        await sleep(2500);
      }} finally {{
        if (worker.activeTaskId) {{
          const latest = await getTaskStatus(worker.activeTaskId).catch(() => null);
          if (!latest || ['downloaded', 'completed', 'failed', 'cancelled', 'manual'].includes(String(latest.status || ''))) worker.activeTaskId = '';
        }}
      }}
    }}
  }}

  workerLoop();
  return {{ ok: true, injected: true, href: location.href }};
}})()
"""


def _page_probe_script() -> str:
    return r"""
(() => ({
  href: location.href,
  extensionPanel: !!document.getElementById('page-service-root'),
  extensionAuth: typeof globalThis.dbvdCheckAuthorized,
  assignedTaskId: (() => {
    try {
      const url = new URL(location.href);
      return url.searchParams.get('dbvdTaskId') || sessionStorage.getItem('dbvdTaskId') || '';
    } catch {
      return sessionStorage.getItem('dbvdTaskId') || '';
    }
  })(),
  devtoolsBridge: !!window.__vmoDoubaoBridgeReady,
  title: document.title,
}))()
"""


def _devtools_tabs(debug_port: int) -> list[dict]:
    tabs = http.get(f"http://127.0.0.1:{debug_port}/json/list", timeout=1.5).json()
    return tabs if isinstance(tabs, list) else []


def _tab_url_task_id(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        query = urllib.parse.parse_qs(parsed.query)
        task_id = (query.get("dbvdTaskId") or [""])[0]
        if task_id:
            return urllib.parse.unquote(task_id)
        match = re.search(r"dbvdTaskId=([^&]+)", parsed.fragment or "")
        return urllib.parse.unquote(match.group(1)) if match else ""
    except Exception:
        return ""


def _tab_url_account_id(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        query = urllib.parse.parse_qs(parsed.query)
        account_id = (query.get("dbvdAccountId") or [""])[0]
        if account_id:
            return urllib.parse.unquote(account_id)
        match = re.search(r"dbvdAccountId=([^&]+)", parsed.fragment or "")
        return urllib.parse.unquote(match.group(1)) if match else ""
    except Exception:
        return ""


def _task_status_for_tab(task_id: str) -> str:
    if not task_id:
        return ""
    try:
        task = get_task(task_id)
        return str((task or {}).get("status") or "")
    except Exception:
        return ""


def _doubao_pages(tabs: list[dict], site: str = DOUBAO_SITE_DEFAULT) -> list[dict]:
    site = normalize_site(site)
    return [
        item for item in tabs
        if item.get("type") == "page" and _url_matches_site(item.get("url") or "", site)
    ]


def _select_doubao_page(tabs: list[dict], task_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> Optional[dict]:
    pages = _doubao_pages(tabs, site)
    if not pages:
        return None
    if task_id:
        exact = next((item for item in pages if _tab_url_task_id(str(item.get("url") or "")) == str(task_id)), None)
        if exact:
            return exact
    idle = next((item for item in pages if not _tab_url_task_id(str(item.get("url") or ""))), None)
    if not task_id:
        return idle or pages[0]
    return None


def _devtools_json_command(debug_port: int, path: str, *, timeout: float = 1.5) -> Any:
    response = http.get(f"http://127.0.0.1:{debug_port}{path}", timeout=timeout)
    response.raise_for_status()
    return response.json()


def _activate_devtools_page(debug_port: int, page: dict) -> None:
    page_id = str(page.get("id") or "")
    if not page_id:
        return
    try:
        _devtools_json_command(debug_port, f"/json/activate/{urllib.parse.quote(page_id, safe='')}", timeout=1.0)
    except Exception:
        try:
            response = http.put(
                f"http://127.0.0.1:{debug_port}/json/activate/{urllib.parse.quote(page_id, safe='')}",
                timeout=1.0,
            )
            response.raise_for_status()
        except Exception:
            pass


def _close_devtools_page(debug_port: int, page: dict) -> bool:
    page_id = str(page.get("id") or "")
    if not page_id:
        return False
    try:
        _devtools_json_command(debug_port, f"/json/close/{urllib.parse.quote(page_id, safe='')}", timeout=1.0)
        return True
    except Exception:
        try:
            response = http.put(
                f"http://127.0.0.1:{debug_port}/json/close/{urllib.parse.quote(page_id, safe='')}",
                timeout=1.0,
            )
            return bool(response.ok)
        except Exception:
            return False


def _cleanup_finished_task_pages(debug_port: int, account_id: str = "", keep_task_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> int:
    try:
        pages = _doubao_pages(_devtools_tabs(debug_port), site)
    except Exception:
        return 0
    closed = 0
    terminal = _task_terminal_statuses()
    for page in pages:
        page_url = str(page.get("url") or "")
        task_id = _tab_url_task_id(page_url)
        if not task_id or task_id == keep_task_id:
            continue
        page_account_id = _tab_url_account_id(page_url)
        if account_id and page_account_id and page_account_id != account_id:
            continue
        status = _task_status_for_tab(task_id)
        if status and status not in terminal:
            continue
        if _close_devtools_page(debug_port, page):
            closed += 1
    if closed:
        logger.info("[DoubaoPool] closed %s finished task page(s) account=%s keep=%s", closed, account_id or "", keep_task_id or "")
    return closed


def _dispatch_devtools_click(task_id: str, account_id: str = "", x: Any = None, y: Any = None) -> dict:
    if not task_id:
        raise DoubaoPoolError("missing task id")
    try:
        click_x = float(x)
        click_y = float(y)
    except Exception as exc:
        raise DoubaoPoolError("invalid click coordinates") from exc
    task = get_task(task_id)
    if not task:
        raise DoubaoPoolError("task not found")
    task_account_id = str(task.get("accountId") or "")
    site = _task_site(task)
    if account_id and task_account_id and str(account_id) != task_account_id:
        raise DoubaoPoolError("account mismatch")
    debug_port = _debug_port({"id": task_account_id or account_id, "site": site})
    tabs = _devtools_tabs(debug_port)
    page = _select_doubao_page(tabs, task_id, site)
    if not page or not page.get("webSocketDebuggerUrl"):
        raise DoubaoPoolError("task page not found")
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
        params = {
            "type": event_type,
            "x": click_x,
            "y": click_y,
            "button": "left",
            "buttons": 1 if event_type == "mousePressed" else 0,
            "clickCount": 1,
        }
        _devtools_send(ws_url, "Input.dispatchMouseEvent", params, timeout=2)
        time.sleep(0.05)
    return {"ok": True, "x": click_x, "y": click_y, "taskId": task_id, "accountId": task_account_id or account_id}


def _refresh_devtools_task_page(task_id: str, account_id: str = "", reason: str = "") -> dict:
    if not task_id:
        raise DoubaoPoolError("missing task id")
    task = get_task(task_id)
    if not task:
        raise DoubaoPoolError("task not found")
    task_account_id = str(task.get("accountId") or "")
    site = _task_site(task)
    if account_id and task_account_id and str(account_id) != task_account_id:
        raise DoubaoPoolError("account mismatch")
    debug_port = _debug_port({"id": task_account_id or account_id, "site": site})
    tabs = _devtools_tabs(debug_port)
    page = _select_doubao_page(tabs, task_id, site)
    if not page or not page.get("webSocketDebuggerUrl"):
        raise DoubaoPoolError("task page not found")
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    _devtools_send(ws_url, "Page.reload", {"ignoreCache": True}, timeout=2)
    now = _now_ms()
    _update_task({
        "id": task_id,
        "stage": "refreshing-result-page",
        "doubaoRefreshReason": reason or "manual-refresh",
        "doubaoLastRefreshAt": now,
        "automationHeartbeatAt": now,
    })
    _probe_account_page_later(debug_port, task_id, task_account_id or account_id)
    return {"ok": True, "taskId": task_id, "accountId": task_account_id or account_id, "reason": reason or ""}


def _devtools_new_page(debug_port: int, url: str, *, timeout: float = 2.5) -> dict:
    endpoint = f"http://127.0.0.1:{debug_port}/json/new?{urllib.parse.quote(url, safe='')}"
    for method in (http.put, http.get):
        try:
            response = method(endpoint, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    return {}


def _open_devtools_task_page(debug_port: int, url: str) -> Optional[dict]:
    created = _devtools_new_page(debug_port, url, timeout=2.5)
    if isinstance(created, dict) and created:
        return created
    return None


def _inject_devtools_bridge(debug_port: int, lock: dict, account_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> dict:
    tabs = _devtools_tabs(debug_port)
    page = _select_doubao_page(tabs, site=site)
    if not page or not page.get("webSocketDebuggerUrl"):
        raise DoubaoPoolError("未找到可注入的豆包页面")
    before = _extract_eval_value(_devtools_eval(page["webSocketDebuggerUrl"], _page_probe_script()))
    injected = _extract_eval_value(_devtools_eval(page["webSocketDebuggerUrl"], _bridge_script(lock, account_id)))
    time.sleep(0.3)
    after = _extract_eval_value(_devtools_eval(page["webSocketDebuggerUrl"], _page_probe_script()))
    return {"before": before, "injected": injected, "after": after}


def _keepalive_inject_account_bridge(debug_port: int, lock: dict, account_id: str = "", task_id: str = "", *, force: bool = False, site: str = DOUBAO_SITE_DEFAULT) -> bool:
    if not debug_port:
        return False
    key = f"{account_id or '__unknown__'}:{task_id or '__idle__'}"
    now = _now_ms()
    with _BROWSER_NAV_LOCK:
        last = int(_ACCOUNT_KEEPALIVE_INJECTS.get(key) or 0)
        if not force and last and now - last < ACCOUNT_KEEPALIVE_INJECT_DEBOUNCE_MS:
            return False
        _ACCOUNT_KEEPALIVE_INJECTS[key] = now
    if task_id:
        _probe_account_page_later(debug_port, task_id, account_id, site=site)
        return True
    _inject_devtools_bridge(debug_port, lock, account_id, site=site)
    return True


def _probe_account_page_later(debug_port: int, task_id: str = "", account_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> None:
    site = normalize_site(site)
    def worker() -> None:
        last_error = ""
        for attempt in range(1, 9):
            time.sleep(1.5 if attempt == 1 else 1)
            try:
                tabs = _devtools_tabs(debug_port)
                page = _select_doubao_page(tabs, task_id, site)
                if not page or not page.get("webSocketDebuggerUrl"):
                    last_error = "no doubao tab"
                    continue
                probe = _extract_eval_value(_devtools_eval(page["webSocketDebuggerUrl"], _page_probe_script(), timeout=3))
                logger.info("[DoubaoPool] page probe attempt=%s task=%s result=%s", attempt, task_id or "", json.dumps(probe, ensure_ascii=False))
                if task_id:
                    injected = _extract_eval_value(_devtools_eval(page["webSocketDebuggerUrl"], _automation_bridge_script(_load_launch_lock(), task_id, account_id), timeout=5))
                    logger.info("[DoubaoPool] devtools automation injected task=%s result=%s", task_id, json.dumps(injected, ensure_ascii=False))
                return
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                logger.info("[DoubaoPool] page probe retry port=%s task=%s attempt=%s: %s", debug_port, task_id, attempt, e)
        logger.warning("[DoubaoPool] page probe failed port=%s task=%s: %s", debug_port, task_id, last_error or "timeout")

    threading.Thread(target=worker, daemon=True, name="doubao-page-probe").start()


def _configure_task_page(page: dict, url: str, account_id: str = "") -> bool:
    ws_url = str(page.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        return False
    expression = rf"""
(() => {{
  const target = new URL({json.dumps(url, ensure_ascii=False)});
  const taskId = target.searchParams.get('dbvdTaskId') || '';
  const targetAccountId = target.searchParams.get('dbvdAccountId') || '';
  const accountId = {json.dumps(account_id or "", ensure_ascii=False)};
  try {{
    if (taskId) sessionStorage.setItem('dbvdTaskId', taskId);
    else sessionStorage.removeItem('dbvdTaskId');
    if (targetAccountId || accountId) sessionStorage.setItem('dbvdAccountId', targetAccountId || accountId);
    else sessionStorage.removeItem('dbvdAccountId');
    const worker = window.__vmoDoubaoAutomationWorker;
    if (worker) {{
      if (taskId) worker.desiredTaskId = taskId;
      else worker.desiredTaskId = '';
      if (targetAccountId || accountId) worker.accountId = targetAccountId || accountId;
      else worker.accountId = '';
      if (!taskId || (taskId && (targetAccountId || accountId))) worker.running = false;
    }}
  }} catch {{}}
  const path = location.pathname || '';
  const current = new URL(location.href);
  const currentTaskId = current.searchParams.get('dbvdTaskId') || (current.hash.match(/dbvdTaskId=([^&]+)/) || [])[1] || '';
  const currentPath = path.replace(/\\/+$|\\?.*$/g, '') || '/';
  const targetPath = (target.pathname || '').replace(/\\/+$/g, '') || '/';
  if (taskId && currentTaskId === taskId && currentPath === targetPath) {{
    return {{ ok: true, navigated: false, href: location.href, taskId, accountId: targetAccountId || accountId }};
  }}
  if (!taskId && currentTaskId) {{
    location.href = target.toString();
    return {{ ok: true, navigated: true, href: location.href, taskId: '', accountId }};
  }}
  if (!path.startsWith('/chat') || (taskId && currentPath !== targetPath)) {{
    location.href = target.toString();
    return {{ ok: true, navigated: true, href: location.href }};
  }}
  if (taskId && location.href !== target.toString()) {{
    location.href = target.toString();
    return {{ ok: true, navigated: true, href: location.href, taskId, accountId: targetAccountId || accountId }};
  }}
  return {{ ok: true, navigated: false, href: location.href, taskId, accountId }};
}})()
"""
    _devtools_eval(ws_url, expression, timeout=2)
    return True


def _navigate_existing_doubao_tab(debug_port: int, url: str, account_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> bool:
    task_id = _tab_url_task_id(url)
    site = normalize_site(site)
    now = _now_ms()
    with _BROWSER_NAV_LOCK:
        try:
            tabs = _devtools_tabs(debug_port)
        except Exception:
            return False

        if task_id:
            _cleanup_finished_task_pages(debug_port, account_id, task_id, site)
            try:
                tabs = _devtools_tabs(debug_port)
            except Exception:
                tabs = []
            exact = _select_doubao_page(tabs, task_id, site)
            if exact:
                try:
                    _configure_task_page(exact, url, account_id)
                    _activate_devtools_page(debug_port, exact)
                except Exception:
                    pass
                _TASK_PAGE_OPEN_REQUESTS.pop(task_id, None)
                return True

            last_open = int(_TASK_PAGE_OPEN_REQUESTS.get(task_id) or 0)
            if last_open and now - last_open < 6000:
                return True

            try:
                page = _open_devtools_task_page(debug_port, url)
                if page:
                    _TASK_PAGE_OPEN_REQUESTS[task_id] = now
                    return True
            except Exception:
                return False
            return False

        _cleanup_finished_task_pages(debug_port, account_id, site=site)
        try:
            tabs = _devtools_tabs(debug_port)
        except Exception:
            tabs = []
        page = _select_doubao_page(tabs, site=site)
        if not page:
            return False
        try:
            _configure_task_page(page, url, account_id)
            _activate_devtools_page(debug_port, page)
        except Exception:
            return False
        return True


def _open_pending_task_page_later(debug_port: int, url: str, account_id: str, task_id: str, site: str = DOUBAO_SITE_DEFAULT) -> None:
    if not task_id:
        return
    now = _now_ms()
    with _BROWSER_NAV_LOCK:
        last = int(_PENDING_TASK_PAGE_OPENS.get(task_id) or 0)
        if last and now - last < 10_000:
            return
        _PENDING_TASK_PAGE_OPENS[task_id] = now

    def worker() -> None:
        try:
            for _ in range(12):
                time.sleep(0.8)
                if _navigate_existing_doubao_tab(debug_port, url, account_id, site):
                    _probe_account_page_later(debug_port, task_id, account_id, site=site)
                    return
        finally:
            with _BROWSER_NAV_LOCK:
                _PENDING_TASK_PAGE_OPENS.pop(task_id, None)

    threading.Thread(target=worker, daemon=True, name="doubao-pending-task-page").start()


def _write_chrome_download_prefs(profile_dir: Path, download_dir: Path) -> None:
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = default_dir / "Preferences"
    prefs = {}
    try:
        if prefs_path.exists():
            prefs = json.loads(prefs_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        prefs = {}
    prefs.setdefault("download", {})
    prefs["download"].update({
        "default_directory": str(download_dir),
        "directory_upgrade": True,
        "prompt_for_download": False,
    })
    prefs_path.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")


def _stop_profile_browser(profile_dir: Path) -> None:
    if os.name != "nt":
        return
    target = str(profile_dir).lower()
    script = (
        "$target = " + json.dumps(target) + ";\n"
        "Get-CimInstance Win32_Process | Where-Object {\n"
        "  ($_.Name -match '^(chrome|msedge|brave|doubao-watermark-browser)\\.exe$') -and\n"
        "  ($_.CommandLine -and $_.CommandLine.ToLower().Contains($target))\n"
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }\n"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=False,
        )
        time.sleep(0.5)
    except Exception as e:  # noqa: BLE001
        logger.warning("[DoubaoPool] failed to stop stale account browser: %s", e)


def _process_alive(pid: Any) -> bool:
    try:
        value = int(pid or 0)
    except Exception:
        return False
    if value <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, value)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(value, 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def _ensure_server() -> dict:
    global _SERVER, _SERVER_THREAD
    with _STATE_LOCK:
        if _SERVER and _SERVER_THREAD and _SERVER_THREAD.is_alive() and _LOCK.get("expiresAt", 0) > _now_ms():
            return dict(_LOCK)
        persisted = _load_launch_lock()
        for existing_port in (int(persisted.get("port") or 0), DEFAULT_SERVER_PORT):
            if existing_port and not _port_available(existing_port) and _server_healthy(existing_port, str(persisted.get("token") or "")):
                lock = _write_launch_lock(existing_port)
                logger.info("[DoubaoPool] reusing existing local task server on 127.0.0.1:%s", existing_port)
                return lock
        port = _preferred_server_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="doubao-pool-server")
        thread.start()
        _SERVER = server
        _SERVER_THREAD = thread
        lock = _write_launch_lock(port)
        logger.info("[DoubaoPool] local task server started on 127.0.0.1:%s", port)
        return lock


def _extension_info(lock: Optional[dict] = None) -> dict:
    lock = lock or {}
    plugin_status = _plugin_install_status()
    return {
        "extensionDir": str(EXTENSION_DIR),
        "extensionReady": _extension_manifest_ready(EXTENSION_DIR),
        "manifest": str(EXTENSION_DIR / "manifest.json"),
        "extraExtensionDirs": [str(path) for path in _browser_extension_dirs()[1:]],
        "duration15ExtensionReady": _extension_manifest_ready(DURATION15_EXTENSION_DIR),
        "duration15ExtensionDir": str(DURATION15_EXTENSION_DIR),
        "watermarkExtensionReady": _extension_manifest_ready(WATERMARK_EXTENSION_DIR),
        "watermarkExtensionDir": str(WATERMARK_EXTENSION_DIR),
        "packagedMultiOpenBrowser": "",
        "packagedBrowserChrome": str(PACKAGED_BROWSER_CHROME) if PACKAGED_BROWSER_CHROME.exists() else "",
        "sourceBrowserBundle": str(PACKAGED_MULTI_OPEN_BROWSER) if PACKAGED_MULTI_OPEN_BROWSER.exists() else "",
        "packagedWatermarkBrowser": "",
        "pluginsInstalled": bool(plugin_status.get("installed")),
        "pluginsEnabled": bool(plugin_status.get("enabled")),
        "pluginsSourceCurrent": bool(plugin_status.get("sourceCurrent")),
        "plugins": plugin_status.get("items") or [],
        "pluginInstall": plugin_status.get("installRecord") or {},
        "durationOptions": plugin_status.get("durationOptions") or list(DOUBAO_VIDEO_DURATION_OPTIONS),
        "durationMode": plugin_status.get("durationMode") or "adaptive",
        "durationSeconds": plugin_status.get("durationSeconds") or DOUBAO_FIXED_VIDEO_DURATION_SECONDS,
        "port": lock.get("port"),
        "expiresAt": lock.get("expiresAt"),
    }


def prepare_extension(force: bool = False) -> dict:
    install = _install_builtin_plugins(force=force)
    lock = _ensure_server()
    return {
        "ok": True,
        **_extension_info(lock),
        "install": install,
        "installMode": "load-unpacked",
    }


def _account_browser_snapshot(account: dict, task_id: str = "") -> dict:
    site = _account_site(account)
    debug_port = _debug_port(account)
    profile_dir = Path(str(account.get("profileDir") or "")) if account.get("profileDir") else None
    pid = int(account.get("browserPid") or 0) if str(account.get("browserPid") or "").isdigit() else 0
    result: dict[str, Any] = {
        "accountId": str(account.get("id") or ""),
        "site": site,
        "siteHost": _site_host(site),
        "debugPort": debug_port,
        "profileDir": str(profile_dir or ""),
        "browserPid": pid,
        "browserProcessKnown": bool(pid),
        "browserProcessAlive": _process_alive(pid) if pid else False,
        "browserDebugConnected": False,
        "doubaoTabOpen": False,
        "taskTabOpen": False,
        "doubaoPageCount": 0,
        "error": "",
    }
    try:
        tabs = _devtools_tabs(debug_port)
        pages = _doubao_pages(tabs, site)
        result["browserDebugConnected"] = True
        result["doubaoTabOpen"] = bool(pages)
        result["doubaoPageCount"] = len(pages)
        if task_id:
            result["taskTabOpen"] = any(_tab_url_task_id(str(item.get("url") or "")) == str(task_id) for item in pages)
        else:
            result["taskTabOpen"] = bool(pages)
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
    return result


def _account_keepalive_status(account: dict) -> dict:
    return {
        "lastKeepaliveAt": int(account.get("lastKeepaliveAt") or 0),
        "lastKeepaliveStatus": account.get("lastKeepaliveStatus") or "",
        "lastKeepaliveAction": account.get("lastKeepaliveAction") or "",
        "lastKeepaliveReason": account.get("lastKeepaliveReason") or "",
        "lastKeepaliveError": account.get("lastKeepaliveError") or "",
        "browserDebugConnected": bool(account.get("browserDebugConnected")),
        "browserProcessAlive": bool(account.get("browserProcessAlive")),
        "doubaoTabOpen": bool(account.get("doubaoTabOpen")),
        "doubaoPageCount": int(account.get("doubaoPageCount") or 0),
    }


def _record_account_keepalive(account_id: str, patch: dict) -> None:
    if not account_id:
        return
    try:
        with _STATE_LOCK:
            state = _read_state()
            changed = False
            for account in state.get("accounts", []):
                if str(account.get("id") or "") != str(account_id):
                    continue
                for key, value in (patch or {}).items():
                    account[key] = value
                account["updatedAt"] = _now_ms()
                changed = True
                break
            if changed:
                _write_state(state)
    except Exception as e:  # noqa: BLE001
        logger.debug("[DoubaoPool] keepalive state write skipped account=%s: %s", account_id, e)


def _keepalive_account_bridge(account: dict, lock: dict, *, task_id: str = "", force: bool = False) -> dict:
    account_id = str((account or {}).get("id") or "")
    site = _account_site(account)
    now = _now_ms()
    result: dict[str, Any] = {
        "accountId": account_id,
        "site": site,
        "checkedAt": now,
        "status": "skipped",
        "action": "none",
        "reason": "",
        "error": "",
    }
    if not account_id or not (account or {}).get("enabled", True):
        result["reason"] = "account-disabled"
        return result
    key = f"{site}:{account_id}:{task_id or '__idle__'}"
    with _BROWSER_NAV_LOCK:
        last = int(_ACCOUNT_KEEPALIVE_SWEEPS.get(key) or 0)
        if not force and last and now - last < ACCOUNT_KEEPALIVE_SWEEP_DEBOUNCE_MS:
            result["reason"] = "debounced"
            return result
        _ACCOUNT_KEEPALIVE_SWEEPS[key] = now
    snapshot = _account_browser_snapshot(account, task_id)
    heartbeat = _heartbeat_snapshot(account_id, site=site)
    last_seen = int(heartbeat.get("pluginLastSeenAt") or 0)
    heartbeat_age = now - last_seen if last_seen else 0
    result.update({
        "browserDebugConnected": bool(snapshot.get("browserDebugConnected")),
        "browserProcessAlive": bool(snapshot.get("browserProcessAlive")),
        "doubaoTabOpen": bool(snapshot.get("doubaoTabOpen")),
        "taskTabOpen": bool(snapshot.get("taskTabOpen")),
        "doubaoPageCount": int(snapshot.get("doubaoPageCount") or 0),
        "pluginConnected": bool(heartbeat.get("pluginConnected")),
        "pluginLastSeenAt": last_seen,
        "pluginHeartbeatAgeMs": heartbeat_age,
    })
    try:
        if snapshot.get("browserDebugConnected") and snapshot.get("doubaoTabOpen"):
            stale_plugin = not heartbeat.get("pluginConnected") or (last_seen and heartbeat_age > ACCOUNT_PLUGIN_RECOVERY_STALE_MS)
            needs_task_probe = bool(task_id and snapshot.get("taskTabOpen"))
            if force or stale_plugin or needs_task_probe:
                injected = _keepalive_inject_account_bridge(
                    int(snapshot.get("debugPort") or 0),
                    lock,
                    account_id,
                    task_id if needs_task_probe else "",
                    force=force or stale_plugin,
                    site=site,
                )
                result["status"] = "recovering" if injected else "ok"
                result["action"] = "inject" if injected else "probe-debounced"
                result["reason"] = "plugin-heartbeat-stale" if stale_plugin else ("task-probe" if needs_task_probe else "bridge-ok")
            else:
                result["status"] = "ok"
                result["reason"] = "heartbeat-fresh"
        elif snapshot.get("browserDebugConnected"):
            result["status"] = "stale"
            result["reason"] = "doubao-tab-missing"
        else:
            result["status"] = "stale"
            result["reason"] = "browser-debug-disconnected"
            result["error"] = str(snapshot.get("error") or "")
    except Exception as e:  # noqa: BLE001
        result["status"] = "error"
        result["action"] = "inject"
        result["error"] = str(e)
        logger.info("[DoubaoPool] keepalive inject failed account=%s task=%s: %s", account_id, task_id or "", e)
    _record_account_keepalive(account_id, {
        "lastKeepaliveAt": now,
        "lastKeepaliveStatus": result.get("status") or "",
        "lastKeepaliveAction": result.get("action") or "",
        "lastKeepaliveReason": result.get("reason") or "",
        "lastKeepaliveError": result.get("error") or "",
        "browserDebugConnected": bool(result.get("browserDebugConnected")),
        "browserProcessAlive": bool(result.get("browserProcessAlive")),
        "doubaoTabOpen": bool(result.get("doubaoTabOpen")),
        "doubaoPageCount": int(result.get("doubaoPageCount") or 0),
    })
    return result


def _keepalive_accounts(account_id: str = "", *, task_id: str = "", force: bool = False, site: str = DOUBAO_SITE_DEFAULT) -> list[dict]:
    site = normalize_site(site)
    try:
        lock = _ensure_server()
        with _STATE_LOCK:
            state = _read_state()
            accounts = [
                dict(account)
                for account in state.get("accounts", [])
                if account.get("enabled", True)
                and _account_matches_site(account, site)
                and (not account_id or str(account.get("id") or "") == str(account_id))
            ]
        return [_keepalive_account_bridge(account, lock, task_id=task_id, force=force) for account in accounts]
    except Exception as e:  # noqa: BLE001
        logger.debug("[DoubaoPool] account keepalive skipped: %s", e)
        return []


def ensure_ready(account_id: str = "", *, open_browser: bool = True, task_id: str = "",
                 site: str = DOUBAO_SITE_DEFAULT,
                 require_capacity: bool = False, force_restart: bool = False) -> dict:
    site = normalize_site(site or (_task_site(get_task(task_id)) if task_id else DOUBAO_SITE_DEFAULT))
    install = _install_builtin_plugins(force=False)
    lock = _ensure_server()
    issues: list[str] = []
    opened = False
    open_debounced = False
    account_public: Optional[dict] = None
    snapshot: dict[str, Any] = {}
    selected_account_id = ""
    early_issues: list[str] = []
    account: Optional[dict] = None

    with _STATE_LOCK:
        state = _read_state()
        accounts = [item for item in state.get("accounts") or [] if _account_matches_site(item, site)]
        if not accounts:
            early_issues = ["请先录入豆包账号"]
        try:
            account = None if early_issues else _pick_account(
                    state,
                    account_id,
                    rotate=not bool(account_id),
                    require_capacity=require_capacity,
                    site=site,
                )
        except DoubaoPoolError as e:
            early_issues = [str(e)]
        if account:
            selected_account_id = str(account.get("id") or "")
            account_public = _public_account(state, account)

    if early_issues:
        return {
            "ok": True,
            "ready": False,
            "opened": False,
            "openDebounced": False,
            "issues": early_issues,
            "state": list_pool(site=site),
            "extension": _extension_info(lock),
            "install": install,
        }

    keepalive_results: list[dict] = []
    snapshot = _account_browser_snapshot(account or {"id": selected_account_id, "site": site}, task_id)
    if open_browser and selected_account_id and snapshot.get("browserDebugConnected") and snapshot.get("doubaoTabOpen"):
        keepalive_results = _keepalive_accounts(selected_account_id, task_id=task_id, force=force_restart, site=site)
        snapshot = _account_browser_snapshot(account or {"id": selected_account_id, "site": site}, task_id)

    if not install.get("installed"):
        issues.append("豆包生产能力资源未就绪，请重新打开豆包视频凭证页")
    if not _find_browser():
        issues.append("未找到豆包本地化 Chromium 内核，请检查软件包资源或设置 DOUBAO_BROWSER_PATH")

    should_open = bool(
        open_browser
        and not issues
        and (
            force_restart
            or not snapshot.get("browserDebugConnected")
            or not snapshot.get("doubaoTabOpen")
            or (task_id and not snapshot.get("taskTabOpen"))
        )
    )
    if should_open:
        now = _now_ms()
        debounce_ms = DOUBAO_TASK_PAGE_OPEN_DEBOUNCE_MS if task_id else DOUBAO_READY_OPEN_DEBOUNCE_MS
        open_key = f"{site}:{selected_account_id}:{task_id or '__idle__'}"
        with _BROWSER_NAV_LOCK:
            last = int(_READY_OPEN_REQUESTS.get(open_key) or 0)
            if not force_restart and last and now - last < debounce_ms:
                should_open = False
                open_debounced = True
            else:
                _READY_OPEN_REQUESTS[open_key] = now

    if should_open:
        opened_result = open_account(
            selected_account_id,
            rotate=False,
            task_id=task_id,
            force_restart=force_restart,
            require_capacity=False,
            site=site,
        )
        opened = True
        if opened_result.get("account"):
            account_public = opened_result.get("account")
    elif open_browser and selected_account_id and snapshot.get("browserDebugConnected") and snapshot.get("doubaoTabOpen"):
        try:
            if task_id:
                _keepalive_inject_account_bridge(int(snapshot.get("debugPort") or 0), lock, selected_account_id, task_id, site=site)
            elif not _heartbeat_snapshot(selected_account_id, site=site).get("pluginConnected"):
                _keepalive_inject_account_bridge(int(snapshot.get("debugPort") or 0), lock, selected_account_id, site=site)
        except Exception as e:  # noqa: BLE001
            logger.info("[DoubaoPool] ready probe skipped account=%s task=%s: %s", selected_account_id, task_id or "", e)

    state_now = list_pool(site=site)
    heartbeat = _heartbeat_snapshot(selected_account_id, site=site)
    ready = bool(
        not issues
        and state_now.get("pluginsInstalled")
        and state_now.get("extensionReady")
        and state_now.get("extensionLockValid")
        and state_now.get("chromePath")
        and state_now.get("accountCount")
    )
    return {
        "ok": True,
        "ready": ready,
        "site": site,
        "siteLabel": _site_label(site),
        "opened": opened,
        "openDebounced": open_debounced,
        "issues": issues,
        "account": account_public,
        "browser": snapshot,
        "keepalive": keepalive_results,
        "extension": _extension_info(lock),
        "install": install,
        "state": state_now,
        **heartbeat,
    }


def diagnose_binding(account_id: str = "", site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    lock = _ensure_server()
    plugin_status = _plugin_install_status()
    with _STATE_LOCK:
        state = _read_state()
        site_accounts = [item for item in state.get("accounts", []) if _account_matches_site(item, site)]
        account = _pick_account(state, account_id, rotate=False, require_capacity=False, site=site) if account_id else (site_accounts or [{}])[0]
        if not account:
            raise DoubaoPoolError("请先录入豆包账号")
        debug_port = _debug_port(account)
        public = _public_account(state, account)
    result: dict[str, Any] = {
        "ok": True,
        "site": site,
        "siteLabel": _site_label(site),
        "account": public,
        "debugPort": debug_port,
        "extensionDir": str(EXTENSION_DIR),
        "extraExtensionDirs": [str(path) for path in _browser_extension_dirs()[1:]],
        "duration15ExtensionReady": _extension_manifest_ready(DURATION15_EXTENSION_DIR),
        "duration15ExtensionDir": str(DURATION15_EXTENSION_DIR),
        "watermarkExtensionReady": _extension_manifest_ready(WATERMARK_EXTENSION_DIR),
        "watermarkExtensionDir": str(WATERMARK_EXTENSION_DIR),
        "packagedMultiOpenBrowser": "",
        "packagedBrowserChrome": str(PACKAGED_BROWSER_CHROME) if PACKAGED_BROWSER_CHROME.exists() else "",
        "sourceBrowserBundle": str(PACKAGED_MULTI_OPEN_BROWSER) if PACKAGED_MULTI_OPEN_BROWSER.exists() else "",
        "packagedWatermarkBrowser": "",
        "pluginsInstalled": bool(plugin_status.get("installed")),
        "pluginsEnabled": bool(plugin_status.get("enabled")),
        "pluginsSourceCurrent": bool(plugin_status.get("sourceCurrent")),
        "plugins": plugin_status.get("items") or [],
        "extensionLockValid": bool(lock.get("token") and lock.get("expiresAt", 0) > _now_ms()),
        **_heartbeat_snapshot(str(account.get("id") or ""), site=site),
        "browserDebugConnected": False,
        "doubaoTabOpen": False,
        "extensionRegistered": False,
        "devtoolsBridgeInjected": False,
        "pageProbe": None,
        "tabs": [],
        "issues": [],
    }
    try:
        version = http.get(f"http://127.0.0.1:{debug_port}/json/version", timeout=1.5).json()
        tabs = http.get(f"http://127.0.0.1:{debug_port}/json/list", timeout=1.5).json()
        result["browserDebugConnected"] = True
        result["browserVersion"] = version.get("Browser") or ""
        result["tabs"] = [
            {"type": item.get("type"), "title": item.get("title"), "url": item.get("url")}
            for item in tabs
        ]
        result["doubaoTabOpen"] = any(_url_matches_site(item.get("url") or "", site) for item in tabs)
        result["extensionRegistered"] = any(
            str(item.get("url") or "").startswith("chrome-extension://")
            or "VMO 豆包号池助手" in str(item.get("title") or "")
            for item in tabs
        )
        if result["doubaoTabOpen"] and not result["pluginConnected"]:
            try:
                bridge = _inject_devtools_bridge(debug_port, lock, str(account.get("id") or ""), site=site)
                result["devtoolsBridgeInjected"] = True
                result["pageProbe"] = bridge
                result.update(_heartbeat_snapshot(str(account.get("id") or ""), site=site))
            except Exception as e:  # noqa: BLE001
                result["issues"].append(f"DevTools 桥接注入失败：{e}")
    except Exception as e:  # noqa: BLE001
        result["issues"].append(f"专用浏览器调试端口未连接：{e}")
    if not result["browserDebugConnected"]:
        result["issues"].append("请关闭旧的专用浏览器窗口，再点击账号的“打开登录”。")
    if result["browserDebugConnected"] and not result["doubaoTabOpen"]:
        result["issues"].append("专用浏览器已打开，但未检测到豆包页面。")
    if result["doubaoTabOpen"] and not result["pluginConnected"]:
        result["issues"].append("豆包页面已打开，但本地联动心跳未到达；已尝试自动桥接。")
    if not result["extensionLockValid"]:
        result["issues"].append("本地联动服务已过期，请重新启用豆包视频凭证。")
    if not result["pluginsInstalled"]:
        result["issues"].append("豆包生产能力资源未就绪，请重新打开豆包视频凭证页。")
    result["healthy"] = bool(result["browserDebugConnected"] and result["doubaoTabOpen"] and result["pluginConnected"])
    return result


def open_extension_dir() -> dict:
    info = {"ok": True, **_extension_info(_load_launch_lock())}
    path = EXTENSION_DIR if EXTENSION_DIR.exists() else DATA_DIR / "doubao-extension"
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return info


def _authorize(query: dict[str, list[str]]) -> bool:
    token = (query.get("token") or [""])[0]
    return bool(token and token == _LOCK.get("token") and _LOCK.get("expiresAt", 0) > _now_ms())


def list_pool(skip_keepalive: bool = False, site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    if not skip_keepalive:
        _keepalive_accounts(site=site)
    with _STATE_LOCK:
        try:
            _load_launch_lock()
        except Exception as e:  # noqa: BLE001
            logger.warning("[DoubaoPool] state lock load failed: %s", e)
        state = _read_state()
        if _refresh_accounts_health_state(state):
            _write_state(state)
        tasks_data = _read_tasks()
        active_counts = _active_task_counts(tasks_data, site=site)
        accounts = [account for account in state.get("accounts", []) if _account_matches_site(account, site)]
        browser = _find_browser()
        use_packaged_browser = _use_packaged_multi_open_browser()
        manifest = EXTENSION_DIR / "manifest.json"
        _prune_account_heartbeats()
        heartbeat = _heartbeat_snapshot(site=site)
        plugin_status = _plugin_install_status()
        lock_valid = bool(_LOCK.get("token") and _LOCK.get("expiresAt", 0) > _now_ms())
        server_alive = bool(
            lock_valid
            and (
                (_SERVER and _SERVER_THREAD and _SERVER_THREAD.is_alive())
                or _server_healthy(int(_LOCK.get("port") or 0), str(_LOCK.get("token") or ""))
            )
        )
        return {
            "site": site,
            "siteLabel": _site_label(site),
            "siteHost": _site_host(site),
            "rootDir": state.get("rootDir"),
            "accounts": [_public_account(state, a, active_counts) for a in accounts],
            "accountHeartbeats": {
                str(account.get("id") or ""): _heartbeat_snapshot(str(account.get("id") or ""), site=site)
                for account in accounts
            },
            "accountCount": len(accounts),
            "remainingTotal": sum(max(0, _account_local_quota(a) - _account_used(a)) for a in accounts),
            "availableTotal": sum(_account_remaining_slots(a, active_counts) if _account_has_slots(a, active_counts) else 0 for a in accounts),
            "activeTotal": sum(active_counts.values()),
            "settings": dict(state.get("settings") or {}),
            "usePackagedBrowser": use_packaged_browser,
            "browserMode": "packaged" if use_packaged_browser and _is_packaged_multi_open_browser_path(browser) else "system",
            "extensionReady": _extension_manifest_ready(EXTENSION_DIR),
            "extensionDir": str(EXTENSION_DIR),
            "extraExtensionDirs": [str(path) for path in _browser_extension_dirs()[1:]],
            "duration15ExtensionReady": _extension_manifest_ready(DURATION15_EXTENSION_DIR),
            "duration15ExtensionDir": str(DURATION15_EXTENSION_DIR),
            "watermarkExtensionReady": _extension_manifest_ready(WATERMARK_EXTENSION_DIR),
            "watermarkExtensionDir": str(WATERMARK_EXTENSION_DIR),
            "extensionManifest": str(manifest),
            "extensionName": "VMO 豆包号池助手",
            "extensionPort": _LOCK.get("port") or 0,
            "extensionLockValid": server_alive,
            "extensionServerAlive": server_alive,
            "pluginRequired": True,
            "pluginsInstalled": bool(plugin_status.get("installed")),
            "pluginsEnabled": bool(plugin_status.get("enabled")),
            "pluginsSourceCurrent": bool(plugin_status.get("sourceCurrent")),
            "plugins": plugin_status.get("items") or [],
            "pluginInstall": plugin_status.get("installRecord") or {},
            "pluginInstallFile": plugin_status.get("installFile") or "",
            "durationOptions": plugin_status.get("durationOptions") or list(DOUBAO_VIDEO_DURATION_OPTIONS),
            "durationMode": plugin_status.get("durationMode") or "adaptive",
            "durationSeconds": plugin_status.get("durationSeconds") or DOUBAO_FIXED_VIDEO_DURATION_SECONDS,
            "chromePath": browser,
            "packagedMultiOpenBrowser": "",
            "packagedBrowserChrome": str(PACKAGED_BROWSER_CHROME) if PACKAGED_BROWSER_CHROME.exists() else "",
            "sourceBrowserBundle": str(PACKAGED_MULTI_OPEN_BROWSER) if PACKAGED_MULTI_OPEN_BROWSER.exists() else "",
            "packagedWatermarkBrowser": "",
            **heartbeat,
        }


def add_account(name: str = "", quota: int = DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA, site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    with _STATE_LOCK:
        state = _read_state()
        site_accounts = [item for item in state.get("accounts", []) if _account_matches_site(item, site)]
        account_id = uuid.uuid4().hex[:12]
        account = {
            "id": account_id,
            "site": site,
            "name": (name or f"豆包账号 {len(state.get('accounts', [])) + 1}").strip(),
            "quota": max(1, int(quota or DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA)),
            "used": 0,
            "enabled": True,
            "createdAt": _now_ms(),
            "lastUsedAt": 0,
        }
        if not str(name or "").strip() and site == DOUBAO_SITE_INTL:
            account["name"] = f"{_site_label(site)} account {len(site_accounts) + 1}"
        state["accounts"].append(account)
        _account_dir(state, account).mkdir(parents=True, exist_ok=True)
        _download_dir(account).mkdir(parents=True, exist_ok=True)
        _write_state(state)
    return list_pool(site=site)


def _safe_account_name(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    return text[:64] if text else fallback


def _copy_path_best_effort(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    try:
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(*DOUBAO_PROFILE_SKIP_NAMES, "*.tmp", "*.lock", "*.log"),
                dirs_exist_ok=True,
            )
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("[DoubaoPool] copy skipped src=%s dst=%s error=%s", src, dst, e)
        return False


def _profile_signal_score(path: Path) -> int:
    if not path.exists() or not path.is_dir():
        return 0
    score = 0
    if (path / "Local State").exists():
        score += 35
    if (path / "Default").is_dir():
        score += 35
    if (path / "Default" / "Network" / "Cookies").exists():
        score += 50
    if (path / "Network" / "Cookies").exists():
        score += 50
    if (path / "Local Storage" / "leveldb").exists():
        score += 20
    if (path / "Preferences").exists():
        score += 15
    return score


def _profile_fingerprint(path: Path) -> str:
    try:
        resolved = str(path.resolve()).lower()
    except Exception:
        resolved = str(path).lower()
    markers = []
    for rel in ("Local State", "Default/Preferences", "Default/Network/Cookies", "Network/Cookies", "Preferences"):
        p = path / Path(rel)
        try:
            if p.exists():
                stat = p.stat()
                markers.append(f"{rel}:{stat.st_size}:{int(stat.st_mtime)}")
        except Exception:
            pass
    raw = "|".join([resolved, *markers])
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


def _is_chromium_profile_leaf(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    name = path.name.lower()
    if name not in DOUBAO_CHROMIUM_PROFILE_NAMES and not re.fullmatch(r"profile\s+\d+", name):
        return False
    if not (path.parent / "Local State").exists():
        return False
    return bool(
        (path / "Preferences").exists()
        or (path / "Network" / "Cookies").exists()
        or (path / "Local Storage" / "leveldb").exists()
    )


def _stable_account_import_fingerprint(raw: dict, json_path: Path, kind: str, profile_hint: str) -> str:
    existing = str(raw.get("importFingerprint") or raw.get("sourceFingerprint") or "").strip()
    if existing:
        return existing[:64]
    if kind == "export":
        stable_id = str(raw.get("id") or raw.get("name") or raw.get("label") or "").strip()
        if stable_id:
            return hashlib.sha256(f"vmo-export:{stable_id}".encode("utf-8", "ignore")).hexdigest()[:24]
    if profile_hint:
        return hashlib.sha256(profile_hint.encode("utf-8", "ignore")).hexdigest()[:24]
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(f"{json_path.name}:{payload}".encode("utf-8", "ignore")).hexdigest()[:24]


def _sanitize_import_account(raw: dict, index: int, source_label: str = "") -> dict:
    now = _now_ms()
    quota = raw.get("quota", raw.get("videoQuota", raw.get("capacity", DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA)))
    used = raw.get("used", raw.get("videoUsed", 0))
    try:
        quota_value = max(1, int(quota or DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA))
    except Exception:
        quota_value = DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA
    try:
        used_value = min(max(0, int(used or 0)), quota_value)
    except Exception:
        used_value = 0
    return {
        "id": uuid.uuid4().hex[:12],
        "name": _safe_account_name(raw.get("name") or raw.get("label") or raw.get("title"), f"导入账号 {index}"),
        "quota": quota_value,
        "used": used_value,
        "enabled": bool(raw.get("enabled", True)),
        "createdAt": now,
        "updatedAt": now,
        "lastUsedAt": 0,
        "importedAt": now,
        "importSource": source_label,
    }


def _clean_runtime_account_fields(account: dict) -> dict:
    cleaned = dict(account)
    for key in (
        "browserPid", "browserLaunchedAt", "debugPort", "profileDir", "downloadDir",
        "lastKeepaliveAt", "lastKeepaliveStatus", "lastKeepaliveAction", "lastKeepaliveReason",
        "lastKeepaliveError", "browserDebugConnected", "browserProcessAlive", "doubaoTabOpen",
        "doubaoPageCount", "pluginConnected", "pluginLastSeenAt", "extensionId",
    ):
        cleaned.pop(key, None)
    return cleaned


def _copy_chrome_profile_to_account(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_dir():
        return False
    dst.mkdir(parents=True, exist_ok=True)
    copied = False
    if _is_chromium_profile_leaf(src):
        copied = _copy_path_best_effort(src.parent / "Local State", dst / "Local State") or copied
        copied = _copy_path_best_effort(src.parent / "First Run", dst / "First Run") or copied
        copied = _copy_path_best_effort(src.parent / "Last Version", dst / "Last Version") or copied
        default_dst = dst / "Default"
        default_dst.mkdir(parents=True, exist_ok=True)
        for name in DOUBAO_PROFILE_COPY_NAMES:
            copied = _copy_path_best_effort(src / name, default_dst / name) or copied
        return copied
    chrome_root = (src / "Default").is_dir() or (src / "Local State").exists()
    if chrome_root:
        for name in ("Local State", "First Run", "Last Version", "Variations"):
            copied = _copy_path_best_effort(src / name, dst / name) or copied
        default_src = src / "Default"
        default_dst = dst / "Default"
        if default_src.is_dir():
            default_dst.mkdir(parents=True, exist_ok=True)
            for name in DOUBAO_PROFILE_COPY_NAMES:
                copied = _copy_path_best_effort(default_src / name, default_dst / name) or copied
        return copied
    default_dst = dst / "Default"
    default_dst.mkdir(parents=True, exist_ok=True)
    copied = _copy_path_best_effort(src / "Local State", dst / "Local State") or copied
    for name in DOUBAO_PROFILE_COPY_NAMES:
        copied = _copy_path_best_effort(src / name, default_dst / name) or copied
    return copied


def _candidate_import_roots(source: str) -> list[Path]:
    raw = str(source or "").strip().strip('"')
    roots: list[Path] = []
    if raw:
        p = Path(raw).expanduser()
        roots.append(p)
        if p.is_file():
            roots.append(p.parent)
        hint = " ".join([str(p.name), *(str(parent.name) for parent in list(p.parents)[:3])]).lower()
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        inferred: list[Path] = []
        if "xingyao" in hint:
            inferred.extend([Path(appdata) / "xingyao-ai", Path(localappdata) / "xingyao-ai"])
        if "comic-generator" in hint or "huajing" in hint or "画镜" in hint:
            inferred.append(Path(appdata) / "comic-generator-electron")
        parts_lower = [str(part).strip().lower() for part in p.parts]
        explicit_doubao_user_data = len(parts_lower) >= 2 and parts_lower[-2:] == ["doubao", "user data"]
        explicit_doubao_app = p.name.strip().lower() in {"doubao", "doubao.exe"}
        if explicit_doubao_user_data or explicit_doubao_app:
            inferred.append(Path(localappdata) / "Doubao" / "User Data")
        roots.extend(inferred)
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key not in seen and resolved.exists():
            seen.add(key)
            out.append(resolved)
    return out


def _read_small_bytes(path: Path, max_size: int = 32 * 1024 * 1024) -> bytes:
    try:
        if not path.exists() or not path.is_file() or path.stat().st_size > max_size:
            return b""
        return path.read_bytes()
    except Exception:
        return b""


def _profile_content_fingerprint(path: Path) -> str:
    parts: list[str] = []
    for rel in (
        "Local State",
        "Default/Preferences",
        "Default/Network/Cookies",
        "Network/Cookies",
        "Preferences",
        "Default/Local Storage/leveldb",
        "Local Storage/leveldb",
    ):
        p = path / Path(rel)
        try:
            if p.is_file():
                data = _read_small_bytes(p, 32 * 1024 * 1024)
                if data:
                    parts.append(f"{rel}:file:{len(data)}:{hashlib.sha256(data).hexdigest()[:24]}")
            elif p.is_dir():
                names = sorted(child.name for child in p.iterdir() if child.is_file())[:32]
                parts.append(f"{rel}:dir:{'|'.join(names)}")
        except Exception:
            pass
    raw = "|".join(parts) or str(path).lower()
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


def _sqlite_has_doubao_cookie(path: Path, site: str = DOUBAO_SITE_DEFAULT) -> bool:
    if not path.exists() or not path.is_file():
        return False
    markers = ("dola",) if normalize_site(site) == DOUBAO_SITE_INTL else ("doubao",)
    try:
        import sqlite3

        uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=0.25)
        try:
            for marker in markers:
                row = conn.execute("select 1 from cookies where host_key like ? limit 1", (f"%{marker}%",)).fetchone()
                if row:
                    return True
        finally:
            conn.close()
    except Exception:
        pass
    data = _read_small_bytes(path).lower()
    return any(marker.encode("utf-8") in data for marker in markers)


def _profile_has_doubao_signal(path: Path, site: str = DOUBAO_SITE_DEFAULT) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    markers = (b"dola",) if normalize_site(site) == DOUBAO_SITE_INTL else (b"doubao",)
    cookie_paths = [
        path / "Default" / "Network" / "Cookies",
        path / "Network" / "Cookies",
    ]
    if any(_sqlite_has_doubao_cookie(item, site) for item in cookie_paths):
        return True
    probe_files = [
        path / "Default" / "Preferences",
        path / "Preferences",
        path / "Default" / "Network" / "Network Persistent State",
        path / "Network" / "Network Persistent State",
    ]
    if any(any(marker in _read_small_bytes(item).lower() for marker in markers) for item in probe_files):
        return True
    for leveldb in (path / "Default" / "Local Storage" / "leveldb", path / "Local Storage" / "leveldb"):
        if not leveldb.exists() or not leveldb.is_dir():
            continue
        try:
            for file in list(leveldb.iterdir())[:80]:
                data = _read_small_bytes(file, 8 * 1024 * 1024).lower()
                if file.is_file() and any(marker in data for marker in markers):
                    return True
        except Exception:
            pass
    return False


def _iter_limited_dirs(root: Path, max_depth: int = 4, max_dirs: int = 900):
    queue: list[tuple[Path, int]] = [(root, 0)]
    seen = 0
    while queue and seen < max_dirs:
        current, depth = queue.pop(0)
        seen += 1
        yield current
        if depth >= max_depth:
            continue
        try:
            children = [p for p in current.iterdir() if p.is_dir()]
        except Exception:
            continue
        for child in children:
            if child.name in DOUBAO_PROFILE_SKIP_NAMES or child.name.lower() in {"node_modules", "resources", "locales"}:
                continue
            queue.append((child, depth + 1))


def _extract_import_zip(source: Path) -> Path:
    IMPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    target = IMPORT_TMP_DIR / f"import-{uuid.uuid4().hex[:10]}"
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    with zipfile.ZipFile(source, "r") as zf:
        for member in zf.infolist():
            name = str(member.filename or "").replace("\\", "/")
            if not name or name.startswith("/") or "/../" in f"/{name}" or name.startswith("../"):
                continue
            dest = (target / name).resolve()
            if not str(dest).lower().startswith(str(target_root).lower()):
                continue
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
    return target


def _find_pool_json_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    if root.is_file() and root.suffix.lower() == ".json":
        return [root]
    names = {"pool.json", "doubao-pool.json", "doubao_accounts.json", "doubao-accounts.json", "accounts.json", "account-pool.json", "vmo-doubao-accounts.json"}
    for folder in _iter_limited_dirs(root, max_depth=4, max_dirs=700):
        for name in names:
            path = folder / name
            if path.exists() and path.is_file():
                candidates.append(path)
    return candidates[:80]


def _extract_accounts_from_json(path: Path) -> tuple[list[dict], str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], "", {}
    if isinstance(data, dict):
        if data.get("type") == "vmo-doubao-accounts" and isinstance(data.get("accounts"), list):
            return [a for a in data.get("accounts") or [] if isinstance(a, dict)], "export", data
        if isinstance(data.get("accounts"), list):
            return [a for a in data.get("accounts") or [] if isinstance(a, dict)], "pool", data
        for key in ("doubaoAccounts", "accountPool", "items"):
            if isinstance(data.get(key), list):
                return [a for a in data.get(key) or [] if isinstance(a, dict)], key, data
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)], "list", {}
    return [], "", {}


def _find_profile_candidates(root: Path, site: str = DOUBAO_SITE_DEFAULT) -> list[Path]:
    site = normalize_site(site)
    candidates: dict[str, tuple[Path, int]] = {}
    scan_roots = [root]
    if root.name.lower() in {"user data", "userdata"}:
        scan_roots.append(root.parent)
    for scan_root in scan_roots:
        if not scan_root.exists() or not scan_root.is_dir():
            continue
        for folder in _iter_limited_dirs(scan_root, max_depth=4, max_dirs=900):
            score = _profile_signal_score(folder)
            if score < 45:
                continue
            if not _profile_has_doubao_signal(folder, site):
                continue
            key = _profile_fingerprint(folder)
            prev = candidates.get(key)
            if not prev or score > prev[1]:
                candidates[key] = (folder, score)
    accepted: list[Path] = []
    for folder, _score in sorted(candidates.values(), key=lambda x: (-x[1], str(x[0]).lower())):
        try:
            resolved = folder.resolve()
        except Exception:
            resolved = folder
        nested = False
        for existing in accepted:
            try:
                resolved.relative_to(existing)
                nested = True
                break
            except Exception:
                pass
            try:
                existing.relative_to(resolved)
                nested = True
                break
            except Exception:
                pass
        if not nested:
            accepted.append(resolved)
        if len(accepted) >= 60:
            break
    return accepted


def _existing_import_fingerprints(state: dict) -> set[str]:
    values = set()
    for account in state.get("accounts", []):
        for key in ("importFingerprint", "sourceFingerprint", "profileContentFingerprint"):
            value = str(account.get(key) or "")
            if value:
                values.add(value)
        account_id = str(account.get("id") or "").strip()
        if account_id:
            values.add(hashlib.sha256(f"vmo-export:{account_id}".encode("utf-8", "ignore")).hexdigest()[:24])
    return values


def import_accounts(source: str, *, quota: int = DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA,
                    include_profiles: bool = True, site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    source_text = str(source or "").strip().strip('"')
    if not source_text:
        raise DoubaoPoolError("请填写要导入的目录或文件路径")
    root = Path(source_text).expanduser()
    temp_root: Optional[Path] = None
    if root.is_file() and root.suffix.lower() == ".zip":
        temp_root = _extract_import_zip(root)
        root = temp_root
    roots = [root] if temp_root else _candidate_import_roots(str(root))
    if root.exists():
        try:
            root_key = str(root.resolve()).lower()
        except Exception:
            root_key = str(root).lower()
        root_keys = set()
        for item in roots:
            try:
                root_keys.add(str(item.resolve()).lower())
            except Exception:
                root_keys.add(str(item).lower())
        if root_key not in root_keys:
            roots.insert(0, root)
    imported: list[dict] = []
    skipped: list[str] = []
    copied_profiles = 0
    now = _now_ms()
    try:
        with _STATE_LOCK:
            state = _read_state()
            accounts = state.setdefault("accounts", [])
            fingerprints = _existing_import_fingerprints(state)
            imported_profile_fingerprints: set[str] = set()
            consumed_profile_roots: list[Path] = []

            pool_entries: list[tuple[dict, Path, str]] = []
            for scan_root in roots:
                for json_path in _find_pool_json_candidates(scan_root):
                    found, kind, meta = _extract_accounts_from_json(json_path)
                    if not found:
                        continue
                    export_site = normalize_site((meta or {}).get("site") or "")
                    if kind == "export" and (meta or {}).get("site") and export_site != site:
                        skipped.append(f"配置包站点不匹配: {_site_label(export_site)}")
                        continue
                    for item in found:
                        pool_entries.append((item, json_path, kind))
                    logger.info("[DoubaoPool] import detected %s account record(s) from %s kind=%s", len(found), json_path, kind)

            for index, (raw, json_path, kind) in enumerate(pool_entries, start=1):
                profile_hint = str(raw.get("profileDir") or raw.get("userDataDir") or raw.get("profilePath") or "").strip()
                exported_profile = json_path.parent / "profiles" / str(raw.get("id") or "")
                if not profile_hint and exported_profile.exists():
                    profile_hint = str(exported_profile)
                src_profile: Optional[Path] = None
                profile_content_fingerprint = ""
                if profile_hint:
                    src_profile = Path(profile_hint)
                    if not src_profile.is_absolute():
                        src_profile = json_path.parent / src_profile
                    if src_profile.exists():
                        try:
                            consumed_profile_roots.append(src_profile.resolve())
                        except Exception:
                            consumed_profile_roots.append(src_profile)
                        profile_content_fingerprint = _profile_content_fingerprint(src_profile)
                fingerprint = _stable_account_import_fingerprint(raw, json_path, kind, profile_hint)
                if fingerprint in fingerprints or (profile_content_fingerprint and profile_content_fingerprint in fingerprints):
                    skipped.append("已存在的账号配置")
                    continue
                account = _sanitize_import_account(raw, len(accounts) + 1, "import")
                account["site"] = site
                account["quota"] = max(1, int(raw.get("quota") or quota or DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA))
                account["importFingerprint"] = fingerprint
                if profile_content_fingerprint:
                    account["profileContentFingerprint"] = profile_content_fingerprint
                account["importedFrom"] = str(json_path)
                account["updatedAt"] = now
                accounts.append(account)
                _account_dir(state, account).mkdir(parents=True, exist_ok=True)
                _download_dir(account).mkdir(parents=True, exist_ok=True)
                if include_profiles and src_profile and src_profile.exists():
                    profile_fingerprint = _profile_fingerprint(src_profile)
                    if _copy_chrome_profile_to_account(src_profile, _account_dir(state, account)):
                        copied_profiles += 1
                        imported_profile_fingerprints.add(profile_fingerprint)
                        fingerprints.add(profile_fingerprint)
                        if profile_content_fingerprint:
                            fingerprints.add(profile_content_fingerprint)
                imported.append({"id": account["id"], "name": account["name"]})
                fingerprints.add(fingerprint)

            if include_profiles:
                profile_candidates: list[Path] = []
                for scan_root in roots:
                    profile_candidates.extend(_find_profile_candidates(scan_root, site))
                seen_profile_keys: set[str] = set()
                for src_profile in profile_candidates:
                    try:
                        src_resolved = src_profile.resolve()
                    except Exception:
                        src_resolved = src_profile
                    skip_consumed = False
                    for consumed in consumed_profile_roots:
                        try:
                            src_resolved.relative_to(consumed)
                            skip_consumed = True
                            break
                        except Exception:
                            pass
                        try:
                            consumed.relative_to(src_resolved)
                            skip_consumed = True
                            break
                        except Exception:
                            pass
                    if skip_consumed:
                        continue
                    fingerprint = _profile_fingerprint(src_profile)
                    content_fingerprint = _profile_content_fingerprint(src_profile)
                    if (
                        fingerprint in fingerprints
                        or content_fingerprint in fingerprints
                        or fingerprint in imported_profile_fingerprints
                        or fingerprint in seen_profile_keys
                        or content_fingerprint in seen_profile_keys
                    ):
                        continue
                    seen_profile_keys.add(fingerprint)
                    seen_profile_keys.add(content_fingerprint)
                    account = _sanitize_import_account({}, len(accounts) + 1, "profile")
                    account["site"] = site
                    account["quota"] = max(1, int(quota or DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA))
                    account["importFingerprint"] = fingerprint
                    account["profileContentFingerprint"] = content_fingerprint
                    account["importedFrom"] = str(src_profile)
                    accounts.append(account)
                    _download_dir(account).mkdir(parents=True, exist_ok=True)
                    if _copy_chrome_profile_to_account(src_profile, _account_dir(state, account)):
                        copied_profiles += 1
                    imported.append({"id": account["id"], "name": account["name"]})
                    imported_profile_fingerprints.add(fingerprint)
                    fingerprints.add(fingerprint)
                    fingerprints.add(content_fingerprint)

            if not imported:
                skipped.append("未识别到可导入的账号环境")
            _write_state(state)
    finally:
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)
    result = {
        "ok": True,
        "imported": len(imported),
        "copiedProfiles": copied_profiles,
        "skipped": skipped[:8],
        "accounts": imported,
        "state": list_pool(skip_keepalive=True, site=site),
    }
    logger.info("[DoubaoPool] import finished imported=%s copied_profiles=%s source=%s", result["imported"], copied_profiles, source_text)
    return result


def export_accounts(site: str = DOUBAO_SITE_DEFAULT) -> Path:
    site = normalize_site(site)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with _STATE_LOCK:
        state = _read_state()
        site_accounts = [account for account in state.get("accounts", []) if _account_matches_site(account, site)]
        accounts = [_clean_runtime_account_fields(account) for account in site_accounts]
        payload = {
            "type": "vmo-doubao-accounts",
            "version": DOUBAO_ACCOUNT_EXPORT_VERSION,
            "site": site,
            "exportedAt": _now_ms(),
            "settings": {
                "automationMode": _normalize_automation_mode((state.get("settings") or {}).get("automationMode")),
                "usePackagedBrowser": bool((state.get("settings") or {}).get("usePackagedBrowser", True)),
            },
            "accounts": accounts,
        }
        filename = f"{_site_label(site).lower()}-accounts-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        zip_path = EXPORT_DIR / filename
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("vmo-doubao-accounts.json", json.dumps(payload, ensure_ascii=False, indent=2))
            for account in site_accounts:
                account_id = str(account.get("id") or "")
                if not account_id:
                    continue
                profile = _account_dir(state, account)
                if not profile.exists():
                    continue
                for root_dir, dirs, files in os.walk(profile):
                    current = Path(root_dir)
                    dirs[:] = [d for d in dirs if d not in DOUBAO_PROFILE_SKIP_NAMES]
                    rel_dir = current.relative_to(profile)
                    if len(rel_dir.parts) > 5:
                        dirs[:] = []
                    for file_name in files:
                        if file_name in DOUBAO_PROFILE_SKIP_NAMES or file_name.endswith((".tmp", ".lock")):
                            continue
                        src = current / file_name
                        try:
                            if src.stat().st_size > 64 * 1024 * 1024:
                                continue
                            arc = Path("profiles") / account_id / src.relative_to(profile)
                            zf.write(src, arc.as_posix())
                        except Exception:
                            continue
    return zip_path


def update_account(account_id: str, patch: dict, site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    with _STATE_LOCK:
        state = _read_state()
        for account in state.get("accounts", []):
            if account.get("id") == account_id and _account_matches_site(account, site):
                if "name" in patch:
                    account["name"] = str(patch.get("name") or "").strip() or account.get("name")
                if "quota" in patch:
                    account["quota"] = max(1, int(patch.get("quota") or 1))
                    if _account_used(account) < _account_capacity(account):
                        _clear_account_exhaustion(account, clear_remote=False)
                if "enabled" in patch:
                    account["enabled"] = bool(patch.get("enabled"))
                    if account["enabled"]:
                        _clear_account_exhaustion(account, clear_remote=False)
                account["updatedAt"] = _now_ms()
                _write_state(state)
                return list_pool(site=site)
    raise DoubaoPoolError("未找到豆包账号")


def reset_account_usage(account_id: str = "", *, used: int = 0, clear_remote: bool = True,
                        site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    with _STATE_LOCK:
        state = _read_state()
        matched = False
        now = _now_ms()
        for account in state.get("accounts", []):
            if account_id and str(account.get("id") or "") != str(account_id or ""):
                continue
            if not _account_matches_site(account, site):
                continue
            quota = _account_capacity(account)
            account["used"] = min(max(0, int(used or 0)), quota) if quota else max(0, int(used or 0))
            account["enabled"] = True
            _clear_account_exhaustion(account, clear_remote=clear_remote)
            account["updatedAt"] = now
            matched = True
        if account_id and not matched:
            raise DoubaoPoolError("未找到豆包账号")
        _write_state(state)
    return list_pool(site=site)


def remove_account(account_id: str, site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    with _STATE_LOCK:
        state = _read_state()
        state["accounts"] = [
            a for a in state.get("accounts", [])
            if not (a.get("id") == account_id and _account_matches_site(a, site))
        ]
        _write_state(state)
    return list_pool(site=site)


def set_root_dir(path: Optional[str], site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    with _STATE_LOCK:
        state = _read_state()
        state["rootDir"] = str(Path(path).expanduser()) if path else str(POOL_DIR / "profiles")
        Path(state["rootDir"]).mkdir(parents=True, exist_ok=True)
        _write_state(state)
    return list_pool(site=site)


def _pick_account(state: dict, account_id: str = "", rotate: bool = True,
                  require_capacity: bool = True, site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site)
    health_changed = _refresh_accounts_health_state(state)
    if health_changed:
        _write_state(state)
    accounts = [a for a in state.get("accounts", []) if a.get("enabled", True) and _account_matches_site(a, site)]
    active_counts = _active_task_counts(site=site)
    if account_id:
        account = next((a for a in accounts if a.get("id") == account_id), None)
        if not account:
            raise DoubaoPoolError("指定豆包账号不可用")
        if require_capacity and not _account_has_slots(account, active_counts):
            raise DoubaoPoolError("指定豆包账号本地额度已用完或正在并发生产中")
        return account
    available = [a for a in accounts if not require_capacity or _account_has_slots(a, active_counts)]
    if not available:
        raise DoubaoPoolError("没有可用豆包账号，请在本地号池新增/启用账号并设置次数")
    return sorted(
        available,
        key=lambda a: (int(a.get("lastUsedAt") or 0), -_account_remaining_slots(a, active_counts)),
    )[0] if rotate else available[0]


def open_account(account_id: str = "", rotate: bool = True, task_id: str = "", force_restart: bool = False,
                 require_capacity: bool = False, site: str = DOUBAO_SITE_DEFAULT) -> dict:
    site = normalize_site(site or (_task_site(get_task(task_id)) if task_id else DOUBAO_SITE_DEFAULT))
    install = _install_builtin_plugins(force=False)
    if not install.get("installed"):
        raise DoubaoPoolError("豆包生产能力资源未就绪，请重新打开豆包视频凭证页")
    lock = _ensure_server()
    with _STATE_LOCK:
        state = _read_state()
        account = _pick_account(state, account_id, rotate, require_capacity=require_capacity, site=site)
        account["site"] = site
        profile_dir = _account_dir(state, account)
        download_dir = _download_dir(account)
        profile_dir.mkdir(parents=True, exist_ok=True)
        download_dir.mkdir(parents=True, exist_ok=True)
        _write_chrome_download_prefs(profile_dir, download_dir)
        browser = _find_browser()
        if not browser:
            raise DoubaoPoolError("未找到豆包本地化 Chromium 内核，请检查软件包资源或设置 DOUBAO_BROWSER_PATH")
        account["lastUsedAt"] = _now_ms()
        account["downloadDir"] = str(download_dir)
        _write_state(state)

    selected_account_id = str(account.get("id") or "")
    if not force_restart and _runtime_extension_version_stale(selected_account_id, site=site):
        heartbeat = _account_heartbeat_record(selected_account_id, site) or _PLUGIN_HEARTBEAT
        logger.info(
            "[DoubaoPool] restarting account browser because extension runtime version is stale runtime=%s expected=%s",
            str((heartbeat or {}).get("version") or ""),
            _vmo_extension_version(),
        )
        force_restart = True

    if force_restart:
        _stop_profile_browser(profile_dir)

    url = _site_page_url(site, str(account.get("id") or ""), task_id)
    debug_port = _debug_port(account)
    if not force_restart and _navigate_existing_doubao_tab(debug_port, url, str(account.get("id") or ""), site):
        logger.info("[DoubaoPool] reused browser account=%s debug_port=%s task=%s", account.get("id"), debug_port, task_id or "")
        if task_id:
            _probe_account_page_later(debug_port, task_id, str(account.get("id") or ""), site=site)
        return {"ok": True, "state": list_pool(site=site), "account": _public_account(state, account), "site": site}

    account_launch_key = f"{site}:{account.get('id') or ''}"
    if not force_restart:
        now = _now_ms()
        with _BROWSER_NAV_LOCK:
            last_launch = int(_ACCOUNT_BROWSER_LAUNCH_REQUESTS.get(account_launch_key) or 0)
            if last_launch and now - last_launch < 10_000:
                if task_id:
                    _open_pending_task_page_later(debug_port, url, str(account.get("id") or ""), task_id, site=site)
                logger.info("[DoubaoPool] browser launch already pending account=%s task=%s", account_launch_key, task_id or "")
                return {"ok": True, "state": list_pool(site=site), "account": _public_account(state, account), "site": site}
            _ACCOUNT_BROWSER_LAUNCH_REQUESTS[account_launch_key] = now

    extension_arg = _browser_extension_arg()
    args = [
        browser,
        f"--user-data-dir={profile_dir}",
        f"--load-extension={extension_arg}",
        f"--disable-extensions-except={extension_arg}",
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=DisableLoadExtensionCommandLineSwitch,ChromeForTestingInfobar,ShowChromeForTestingInfobar",
        f"--download-default-directory={download_dir}",
        url,
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with _STATE_LOCK:
        state_after_launch = _read_state()
        account_after_launch = next(
            (
                a for a in state_after_launch.get("accounts", [])
                if str(a.get("id") or "") == selected_account_id and _account_matches_site(a, site)
            ),
            None,
        )
        if account_after_launch is not None:
            account_after_launch["browserPid"] = int(proc.pid or 0)
            account_after_launch["browserLaunchedAt"] = _now_ms()
            account_after_launch["debugPort"] = debug_port
            account_after_launch["profileDir"] = str(profile_dir)
            account_after_launch["downloadDir"] = str(download_dir)
            _write_state(state_after_launch)
        else:
            state_after_launch = state
            account_after_launch = account
    logger.info("[DoubaoPool] launched browser account=%s debug_port=%s server_port=%s force_restart=%s task=%s browser=%s extensions=%s", account.get("id"), debug_port, lock.get("port"), force_restart, task_id or "", browser, extension_arg)
    if not force_restart:
        def clear_launch_marker() -> None:
            time.sleep(10)
            with _BROWSER_NAV_LOCK:
                _ACCOUNT_BROWSER_LAUNCH_REQUESTS.pop(account_launch_key, None)

        threading.Thread(target=clear_launch_marker, daemon=True, name="doubao-launch-marker").start()
    if task_id:
        _probe_account_page_later(debug_port, task_id, str(account.get("id") or ""), site=site)
    return {"ok": True, "state": list_pool(site=site), "account": _public_account(state_after_launch, account_after_launch), "site": site}


def _save_ref_image(img_b64: str, task_id: str, index: int = 0) -> str:
    if not img_b64:
        return ""
    raw = base64.b64decode(img_b64)
    folder = POOL_DIR / "task-images" / task_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"ref_{index + 1:02d}.png"
    path.write_bytes(raw)
    return str(path)


def _save_ref_images(ref_images_b64: Optional[list[str]], task_id: str) -> list[str]:
    paths = []
    for index, img_b64 in enumerate((ref_images_b64 or [])[:9]):
        if img_b64:
            paths.append(_save_ref_image(img_b64, task_id, index))
    return paths


def _force_prompt_duration(prompt: str, seconds: int = DOUBAO_FIXED_VIDEO_DURATION_SECONDS) -> str:
    text = str(prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    duration_field = re.compile(
        r"((?:总时长|视频时长|生成时长|时长|duration)\s*[:：]?\s*)\d+(?:\.\d+)?\s*(?:秒|s|sec|seconds?)?",
        re.IGNORECASE,
    )
    has_duration_field = re.compile(
        r"(?:总时长|视频时长|生成时长|时长|duration)\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:秒|s|sec|seconds?)?",
        re.IGNORECASE,
    )
    skip_line = re.compile(
        r"时间轴|^\s*(?:\d+(?:\.\d+)?\s*[-~至到]\s*\d+(?:\.\d+)?\s*秒|[【\[]?(?:动作过程|镜头动态|运镜/景别)[】\]]?)"
    )
    lines = text.split("\n") if text else []
    rewritten = False
    out = []
    for line in lines:
        if has_duration_field.search(line) and not skip_line.search(line):
            line = duration_field.sub(lambda m: f"{m.group(1)}{seconds}秒", line)
            rewritten = True
        out.append(line)
    if not rewritten:
        out.append(f"时长：{seconds}秒")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _duration_number(value, default: float = 0) -> float:
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            value = match.group(0)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def select_doubao_duration(duration) -> int:
    """Map storyboard duration to Doubao's available 5/10/15 second controls."""
    value = _duration_number(duration, DOUBAO_FIXED_VIDEO_DURATION_SECONDS)
    if value > 10:
        return 15
    if value > 5:
        return 10
    return 5


def create_video_task(
    prompt: str,
    *,
    duration: int,
    aspect_ratio: str,
    ref_images_b64: Optional[list[str]] = None,
    account_id: str = "",
    site: str = DOUBAO_SITE_DEFAULT,
    shot_no: str = "",
    filename_prefix: str = "",
    batch_id: str = "",
    project_id: str = "",
    sequence_no: Optional[int] = None,
) -> dict:
    site = normalize_site(site)
    with _STATE_LOCK:
        state = _read_state()
        automation_mode = _state_automation_mode(state)
        account = _pick_account(state, account_id, True, require_capacity=True, site=site)
        account["site"] = site
        task_id = "dbvd_" + uuid.uuid4().hex[:16]
        image_paths = _save_ref_images(ref_images_b64, task_id)
        source_duration = _duration_number(duration, DOUBAO_FIXED_VIDEO_DURATION_SECONDS)
        target_duration = select_doubao_duration(source_duration)
        fixed_prompt = _force_prompt_duration(prompt, target_duration)
        now = _now_ms()
        trace_label = str(shot_no or filename_prefix or "").strip()
        account_id_value = str(account["id"])
        task = {
            "id": task_id,
            "site": site,
            "siteLabel": _site_label(site),
            "accountId": account_id_value,
            "currentAccountId": account_id_value,
            "initialAccountId": account_id_value,
            "accountIdOriginal": account_id_value,
            "lockedAccountId": "",
            "accountSwitchLocked": False,
            "accountSwitchPolicy": "pre-submit-only",
            "accountSwitchHistory": [],
            "accountAssignmentReason": "initial-capacity-pick" if not account_id else "user-selected",
            "automationMode": automation_mode,
            "doubaoAutomationMode": automation_mode,
            "title": f"{trace_label} VMO {time.strftime('%H%M%S')}".strip(),
            "shotNo": trace_label,
            "shot_no": trace_label,
            "filenamePrefix": str(filename_prefix or trace_label or "").strip(),
            "batchId": str(batch_id or "").strip(),
            "projectId": str(project_id or "").strip(),
            "sequenceNo": sequence_no,
            "traceKey": trace_label or task_id,
            "prompt": fixed_prompt,
            "ratio": aspect_ratio,
            "duration": target_duration,
            "sourceDuration": source_duration,
            "requestedDuration": source_duration,
            "doubaoTargetDuration": target_duration,
            "imagePath": image_paths[0] if image_paths else "",
            "imagePaths": image_paths,
            "imageCount": len(image_paths),
            "status": "queued",
            "reservedAt": now,
            "createdAt": now,
            "updatedAt": now,
            "downloadDir": str(_download_dir(account)),
            "error": "",
            "automationRetryCount": 0,
            "downloadRetryCount": 0,
        }
        tasks = _read_tasks()
        tasks["tasks"].append(task)
        _write_tasks(tasks)
    ensure_ready(account["id"], open_browser=True, task_id=task_id, require_capacity=False, site=site)
    return task


def get_task(task_id: str) -> Optional[dict]:
    with _STATE_LOCK:
        return next((t for t in _read_tasks().get("tasks", []) if t.get("id") == task_id), None)


def cancel_task(task_id: str, reason: str = "用户已停止") -> Optional[dict]:
    if not task_id:
        return None
    return _update_task({"id": task_id, "status": "cancelled", "error": reason})


def _is_task_stale(task: dict, *, now_ms: Optional[int] = None, stale_ms: int = TASK_HEARTBEAT_STALE_MS) -> bool:
    now = now_ms or _now_ms()
    heartbeat = int(task.get("automationHeartbeatAt") or 0)
    updated = int(task.get("updatedAt") or 0)
    marker = heartbeat or updated
    return bool(marker and now - marker > stale_ms)


def _task_retry_count(task: dict, key: str) -> int:
    try:
        return max(0, int((task or {}).get(key) or 0))
    except Exception:
        return 0


def _bump_task_counter(task_id: str, key: str) -> Optional[dict]:
    task = get_task(task_id) or {}
    return _update_task({"id": task_id, key: _task_retry_count(task, key) + 1})


def _reset_task_for_retry(task_id: str, reason: str = "") -> Optional[dict]:
    task = get_task(task_id) or {}
    retry_count = _task_retry_count(task, "automationRetryCount") + 1
    return _update_task({
        "id": task_id,
        "status": "queued",
        "error": reason,
        "automationHeartbeatAt": 0,
        "automationRunnerId": "",
        "stage": "retry-queued",
        "automationRetryCount": retry_count,
    })


def _recover_stale_task(task: dict, *, force_restart: bool = False) -> bool:
    task_id = str((task or {}).get("id") or "")
    account_id = str((task or {}).get("accountId") or "")
    site = _task_site(task)
    if not task_id or not account_id:
        return False
    try:
        ensure_ready(account_id, open_browser=True, task_id=task_id, force_restart=force_restart, site=site)
        _update_task({
            "id": task_id,
            "lastRescueAt": _now_ms(),
            "rescueCount": _task_retry_count(task, "rescueCount") + 1,
            "error": "" if str(task.get("status") or "") != "failed" else task.get("error", ""),
        })
        logger.info("[DoubaoPool] rescued task id=%s status=%s force_restart=%s", task_id, task.get("status"), force_restart)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[DoubaoPool] rescue stale task failed id=%s: %s", task_id, e)
        return False


def _resume_download_if_possible(task: dict) -> Optional[dict]:
    if not task:
        return None
    status = str(task.get("status") or "")
    stage = str(task.get("stage") or "")
    if status != "downloading" and not (stage == "download-retry-waiting" or (status == "submitted" and str(task.get("assetUrl") or task.get("backupUrl") or "").strip())):
        return None
    task_id = str(task.get("id") or "")
    asset_url = str(task.get("assetUrl") or "").strip()
    backup_url = str(task.get("backupUrl") or "").strip()
    if not task_id or not (asset_url or backup_url):
        return None
    if _task_retry_count(task, "downloadRetryCount") >= DOUBAO_RESUME_DOWNLOAD_RETRY_LIMIT:
        return None
    try:
        _bump_task_counter(task_id, "downloadRetryCount")
        return _download_task_video(task_id, {
            "assetUrl": asset_url,
            "backupUrl": backup_url,
            "doubaoResultKey": task.get("doubaoResultKey") or "",
            "doubaoInternalTaskId": task.get("doubaoInternalTaskId") or "",
            "doubaoInternalMessageId": task.get("doubaoInternalMessageId") or "",
            "doubaoInternalVideoId": task.get("doubaoInternalVideoId") or "",
            "doubaoResultMeta": task.get("doubaoResultMeta") or {},
            "title": task.get("title") or task_id,
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("[DoubaoPool] resume task download failed id=%s: %s", task_id, e)
        return None


def wait_for_task(task_id: str, *, timeout: int = 1800, interval: int = 5,
                  on_progress=None, on_stage=None, cancel_checker=None) -> dict:
    start = time.time()
    last_rescue_at = 0.0
    initial_ready_sent = False
    while time.time() - start < timeout:
        if cancel_checker and cancel_checker():
            cancel_task(task_id, "用户已停止")
            raise DoubaoPoolError("豆包任务已停止")
        task = get_task(task_id)
        if not task:
            raise DoubaoPoolError("豆包任务不存在")
        if not initial_ready_sent:
            _recover_stale_task(task)
            last_rescue_at = time.time()
            initial_ready_sent = True
        status = str(task.get("status") or "")
        elapsed = int(time.time() - start)
        doubao_progress = task.get("doubaoProgress")
        try:
            doubao_progress = int(float(doubao_progress))
        except Exception:
            doubao_progress = None
        progress_value = (
            min(98, max(8, doubao_progress))
            if doubao_progress is not None and doubao_progress > 0
            else min(95, 8 + int(elapsed / max(1, timeout) * 85))
        )
        stage_label = _task_stage_label(task, status)
        if on_progress:
            on_progress(progress_value)
        if on_stage:
            on_stage("polling", stage_label, progress_value)
        if status in {"downloaded", "completed"}:
            file_path = _latest_downloaded_file(task)
            if file_path:
                return {**task, "filepath": str(file_path), "video_url": str(file_path)}
            if str(task.get("assetUrl") or "").strip():
                recovered = _resume_download_if_possible({**task, "status": "downloading"})
                if recovered:
                    task = recovered
                    file_path = _latest_downloaded_file(task)
                    if file_path:
                        return {**task, "filepath": str(file_path), "video_url": str(file_path)}
        if status == "cancelled":
            raise DoubaoPoolError(task.get("error") or "豆包任务已停止")
        if status == "manual":
            raise DoubaoPoolError(task.get("error") or task.get("doubaoPageMessage") or "豆包半自动模式已填入提示词和参考图，请在豆包网页中手动设置参数、生成并下载")
        if status == "failed":
            if _should_revive_failed_task(task):
                revived = _update_task({"id": task_id, "status": "submitted", "error": "", "stage": task.get("stage") or "waiting-result"})
                if revived:
                    task = revived
                    status = str(task.get("status") or "submitted")
                    logger.info("[DoubaoPool] revived failed task with active page signal id=%s", task_id)
                else:
                    status = "submitted"
            if status == "failed":
                retry_count = _task_retry_count(task, "automationRetryCount")
                if retry_count < DOUBAO_AUTOMATION_RETRY_LIMIT and not _is_failed_page_signal(task) and not _should_skip_automation_retry(task):
                    reset = _reset_task_for_retry(task_id, task.get("error") or "自动化临时失败，已重新排队")
                    if reset:
                        task = reset
                        status = str(task.get("status") or "queued")
                        _recover_stale_task(task)
                        last_rescue_at = time.time()
                        time.sleep(max(2, interval))
                        continue
                raise DoubaoPoolError(task.get("error") or "豆包网页视频生成失败")
        if status in {"queued", "claimed", "prepared"} and _task_has_generation_signal(task):
            revived = _update_task({"id": task_id, "status": "submitted", "error": "", "stage": task.get("stage") or "waiting-result"})
            if revived:
                task = revived
                status = str(task.get("status") or "submitted")
        if status == "downloading" or str(task.get("stage") or "") == "download-retry-waiting":
            recovered = _resume_download_if_possible(task)
            if recovered:
                task = recovered
                status = str(task.get("status") or "downloading")
                file_path = _latest_downloaded_file(task)
                if file_path:
                    return {**task, "filepath": str(file_path), "video_url": str(file_path)}
        if status in {"queued", "claimed", "prepared", "submitted", "downloading"} and time.time() - last_rescue_at > 45:
            if status == "queued" or _is_task_stale(task, stale_ms=45_000):
                rescue_count = _task_retry_count(task, "rescueCount")
                force_restart = bool(rescue_count >= 2 and status in {"claimed", "prepared"} and not _task_has_generation_signal(task))
                if _recover_stale_task(task, force_restart=force_restart):
                    last_rescue_at = time.time()
                latest = get_task(task_id) or task
                if (
                    status in {"claimed", "prepared"}
                    and not _task_has_generation_signal(latest)
                    and _task_retry_count(latest, "automationRetryCount") < DOUBAO_AUTOMATION_RETRY_LIMIT
                    and _is_task_stale(latest, stale_ms=120_000)
                ):
                    reset = _reset_task_for_retry(task_id, "自动化长时间未推进，已重新排队")
                    if reset and _recover_stale_task(reset):
                        last_rescue_at = time.time()
        time.sleep(max(2, interval))
    raise DoubaoPoolError("豆包网页视频生成超时，请检查豆包窗口是否已登录、是否完成提示词填入、参考图上传和自动提交")


def _task_stage_label(task: dict, status: str) -> str:
    message = str(task.get("doubaoPageMessage") or "").strip()
    if message:
        return message
    page_status = str(task.get("doubaoPageStatus") or "").strip()
    if page_status == "completed-signal":
        return "豆包已完成，正在抓取结果"
    if page_status == "duration-confirm":
        return f"豆包确认 {select_doubao_duration(task.get('sourceDuration') or task.get('duration'))} 秒生成中"
    if page_status == "failed-signal":
        return str(task.get("doubaoFailureReason") or "豆包提示生成失败").strip()
    if page_status in {"generating", "queued-signal"}:
        details = []
        if task.get("doubaoModel"):
            details.append(str(task.get("doubaoModel")))
        if task.get("doubaoCreditCost") not in (None, ""):
            details.append(f"消耗{task.get('doubaoCreditCost')}额度")
        if task.get("doubaoCreditRemaining") not in (None, ""):
            details.append(f"剩余{task.get('doubaoCreditRemaining')}额度")
        if task.get("doubaoEstimatedWait"):
            details.append(f"预计{task.get('doubaoEstimatedWait')}")
        title = "豆包排队中" if page_status == "queued-signal" else "豆包正在生成"
        return title + (f" · {' · '.join(details)}" if details else "")
    stage = str(task.get("stage") or "").strip()
    stage_labels = {
        "completed-card-detected": "豆包已完成，正在抓取结果",
        "result-candidate-confirming": "已发现豆包结果，正在确认视频归属",
        "download-retry-waiting": "豆包视频下载未完成，正在重试抓取",
        "refreshing-result-page": "豆包结果未实时暴露，正在刷新读取",
        "network-result-captured": "已捕获豆包视频接口结果，正在绑定任务",
        "watermark-resolving": "已抓到视频，正在解析无水印地址",
        "watermark-unresolved": "无水印解析未成功，已阻止下载带水印版本",
        "semi-auto-ready": "半自动模式已填入提示词和参考图，等待手动生成",
    }
    stage_labels.setdefault("account-reroute-blocked", "当前任务已绑定提交账号，已停止自动切号")
    stage_labels.setdefault("account-exhausted", "当前豆包账号额度不足，且没有可切换账号")
    if stage in stage_labels:
        return stage_labels[stage]
    labels = {
        "queued": "等待豆包账号窗口领取任务",
        "claimed": "豆包账号已领取任务",
        "prepared": "已填入豆包提示词，准备提交",
        "submitted": "已提交豆包，等待生成结果",
        "downloading": "正在下载豆包视频",
        "downloaded": "豆包视频已下载",
        "manual": "半自动模式已交给用户手动处理",
    }
    return labels.get(status or "", f"等待豆包网页任务：{status or 'queued'}")


def _latest_downloaded_file(task: dict) -> Optional[Path]:
    exact = Path(str(task.get("downloadFilename") or ""))
    if exact.exists() and exact.is_file():
        return exact
    folder = Path(task.get("downloadDir") or "")
    if not folder.exists():
        return None
    files = [p for p in folder.glob("*.mp4") if p.is_file()]
    if not files:
        return None
    created_at = int(task.get("createdAt") or 0) / 1000
    files = [p for p in files if p.stat().st_mtime >= created_at - 5] or files
    return max(files, key=lambda p: p.stat().st_mtime)


def _safe_video_filename(value: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", str(value or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:80] or "doubao-video") + ".mp4"


def _video_result_identity(video: dict) -> str:
    for key in (
        "doubaoResultKey",
        "doubaoInternalTaskId",
        "doubaoInternalVideoId",
        "doubaoInternalMessageId",
        "vid",
        "messageId",
    ):
        value = str((video or {}).get(key) or "").strip()
        if value:
            return value
    return uuid.uuid4().hex[:8]


def _selected_video_patch(video: dict) -> dict:
    video = video or {}
    result_keys = []

    def add(prefix: str, value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        key = text if re.match(r"^(result|task|message|video|vid|url):", text, re.I) else f"{prefix}:{text}"
        if key not in result_keys:
            result_keys.append(key)

    add("result", video.get("doubaoResultKey"))
    add("task", video.get("doubaoInternalTaskId"))
    add("video", video.get("doubaoInternalVideoId"))
    add("message", video.get("doubaoInternalMessageId"))
    add("vid", video.get("vid"))
    add("message", video.get("messageId"))
    add("url", video.get("noWatermarkUrl"))
    add("url", video.get("assetUrl"))
    add("url", video.get("backupUrl"))
    meta = dict(video.get("doubaoResultMeta") or {})
    existing = meta.get("resultKeys") if isinstance(meta.get("resultKeys"), list) else []
    merged_keys = []
    for item in [*existing, *result_keys]:
        text = str(item or "").strip()
        if text and text not in merged_keys:
            merged_keys.append(text)
    meta["resultKeys"] = merged_keys[-40:]
    if video.get("source"):
        meta["source"] = str(video.get("source") or "")[:120]
    if video.get("doubaoWatermarkSource"):
        meta["watermarkSource"] = str(video.get("doubaoWatermarkSource") or "")[:80]
    return {
        "doubaoInternalTaskId": str(video.get("doubaoInternalTaskId") or ""),
        "doubaoInternalMessageId": str(video.get("doubaoInternalMessageId") or video.get("messageId") or ""),
        "doubaoInternalVideoId": str(video.get("doubaoInternalVideoId") or video.get("vid") or ""),
        "doubaoResultKey": str(video.get("doubaoResultKey") or (result_keys[0] if result_keys else "")),
        "networkAnchorTaskId": str(video.get("networkAnchorTaskId") or ""),
        "networkAnchorAccountId": str(video.get("networkAnchorAccountId") or ""),
        "networkAnchorSubmittedAt": int(_to_float(video.get("networkAnchorSubmittedAt")) or 0),
        "networkAnchorTrusted": bool(video.get("networkAnchorTrusted")),
        "networkPromptMatched": bool(video.get("networkPromptMatched")),
        "noWatermarkUrl": str(video.get("noWatermarkUrl") or ""),
        "doubaoWatermarkSource": str(video.get("doubaoWatermarkSource") or ""),
        "doubaoWatermarkResolved": bool(_trusted_doubao_no_watermark_url(video)),
        "doubaoResultMeta": meta,
    }


def _video_download_urls(video: dict) -> list[str]:
    urls: list[str] = []
    no_watermark = _trusted_doubao_no_watermark_url(video or {})
    if no_watermark:
        urls.append(no_watermark)
    if (video or {}).get("requireNoWatermark"):
        return urls
    for value in ((video or {}).get("assetUrl"), (video or {}).get("backupUrl")):
        url = _normalize_doubao_video_url(value)
        if not url or url in urls or not _is_likely_doubao_video_resource_url(url):
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in {"http", "https"}:
            urls.append(url)
    return sorted(urls, key=lambda item: 0 if _is_doubao_no_watermark_url(item) else 1)


def _normalize_doubao_video_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url or url.startswith(("blob:", "data:")):
        return ""
    url = url.replace("\\u0026", "&").replace("&amp;", "&").replace("\\/", "/")
    if re.match(r"^https%3A%2F%2F", url, re.I):
        try:
            url = urllib.parse.unquote(url)
        except Exception:
            pass
    return url


def _normalize_doubao_no_watermark_url(value: Any) -> str:
    url = _to_doubao_no_watermark_url(value)
    if not url:
        return ""
    if not _is_doubao_no_watermark_url(url):
        return ""
    if _is_explicit_doubao_watermark_url(url):
        return ""
    if not _is_likely_doubao_video_resource_url(url):
        return ""
    return url


def _is_doubao_no_watermark_url(value: Any) -> bool:
    text = str(value or "")
    return bool(
        re.search(r"(?:[?&]lr=video_gen_no_watermark|video_gen_no_watermark)", text, re.I)
        or _is_dola_clean_video_url(text)
    )


def _is_dola_clean_video_url(value: Any) -> bool:
    url = _normalize_doubao_video_url(value)
    if not url or _is_explicit_doubao_watermark_url(url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query or "")
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    host_ok = host == "dola.com" or host.endswith(".dola.com") or host == "byteintlapi.com" or host.endswith(".byteintlapi.com")
    if not host_ok:
        return False
    lr = str((query.get("lr") or [""])[0] or "")
    logo_type = str((query.get("logo_type") or [""])[0] or "")
    marker_ok = bool(
        re.fullmatch(r"cici_ai", lr, re.I)
        or re.fullmatch(r"cici_ai", logo_type, re.I)
        or re.search(r"(?:[?&](?:lr|logo_type)=cici_ai)(?:[&#]|$)", url, re.I)
    )
    video_ok = bool(re.search(
        r"/video(?:/|_|-)|mime_type=video|download=true|\.(?:mp4|mov|webm|m4v)(?:$|[?#])|/fplay/|tos-mya|tos-[^/?#]+",
        f"{parsed.path}?{parsed.query}",
        re.I,
    ))
    return marker_ok and video_ok


def _is_explicit_doubao_watermark_url(value: Any) -> bool:
    text = str(value or "")
    return bool(
        re.search(r"video_gen_watermark(?:_[a-z0-9]+)*", text, re.I)
        or re.search(r"(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|logo_type=(?!video_gen_no_watermark)[^&#]*watermark)", text, re.I)
    )


def _is_blocked_doubao_watermark_variant_url(value: Any) -> bool:
    return bool(re.search(
        r"(?:video_(?:pre|dsz[0-9]*)_watermark|tplv-[^?#]*watermark|(?:[?&]logo_type=(?!video_gen_no_watermark)[^&#]*watermark))",
        str(value or ""),
        re.I,
    ))


def _to_doubao_no_watermark_url(value: Any) -> str:
    url = _normalize_doubao_video_url(value)
    if not url or _is_blocked_doubao_watermark_variant_url(url):
        return ""
    return re.sub(r"video_gen_watermark(?:_[a-z0-9]+)*", "video_gen_no_watermark", url, flags=re.I)


def _is_likely_doubao_html_document_url(value: Any) -> bool:
    url = _normalize_doubao_video_url(value)
    if not url:
        return False
    if re.search(r"\.(?:html?|shtml)(?:$|[?#])", url, re.I):
        return True
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "/").rstrip("/") or "/"
    has_encoded_media = bool(re.search(r"(?:[?&](?:download|media_url|play_url|main_url|backup_url)=https?%3A)", f"?{parsed.query}", re.I))
    if host in {"doubao.com", "dola.com"} and not has_encoded_media:
        if path == "/" or re.search(r"^/(?:chat|download|login|auth|home|app)(?:/|$)", path, re.I):
            return True
    return False


def _is_likely_doubao_video_resource_url(value: Any) -> bool:
    url = _normalize_doubao_video_url(value)
    if not url or _is_likely_doubao_html_document_url(url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    if (
        re.search(r"/(?:api|samantha|alice|im|service|biz)/", path, re.I)
        and not re.search(r"\.(?:mp4|mov|webm|m4v)(?:$|[?#])", path, re.I)
        and not re.search(r"(?:[?&](?:download|media_url|play_url|main_url|backup_url)=https?%3A)", query, re.I)
    ):
        return False
    if _is_explicit_doubao_watermark_url(url):
        return False
    if re.search(r"\.(?:png|jpe?g|webp|gif|avif|svg|heic|heif)(?:~|$|[?#])", url, re.I):
        return False
    if re.search(r"image_generation|tplv-[^/?#]*image|\.image(?:$|[?#~])", url, re.I) and not re.search(r"video|play|media", url, re.I):
        return False
    return bool(re.search(
        r"\.(?:mp4|mov|webm|m4v)(?:$|[?#])"
        r"|(?:[?&](?:vid|video_id|videoId|item_id|itemId|media_id|play_id)=)"
        r"|/video(?:[_/-]|$)"
        r"|video_generation|play_?url|main_?url|download_?url|backup_?url|media_?url"
        r"|tos-[^/?#]+/obj/[^?#]*(?:video|media|mp4)"
        r"|byteimg\.com/[^?#]*(?:video|tos|obj|mp4)|bytevod|voddos|vod-|video\.?tos",
        url,
        re.I,
    ))


def _is_trusted_doubao_watermark_source(video: dict) -> bool:
    video = video or {}
    marker = str(video.get("doubaoWatermarkSource") or video.get("watermarkSource") or video.get("source") or "")
    if re.search(r"derived|dom-|performance|network-capture|native", marker, re.I):
        return False
    if re.search(r"samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api", marker, re.I):
        return True
    meta = video.get("doubaoResultMeta") if isinstance(video.get("doubaoResultMeta"), dict) else {}
    meta_source = str(meta.get("watermarkSource") or meta.get("source") or "")
    return bool(re.search(r"samantha-(?:media|video)-get-play-info|samantha[-_/]?(?:media|video)[-_/]?get[-_]?play[-_]?info|alice-share-save|creativity-share-info|get-play-info|mget-play-info|generation-task-list|get-media-info|share-info|watermark-extractor|no-watermark-api|network-no-watermark|doubao-api", meta_source, re.I))


def _trusted_doubao_no_watermark_url(video: dict) -> str:
    video = video or {}
    url = _normalize_doubao_no_watermark_url(video.get("noWatermarkUrl") or video.get("no_watermark_url") or "")
    if not url or not _is_trusted_doubao_watermark_source(video):
        return ""
    return url


def _content_disposition_filename(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", text, re.I)
    if match:
        try:
            return urllib.parse.unquote(match.group(1)).strip().strip('"')
        except Exception:
            return match.group(1).strip().strip('"')
    match = re.search(r'filename="?([^";]+)"?', text, re.I)
    return match.group(1).strip() if match else ""


def _is_html_like_download_response(response: Any, source_url: str) -> bool:
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("Content-Type") or headers.get("content-type") or "").lower()
    disposition = str(headers.get("Content-Disposition") or headers.get("content-disposition") or "")
    final_url = str(getattr(response, "url", "") or source_url or "")
    disposition_name = _content_disposition_filename(disposition)
    if re.search(r"(?:text/html|application/xhtml\+xml|text/plain|application/json)", content_type, re.I):
        return True
    if re.search(r"\.(?:html?|shtml)(?:$|[?#])", disposition_name, re.I):
        return True
    if _is_likely_doubao_html_document_url(final_url) or _is_likely_doubao_html_document_url(source_url):
        return True
    if content_type.startswith("image/"):
        return True
    return False


def _looks_like_html_download_chunk(chunk: bytes) -> bool:
    head = bytes(chunk or b"").lstrip()[:1024].lower()
    if not head:
        return False
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<head")
        or b"<title>" in head[:256]
        or b'<meta charset="' in head[:512]
    )


def _looks_like_video_download_chunk(chunk: bytes, source_url: str = "") -> bool:
    head = bytes(chunk or b"")[:64]
    if len(head) < 4:
        return False
    if head[4:8] == b"ftyp":
        return True
    if head.startswith(b"\x1aE\xdf\xa3"):
        return True
    if head.startswith(b"RIFF") and head[8:12] in {b"AVI ", b"WAVE"}:
        return True
    return bool(re.search(r"\.(?:mp4|mov|webm|m4v)(?:$|[?#])", source_url, re.I))


def _download_task_video(task_id: str, video: dict) -> Optional[dict]:
    if not task_id:
        raise DoubaoPoolError("missing task id")
    task = get_task(task_id)
    if not task:
        raise DoubaoPoolError("豆包任务不存在")
    if task.get("status") == "cancelled":
        return task
    video = dict(video or {})
    video["noWatermarkUrl"] = _trusted_doubao_no_watermark_url(video)
    if video["noWatermarkUrl"]:
        video["assetUrl"] = video["noWatermarkUrl"]
    video["assetUrl"] = _normalize_doubao_video_url(video.get("assetUrl") or "")
    video["backupUrl"] = _normalize_doubao_video_url(video.get("backupUrl") or "")
    urls = _video_download_urls(video)
    if not urls:
        if video.get("requireNoWatermark"):
            raise DoubaoPoolError("无水印解析未成功，已阻止下载带水印版本")
        raise DoubaoPoolError("豆包结果没有可下载地址")
    if video.get("requireNoWatermark") and any(not _is_doubao_no_watermark_url(url) for url in urls):
        raise DoubaoPoolError("no-watermark download requires a trusted clean URL")
    raw_dir = str(task.get("downloadDir") or "").strip()
    folder = Path(raw_dir) if raw_dir else Path()
    if not raw_dir:
        with _STATE_LOCK:
            state = _read_state()
            account = next((a for a in state.get("accounts", []) if a.get("id") == task.get("accountId") and _account_matches_site(a, _task_site(task))), {})
            folder = _download_dir(account)
    folder.mkdir(parents=True, exist_ok=True)
    title = str((video or {}).get("title") or task.get("title") or task_id)
    shot_label = str(task.get("shotNo") or task.get("shot_no") or task.get("filenamePrefix") or "").strip()
    if shot_label and shot_label not in title:
        title = f"{shot_label}-{title}"
    vid = _video_result_identity(video or {})
    filename = _safe_video_filename(f"{title}-{vid}")
    target = folder / filename
    if target.exists():
        target = folder / _safe_video_filename(f"{target.stem}-{uuid.uuid4().hex[:6]}")
    temp = target.with_suffix(target.suffix + ".part")
    selected_patch = _selected_video_patch(video or {})
    last_error: Optional[Exception] = None
    for index, url in enumerate(urls, start=1):
        _update_task({
            "id": task_id,
            "status": "downloading",
            "assetUrl": url,
            "backupUrl": (video or {}).get("backupUrl") or "",
            "stage": f"downloading-url-{index}/{len(urls)}",
            **selected_patch,
        })
        try:
            with http.get(url, stream=True, timeout=(20, 240), headers={"User-Agent": "Mozilla/5.0"}) as response:
                response.raise_for_status()
                if _is_html_like_download_response(response, url):
                    raise DoubaoPoolError("download response is a webpage, blocked")
                with temp.open("wb") as fh:
                    saw_chunk = False
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        if not saw_chunk:
                            saw_chunk = True
                            if _looks_like_html_download_chunk(chunk):
                                raise DoubaoPoolError("download response is HTML, blocked")
                            content_type = str(response.headers.get("Content-Type") or "").lower()
                            if content_type and not re.search(r"video/|octet-stream|binary|mp4|quicktime|webm", content_type, re.I):
                                if not _looks_like_video_download_chunk(chunk, url):
                                    raise DoubaoPoolError(f"download response is not video content: {content_type}")
                        latest = get_task(task_id) or {}
                        if latest.get("status") == "cancelled":
                            raise DoubaoPoolError(latest.get("error") or "豆包任务已停止")
                        fh.write(chunk)
            if not temp.exists() or temp.stat().st_size <= 0:
                raise DoubaoPoolError("下载的豆包视频为空")
            temp.replace(target)
            return _update_task({
                "id": task_id,
                "status": "downloaded",
                "downloadFilename": str(target),
                "assetUrl": url,
                "backupUrl": (video or {}).get("backupUrl") or "",
                "downloadSize": target.stat().st_size,
                **selected_patch,
            })
        except Exception as e:  # noqa: BLE001
            last_error = e
            try:
                if temp.exists():
                    temp.unlink()
            except Exception:
                pass
            logger.warning("[DoubaoPool] download url failed task=%s attempt=%s/%s error=%s", task_id, index, len(urls), e)
    raise last_error or DoubaoPoolError("豆包视频下载失败")


def copy_result(task: dict, *, save_dir: str | Path, model: str, duration: int, filename_prefix: str = "") -> dict:
    src = Path(task.get("filepath") or "")
    if not src.exists():
        raise DoubaoPoolError("豆包任务已完成，但未找到下载文件")
    target_dir = Path(save_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    shot_no = str(task.get("shotNo") or task.get("shot_no") or "").strip()
    trace_prefix = str(filename_prefix or shot_no or task.get("filenamePrefix") or "").strip()
    video_id = (trace_prefix + "_" if trace_prefix else "") + uuid.uuid4().hex[:8]
    filename = f"{video_id}_{model.replace('/', '_')}.mp4"
    dst = target_dir / filename
    shutil.copy2(src, dst)
    initial_account_id = str(task.get("initialAccountId") or task.get("accountIdOriginal") or task.get("accountId") or "")
    current_account_id = str(task.get("accountId") or task.get("currentAccountId") or "")
    return {
        "video_id": video_id,
        "filename": filename,
        "filepath": str(dst),
        "video_url": str(src),
        "model": model,
        "duration": duration,
        "created_at": time.time(),
        "shot_no": shot_no or trace_prefix,
        "doubao_shot_no": shot_no or trace_prefix,
        "doubao_task_id": task.get("id"),
        "doubao_account_id": current_account_id,
        "doubao_current_account_id": current_account_id,
        "doubao_initial_account_id": initial_account_id,
        "doubao_account_locked": bool(task.get("accountSwitchLocked") or task.get("lockedAccountId")),
        "doubao_locked_account_id": task.get("lockedAccountId") or "",
        "doubao_account_switch_history": task.get("accountSwitchHistory") or [],
        "doubao_reroute_skipped_reason": task.get("rerouteSkippedReason") or "",
        "doubao_result_key": task.get("doubaoResultKey") or "",
        "doubao_internal_task_id": task.get("doubaoInternalTaskId") or "",
        "doubao_internal_message_id": task.get("doubaoInternalMessageId") or "",
        "doubao_internal_video_id": task.get("doubaoInternalVideoId") or "",
        "doubao_result_meta": task.get("doubaoResultMeta") or {},
    }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        logger.info("[DoubaoPoolServer] " + fmt, *args)

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not _authorize(query):
            return self._json(403, {"ok": False, "error": "unauthorized"})
        if parsed.path == "/__dbvd_auth":
            return self._json(200, {"ok": True, "expiresAt": _LOCK.get("expiresAt")})
        if parsed.path == "/__dbvd_heartbeat":
            return self._json(200, {"ok": True, **_record_plugin_heartbeat({
                "url": (query.get("url") or [""])[0],
                "extensionId": (query.get("rid") or [""])[0],
                "version": (query.get("version") or [""])[0],
                "accountId": (query.get("accountId") or [""])[0],
                "taskId": (query.get("taskId") or [""])[0],
            })})
        if parsed.path == "/__dbvd_task":
            task_id = (query.get("taskId") or [""])[0]
            account_id = (query.get("accountId") or [""])[0]
            runner_id = (query.get("rid") or [""])[0]
            task = _claim_task(task_id, account_id=account_id, runner_id=runner_id)
            return self._json(200, {"ok": True, "task": task})
        if parsed.path == "/__dbvd_task_status":
            task_id = (query.get("taskId") or [""])[0]
            return self._json(200, {"ok": True, "task": get_task(task_id)})
        if parsed.path == "/__dbvd_file":
            task_id = (query.get("taskId") or [""])[0]
            task = get_task(task_id)
            index = int((query.get("index") or ["0"])[0] or 0)
            paths = list((task or {}).get("imagePaths") or [])
            if not paths and (task or {}).get("imagePath"):
                paths = [str((task or {}).get("imagePath"))]
            path = Path(paths[index] if 0 <= index < len(paths) else "")
            if not path.exists():
                self.send_response(404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            raw = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not _authorize(query):
            return self._json(403, {"ok": False, "error": "unauthorized"})
        if parsed.path == "/__dbvd_heartbeat":
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            return self._json(200, {"ok": True, **_record_plugin_heartbeat({
                **body,
                "extensionId": body.get("extensionId") or (query.get("rid") or [""])[0],
            })})
        if parsed.path == "/__dbvd_download":
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            try:
                task = _download_task_video(str(body.get("id") or ""), body.get("video") or {})
                return self._json(200, {"ok": True, "task": task})
            except Exception as e:  # noqa: BLE001
                task_id = str(body.get("id") or "")
                task = None
                if task_id:
                    task = _update_task({
                        "id": task_id,
                        "status": "submitted",
                        "stage": "download-retry-waiting",
                        "error": "",
                        "doubaoDownloadError": str(e),
                    })
                return self._json(500, {"ok": False, "error": str(e), "task": task})
        if parsed.path == "/__dbvd_click":
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            try:
                result = _dispatch_devtools_click(
                    str(body.get("taskId") or ""),
                    str(body.get("accountId") or ""),
                    body.get("x"),
                    body.get("y"),
                )
                return self._json(200, {"ok": True, **result})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(e)})
        if parsed.path == "/__dbvd_refresh":
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            try:
                result = _refresh_devtools_task_page(
                    str(body.get("taskId") or ""),
                    str(body.get("accountId") or ""),
                    str(body.get("reason") or ""),
                )
                return self._json(200, {"ok": True, **result})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(e)})
        if parsed.path == "/__dbvd_llm_decide":
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            try:
                decision = _doubao_llm_decide(body)
                return self._json(200, {"ok": True, "decision": decision})
            except Exception as e:  # noqa: BLE001
                logger.warning("[DoubaoPool] llm decision failed: %s", e)
                return self._json(200, {"ok": True, "decision": {
                    "action": "no_action",
                    "reason": str(e)[:240],
                    "confidence": 0,
                    "enabled": False,
                    "usedLlm": False,
                    "source": "error-fallback",
                }})
        if parsed.path != "/__dbvd_task":
            return self._json(404, {"ok": False, "error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
        task = _update_task(body)
        return self._json(200, {"ok": True, "task": task})


def _claim_task(task_id: str, *, account_id: str = "", runner_id: str = "") -> Optional[dict]:
    with _STATE_LOCK:
        state = _read_state()
        current_mode = _state_automation_mode(state)
        data = _read_tasks()
        tasks = list(data.get("tasks", []))
        if not task_id:
            tasks = sorted(tasks, key=lambda item: int(item.get("createdAt") or 0), reverse=True)
        now = _now_ms()
        for task in tasks:
            if task_id and task.get("id") != task_id:
                continue
            task_account_id = str(task.get("accountId") or "")
            if task_id and task_account_id and not account_id:
                continue
            if account_id and task_account_id != str(account_id):
                continue
            if not task.get("doubaoAutomationMode") and not task.get("automationMode"):
                task["automationMode"] = current_mode
                task["doubaoAutomationMode"] = current_mode
            status = str(task.get("status") or "")
            if status == "failed" and _should_revive_failed_task(task):
                task["status"] = "submitted"
                task["error"] = ""
                task["updatedAt"] = now
                status = "submitted"
            if status in _task_terminal_statuses():
                continue
            if status in {"submitted", "downloading"} and not task_id:
                continue
            owner = str(task.get("automationRunnerId") or "")
            if status in {"claimed", "prepared", "submitted", "downloading"} and not _is_task_stale(task, now_ms=now):
                if task_id:
                    if owner and runner_id and owner != runner_id:
                        continue
                    if runner_id and not owner:
                        task["automationRunnerId"] = runner_id
                    task["automationHeartbeatAt"] = now
                    task["updatedAt"] = now
                    _write_tasks(data)
                    return task
                continue
            task["status"] = "claimed"
            if runner_id:
                task["automationRunnerId"] = runner_id
            task["automationHeartbeatAt"] = now
            task["updatedAt"] = now
            _write_tasks(data)
            logger.info("[DoubaoPool] claimed task id=%s status=%s", task.get("id"), task.get("status"))
            return task
    if task_id:
        logger.debug("[DoubaoPool] no claimable task for id=%s account=%s runner=%s", task_id, account_id or "<any>", runner_id or "")
    else:
        logger.info("[DoubaoPool] no claimable task for id=%s account=%s", "<any>", account_id or "<any>")
    return None


def _update_task(patch: dict) -> Optional[dict]:
    task_id = str(patch.get("id") or "")
    if not task_id:
        return None
    reroute_low_credit = False
    reroute_account_id = ""
    reroute_reason = ""
    result_task: Optional[dict] = None
    with _STATE_LOCK:
        data = _read_tasks()
        for task in data.get("tasks", []):
            if task.get("id") != task_id:
                continue
            if task.get("status") == "cancelled" and patch.get("status") != "cancelled":
                return task
            if str(task.get("status") or "") in {"downloaded", "completed"} and str(patch.get("status") or "") == "failed":
                return task
            runner_id = str(patch.get("automationRunnerId") or "")
            owner = str(task.get("automationRunnerId") or "")
            if runner_id and owner and runner_id != owner and not _is_task_stale(task):
                return task
            previous_status = str(task.get("status") or "")
            previous_stage = str(task.get("stage") or "")
            previous_page_status = str(task.get("doubaoPageStatus") or "")
            patch = dict(patch)
            if task.get("accountId"):
                task.setdefault("initialAccountId", task.get("accountId"))
                task.setdefault("accountIdOriginal", task.get("initialAccountId") or task.get("accountId"))
            preserve_generation = _should_preserve_generation_failure(task, patch)
            if preserve_generation:
                patch["status"] = "submitted"
                patch["error"] = ""
                logger.info("[DoubaoPool] preserved active generation task=%s from soft failure", task_id)
            for key in ("status", "error"):
                if key in patch:
                    task[key] = patch.get(key)
            for key in (
                "downloadId", "downloadFilename", "assetUrl", "backupUrl", "downloadSize",
                "automationHeartbeatAt", "automationRunnerId", "stage", "beforeVideoKeys", "resultGuard",
                "doubaoPageStatus", "doubaoPageMessage", "doubaoProgress",
                "doubaoCreditCost", "doubaoCreditRemaining", "doubaoEstimatedWait",
                "doubaoModel", "doubaoFailureReason", "doubaoSignals", "doubaoLastText",
                "doubaoInternalTaskId", "doubaoInternalMessageId", "doubaoInternalVideoId",
                "doubaoResultKey", "doubaoResultMeta",
                "noWatermarkUrl", "doubaoWatermarkResolved", "doubaoWatermarkDiagnostic",
                "networkAnchorTaskId", "networkAnchorAccountId", "networkAnchorSubmittedAt",
                "networkAnchorTrusted", "networkPromptMatched",
                "doubaoLlmDecision", "doubaoLlmDecisionAt", "doubaoLlmAction",
                "doubaoAutomationDiagnostic", "doubaoDownloadError",
                "doubaoRefreshReason", "doubaoLastRefreshAt",
                "automationRetryCount", "downloadRetryCount", "rescueCount", "lastRescueAt",
            ):
                if key in patch:
                    task[key] = patch.get(key)
            task["updatedAt"] = _now_ms()
            if _task_account_should_lock(task):
                _lock_task_account(task, str(task.get("stage") or task.get("status") or "generation-bound"))
            if previous_status != "downloaded" and task.get("status") == "downloaded":
                _mark_account_used(task.get("accountId"), site=_task_site(task))
            has_generation_signal = _task_has_generation_signal(task)
            credit_sync_stages = {"submitted", "waiting-result", "downloading", "doubao-generating", "doubao-queued", "doubao-duration-confirm"}
            credit_sync_statuses = {"submitted", "downloading", "downloaded", "completed"}
            patch_stage = str(patch.get("stage") or task.get("stage") or "")
            if (
                "doubaoCreditRemaining" in patch
                and (
                    previous_status in credit_sync_statuses
                    or str(task.get("status") or "") in credit_sync_statuses
                    or patch_stage in credit_sync_stages
                )
            ):
                _sync_account_remote_credits(task.get("accountId"), patch.get("doubaoCreditRemaining"), site=_task_site(task))
                credits = _to_float(patch.get("doubaoCreditRemaining"))
                if (
                    credits is not None
                    and credits <= 0
                    and str(task.get("status") or "") in _task_active_statuses()
                    and not has_generation_signal
                ):
                    reroute_account_id = str(task.get("accountId") or "")
                    reroute_reason = "当前豆包账号剩余额度为 0"
            failure_text = " ".join(str(x or "") for x in (
                patch.get("error"), patch.get("doubaoFailureReason"), patch.get("doubaoPageMessage"),
                task.get("error"), task.get("doubaoFailureReason"), task.get("doubaoPageMessage"),
            ))
            if (
                str(task.get("status") or "") == "failed"
                and previous_status not in {"downloaded", "completed", "cancelled"}
                and _is_login_required_signal(failure_text)
                and not _task_has_generation_signal(task)
            ):
                login_account_id = str(task.get("accountId") or "")
                if login_account_id:
                    state = _read_state()
                    _mark_account_login_required_in_state(state, login_account_id, failure_text or "豆包页面提示需要重新登录", site=_task_site(task))
                    _write_state(state)
                task["stage"] = "account-login-required"
            if (
                str(task.get("status") or "") == "failed"
                and previous_status not in {"downloaded", "completed", "cancelled"}
                and _is_low_credit_signal(failure_text)
                and not _is_login_required_signal(failure_text)
                and not _task_has_generation_signal(task)
            ):
                reroute_low_credit = True
                reroute_account_id = str(task.get("accountId") or "")
                reroute_reason = str(patch.get("error") or patch.get("doubaoFailureReason") or "当前豆包账号额度不足").strip()
            if reroute_account_id and reroute_reason:
                if _task_allows_account_reroute(task):
                    reroute_low_credit = True
                else:
                    reroute_low_credit = False
                    _remember_account_exhausted(reroute_account_id, reroute_reason, issue_key=f"task:{task_id}:reroute-blocked", site=_task_site(task))
                    task["rerouteSkippedReason"] = "account-locked-after-submit-or-result-bound"
                    task["stage"] = "account-reroute-blocked"
                    task["rerouteSkippedAt"] = _now_ms()
            _write_tasks(data)
            result_task = dict(task)
            current_status = str(task.get("status") or "")
            current_stage = str(task.get("stage") or "")
            current_page_status = str(task.get("doubaoPageStatus") or "")
            meaningful = bool(
                previous_status != current_status
                or previous_stage != current_stage
                or previous_page_status != current_page_status
                or any(key in patch for key in (
                "assetUrl", "backupUrl", "downloadFilename", "doubaoLlmAction",
                    "doubaoAutomationDiagnostic", "doubaoDownloadError", "doubaoRefreshReason",
                    "doubaoResultKey", "doubaoInternalTaskId", "noWatermarkUrl", "doubaoWatermarkDiagnostic",
                ))
            )
            if meaningful:
                decision = task.get("doubaoLlmDecision") if isinstance(task.get("doubaoLlmDecision"), dict) else {}
                diagnostic = task.get("doubaoAutomationDiagnostic") if isinstance(task.get("doubaoAutomationDiagnostic"), dict) else {}
                watermark_diagnostic = task.get("doubaoWatermarkDiagnostic") if isinstance(task.get("doubaoWatermarkDiagnostic"), dict) else {}
                logger.info(
                    "[DoubaoPool] task update id=%s status=%s->%s stage=%s->%s page=%s->%s action=%s source=%s used_llm=%s diag=%s watermark=%s no_watermark=%s error=%s",
                    task_id,
                    previous_status or "",
                    current_status or "",
                    previous_stage or "",
                    current_stage or "",
                    previous_page_status or "",
                    current_page_status or "",
                    str(task.get("doubaoLlmAction") or decision.get("action") or ""),
                    str(decision.get("source") or ""),
                    bool(decision.get("usedLlm")),
                    str(diagnostic.get("reason") or "")[:120],
                    str(watermark_diagnostic.get("reason") or "")[:120],
                    bool(task.get("doubaoWatermarkResolved") or task.get("noWatermarkUrl")),
                    str(task.get("error") or "")[:180],
                )
            break
    if reroute_low_credit:
        return _reroute_task_to_next_account(task_id, failed_account_id=reroute_account_id, reason=reroute_reason)
    return result_task


def _reroute_task_to_next_account(task_id: str, *, failed_account_id: str = "", reason: str = "") -> Optional[dict]:
    next_account_id = ""
    with _STATE_LOCK:
        state = _read_state()
        data = _read_tasks()
        tasks = data.get("tasks", [])
        task = next((item for item in tasks if item.get("id") == task_id), None)
        if not task:
            return None
        if str(task.get("status") or "") in {"downloaded", "completed", "cancelled"}:
            return task
        if task.get("accountId"):
            task.setdefault("initialAccountId", task.get("accountId"))
            task.setdefault("accountIdOriginal", task.get("initialAccountId") or task.get("accountId"))
        if not _task_allows_account_reroute(task):
            current_account_id = str(failed_account_id or task.get("accountId") or "")
            if current_account_id:
                _mark_account_exhausted_in_state(state, current_account_id, reason or "account reroute blocked", issue_key=f"task:{task_id}:reroute-blocked", site=_task_site(task))
            if _task_account_should_lock(task):
                _lock_task_account(task, "reroute-blocked")
            task["stage"] = "account-reroute-blocked"
            task["rerouteSkippedReason"] = "account-locked-after-submit-or-result-bound"
            task["rerouteSkippedAt"] = _now_ms()
            task["updatedAt"] = _now_ms()
            _write_state(state)
            _write_tasks(data)
            logger.info(
                "[DoubaoPool] blocked reroute task id=%s account=%s status=%s reason=%s",
                task_id, current_account_id or "", task.get("status"), reason or "",
            )
            return task
        blocked = set(str(item) for item in (task.get("exhaustedAccountIds") or []) if item)
        current_account_id = str(failed_account_id or task.get("accountId") or "")
        if current_account_id:
            blocked.add(current_account_id)
        site = _task_site(task)
        if current_account_id:
            _mark_account_exhausted_in_state(state, current_account_id, reason or "网页提示额度不足", issue_key=f"task:{task_id}:low-credit", site=site)
        active_counts = _active_task_counts(data, ignore_task_id=task_id, site=site)
        next_account = _pick_account_with_capacity(state, exclude_ids=blocked, active_counts=active_counts, site=site)
        if not next_account:
            task["status"] = "failed"
            task["error"] = reason or "当前豆包账号额度不足，且没有其它可用账号"
            task["stage"] = "account-exhausted"
            task["exhaustedAccountIds"] = sorted(blocked)
            task["updatedAt"] = _now_ms()
            _write_state(state)
            _write_tasks(data)
            return task
        next_account_id = str(next_account.get("id") or "")
        history = list(task.get("accountSwitchHistory") or [])
        history.append({
            "fromAccountId": current_account_id,
            "toAccountId": next_account_id,
            "reason": reason or "low-credit-before-submit",
            "statusBefore": str(task.get("status") or ""),
            "at": _now_ms(),
        })
        task["accountId"] = next_account_id
        task["site"] = site
        task["currentAccountId"] = next_account_id
        task["previousAccountId"] = current_account_id
        task["downloadDir"] = str(_download_dir(next_account))
        task["status"] = "queued"
        task["error"] = ""
        task["automationRunnerId"] = ""
        task["automationHeartbeatAt"] = 0
        task["stage"] = "rerouted"
        task["reservedAt"] = _now_ms()
        task["exhaustedAccountIds"] = sorted(blocked)
        task["accountSwitchHistory"] = history[-20:]
        task["accountAssignmentReason"] = "low-credit-before-submit"
        task["accountSwitchLocked"] = False
        task["lockedAccountId"] = ""
        task.pop("accountLockedAt", None)
        task.pop("accountLockReason", None)
        task["reroutedAt"] = _now_ms()
        task["rerouteReason"] = reason or "当前账号额度不足，已切换账号"
        task["updatedAt"] = _now_ms()
        next_account["lastUsedAt"] = _now_ms()
        _write_state(state)
        _write_tasks(data)
        logger.info(
            "[DoubaoPool] rerouted task id=%s from_account=%s to_account=%s reason=%s",
            task_id, current_account_id or "", next_account_id, reason or "",
        )
    if next_account_id:
        try:
            open_account(next_account_id, rotate=False, task_id=task_id, force_restart=False, require_capacity=False, site=_task_site(get_task(task_id)))
        except Exception as e:  # noqa: BLE001
            logger.warning("[DoubaoPool] open rerouted account failed task=%s account=%s: %s", task_id, next_account_id, e)
    return get_task(task_id)


def _mark_account_exhausted_in_state(
    state: dict,
    account_id: str,
    reason: str = "",
    *,
    issue_key: str = "",
    site: Any = None,
) -> None:
    if _is_login_required_signal(reason):
        _mark_account_login_required_in_state(state, account_id, reason, site=site)
        return
    _mark_account_issue_in_state(
        state,
        account_id,
        reason or "low credit",
        site=site,
        kind="low-credit",
        issue_key=issue_key or f"low-credit:{account_id}:{int(_now_ms() // 60_000)}:{hash(reason or '')}",
        hard=False,
    )
    return
    now = _now_ms()
    for account in state.get("accounts", []):
        if str(account.get("id") or "") == str(account_id or ""):
            account["remoteCreditRemaining"] = 0
            account["remoteRemainingVideos"] = 0
            account["remoteCreditCheckedAt"] = now
            account["lastExhaustedAt"] = now
            account["exhaustedReason"] = reason or "网页提示额度不足"
            account["updatedAt"] = now
            return


def _sync_account_remote_credits(account_id: str, credit_remaining: Any, *, site: Any = None) -> None:
    if credit_remaining in (None, ""):
        return
    try:
        credits = float(credit_remaining)
    except Exception:
        return
    remaining_videos = max(0, int(credits // DOUBAO_CREDITS_PER_VIDEO))
    state = _read_state()
    changed = False
    now = _now_ms()
    target_site = normalize_site(site) if site is not None else ""
    for account in state.get("accounts", []):
        if str(account.get("id") or "") == str(account_id or ""):
            if target_site and _account_site(account) != target_site:
                continue
            account["remoteCreditRemaining"] = credits
            account["remoteRemainingVideos"] = remaining_videos
            account["remoteCreditCheckedAt"] = now
            if remaining_videos <= 0:
                account.pop("remoteRemainingVideos", None)
                _mark_account_issue_in_state(
                    state,
                    account_id,
                    "remote credit is 0",
                    site=site,
                    kind="remote-credit-zero",
                    issue_key=f"remote-credit-zero:{account_id}:{int(now // 60_000)}",
                    hard=False,
                )
                changed = True
                break
                account["lastExhaustedAt"] = now
                account["exhaustedReason"] = "网页提示剩余额度为 0"
            else:
                _clear_account_exhaustion(account, clear_remote=False)
            changed = True
            break
    if changed:
        _write_state(state)


def _mark_account_used(account_id: str, *, site: Any = None) -> None:
    state = _read_state()
    target_site = normalize_site(site) if site is not None else ""
    for account in state.get("accounts", []):
        if account.get("id") == account_id:
            if target_site and _account_site(account) != target_site:
                continue
            account["used"] = int(account.get("used") or 0) + 1
            account["lastUsedAt"] = _now_ms()
            _write_state(state)
            return
