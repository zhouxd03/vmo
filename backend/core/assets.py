"""Asset library (Phase 3): characters / scenes / props with @ / # / $ triggers.

Assets live inside a project (project["assets"]) so they share the project's
lifecycle and numbering. Each asset can own a reference image (generated or
uploaded) which downstream generation feeds to the model as a material.

Mirrors xingyao's real mechanism (verified from source):
  @ = character, # = scene, $ = prop  → resolved to reference-image materials,
  in-text mentions replaced by positional placeholders @image1/@image2/…
"""

import re
import threading
import time
import uuid
from typing import Any, Optional

from . import projects

# Serialize asset read-modify-write so concurrent一键生成 workers (ThreadPool)
# don't clobber each other's project.json updates (lost-write race).
_write_lock = threading.RLock()

TRIGGERS = {"character": "@", "scene": "#", "prop": "$"}
TYPE_BY_TRIGGER = {v: k for k, v in TRIGGERS.items()}


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def list_assets(pid: str) -> list:
    p = projects.get_project(pid)
    if not p:
        return []
    return p.get("assets", [])


def _save_assets(pid: str, assets: list) -> Optional[dict]:
    return projects.update_project(pid, {"assets": assets})


def add_asset(pid: str, asset_type: str, name: str, desc: str = "", appearance: str = "",
              role: str = "main") -> dict:
    if asset_type not in TRIGGERS:
        raise ValueError(f"未知资产类型: {asset_type}")
    name = (name or "").strip()
    if not name:
        raise ValueError("资产名称不能为空")
    assets = list_assets(pid)
    if any(a["type"] == asset_type and a["name"] == name for a in assets):
        raise ValueError(f"同类型资产「{name}」已存在")
    asset = {
        "id": _new_id(),
        "type": asset_type,
        "trigger": TRIGGERS[asset_type],
        "name": name,
        "desc": desc or "",
        "appearance": appearance or "",
        # main vs support — only meaningful for characters; drives reference-image
        # priority when capping (角色图 ＞ 配角图).
        "role": role if role in ("main", "support") else "main",
        "ref_image": None,   # filename under data/projects/<pid>/assets/
        "created_at": time.time(),
    }
    assets.append(asset)
    _save_assets(pid, assets)
    return asset


def update_asset(pid: str, asset_id: str, patch: dict[str, Any]) -> Optional[dict]:
    with _write_lock:
        assets = list_assets(pid)
        found = None
        for a in assets:
            if a["id"] == asset_id:
                for k in ("name", "desc", "appearance", "ref_image", "role",
                          "aerial_image", "ref_source"):
                    if k in patch:
                        a[k] = patch[k]
                found = a
                break
        if found:
            _save_assets(pid, assets)
        return found


def delete_asset(pid: str, asset_id: str) -> None:
    assets = [a for a in list_assets(pid) if a["id"] != asset_id]
    _save_assets(pid, assets)


def seed_from_bible(pid: str) -> list:
    """Populate assets from a project's StoryBible (characters/scenes/props).

    Idempotent on name: existing assets of the same type+name are kept.
    """
    p = projects.get_project(pid)
    if not p:
        return []
    bible = p.get("story_bible") or {}
    assets = p.get("assets", [])
    existing = {(a["type"], a["name"]) for a in assets}

    def _add(asset_type, name, desc, appearance=""):
        key = (asset_type, (name or "").strip())
        if not key[1] or key in existing:
            return
        assets.append({
            "id": _new_id(),
            "type": asset_type,
            "trigger": TRIGGERS[asset_type],
            "name": key[1],
            "desc": desc or "",
            "appearance": appearance or "",
            "ref_image": None,
            "created_at": time.time(),
        })
        existing.add(key)

    for c in bible.get("characters", []) or []:
        _add("character", c.get("name", ""), c.get("note", ""), c.get("appearance", ""))
    for s in bible.get("scenes", []) or []:
        _add("scene", s.get("name", ""), s.get("desc", ""))
    for pr in bible.get("props", []) or []:
        _add("prop", pr.get("name", ""), pr.get("desc", ""))

    _save_assets(pid, assets)
    return assets


# ── @ / # / $ auto reference resolution ─────────────────────────────────────
def resolve_mentions(text: str, assets: list) -> dict:
    """Scan text, auto-detect referenced assets, build materials + placeholder text.

    Returns {
      "materials": [{asset_id, trigger, type, role, name, ref_image, placeholder}],
      "text": text with each mention replaced by a unified @名 token,
      "warnings": ["引用了 @X 但缺少参考图"],
    }
    Matches by explicit "@name"/"#name"/"$name" first, then by bare name.

    生成提示词里所有引用统一用 ``@`` 前缀（``@角色``/``@背景``/``@道具``）——库内
    #场景/$道具 仅作分类，喂给生成模型时一律 @名。库分类符先被替换成与名字无关的
    哨兵，最后再一次性映射为 @名，避免名字互为子串时的二次替换串台。
    """
    text = text or ""
    materials: list = []
    warnings: list = []
    seen: dict = {}
    sentinels: dict = {}

    def _token_for(asset) -> str:
        if asset["id"] in seen:
            return seen[asset["id"]]
        token = "@" + asset["name"]          # 统一 @名（不论 @/#/$ 分类）
        seen[asset["id"]] = token
        materials.append({
            "asset_id": asset["id"],
            "trigger": asset["trigger"],
            "type": asset.get("type"),
            "role": asset.get("role") or "main",
            "name": asset["name"],
            "ref_image": asset.get("ref_image"),
            "placeholder": token,
        })
        if not asset.get("ref_image"):
            warnings.append(f"引用了 {asset['trigger']}{asset['name']} 但缺少参考图，将跳过垫图")
        return token

    # longer names first to avoid partial shadowing
    ordered = sorted(assets, key=lambda a: len(a["name"]), reverse=True)
    for idx, a in enumerate(ordered):
        name = re.escape(a["name"])
        trig = re.escape(a["trigger"])
        pat_explicit = re.compile(trig + name)   # @name / #name / $name
        pat_bare = re.compile(name)              # bare name
        if pat_explicit.search(text) or pat_bare.search(text):
            _token_for(a)
            sent = f"\x00M{idx}\x00"             # 名字无关的哨兵，杜绝子串串台
            sentinels[sent] = seen[a["id"]]
            text = pat_explicit.sub(sent, text)
            text = pat_bare.sub(sent, text)
    for sent, token in sentinels.items():
        text = text.replace(sent, token)
    return {"materials": materials, "text": text, "warnings": warnings}


def auto_tag(text: str, assets: list) -> list:
    """Return assets whose name appears in text (no text mutation)."""
    text = text or ""
    hits = []
    for a in assets:
        if a["name"] and a["name"] in text:
            hits.append({"id": a["id"], "trigger": a["trigger"], "name": a["name"], "type": a["type"]})
    return hits
