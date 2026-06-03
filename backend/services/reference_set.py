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


def token_for(it: dict) -> str:
    """Unified @ token for a reference item. 连续性图用固定别名，资产用 @名。"""
    role = it.get("role")
    if role == "first_frame":
        return "@首帧"
    if role == "director":
        return "@导演图"
    if role == "staging":
        return "@站位图"
    name = (it.get("name") or "").strip()
    return ("@" + name) if name else "@参考"


def _sentence_for(role: str, token: str, kind: str) -> str:
    """Reference explanation phrasing per role (放在提示词前方的引用说明)。"""
    is_video = (kind == "video")
    if role == "character":
        return f"{token} 是主要角色"
    if role == "support":
        return f"{token} 是配角"
    if role == "scene":
        return f"{token} 是背景/场景"
    if role == "prop":
        return f"{token} 是道具"
    if role == "director":
        return (f"参考 {token} 生成视频，遵循其构图、分镜与镜头语言"
                if is_video else f"参考 {token} 确定画面构图与分镜")
    if role == "staging":
        return (f"生成视频时参考 {token} 确定角色站位"
                if is_video else f"参考 {token} 确定角色站位关系")
    if role == "first_frame":
        return f"{token} 作为视频首帧画面，从该画面自然运动起势"
    return f"{token} 作为参考"


def build_legend(kept: list, kind: str = "video") -> tuple[list, str]:
    """Return (ordered tokens, joined reference-explanation sentences)."""
    tokens = [token_for(it) for it in (kept or [])]
    sentences = "；".join(
        _sentence_for(it.get("role"), tok, kind)
        for it, tok in zip(kept or [], tokens)
    )
    return tokens, sentences


def compose_prompt(base_text: str, materials: list, kept: list,
                   kind: str = "video") -> str:
    """Prepend a unified @ reference legend to *base_text*.

    生成提示词里所有引用统一 @：资产 @名、连续性图 @导演图/@站位图/@首帧。
    *base_text* 已由 resolve_mentions 把提及替换成 @名（即各 material 的
    placeholder）。对最终 *kept* 集：
      - 留存的资产 @名 原样保留（已在文中）；
      - 被裁掉的资产把 @名 还原为纯名字（去掉 @，避免悬空引用无配图）；
      - 在最前面注入「随附参考图依次为 …」+ 逐条引用说明文本。
    """
    text = base_text or ""

    # dropped assets → strip their @名 back to bare name (longest token first so
    # @林夏 is handled before @林).
    kept_ids = {it.get("asset_id") for it in (kept or []) if it.get("asset_id")}
    drop_repl = {}
    for m in (materials or []):
        ph = m.get("placeholder")
        if ph and m.get("asset_id") not in kept_ids:
            drop_repl[ph] = m.get("name") or ph.lstrip("@")
    for ph in sorted(drop_repl, key=len, reverse=True):
        text = text.replace(ph, drop_repl[ph])

    tokens, sentences = build_legend(kept, kind)
    if not tokens:
        return text
    head = (
        "【参考图引用】随附参考图依次为：" + "、".join(tokens) + "。" + sentences +
        "。务必严格保持各 @ 引用对象的形象/服装/场景/道具与参考图一致。"
    )
    return head + "\n" + text if text else head
