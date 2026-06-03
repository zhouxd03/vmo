"""Reference-image assembly: priority capping + labeled prompt legend.

Requirements (Phase 8F):
  - The total number of reference images (垫图) fed to the model for a single
    shot must not exceed a configurable cap (``settings.max_reference_images``,
    default 8).
  - Continuity-generated images (上一镜尾帧 → 首帧, 导演图, 站位图) must be
    referenced explicitly, and the prompt must state whether each image is used
    as the FIRST FRAME (首帧) or merely as a REFERENCE (参考).
  - When the cap is exceeded, the lowest-priority images are dropped first, in
    the fallback order:
        导演图/首帧图 ＞ 角色图 ＞ 背景(场景)图 ＞ 配角图 ＞ 道具图

A "reference item" is a plain dict:
    {
      "b64":   "<base64 png/jpg>",   # required
      "role":  "first_frame" | "director" | "staging" |
               "character" | "scene" | "support" | "prop",
      "name":  "顾沉",               # optional, shown in the legend
      "asset_id": "ab12cd34",        # optional, links back to an asset material
      "old_ph":  "@image2",          # optional, the placeholder already baked
                                     # into the shot prompt by resolve_mentions
    }
"""

import re
from typing import Optional

_PH_RE = re.compile(r"@image\d+")

# lower rank = higher priority (kept first when capping)
ROLE_RANK = {
    "first_frame": 0,   # 上一镜尾帧作为首帧
    "director": 0,      # 导演图
    "staging": 0,       # 站位图
    "character": 1,     # 角色图（主角）
    "scene": 2,         # 背景 / 场景图
    "support": 3,       # 配角图
    "prop": 4,          # 道具图
}

ROLE_LABEL = {
    "first_frame": "首帧画面（作为首帧）",
    "director": "导演图（作为参考）",
    "staging": "站位图（作为参考）",
    "character": "角色参考",
    "scene": "场景参考",
    "support": "配角参考",
    "prop": "道具参考",
}

_FIRST_FRAME_ROLES = {"first_frame"}


def material_role(material: dict) -> str:
    """Map an asset material (from resolve_mentions) to a priority role."""
    trig = material.get("trigger")
    if trig == "#":
        return "scene"
    if trig == "$":
        return "prop"
    # character (@) — main vs support drives 角色图 ＞ 配角图
    return "support" if (material.get("role") == "support") else "character"


def cap(items: list, max_n: int) -> list:
    """Return the highest-priority *max_n* items (stable within a priority tier)."""
    items = [it for it in (items or []) if it.get("b64")]
    if max_n is None or max_n <= 0:
        return items
    # Python's sort is stable, so equal-rank items keep their insertion order.
    ordered = sorted(items, key=lambda it: ROLE_RANK.get(it.get("role"), 99))
    return ordered[:max_n]


def has_first_frame(kept: list) -> bool:
    return any(it.get("role") in _FIRST_FRAME_ROLES for it in (kept or []))


def build_legend(kept: list) -> str:
    """Human + model-readable legend mapping @imageN → role/usage."""
    parts = []
    for i, it in enumerate(kept or [], 1):
        label = ROLE_LABEL.get(it.get("role"), "参考")
        name = it.get("name")
        parts.append(f"@image{i}={label}" + (f"：{name}" if name else ""))
    return "；".join(parts)


def compose_prompt(base_text: str, materials: list, kept: list) -> str:
    """Prepend a reference legend to *base_text* and renumber its placeholders.

    *materials* are the asset materials originally produced by resolve_mentions
    (each may carry an ``@imageN`` placeholder baked into *base_text*). For the
    final *kept* set we:
      - remap surviving asset placeholders to their new @imageN position,
      - replace dropped asset placeholders with the asset name (so the prompt
        text stays readable instead of dangling a stale @imageN),
      - prepend a legend describing every kept image's role / usage.
    """
    text = base_text or ""

    # new placeholder per kept item (by asset_id when present)
    new_ph_by_asset = {}
    for i, it in enumerate(kept or [], 1):
        if it.get("asset_id"):
            new_ph_by_asset[it["asset_id"]] = f"@image{i}"

    # build a single old-placeholder → replacement map (survivors remap to their
    # new @imageN; dropped ones fall back to the asset name) so the rewrite is a
    # single pass and old/new tokens can't collide with each other.
    repl = {}
    for m in (materials or []):
        old_ph = m.get("placeholder")
        if not old_ph:
            continue
        new_ph = new_ph_by_asset.get(m.get("asset_id"))
        repl[old_ph] = new_ph if new_ph else (m.get("name") or "")
    if repl:
        text = _PH_RE.sub(lambda mo: repl.get(mo.group(0), mo.group(0)), text)

    legend = build_legend(kept)
    if not legend:
        return text
    head = (
        "【参考图说明】以下 @imageN 依次对应随附的参考图，请严格保持其形象/场景一致；"
        "标注「作为首帧」的请用作视频首帧画面，其余仅作参考：" + legend
    )
    return head + "\n" + text if text else head
