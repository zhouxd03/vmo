"""Flask application for vmo studio.

Serves the built Vue frontend and exposes the JSON API. Reuses shengtu's
generation request logic (refactored into backend/services). Designed to run
inside a PyWebview frameless window (see run_desktop.py) or standalone in a
browser for development.
"""

import logging
import os
import re
import sys
import time
from pathlib import Path

import requests as http
from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from . import routes_projects
from .core import auth_session, credentials, settings
from .core.logbus import clear_logs, export_text, get_logs, setup_logging
from .core.paths import APP_DIR, DATA_DIR, OUTPUT_DIR, PROJECTS_DIR, STATIC_DIR, ensure_dirs
from .services import doubao_pool, llm, video_gen, vidu_gen

logger = setup_logging()
STARTED_AT = time.time()
APP_VERSION = "1.0.7"

HUAJING_BASE_URL = "https://aibac.lizer.cc"


def _enable_doubao_pool_credential() -> dict:
    raw = credentials.load_raw()
    existing = next((e for e in raw.get("video", []) if e.get("provider") == "doubao_pool"), None)
    body = {
        "id": existing.get("id") if existing else None,
        "alias": "VMO 本地豆包号池",
        "provider": "doubao_pool",
        "base_url": "local://doubao-pool",
        "api_key": "",
        "model": "doubao-web-video",
        "enabled": True,
        "is_default": True,
        "note": "本地 Chrome/Edge 豆包网页号池",
    }
    if not body["id"]:
        body.pop("id")
    return credentials.upsert("video", body)


def _doubao_site_from_request(default: str = "cn") -> str:
    data = request.get_json(silent=True) if request.method != "GET" else None
    value = (
        request.args.get("site")
        or (data or {}).get("site")
        or (data or {}).get("doubaoSite")
        or default
    )
    return doubao_pool.normalize_site(value)


def _ensure_doubao_pool_credential_for_site(site: str = "cn") -> dict:
    site = doubao_pool.normalize_site(site)
    if site == doubao_pool.DOUBAO_SITE_CN:
        return _enable_doubao_pool_credential()
    raw = credentials.load_raw()
    provider = "doubao_pool_intl"
    existing = next((e for e in raw.get("video", []) if e.get("provider") == provider), None)
    body = {
        "id": existing.get("id") if existing else None,
        "alias": "VMO 国际版豆包号池",
        "provider": provider,
        "base_url": "local://doubao-pool/intl",
        "api_key": "",
        "model": "dola-web-video",
        "enabled": True,
        "is_default": False,
        "note": "本地国际版豆包网页号池",
    }
    if not body["id"]:
        body.pop("id")
    return credentials.upsert("video", body)


def _candidate_huajing_roots(value: str) -> list[Path]:
    raw = (value or "").strip().strip('"')
    roots: list[Path] = []
    if raw:
        p = Path(raw)
        roots.append(p)
        # If the user points at the installed app folder, the real Chromium
        # user-data folder is usually under AppData\Roaming.
        if p.name.lower() in {"comic-generator-electron", "画镜", "huajing"}:
            roots.append(Path(os.environ.get("APPDATA", "")) / "comic-generator-electron")
    roots.append(Path(os.environ.get("APPDATA", "")) / "comic-generator-electron")
    out = []
    seen = set()
    for root in roots:
        if not root:
            continue
        try:
            resolved = root.expanduser().resolve()
        except Exception:  # noqa: BLE001
            resolved = root
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    return out


def _read_locked_file(path: Path) -> bytes:
    # Windows Chromium cache files can be locked while Huajing is running.
    # Plain Path.read_bytes may fail; this lower-level open still works for
    # ordinary files and keeps failures isolated per file.
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            GENERIC_READ = 0x80000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            FILE_SHARE_DELETE = 0x00000004
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x00000080
            INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE

            handle = create_file(
                str(path), GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
            )
            if handle == INVALID_HANDLE_VALUE:
                raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(path))
            try:
                size = path.stat().st_size
                buf = ctypes.create_string_buffer(size)
                read = wintypes.DWORD(0)
                ok = kernel32.ReadFile(handle, buf, size, ctypes.byref(read), None)
                if not ok:
                    raise OSError(ctypes.get_last_error(), "ReadFile failed", str(path))
                return buf.raw[:read.value]
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            pass
    with path.open("rb") as f:
        return f.read()


