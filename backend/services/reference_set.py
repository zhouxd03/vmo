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

# lower rank = higher priority (kept first when capping)
ROLE_RANK = {
    "first_frame": 0,   # 上一镜尾帧作为首帧
    "director": 0,      # 导演图
    "staging": 0,       # 站位图
    "character": 1,     # 角色图（主角）
    "scene": 2,         # 背景 / 场景图
    "scene_aerial": 2,  # 场景鸟瞰图（紧随主场景图，稳定排序保证主图在前）
    "support": 3,       # 配角图
    "prop": 4,          # 道具图
}

ROLE_LABEL = {
    "first_frame": "首帧画面（作为首帧）",
    "director": "导演图（作为参考）",
    "staging": "站位图（作为参考）",
    "character": "角色参考",
    "scene": "场景参考",
    "scene_aerial": "场景鸟瞰图（固定空间位置）",
    "support": "配角参考",
    "prop": "道具参考",
}

_FIRST_FRAME_ROLES = {"first_frame"}


def clean_ref_name(value) -> str:
    return str(value or "").lstrip("@#$").strip()


def ordered_names(values) -> list[str]:
    out, seen = [], set()
    for value in values or []:
        name = clean_ref_name(value)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def shot_sort_key(shot: dict, item: dict) -> tuple:
    """Canonical reference order used by preview, prompt numbering, and upload.

    The order is based on the decomposed shot fields, never on raw mention
    detection order:
      characters[] -> scene -> props[] -> other refs.
    Continuity refs should be appended by the caller in the explicit decision
    order after asset refs.
    """
    role = item.get("role") or ""
    name = clean_ref_name(item.get("name"))
    characters = ordered_names((shot or {}).get("characters") or (shot or {}).get("characters_ref"))
    scene = clean_ref_name((shot or {}).get("scene") or (shot or {}).get("scene_ref"))
    props = ordered_names((shot or {}).get("props"))
    if role in {"character", "support"}:
        idx = characters.index(name) if name in characters else 999
        return (0, idx, name)
    if role == "scene":
        idx = 0 if scene and name == scene else 999
        return (1, idx, name)
    if role == "prop":
        idx = props.index(name) if name in props else 999
        return (2, idx, name)
    return (3, ROLE_RANK.get(role, 99), name)


def sort_for_shot(items: list, shot: dict) -> list:
    return sorted(list(items or []), key=lambda it: shot_sort_key(shot or {}, it))


def material_role(material: dict) -> str:
    """Map an asset material (from resolve_mentions) to a priority role."""
    trig = material.get("trigger")
    if trig == "#":
        return "scene"
    if trig == "$":
        return "prop"
    # character (@) — main vs support drives 角色图 ＞ 配角图
    return "support" if (material.get("role") == "support") else "character"


def cap(items: list, max_n: int, require_b64: bool = True) -> list:
    """Return the highest-priority *max_n* items (stable within a priority tier).

    require_b64=True（生成时）：只保留真正随附了 b64 的图，与最终发送的图片数组一一
    对应。False（工作台预览）：按相同优先级排序/裁剪资产元数据（不读 b64），使预览
    的【画面定义】@图N 编号与实际发送数组逐位一致。"""
    if require_b64:
        items = [it for it in (items or []) if it.get("b64")]
    else:
        items = list(items or [])
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
    if role == "scene_aerial":
        return "@场景鸟瞰图"
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
    if role == "scene_aerial":
        return f"{token} 是场景鸟瞰俯视图，用于固定场景空间位置与机位布局"
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


def _definition_desc(it: dict, kind: str) -> str:
    """「@图N 是 …」定义行里 `是` 之后的说明文本（按角色/资产名）。"""
    role = it.get("role")
    name = (it.get("name") or "").strip()
    voice = (it.get("voice") or "").strip()
    is_video = (kind == "video")
    if role == "director":
        return ("是导演图，请参考该图中的构图、分镜与镜头语言生成剧情视频"
                if is_video else "是导演图，请参考该图确定画面构图与分镜")
    if role == "staging":
        return "是站位图，仅用于确定各角色站位、左右关系、视线方向与空间相对位置；不要照抄其俯视图视角、草图画风或图中文字"
    if role == "first_frame":
        return "是上一镜尾帧，作为本镜视频首帧画面，从该画面自然运动起势"
    if role == "scene":
        return f"是 {name}" if name else "是 背景/场景"
    if role == "scene_aerial":
        base = f"是 {name} 的鸟瞰俯视图" if name else "是场景鸟瞰俯视图"
        return base + "，用于固定场景空间位置与机位布局，请保持与场景一致"
    if role == "prop":
        return f"是 {name}" if name else "是 道具"
    if role == "support":
        base = f"是 {name}" if name else "是 配角"
        return base + (f"（音色为：{voice}）" if voice else "")
    # character (@主角)
    base = f"是 {name}" if name else "是 主要角色"
    return base + (f"（音色为：{voice}）" if voice else "")


_DEF_HEAD = "【画面定义】（@图N 按 reference_image_urls 数组顺序编号；仅说明随附参考图的对应关系，不作为画面内容/文字输出）"
_DEF_TAIL = "务必严格保持各 @图 对象的形象/服装/场景/道具与参考图一致。"
# 用于幂等：识别并剥离已存在于文本开头的【画面定义】块（含其后的空行）。
_DEF_BLOCK_RE = re.compile(
    r"^\s*" + re.escape(_DEF_HEAD) + r".*?" + re.escape(_DEF_TAIL) + r"\s*",
    re.DOTALL,
)
_DEF_FALLBACK_RE = re.compile(
    r"^\s*【画面定义】[^\n]*(?:\n@图\d+[^\n]*)*(?:\n" + re.escape(_DEF_TAIL) + r")?\s*",
    re.DOTALL,
)


def strip_definition_block(text: str) -> str:
    """移除文本开头已有的【画面定义】块（若有），保证 compose_prompt 幂等——
    无论可编辑/预览提示词里是否已带定义行，生成时都重新生成权威定义、不重复堆叠。"""
    if not text:
        return text or ""
    stripped = _DEF_BLOCK_RE.sub("", text, count=1)
    if stripped != text:
        return stripped
    return _DEF_FALLBACK_RE.sub("", text, count=1)


def build_definition_block(kept: list, kind: str = "video",
                           require_b64: bool = True) -> str:
    """开头【画面定义】块：把随附参考图按附带顺序编号为 @图1、@图2…，逐行声明各图
    指代的角色/场景/道具/导演图/站位图（空模块不输出）。仅作 @ 引用的对应说明，不
    会作为画面文字出现。导演图/站位图等仅在确实调用时才写对应行（如无则忽略）。

    require_b64=True（生成时）：仅对真正随附的图（含 b64）编号；False（工作台预览）：
    按已校验有参考图的资产元数据预排，给用户可见的近似定义（生成时会以权威定义覆盖）。
    """
    if require_b64:
        kept = [it for it in (kept or []) if it.get("b64")]
    else:
        kept = list(kept or [])
    if not kept:
        return ""
    lines = [f"@图{i + 1} {_definition_desc(it, kind)}" for i, it in enumerate(kept)]
    return _DEF_HEAD + "\n" + "\n".join(lines) + "\n" + _DEF_TAIL


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
    # 幂等：先剥离可能已混入文本开头的旧【画面定义】块（来自工作台预览/手工编辑），
    # 生成时统一以权威定义重建，避免重复堆叠。
    text = strip_definition_block(base_text or "")

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

    head = build_definition_block(kept, kind)
    if not head:
        return text
    return head + "\n\n" + text if text else head
