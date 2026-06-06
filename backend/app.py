"""Flask application for the Batch Anime Creation desktop tool.

Serves the built Vue frontend and exposes the JSON API. Reuses shengtu's
generation request logic (refactored into backend/services). Designed to run
inside a PyWebview frameless window (see run_desktop.py) or standalone in a
browser for development.
"""

import logging
import os
import sys
import time

import requests as http
from flask import Flask, Response, jsonify, request, send_from_directory

from . import routes_projects
from .core import credentials, settings
from .core.logbus import clear_logs, export_text, get_logs, setup_logging
from .core.paths import APP_DIR, DATA_DIR, OUTPUT_DIR, PROJECTS_DIR, STATIC_DIR, ensure_dirs
from .services import llm, video_gen, vidu_gen

logger = setup_logging()
STARTED_AT = time.time()
APP_VERSION = "1.0.1"

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
            "service": "batch-studio",
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
        fname = f"batch-studio-logs-{_t.strftime('%Y%m%d-%H%M%S')}.log"
        resp = Response(text, mimetype="text/plain; charset=utf-8")
        resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        return resp

    @app.route("/api/logs/clear", methods=["POST"])
    def logs_clear():
        n = clear_logs()
        logger.info("日志已清空（移除 %d 条）", n)
        return jsonify({"ok": True, "removed": n})

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
            return jsonify(credentials.upsert(category, request.json or {}))
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
            base_url = _credential_base_url(category, body)
            api_key = _credential_api_key(category, body)
            provider = video_gen._detect_provider(base_url or "", body.get("provider", ""))
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
        api_key = _credential_api_key(category, body)
        try:
            if category == "llm":
                return jsonify({"models": llm.list_models(base_url=body.get("base_url"), api_key=api_key)})
            if category == "video":
                provider = video_gen._detect_provider(body.get("base_url") or "", body.get("provider", ""))
                if provider == "vidu":
                    return jsonify({"models": vidu_gen.VIDU_VIDEO_MODEL_OPTIONS, "builtin": True})
                if provider in ("seedance", "seedance_web"):
                    return jsonify({"models": video_gen.SEEDANCE_MODELS, "builtin": True})
            if category == "image" and (body.get("provider", "").strip().lower() == "vidu"):
                return jsonify({"models": vidu_gen.VIDU_IMAGE_MODEL_OPTIONS, "builtin": True})
            if category in ("image", "video"):
                return jsonify({"models": _openai_model_list(body.get("base_url"), api_key)})
            return jsonify({"models": []})
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 502

    routes_projects.register(app)

    logger.info("Flask app created")
    return app


app = create_app()