def _scan_huajing_token(search_root: str = "") -> dict:
    roots = _candidate_huajing_roots(search_root)
    subdirs = [
        Path("Cache") / "Cache_Data",
        Path("Code Cache"),
        Path("Network"),
        Path("Local Storage") / "leveldb",
        Path("Session Storage"),
        Path("IndexedDB"),
        Path("."),
    ]
    access_re = re.compile(rb'"(?:access_token|token)"\s*:\s*"([^"]{40,})"', re.I)
    refresh_re = re.compile(rb'"refresh_token"\s*:\s*"([^"]{30,})"', re.I)
    jwt_re = re.compile(rb"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")
    max_size = 100 * 1024 * 1024
    best_access = ""
    best_refresh = ""
    best_source = ""
    scanned = 0

    for root in roots:
        if not root.exists():
            continue
        for subdir in subdirs:
            folder = root / subdir
            if not folder.exists():
                continue
            files = [folder] if folder.is_file() else folder.rglob("*")
            for file in files:
                try:
                    if not file.is_file():
                        continue
                    size = file.stat().st_size
                    if size <= 0 or size > max_size:
                        continue
                    scanned += 1
                    blob = _read_locked_file(file)
                except Exception:  # noqa: BLE001
                    continue
                if not best_access:
                    match = access_re.search(blob)
                    if match:
                        candidate = match.group(1).decode("utf-8", "ignore")
                        if candidate.startswith("eyJ"):
                            best_access = candidate
                            best_source = str(file)
                if not best_access:
                    match = jwt_re.search(blob)
                    if match:
                        best_access = match.group(0).decode("utf-8", "ignore")
                        best_source = str(file)
                if not best_refresh:
                    match = refresh_re.search(blob)
                    if match:
                        best_refresh = match.group(1).decode("utf-8", "ignore")
                if best_access and best_refresh:
                    return {
                        "access_token": best_access,
                        "refresh_token": best_refresh,
                        "source": best_source,
                        "scanned_files": scanned,
                    }
    if not best_access:
        raise ValueError("未扫描到画镜 token。请确认画镜已登录，并选择 comic-generator-electron 用户数据目录或安装目录。")
    return {
        "access_token": best_access,
        "refresh_token": best_refresh,
        "source": best_source,
        "scanned_files": scanned,
    }


