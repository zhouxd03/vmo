"""Localized, variable-based prompt templates.

These are OUR localized rewrites (logic borrowed, not copied) — variables use a
``{{name}}`` syntax so the bodies can freely contain JSON braces. Persisted to
data/templates.json so they are user-editable later (Phase 6). The engine
controls continuity/handoff/numbering/asset-refs; the prompt only does the
analysis it is good at.
"""

import re
import uuid
from typing import Any

from .paths import TEMPLATES_FILE
from .store import read_json, write_json

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render(template: str, variables: dict[str, Any]) -> str:
    def repl(m):
        key = m.group(1)
        val = variables.get(key, "")
        return str(val) if val is not None else ""
    return _VAR_RE.sub(repl, template)


def extract_vars(template: str) -> list[str]:
    """Return the ordered, de-duplicated variable names referenced in a body."""
    seen: list[str] = []
    for m in _VAR_RE.finditer(template or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


# Shared metadata for every variable that appears in any template: a short
# human description + a sample value used for the live preview in the editor.
VAR_META: dict[str, dict[str, str]] = {
    "source_type": {"desc": "来源类型（srt/txt）", "sample": "txt"},
    "full_text": {"desc": "全文文本（或浓缩）", "sample": "林夏走进废弃停车场……（剧本全文）"},
    "story_bible": {"desc": "故事圣经 JSON", "sample": '{"title":"暗夜追踪","style":"中国现代/3D动漫风格"}'},
    "prev_handoff": {"desc": "上一块/上一段衔接接口摘要", "sample": "林夏立于场地中央，紧握青铜酒爵，神情警惕"},
    "chunk": {"desc": "本块剧本片段", "sample": "陈默从阴影中走出，两人对峙……"},
    "style": {"desc": "整体美术风格", "sample": "中国现代,3D动漫风格"},
    "appearance": {"desc": "角色外形/服装锚定", "sample": "20岁女大学生，长发微乱，浅色居家睡衣外搭针织开衫"},
    "desc": {"desc": "资产设定描述", "sample": "古代青铜酒爵，非现代玻璃高脚杯"},
    "prev_state": {"desc": "上一镜状态 JSON", "sample": '{"scene":"废弃停车场","characters":[{"name":"林夏"}]}'},
    "scene": {"desc": "本镜场景", "sample": "废弃停车场"},
    "characters": {"desc": "在场人物", "sample": "林夏、陈默"},
    "props": {"desc": "道具", "sample": "青铜酒爵"},
    "action": {"desc": "动作/事件", "sample": "陈默从阴影中走出，两人对峙"},
    "camera": {"desc": "机位/景别/运动", "sample": "过肩中景"},
    "prev_blocking": {"desc": "需保持的空间/朝向关系（承接上一镜）", "sample": "林夏居中偏左，面向画面右侧"},
    "context": {"desc": "复核用文字上下文", "sample": "上一镜尾帧：林夏立于场中；本镜拟用站位图……"},
}


STAGE_LABELS: dict[str, str] = {
    "stage1": "剧本解析 · 阶段一",
    "stage2": "剧本解析 · 阶段二",
    "asset": "资产参考图",
    "continuity": "连续性引擎",
}


# ── Default templates ──────────────────────────────────────────────────────
GLOBAL_ANALYSIS = """你是资深动漫导演与剧本分析师。下面是一篇{{source_type}}剧本/文稿的全文（或其浓缩）。
请通读全篇，产出"故事圣经"（StoryBible）——只做全局锚定，不要逐镜拆解。

严格输出 JSON（不要任何额外文字），结构如下：
{
  "title": "作品标题（可推断）",
  "logline": "一句话梗概",
  "style": "整体美术风格（如：中国现代/3D动漫风格 等）",
  "characters": [{"name":"角色名","trigger":"@","appearance":"外形/服装/发型/特征锚定","note":"性格/戏剧功能"}],
  "scenes": [{"name":"场景名","trigger":"#","desc":"地点/时代/光影/空间布局"}],
  "props": [{"name":"道具名","trigger":"$","desc":"形制/年代/材质（防穿帮，如：古代青铜酒爵，非现代高脚杯）","note":"戏剧功能/伏笔"}],
  "timeline": "时间线/事件顺序概述",
  "continuity_constraints": ["全局连续性约束（如：A 全程穿同一件外套；夜戏统一冷色光）"]
}

全文：
{{full_text}}"""

BATCH_DECOMPOSE = """你是资深动漫分镜师。基于下面的【故事圣经】与【上一块衔接摘要】，把【本块剧本片段】拆解为结构化分镜。
要求：
- 仅依据本块内容拆镜，但人物/场景/道具命名必须与故事圣经一致，并用触发符引用：人物 @名、场景 #名、道具 $名。
- 保持与上一块的连续性（承接"上一块衔接摘要"里的人物状态/站位/情绪/道具）。
- 每个分镜给出"handoff"（下一段接口：本镜结束时人物最终姿态/站位/道具状态/情绪），供下一块承接。

严格输出 JSON 数组（不要额外文字），每个元素：
{
  "seq": 源片段序号(整数),
  "scene": "#场景名",
  "characters": ["@角色名"],
  "props": ["$道具名"],
  "action": "动作/事件",
  "camera": "机位/景别/运动",
  "dialogue": "对白（无则空）",
  "emotion": "情绪",
  "key_elements": "画面关键元素",
  "handoff": "下一段接口"
}

【故事圣经】
{{story_bible}}

【上一块衔接摘要】
{{prev_handoff}}

【本块剧本片段】
{{chunk}}"""

# ── 资产参考图模板（本地化重写，逻辑借鉴、非照搬）─────────────────────────────
CHARACTER_SHEET = """{{style}}，

画面为横向构图，纯净浅灰色或中性灰色摄影棚背景，整体排版清晰、对称、专业，无文字、无 logo、无水印、无 UI 元素、无边框、无网格线、无多余道具。
**画面左侧占据约 70% 主视觉区域：** 展示同一名原创虚构 3D 数字角色的标准全身三视图（正面 / 正左侧面 / 正背面全身站姿），人物完整不裁切、平视正交、自然标准站姿、双臂自然下垂。三视角必须 100% 统一：五官、脸型、发型、发色、发际线、肤色、体型、身高比例、服装版型与材质、鞋子、配饰完全一致。
**画面右侧占据约 30% 区域：** 以 2×2 等距网格感（不显示网格线）展示 4 张标准头部参考图（正面五官特写 / 俯视头顶发缝 / 正后方后脑勺 / 3-4 正侧方脸部）。所有头部视角五官、脸型、发型、发色、发际线、肤色、耳朵形状完全一致，完整清晰、无裁切遮挡。

角色设定：{{appearance}}"""

SCENE_SHEET = """{{style}}，

场景概念参考图：纯净构图，展示同一场景的整体环境定妆，无人物、无文字、无水印。
要求清晰交代地点、时代、空间布局、光影氛围与材质质感，保持后续分镜可复用的统一基调。

场景设定：{{desc}}"""

PROP_SHEET = """{{style}}，

道具参考图：中性背景下的单体道具特写，多角度清晰展示形制、年代、材质与细节，无人物、无文字、无水印。
严格遵循设定的年代与形制，避免年代/材质穿帮（如：古代青铜酒爵，绝非现代玻璃高脚杯）。

道具设定：{{desc}}"""

# ── 连续性引擎模板（Phase 5）─────────────────────────────────────────────────
HANDOFF_DECISION = """你是动漫分镜连续性导演。判断【本镜】相对【上一镜状态】应如何衔接。
仅输出 JSON（不要额外文字）：
{
  "scene_cut": true/false,            // 是否切换到全新时空（与上一镜不连续）
  "use_tail_frame": true/false,       // 是否用上一镜尾帧作为本镜首帧参考（时空连续时通常 true）
  "use_staging": true/false,          // 是否需要生成"站位图"统一人物位置/构图（人物站位延续且重要时 true）
  "use_director_board": true/false,   // 是否需要生成"导演分镜信息图"作为构图蓝本（复杂多镜调度时 true）
  "reason": "一句话说明判断依据"
}
判断准则：
- 同一场景 / 紧接上一镜动作 → scene_cut=false，use_tail_frame=true。
- 切到新场景 / 时间跳跃 → scene_cut=true，use_tail_frame=false。
- 上一镜与本镜有相同在场人物且站位关系需保持 → use_staging=true。
- 本镜是一个新段落开端、需要整体构图规划 → use_director_board=true。

【上一镜状态】
{{prev_state}}

【本镜】
场景: {{scene}}
人物: {{characters}}
道具: {{props}}
动作: {{action}}
机位: {{camera}}"""

STAGING_DIAGRAM = """{{style}}，
俯视调度/站位参考图（blocking diagram）：以简洁示意的方式标出本镜各人物在画面中的相对站位与朝向、镜头方向，保持与上一镜的空间关系一致，不要写文字标签，无水印。
本镜场景：{{scene}}
在场人物与站位：{{characters}}
需保持的空间/朝向关系（承接上一镜）：{{prev_blocking}}
道具位置：{{props}}"""

DIRECTOR_BOARD = """{{style}}，
导演分镜信息图（16:9 横版蓝图）：用于指导本段落生成，包含主场景定调、人物区（引用 @ 人物）、俯视调度站位、若干关键镜头分格、道具区（强调 $ 道具形制年代材质防穿帮）、灯光情绪标注、以及"下一段接口"（本段最后人物姿态/站位/道具状态/情绪）。严格承接上一段接口，避免上下割裂。
本段场景：{{scene}}
人物：{{characters}}
道具：{{props}}
上一段接口（必须承接）：{{prev_handoff}}
本段动作概述：{{action}}"""

CONTINUITY_REVIEW = """你是动漫连续性复核员。下面给出【上一镜尾帧】与【本镜拟用参考图/站位图】等图片，以及文字上下文。
请判断本镜与上一镜的衔接是否成立：人物外形/服装是否一致、站位与朝向是否连贯、道具形制是否未穿帮、光线与风格是否统一。
仅输出 JSON（不要额外文字）：
{
  "pass": true/false,
  "score": 0-100,
  "issues": ["发现的不一致点（无则空数组）"],
  "suggestion": "若不通过，给出可操作的修正建议（改参考图/调提示词/重生站位图等）"
}

文字上下文：
{{context}}"""


DEFAULT_TEMPLATES = {
    "global_analysis": {
        "name": "全局分析（故事圣经）",
        "stage": "stage1",
        "body": GLOBAL_ANALYSIS,
        "variables": ["source_type", "full_text"],
    },
    "batch_decompose": {
        "name": "分批拆解（结构化分镜）",
        "stage": "stage2",
        "body": BATCH_DECOMPOSE,
        "variables": ["story_bible", "prev_handoff", "chunk"],
    },
    "character_sheet": {
        "name": "人物参考图（三视图+四头像）",
        "stage": "asset",
        "body": CHARACTER_SHEET,
        "variables": ["style", "appearance"],
    },
    "scene_sheet": {
        "name": "场景参考图",
        "stage": "asset",
        "body": SCENE_SHEET,
        "variables": ["style", "desc"],
    },
    "prop_sheet": {
        "name": "道具参考图（防穿帮）",
        "stage": "asset",
        "body": PROP_SHEET,
        "variables": ["style", "desc"],
    },
    "handoff_decision": {
        "name": "衔接决策（尾帧/站位图/导演图）",
        "stage": "continuity",
        "body": HANDOFF_DECISION,
        "variables": ["prev_state", "scene", "characters", "props", "action", "camera"],
    },
    "staging_diagram": {
        "name": "站位图（俯视调度）",
        "stage": "continuity",
        "body": STAGING_DIAGRAM,
        "variables": ["style", "scene", "characters", "prev_blocking", "props"],
    },
    "director_board": {
        "name": "导演分镜信息图",
        "stage": "continuity",
        "body": DIRECTOR_BOARD,
        "variables": ["style", "scene", "characters", "props", "prev_handoff", "action"],
    },
    "continuity_review": {
        "name": "AI 连续性复核闸门",
        "stage": "continuity",
        "body": CONTINUITY_REVIEW,
        "variables": ["context"],
    },
}


# ── preset helpers ──────────────────────────────────────────────────────────
# Each template owns a list of `presets` (多套备选方案); one is `active`.
# Legacy stored templates carry a single `body` and are migrated on load.

def _migrate(templates: dict) -> bool:
    """In-place upgrade legacy single-`body` templates to the preset model.
    Returns True if anything changed."""
    changed = False
    for key, t in templates.items():
        if "presets" not in t or not isinstance(t.get("presets"), list) or not t["presets"]:
            body = t.pop("body", "") or DEFAULT_TEMPLATES.get(key, {}).get("body", "")
            t["presets"] = [{"id": "default", "name": "默认方案", "body": body}]
            t["active"] = "default"
            t.pop("variables", None)  # was derived; recomputed on decorate
            changed = True
        if not t.get("active") or not any(p["id"] == t["active"] for p in t["presets"]):
            t["active"] = t["presets"][0]["id"]
            changed = True
    return changed


def _find_preset(t: dict, preset_id: str) -> dict | None:
    return next((p for p in t.get("presets", []) if p["id"] == preset_id), None)


def _active_preset(t: dict) -> dict:
    return _find_preset(t, t.get("active")) or t["presets"][0]


def _decorate(templates: dict) -> dict:
    """Attach UI metadata (per-variable desc/sample, stage label, default flags)
    without persisting it — keeps the stored file lean."""
    out: dict = {}
    for key, t in templates.items():
        default_body = DEFAULT_TEMPLATES.get(key, {}).get("body", "")
        presets = []
        for p in t.get("presets", []):
            body = p.get("body", "")
            presets.append({
                "id": p["id"],
                "name": p.get("name", "未命名"),
                "body": body,
                "variables": [
                    {
                        "name": v,
                        "desc": VAR_META.get(v, {}).get("desc", ""),
                        "sample": VAR_META.get(v, {}).get("sample", ""),
                    }
                    for v in extract_vars(body)
                ],
                "is_builtin": p["id"] == "default",
                "matches_default": default_body != "" and body == default_body,
            })
        out[key] = {
            "name": t.get("name", key),
            "stage": t.get("stage", ""),
            "stage_label": STAGE_LABELS.get(t.get("stage", ""), t.get("stage", "")),
            "active": t.get("active"),
            "has_default": key in DEFAULT_TEMPLATES,
            "presets": presets,
        }
    return out


def load_templates(*, decorate: bool = False) -> dict:
    saved = read_json(TEMPLATES_FILE, None)
    if not saved:
        saved = {}
        for k, v in DEFAULT_TEMPLATES.items():
            saved[k] = {"name": v["name"], "stage": v["stage"]}
        _migrate(saved)
        write_json(TEMPLATES_FILE, saved)
    else:
        changed = False
        # merge in any new default keys without clobbering user edits
        for k, v in DEFAULT_TEMPLATES.items():
            if k not in saved:
                saved[k] = {"name": v["name"], "stage": v["stage"]}
                changed = True
        if _migrate(saved):
            changed = True
        if changed:
            write_json(TEMPLATES_FILE, saved)
    return _decorate(saved) if decorate else saved


def _save(templates: dict, key: str) -> dict:
    write_json(TEMPLATES_FILE, templates)
    return _decorate(templates)[key]


def _require(templates: dict, key: str) -> dict:
    if key not in templates:
        raise ValueError(f"未知模板: {key}")
    return templates[key]


def save_preset(key: str, preset_id: str, body: str) -> dict:
    """Update the body of an existing preset."""
    templates = load_templates()
    t = _require(templates, key)
    p = _find_preset(t, preset_id)
    if not p:
        raise ValueError(f"未知预设: {preset_id}")
    p["body"] = body
    return _save(templates, key)


def add_preset(key: str, name: str, body: str = "", base_id: str | None = None) -> dict:
    """Add a new preset (optionally cloning an existing one's body) and activate it."""
    templates = load_templates()
    t = _require(templates, key)
    if not body and base_id:
        src = _find_preset(t, base_id)
        body = src.get("body", "") if src else ""
    pid = uuid.uuid4().hex[:12]
    t["presets"].append({"id": pid, "name": (name or "新方案").strip(), "body": body})
    t["active"] = pid
    decorated = _save(templates, key)
    return {"template": decorated, "preset_id": pid}


def rename_preset(key: str, preset_id: str, name: str) -> dict:
    templates = load_templates()
    t = _require(templates, key)
    p = _find_preset(t, preset_id)
    if not p:
        raise ValueError(f"未知预设: {preset_id}")
    p["name"] = (name or "未命名").strip()
    return _save(templates, key)


def delete_preset(key: str, preset_id: str) -> dict:
    templates = load_templates()
    t = _require(templates, key)
    if len(t["presets"]) <= 1:
        raise ValueError("至少保留一套方案，不能删除最后一个预设")
    if not _find_preset(t, preset_id):
        raise ValueError(f"未知预设: {preset_id}")
    t["presets"] = [p for p in t["presets"] if p["id"] != preset_id]
    if t.get("active") == preset_id:
        t["active"] = t["presets"][0]["id"]
    return _save(templates, key)


def set_active_preset(key: str, preset_id: str) -> dict:
    templates = load_templates()
    t = _require(templates, key)
    if not _find_preset(t, preset_id):
        raise ValueError(f"未知预设: {preset_id}")
    t["active"] = preset_id
    return _save(templates, key)


def reset_template(key: str) -> dict:
    """Restore the built-in 'default' preset body and make it active."""
    if key not in DEFAULT_TEMPLATES:
        raise ValueError(f"该模板无默认值可恢复: {key}")
    templates = load_templates()
    t = _require(templates, key)
    p = _find_preset(t, "default")
    if not p:
        p = {"id": "default", "name": "默认方案", "body": ""}
        t["presets"].insert(0, p)
    p["body"] = DEFAULT_TEMPLATES[key]["body"]
    t["active"] = "default"
    return _save(templates, key)


# Backwards-compatible single-body save (writes to the active preset).
def save_template(key: str, body: str) -> dict:
    templates = load_templates()
    t = _require(templates, key)
    _active_preset(t)["body"] = body
    return _save(templates, key)


def preview_template(
    key: str, overrides: dict[str, Any] | None = None, preset_id: str | None = None
) -> str:
    """Render a preset (active by default) with sample values + optional overrides."""
    templates = load_templates()
    t = _require(templates, key)
    p = _find_preset(t, preset_id) if preset_id else _active_preset(t)
    body = (p or _active_preset(t)).get("body", "")
    values = {v: VAR_META.get(v, {}).get("sample", f"<{v}>") for v in extract_vars(body)}
    if overrides:
        values.update({k: v for k, v in overrides.items() if v not in (None, "")})
    return render(body, values)


def get_template_body(key: str) -> str:
    """Body of the active preset — what the engine actually uses."""
    return _active_preset(load_templates()[key]).get("body", "")
