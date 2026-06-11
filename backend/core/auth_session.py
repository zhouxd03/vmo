"""Local proxy/session store for vmo studio account authentication."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .paths import AUTH_SESSION_FILE, DATA_DIR

ACCOUNT_BASE_URL = os.environ.get("VMO_ACCOUNT_BASE_URL", "https://account.5679678.xyz").rstrip("/")
TIMEOUT = 30
DEFAULT_REMEMBER_DAYS = 7
MAX_REMEMBER_DAYS = 90
DEV_USER = {"id": "dev-local", "email": "dev@local", "nickname": "本地开发"}


class AuthSessionError(RuntimeError):
    """Raised when the account service or local session cannot be used."""


def is_dev_auth_bypass() -> bool:
    return os.environ.get("VMO_DEV_AUTH_BYPASS", "").strip().lower() in {"1", "true", "yes", "on"}


def _dev_auth_response() -> dict:
    return {
        "ok": True,
        "user": DEV_USER,
        "remember_days": MAX_REMEMBER_DAYS,
        "account_base_url": ACCOUNT_BASE_URL,
        "dev_auth_bypass": True,
    }


def _read() -> dict[str, Any]:
    try:
        if AUTH_SESSION_FILE.exists():
            return json.loads(AUTH_SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _write(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _remember_days(value: Any) -> int:
    try:
        days = int(value)
    except Exception:
        days = DEFAULT_REMEMBER_DAYS
    return max(1, min(MAX_REMEMBER_DAYS, days))


def clear() -> None:
    try:
        AUTH_SESSION_FILE.unlink()
    except FileNotFoundError:
        pass


def _friendly_error(code: str) -> str:
    mapping = {
        "invalid_credentials": "账号或密码不正确",
        "user_disabled": "账号已被停用",
        "invite_not_found": "邀请码不存在",
        "invite_used": "邀请码已被使用",
        "invite_expired": "邀请码已过期",
        "email_exists": "该邮箱已注册",
        "captcha_failed": "人机验证失败，请联系管理员处理注册",
        "too_many_login_attempts": "登录尝试过多，请稍后再试",
        "too_many_registrations": "注册尝试过多，请稍后再试",
        "refresh_revoked": "登录已过期，请重新登录",
    }
    return mapping.get(code, code)


def _extract_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200] or f"HTTP {resp.status_code}"
    detail = data.get("detail") or data.get("error") or data.get("message")
    if isinstance(detail, dict):
        detail = detail.get("code") or detail.get("message") or json.dumps(detail, ensure_ascii=False)
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            detail = first.get("msg") or first.get("message") or str(first)
    return str(detail or f"HTTP {resp.status_code}")


def _request(method: str, path: str, *, token: str = "", json_body: dict | None = None) -> dict:
    headers = {"User-Agent": "vmo-studio-desktop/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{ACCOUNT_BASE_URL}{path}"
    try:
        resp = requests.request(method, url, json=json_body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise AuthSessionError(f"账号服务器连接失败: {e}") from e
    if resp.status_code >= 400:
        raise AuthSessionError(_friendly_error(_extract_error(resp)))
    if resp.status_code == 204:
        return {}
    try:
        return resp.json()
    except ValueError as e:
        raise AuthSessionError("账号服务器返回了非 JSON 响应") from e


def _save_auth(payload: dict, *, remember_days: int = DEFAULT_REMEMBER_DAYS) -> dict:
    user = payload.get("user") or {}
    tokens = payload.get("tokens") or {}
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise AuthSessionError("账号服务器响应缺少 token")
    now = time.time()
    remember_days = _remember_days(remember_days)
    session = {
        "user": user,
        "tokens": tokens,
        "saved_at": now,
        "last_refresh_at": now,
        "remember_days": remember_days,
        "account_base_url": ACCOUNT_BASE_URL,
    }
    _write(session)
    return {"ok": True, "user": user, "remember_days": remember_days, "account_base_url": ACCOUNT_BASE_URL}


def login(email: str, password: str, *, remember_days: int = DEFAULT_REMEMBER_DAYS) -> dict:
    if is_dev_auth_bypass():
        return _dev_auth_response()
    payload = _request("POST", "/auth/login", json_body={"email": email, "password": password})
    return _save_auth(payload, remember_days=remember_days)


def register(
    email: str,
    password: str,
    invite_code: str,
    nickname: str = "",
    *,
    remember_days: int = DEFAULT_REMEMBER_DAYS,
) -> dict:
    if is_dev_auth_bypass():
        return _dev_auth_response()
    body = {
        "email": email,
        "password": password,
        "invite_code": invite_code,
        "nickname": nickname or None,
    }
    return _save_auth(_request("POST", "/auth/register", json_body=body), remember_days=remember_days)


def refresh() -> dict:
    if is_dev_auth_bypass():
        return {"access_token": "dev-local-access", "refresh_token": "dev-local-refresh"}
    session = _read()
    refresh_token = (session.get("tokens") or {}).get("refresh_token")
    if not refresh_token:
        raise AuthSessionError("请先登录")
    tokens = _request("POST", "/auth/refresh", json_body={"refresh_token": refresh_token})
    session["tokens"] = tokens
    session["last_refresh_at"] = time.time()
    _write(session)
    return tokens


def _refresh_if_due(session: dict[str, Any]) -> str:
    tokens = session.get("tokens") or {}
    token = tokens.get("access_token", "")
    remember_days = _remember_days(session.get("remember_days", DEFAULT_REMEMBER_DAYS))
    last_refresh_at = float(session.get("last_refresh_at") or session.get("saved_at") or 0)
    if token and time.time() - last_refresh_at < remember_days * 86400:
        return token
    return refresh().get("access_token", token)


def me() -> dict:
    if is_dev_auth_bypass():
        return _dev_auth_response()
    session = _read()
    token = (session.get("tokens") or {}).get("access_token")
    if not token:
        return {"ok": False, "user": None, "account_base_url": ACCOUNT_BASE_URL}
    try:
        token = _refresh_if_due(session)
        user = _request("GET", "/me", token=token)
    except AuthSessionError:
        try:
            token = refresh().get("access_token", "")
            user = _request("GET", "/me", token=token)
        except AuthSessionError:
            clear()
            return {"ok": False, "user": None, "account_base_url": ACCOUNT_BASE_URL}
    session = _read()
    session["user"] = user
    _write(session)
    return {
        "ok": True,
        "user": user,
        "remember_days": _remember_days(session.get("remember_days", DEFAULT_REMEMBER_DAYS)),
        "account_base_url": ACCOUNT_BASE_URL,
    }


def logout() -> dict:
    if is_dev_auth_bypass():
        return {"ok": True, "dev_auth_bypass": True}
    session = _read()
    refresh_token = (session.get("tokens") or {}).get("refresh_token")
    if refresh_token:
        try:
            _request("POST", "/auth/logout", json_body={"refresh_token": refresh_token})
        except AuthSessionError:
            pass
    clear()
    return {"ok": True}