def _scan_huajing_token(search_root: str = "") -> dict:
    roots = _candidate_huajing_roots(search_root)
    subdirs = [
        Path("Local Storage") / "leveldb",
        Path("Session Storage"),
        Path("IndexedDB"),
        Path("Network"),
        Path("Cache") / "Cache_Data",
        Path("Code Cache"),
        Path("."),
    ]
    access_re = re.compile(rb'"(?:access_token|token)"\s*:\s*"([^"]{40,})"', re.I)
    refresh_re = re.compile(rb'"refresh_token"\s*:\s*"([^"]{30,})"', re.I)
    jwt_re = re.compile(rb"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")
    max_size = 100 * 1024 * 1024
    best_refresh = ""
    candidates: dict[str, dict] = {}
    scanned = 0

    for root in roots:
        if not root.exists():
            continue
        for subdir in subdirs:
            folder = root / subdir
            if not folder.exists():
                continue
            files = [folder] if folder.is_file() else folder.rglob("*")
            for file in files:
                try:
                    if not file.is_file():
                        continue
                    stat = file.stat()
                    if stat.st_size <= 0 or stat.st_size > max_size:
                        continue
                    scanned += 1
                    blob = _read_locked_file(file)
                except Exception:  # noqa: BLE001
                    continue

                found_tokens = []
                for match in access_re.finditer(blob):
                    candidate = match.group(1).decode("utf-8", "ignore")
                    if candidate.startswith("eyJ"):
                        found_tokens.append(candidate)
                for match in jwt_re.finditer(blob):
                    candidate = match.group(0).decode("utf-8", "ignore")
                    if candidate.startswith("eyJ"):
                        found_tokens.append(candidate)
                for candidate in found_tokens:
                    existing = candidates.get(candidate)
                    if not existing or stat.st_mtime >= existing.get("mtime", 0):
                        candidates[candidate] = {"source": str(file), "mtime": stat.st_mtime}

                if not best_refresh:
                    match = refresh_re.search(blob)
                    if match:
                        best_refresh = match.group(1).decode("utf-8", "ignore")

    if not candidates:
        raise ValueError("未扫描到画镜 token。请确认画镜已登录，并选择 comic-generator-electron 用户数据目录或安装目录。")

    membership_rank = {"spark": 1, "flame": 2, "blaze": 3, "nova": 4, "cosmos": 5}
    ranked = []
    for token, meta in candidates.items():
        profile = {}
        try:
            profile = video_gen.huajing_user_info(HUAJING_BASE_URL, token)
        except Exception:
            profile = {}
        try:
            quota_score = float(profile.get("remaining_quota") if profile.get("remaining_quota") is not None else -1)
        except Exception:  # noqa: BLE001
            quota_score = -1
        level_score = membership_rank.get(str(profile.get("membership_level") or "").lower(), 0)
        ranked.append((1 if profile else 0, quota_score, level_score, meta.get("mtime", 0), token, meta, profile))

    ranked.sort(reverse=True)
    valid, _quota, _level, _mtime, best_access, best_meta, profile = ranked[0]
    if valid:
        logger.info(
            "[Huajing] selected token profile id=%s nickname=%s remaining_quota=%s level=%s source=%s candidates=%s",
            profile.get("id"), profile.get("nickname"), profile.get("remaining_quota"),
            profile.get("membership_level"), best_meta.get("source"), len(candidates),
        )
    else:
        logger.warning("[Huajing] token candidates found but none passed profile validation; using newest candidate")

    return {
        "access_token": best_access,
        "refresh_token": best_refresh,
        "source": best_meta.get("source", ""),
        "scanned_files": scanned,
        "profile": profile,
        "candidate_count": len(candidates),
    }


def _prepare_huajing_credential_body(body: dict) -> dict:
    entry = dict(body or {})
    if (entry.get("provider") or "").strip().lower() != "huajing":
        return entry
    entry["base_url"] = HUAJING_BASE_URL
    token_or_path = (entry.get("api_key") or "").strip()
    model_aliases = {
        "foldin": "doubao-seedance-2-0-fast-260128",
        "seedance_2_fast": "doubao-seedance-2-0-fast-260128",
        "seedance_2": "doubao-seedance-2-0-260128",
    }
    model = (entry.get("model") or "").strip()
    entry["model"] = model_aliases.get(model, model or "doubao-seedance-2-0-fast-260128")
    scan_root = None
    if token_or_path and "***" not in token_or_path and not token_or_path.startswith("eyJ"):
        scan_root = token_or_path
    elif not token_or_path or "***" in token_or_path:
        # Existing Huajing credentials arrive from the UI as a masked key.
        # Re-scan the current Electron login so switching Huajing accounts updates
        # the stored token instead of silently reusing the old account.
        scan_root = ""
    if scan_root is not None:
        try:
            found = _scan_huajing_token(scan_root)
            entry["api_key"] = found["access_token"]
            note = (entry.get("note") or "").strip()
            scan_note = f"Huajing token source: {found.get('source') or 'unknown'}"
            entry["note"] = f"{note}\n{scan_note}".strip() if note else scan_note
        except Exception:
            if not entry.get("id") and not token_or_path:
                raise
            logger.warning("[Huajing] token rescan failed; keeping existing credential", exc_info=True)
    return entry

