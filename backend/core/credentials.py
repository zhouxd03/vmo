"""Credential Vault — unified, categorized API key management.

Replaces shengtu's flat `api_key` / `video_api_key` in config.json. Every place
in the app that needs a key pulls it from here by category. Categories:
  - image       : 文生图 / 垫图 (OpenAI-compatible images API)
  - video       : 文生视频 / 图生视频 (external video API, base_url + key)
  - llm         : 剧本分析 / 多模态复核 (OpenAI-compatible chat API)
  - image_host  : 图床 (optional token-based hosts)

Each category holds a list of credential entries; one is marked default.
Stored locally only (single-machine, no cloud). Light obfuscation only — this
is not a secret manager; the file lives on the user's own machine.
"""

import base64
import uuid
from typing import Any, Optional

from .paths import CREDENTIALS_FILE
from .store import read_json, write_json

CATEGORIES = ["image", "video", "llm", "image_host"]

_DEFAULT: dict[str, list] = {c: [] for c in CATEGORIES}


def _obfuscate(value: str) -> str:
    if not value:
        return ""
    return "b64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _deobfuscate(value: str) -> str:
    if isinstance(value, str) and value.startswith("b64:"):
        try:
            return base64.b64decode(value[4:]).decode("utf-8")
        except Exception:
            return ""
    return value or ""


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return "***"
    return key[:6] + "***" + key[-4:]


def load_raw() -> dict:
    data = read_json(CREDENTIALS_FILE, dict(_DEFAULT))
    for c in CATEGORIES:
        data.setdefault(c, [])
    return data


def _resolve_entry(entry: dict) -> dict:
    """Return entry with the real api_key decoded (internal use)."""
    e = dict(entry)
    e["api_key"] = _deobfuscate(entry.get("api_key", ""))
    return e


def get_default(category: str) -> Optional[dict]:
    """Return the resolved (decoded) default credential for a category, or None."""
    data = load_raw()
    entries = data.get(category, [])
    if not entries:
        return None
    chosen = next((e for e in entries if e.get("is_default")), entries[0])
    return _resolve_entry(chosen)


def list_masked() -> dict:
    """Return all credentials with masked keys, safe for the frontend."""
    data = load_raw()
    out: dict[str, list] = {}
    for c in CATEGORIES:
        out[c] = []
        for e in data.get(c, []):
            out[c].append({
                "id": e.get("id"),
                "alias": e.get("alias", ""),
                "base_url": e.get("base_url", ""),
                "model": e.get("model", ""),
                "provider": e.get("provider", ""),
                "managed": e.get("provider", "") in {"doubao_pool", "doubao_pool_intl"},
                "enabled": e.get("enabled", True),
                "is_default": e.get("is_default", False),
                "api_key_masked": _mask(_deobfuscate(e.get("api_key", ""))),
                "note": e.get("note", ""),
            })
    return out


def upsert(category: str, entry: dict) -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"未知凭据类别: {category}")
    data = load_raw()
    entries = data[category]
    eid = entry.get("id")
    record = {
        "id": eid or uuid.uuid4().hex[:12],
        "alias": entry.get("alias", "").strip(),
        "base_url": entry.get("base_url", "").strip(),
        "model": entry.get("model", "").strip(),
        "provider": entry.get("provider", "").strip(),
        "enabled": bool(entry.get("enabled", True)),
        "is_default": bool(entry.get("is_default", False)),
        "note": entry.get("note", ""),
    }
    # Only overwrite the key if a new (non-masked) value is provided.
    new_key = entry.get("api_key", "")
    if eid:
        existing = next((e for e in entries if e.get("id") == eid), None)
        if existing and (not new_key or "***" in new_key):
            record["api_key"] = existing.get("api_key", "")
        else:
            record["api_key"] = _obfuscate(new_key)
        entries[:] = [record if e.get("id") == eid else e for e in entries]
        if not any(e.get("id") == eid for e in entries):
            entries.append(record)
    else:
        record["api_key"] = _obfuscate(new_key)
        entries.append(record)

    if record["is_default"]:
        for e in entries:
            e["is_default"] = e.get("id") == record["id"]
    elif not any(e.get("is_default") for e in entries) and entries:
        entries[0]["is_default"] = True

    write_json(CREDENTIALS_FILE, data)
    return {"id": record["id"]}


def delete(category: str, entry_id: str) -> None:
    data = load_raw()
    entries = data.get(category, [])
    entries[:] = [e for e in entries if e.get("id") != entry_id]
    if entries and not any(e.get("is_default") for e in entries):
        entries[0]["is_default"] = True
    write_json(CREDENTIALS_FILE, data)


def set_default(category: str, entry_id: str) -> None:
    data = load_raw()
    for e in data.get(category, []):
        e["is_default"] = e.get("id") == entry_id
    write_json(CREDENTIALS_FILE, data)