def _openai_model_list(base_url: str, api_key: str) -> list[str]:
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base or not key:
        raise ValueError("请先填写 API 地址和 Key")
    url = f"{base if base.endswith('/v1') else base + '/v1'}/models"
    resp = http.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if resp.status_code >= 400:
        raise ValueError(f"获取模型列表失败 (HTTP {resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    items = data.get("data", data if isinstance(data, list) else [])
    models = []
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            models.append(item["id"])
        elif isinstance(item, str):
            models.append(item)
    return sorted(set(models))


def _credential_api_key(category: str, body: dict) -> str:
    api_key = body.get("api_key")
    if api_key and "***" not in api_key:
        return api_key
    entry_id = body.get("id")
    if entry_id:
        for entry in credentials.load_raw().get(category, []):
            if entry.get("id") == entry_id:
                return credentials._resolve_entry(entry).get("api_key", "")
    default = credentials.get_default(category)
    return (default or {}).get("api_key", "")


def _credential_base_url(category: str, body: dict) -> str:
    base_url = (body.get("base_url") or "").strip()
    if base_url:
        return base_url
    entry_id = body.get("id")
    if entry_id:
        for entry in credentials.load_raw().get(category, []):
            if entry.get("id") == entry_id:
                return credentials._resolve_entry(entry).get("base_url", "")
    default = credentials.get_default(category)
    return (default or {}).get("base_url", "")


def create_app() -> Flask:
    ensure_dirs()
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/assets_static")
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

    @app.after_request
    def no_cache_for_frontend(resp):
        # Desktop WebView2 can keep the old SPA shell/chunks after local rebuilds,
        # leaving only the app chrome visible while the route body fails to mount.
        # Keep frontend/static/API responses fresh; leave media ranges cacheable
        # enough for playback seeking.
        if not request.path.startswith("/api/output/"):
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    # ── SPA shell ──
    @app.route("/")
    def index():
        return send_from_directory(str(STATIC_DIR), "index.html")

    @app.route("/<path:path>")
    def spa(path):
        target = STATIC_DIR / path
        if target.exists() and target.is_file():
            return send_from_directory(str(STATIC_DIR), path)
        return send_from_directory(str(STATIC_DIR), "index.html")

    # ── health / meta ──
    @app.route("/api/health")
    def health():
        return jsonify({
            "ok": True,
            "service": "vmo-studio",
            "version": APP_VERSION,
            "time": time.time(),
            "uptime_sec": round(time.time() - STARTED_AT, 2),
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "paths": {
                "app": str(APP_DIR),
                "data": str(DATA_DIR),
                "projects": str(PROJECTS_DIR),
                "output": str(OUTPUT_DIR),
                "static": str(STATIC_DIR),
            },
            "checks": {
                "data_dir": DATA_DIR.exists(),
                "projects_dir": PROJECTS_DIR.exists(),
                "output_dir": OUTPUT_DIR.exists(),
                "static_dir": STATIC_DIR.exists(),
                "index_html": (STATIC_DIR / "index.html").exists(),
            },
        })

    @app.route("/api/logs")
    def logs():
        since = int(request.args.get("since_id", 0))
        return jsonify(get_logs(since))

    @app.route("/api/logs/export")
    def logs_export():
        import time as _t
        text = export_text(APP_VERSION)
        fname = f"vmo-studio-logs-{_t.strftime('%Y%m%d-%H%M%S')}.log"
        resp = Response(text, mimetype="text/plain; charset=utf-8")
        resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        return resp

    @app.route("/api/logs/clear", methods=["POST"])
    def logs_clear():
        n = clear_logs()
        logger.info("日志已清空（移除 %d 条）", n)
        return jsonify({"ok": True, "removed": n})

    # ── account auth proxy ──
    @app.route("/api/auth/config", methods=["GET"])
    def auth_config():
        return jsonify({"ok": True, "account_base_url": auth_session.ACCOUNT_BASE_URL, "app_name": "vmo studio"})

    @app.route("/api/auth/me", methods=["GET"])
    def auth_me():
        return jsonify(auth_session.me())

    @app.route("/api/auth/login", methods=["POST"])
    def auth_login():
        body = request.json or {}
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        remember_days = body.get("remember_days", auth_session.DEFAULT_REMEMBER_DAYS)
        if not email or not password:
            return jsonify({"ok": False, "error": "请填写邮箱和密码"}), 400
        try:
            return jsonify(auth_session.login(email, password, remember_days=remember_days))
        except auth_session.AuthSessionError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/auth/register", methods=["POST"])
    def auth_register():
        body = request.json or {}
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        invite_code = (body.get("invite_code") or "").strip()
        nickname = (body.get("nickname") or "").strip()
        remember_days = body.get("remember_days", auth_session.DEFAULT_REMEMBER_DAYS)
        if not email or not password or not invite_code:
            return jsonify({"ok": False, "error": "请填写邮箱、密码和邀请码"}), 400
        try:
            return jsonify(auth_session.register(email, password, invite_code, nickname, remember_days=remember_days))
        except auth_session.AuthSessionError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/auth/logout", methods=["POST"])
    def auth_logout():
        return jsonify(auth_session.logout())

    # ── settings ──
    @app.route("/api/settings", methods=["GET"])
    def get_settings():
        return jsonify(settings.load_settings())

    @app.route("/api/settings", methods=["POST"])
    def post_settings():
        return jsonify(settings.save_settings(request.json or {}))

    # ── credential vault ──
    @app.route("/api/credentials", methods=["GET"])
    def list_creds():
        return jsonify(credentials.list_masked())

    @app.route("/api/credentials/<category>", methods=["POST"])
    def upsert_cred(category):
        try:
            body = request.json or {}
            if category == "video":
                body = _prepare_huajing_credential_body(body)
            return jsonify(credentials.upsert(category, body))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/credentials/<category>/<entry_id>", methods=["DELETE"])
    def delete_cred(category, entry_id):
        credentials.delete(category, entry_id)
        return jsonify({"ok": True})

    @app.route("/api/credentials/<category>/<entry_id>/default", methods=["POST"])
    def default_cred(category, entry_id):
        credentials.set_default(category, entry_id)
        return jsonify({"ok": True})

    @app.route("/api/credentials/<category>/test", methods=["POST"])
    def test_cred(category):
        """Connectivity test. For llm, tries to list models."""
        body = request.json or {}
        base_url = body.get("base_url")
        api_key = body.get("api_key")
        if api_key and "***" in api_key:
            api_key = None  # use stored
        try:
            if category == "llm":
                models = llm.list_models(base_url=base_url, api_key=api_key)
                return jsonify({"ok": True, "models": models[:200]})
            if category == "video":
                if (body.get("provider") or "").strip().lower() == "huajing":
                    body = _prepare_huajing_credential_body(body)
                    base_url = HUAJING_BASE_URL
                    api_key = body.get("api_key") or api_key
                provider = video_gen._detect_provider(base_url or "", body.get("provider", ""))
                if provider == "vidu":
                    api_key = api_key or _credential_api_key(category, body)
                    info = vidu_gen.test_cookie("", api_key)
                    return jsonify({"ok": True, "models": vidu_gen.VIDU_VIDEO_MODEL_OPTIONS, "info": info.get("user")})
                if provider in ("seedance", "seedance_web"):
                    # resolve stored key if the form sent a masked placeholder
                    b, k, _m, _p = video_gen._resolve_creds(base_url, api_key)
                    info = video_gen.seedance_user_info(b, k)
                    return jsonify({"ok": True, "models": [], "info": {
                        "Token": info.get("Token"), "FastToken": info.get("FastToken"),
                        "SdDuration": info.get("SdDuration"),
                    }})
                if provider == "huajing":
                    api_key = api_key or _credential_api_key(category, body)
                    models = video_gen.huajing_list_video_models(base_url, api_key)
                    return jsonify({"ok": True, "models": models[:300], "info": {
                        "token_status": "found",
                        "token_prefix": api_key[:8],
                        "token_length": len(api_key),
                        "base_url": HUAJING_BASE_URL,
                    }})
                if provider in {"doubao_pool", "doubao_pool_intl"}:
                    site = "intl" if provider == "doubao_pool_intl" else "cn"
                    state = doubao_pool.list_pool(site=site)
                    models = doubao_pool.DOUBAO_INTL_MODELS if site == "intl" else doubao_pool.DOUBAO_MODELS
                    return jsonify({"ok": True, "models": models, "info": {
                        "accountCount": state.get("accountCount", 0),
                        "remainingTotal": state.get("remainingTotal", 0),
                        "extensionReady": state.get("extensionReady"),
                        "chromePath": state.get("chromePath"),
                    }})
                api_key = api_key or _credential_api_key(category, body)
                models = _openai_model_list(base_url, api_key)
                return jsonify({"ok": True, "models": models[:300]})
            if category == "image" and (body.get("provider", "").strip().lower() == "vidu"):
                api_key = api_key or _credential_api_key(category, body)
                info = vidu_gen.test_cookie("", api_key)
                return jsonify({"ok": True, "models": vidu_gen.VIDU_IMAGE_MODEL_OPTIONS, "info": info.get("user")})
            # image/image_host (and OpenAI-style video): lightweight reachability
            return jsonify({"ok": True, "models": []})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 200

    @app.route("/api/credentials/<category>/user-info", methods=["POST"])
    def credential_user_info(category):
        body = request.json or {}
        try:
            if category != "video":
                return jsonify({"error": "当前只有生视频 Seedance 接口支持用户首页信息查询"}), 400
            if (body.get("provider") or "").strip().lower() == "huajing":
                body = _prepare_huajing_credential_body(body)
                base_url = HUAJING_BASE_URL
                api_key = body.get("api_key") or _credential_api_key(category, body)
                profile = video_gen.huajing_user_info(base_url, api_key)
                return jsonify({"ok": True, "info": {
                    "token_status": "found",
                    "token_prefix": api_key[:8],
                    "token_length": len(api_key),
                    "base_url": base_url,
                    "id": profile.get("id"),
                    "username": profile.get("username"),
                    "nickname": profile.get("nickname"),
                    "remaining_quota": profile.get("remaining_quota"),
                    "free_quota": profile.get("free_quota"),
                    "used_quota": profile.get("used_quota"),
                    "membership_level": profile.get("membership_level"),
                    "membership_expired_at": profile.get("membership_expired_at"),
                    "status": profile.get("status"),
                    "brand_key": profile.get("brand_key"),
                }})
            base_url = _credential_base_url(category, body)
            api_key = _credential_api_key(category, body)
            provider = video_gen._detect_provider(base_url or "", body.get("provider", ""))
            if provider == "huajing":
                profile = video_gen.huajing_user_info(base_url, api_key)
                return jsonify({"ok": True, "info": {
                    "token_status": "stored",
                    "token_prefix": api_key[:8],
                    "token_length": len(api_key),
                    "base_url": HUAJING_BASE_URL,
                    "id": profile.get("id"),
                    "username": profile.get("username"),
                    "nickname": profile.get("nickname"),
                    "remaining_quota": profile.get("remaining_quota"),
                    "free_quota": profile.get("free_quota"),
                    "used_quota": profile.get("used_quota"),
                    "membership_level": profile.get("membership_level"),
                    "membership_expired_at": profile.get("membership_expired_at"),
                    "status": profile.get("status"),
                    "brand_key": profile.get("brand_key"),
                }})
            if provider in {"doubao_pool", "doubao_pool_intl"}:
                site = "intl" if provider == "doubao_pool_intl" else "cn"
                state = doubao_pool.list_pool(site=site)
                return jsonify({"ok": True, "info": {
                    "accountCount": state.get("accountCount", 0),
                    "remainingTotal": state.get("remainingTotal", 0),
                    "extensionReady": state.get("extensionReady"),
                    "chromePath": state.get("chromePath"),
                    "rootDir": state.get("rootDir"),
                }})
            if provider not in ("seedance", "seedance_web"):
                return jsonify({"error": "该查询仅适用于 Seedance / 飞书文档中的 seedanceapi 用户首页接口"}), 400
            info = video_gen.seedance_user_info(base_url, api_key)
            logger.info(
                "[Seedance][UserIndex] queried user info: token=%s fast=%s sd_duration=%s",
                info.get("Token"), info.get("FastToken"), info.get("SdDuration"),
            )
            return jsonify({"ok": True, "info": info})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 502

    @app.route("/api/llm/models", methods=["POST"])
    def llm_models():
        body = request.json or {}
        api_key = body.get("api_key")
        if api_key and "***" in api_key:
            api_key = None
        try:
            return jsonify({"models": llm.list_models(base_url=body.get("base_url"), api_key=api_key)})
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 502

    @app.route("/api/credentials/<category>/models", methods=["POST"])
    def credential_models(category):
        body = request.json or {}
        try:
            if category == "video" and (body.get("provider") or "").strip().lower() == "huajing":
                body = _prepare_huajing_credential_body(body)
            api_key = body.get("api_key") or _credential_api_key(category, body)
            if category == "llm":
                return jsonify({"models": llm.list_models(base_url=body.get("base_url"), api_key=api_key)})
            if category == "video":
                provider = video_gen._detect_provider(body.get("base_url") or "", body.get("provider", ""))
                if provider == "vidu":
                    return jsonify({"models": vidu_gen.VIDU_VIDEO_MODEL_OPTIONS, "builtin": True})
                if provider in ("seedance", "seedance_web"):
                    return jsonify({"models": video_gen.SEEDANCE_MODELS, "builtin": True})
                if provider == "huajing":
                    return jsonify({"models": video_gen.huajing_list_video_models(body.get("base_url"), api_key), "builtin": True})
                if provider in {"doubao_pool", "doubao_pool_intl"}:
                    models = doubao_pool.DOUBAO_INTL_MODELS if provider == "doubao_pool_intl" else doubao_pool.DOUBAO_MODELS
                    return jsonify({"models": models, "builtin": True})
            if category == "image" and (body.get("provider", "").strip().lower() == "vidu"):
                return jsonify({"models": vidu_gen.VIDU_IMAGE_MODEL_OPTIONS, "builtin": True})
            if category in ("image", "video"):
                return jsonify({"models": _openai_model_list(body.get("base_url"), api_key)})
            return jsonify({"models": []})
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 502

    @app.route("/api/doubao-pool", methods=["GET"])
    def doubao_pool_list():
        site = _doubao_site_from_request()
        _ensure_doubao_pool_credential_for_site(site)
        auto_open = str(request.args.get("autoOpen") or "").lower() in {"1", "true", "yes"}
        if auto_open:
            ready = doubao_pool.ensure_ready(open_browser=True, require_capacity=False, site=site)
            return jsonify({"ok": True, "state": ready.get("state") or doubao_pool.list_pool(site=site), "credentialReady": True, "ready": ready})
        return jsonify({"ok": True, "state": doubao_pool.list_pool(site=site), "credentialReady": True})

    @app.route("/api/doubao-pool/enable-credential", methods=["POST"])
    def doubao_pool_enable_credential():
        result = _ensure_doubao_pool_credential_for_site(_doubao_site_from_request())
        return jsonify({"ok": True, "credential": result})

    @app.route("/api/doubao-pool/extension/prepare", methods=["POST"])
    def doubao_pool_prepare_extension():
        try:
            body = request.json or {}
            return jsonify(doubao_pool.prepare_extension(force=bool(body.get("force", False))))
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/settings", methods=["POST"])
    def doubao_pool_settings():
        try:
            body = request.json or {}
            state = doubao_pool.update_settings(body)
            state = doubao_pool.list_pool(site=doubao_pool.normalize_site(body.get("site") or "cn"))
            return jsonify({"ok": True, "state": state})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/ready", methods=["POST"])
    def doubao_pool_ready():
        body = request.json or {}
        try:
            site = doubao_pool.normalize_site(body.get("site") or body.get("doubaoSite") or "cn")
            _ensure_doubao_pool_credential_for_site(site)
            return jsonify(doubao_pool.ensure_ready(
                account_id=body.get("accountId") or "",
                open_browser=bool(body.get("openBrowser", True)),
                task_id=body.get("taskId") or "",
                site=site,
                require_capacity=bool(body.get("requireCapacity", False)),
                force_restart=bool(body.get("forceRestart", False)),
            ))
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/extension/open-dir", methods=["POST"])
    def doubao_pool_open_extension_dir():
        try:
            return jsonify(doubao_pool.open_extension_dir())
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/diagnose", methods=["POST"])
    def doubao_pool_diagnose():
        try:
            body = request.json or {}
            return jsonify(doubao_pool.diagnose_binding(body.get("accountId") or "", site=body.get("site") or "cn"))
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/accounts", methods=["POST"])
    def doubao_pool_add_account():
        body = request.json or {}
        try:
            site = doubao_pool.normalize_site(body.get("site") or body.get("doubaoSite") or "cn")
            _ensure_doubao_pool_credential_for_site(site)
            state = doubao_pool.add_account(
                body.get("name") or "",
                int(body.get("quota") or doubao_pool.DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA),
                site=site,
            )
            return jsonify({"ok": True, "state": state})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/accounts/import", methods=["POST"])
    def doubao_pool_import_accounts():
        body = request.json or {}
        try:
            site = doubao_pool.normalize_site(body.get("site") or body.get("doubaoSite") or "cn")
            _ensure_doubao_pool_credential_for_site(site)
            return jsonify(doubao_pool.import_accounts(
                body.get("source") or body.get("path") or "",
                quota=int(body.get("quota") or doubao_pool.DOUBAO_DEFAULT_ACCOUNT_VIDEO_QUOTA),
                include_profiles=bool(body.get("includeProfiles", True)),
                site=site,
            ))
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/accounts/export", methods=["GET"])
    def doubao_pool_export_accounts():
        try:
            site = doubao_pool.normalize_site(request.args.get("site") or "cn")
            zip_path = doubao_pool.export_accounts(site=site)
            return send_file(
                str(zip_path),
                as_attachment=True,
                download_name=zip_path.name,
                mimetype="application/zip",
            )
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/accounts/<account_id>", methods=["PATCH"])
    def doubao_pool_update_account(account_id):
        try:
            body = request.json or {}
            state = doubao_pool.update_account(account_id, body, site=body.get("site") or "cn")
            return jsonify({"ok": True, "state": state})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/accounts/<account_id>/reset-usage", methods=["POST"])
    def doubao_pool_reset_account_usage(account_id):
        body = request.json or {}
        try:
            state = doubao_pool.reset_account_usage(
                account_id,
                used=int(body.get("used") or 0),
                clear_remote=bool(body.get("clearRemote", True)),
                site=body.get("site") or "cn",
            )
            return jsonify({"ok": True, "state": state})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/accounts/reset-usage", methods=["POST"])
    def doubao_pool_reset_all_account_usage():
        body = request.json or {}
        try:
            state = doubao_pool.reset_account_usage(
                "",
                used=int(body.get("used") or 0),
                clear_remote=bool(body.get("clearRemote", True)),
                site=body.get("site") or "cn",
            )
            return jsonify({"ok": True, "state": state})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/accounts/<account_id>", methods=["DELETE"])
    def doubao_pool_remove_account(account_id):
        try:
            site = (request.json or {}).get("site") if request.is_json else request.args.get("site")
            state = doubao_pool.remove_account(account_id, site=site or "cn")
            return jsonify({"ok": True, "state": state})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/root", methods=["POST"])
    def doubao_pool_set_root():
        try:
            body = request.json or {}
            state = doubao_pool.set_root_dir(body.get("rootDir"), site=body.get("site") or "cn")
            return jsonify({"ok": True, "state": state})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/doubao-pool/open", methods=["POST"])
    def doubao_pool_open():
        body = request.json or {}
        try:
            site = doubao_pool.normalize_site(body.get("site") or body.get("doubaoSite") or "cn")
            _ensure_doubao_pool_credential_for_site(site)
            return jsonify(doubao_pool.open_account(
                account_id=body.get("accountId") or "",
                rotate=bool(body.get("rotate", True)),
                site=site,
            ))
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 400

    routes_projects.register(app)

    logger.info("Flask app created")
    return app


app = create_app()
