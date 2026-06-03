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
    "handoff": {"desc": "本镜衔接接口/长镜头标记", "sample": "陈默逼近，林夏后退半步（普通切镜）"},
    "prev_blocking": {"desc": "需保持的空间/朝向关系（承接上一镜）", "sample": "林夏居中偏左，面向画面右侧"},
    "context": {"desc": "复核用文字上下文", "sample": "上一镜尾帧：林夏立于场中；本镜拟用站位图……"},
    "image_prompt": {"desc": "本镜图片提示词（风格+景别+场景/人物/道具解析）", "sample": "中国现代,3D动漫风格，过肩中景，废弃停车场，陈默从阴影中走出，两人对峙，@林夏 @陈默 $青铜酒爵"},
    "dynamics": {"desc": "镜头动态（引擎按首帧/运镜/动作时间轴/衔接/节奏自动合成，可改）", "sample": "【首帧锚定·最高权重】@陈默、@林夏，位于@废弃停车场，定格为视频起幅静帧\n【运镜/景别】过肩中景\n【动作过程·时间轴】0–3秒（起势）：陈默从阴影中走出；3–6秒（核心动作）：两人对峙\n【衔接】与上一镜切镜头/景别切换衔接，承接其人物状态与站位\n【节奏】总时长约6秒，紧张情绪，零删减、动作均匀铺满时间轴\n【防穿帮/一致性】镜头运动自然流畅、画面无跳帧；保持人物外形、服装与道具形制和参考图严格一致"},
    "action_timeline": {"desc": "动作时间轴（按目标时长把动作拆成起势/核心/转折/收束小节）", "sample": "0–3秒（起势）：陈默从阴影中走出；3–6秒（收束钩子）：两人对峙"},
    "dialogue": {"desc": "本镜对白（无则空）", "sample": "「你来了。」"},
    "emotion": {"desc": "本镜情绪", "sample": "紧张"},
    "duration": {"desc": "本镜时长（秒，按台词/情绪估算）", "sample": "6"},
    "consistency": {"desc": "一致性约束句（固定守则，可改）", "sample": "镜头运动自然流畅、画面无跳帧；保持人物外形、服装与道具形制和参考图一致"},
    "shot_target": {"desc": "单镜目标时长（秒，来自设置）", "sample": "10"},
    "shot_chars": {"desc": "单镜目标承载字数（按目标时长×8字/秒折算）", "sample": "80"},
    "first_frame": {"desc": "首帧动作锚定（主体+姿态/动作+场景，权重最高）", "sample": "陈默从阴影走出、半身前倾，位于废弃停车场"},
    "transition": {"desc": "本镜衔接/转场方式（引擎按 handoff 判定）", "sample": "与上一镜切镜头/景别切换衔接，承接其人物状态与站位"},
    "digest": {"desc": "剧本片段摘要（每行「[序号] 段首文字」，供 LLM 判断分集边界）", "sample": "[1] 第一章 雨夜\n[2] 林夏推开门……\n[37] 三个月后，城东"},
}


STAGE_LABELS: dict[str, str] = {
    "stage1": "剧本解析 · 阶段一",
    "stage2": "剧本解析 · 阶段二",
    "asset": "资产参考图",
    "continuity": "连续性引擎",
    "video": "视频生成",
}


# ── Default templates ──────────────────────────────────────────────────────
GLOBAL_ANALYSIS = """你是资深动漫导演与剧本分析师。下面是一篇{{source_type}}剧本/文稿的全文（或其浓缩）。
请通读全篇，产出"故事圣经"（StoryBible）——只做全局锚定，不要逐镜拆解。

【角色多形态拆分（重要）】
- 若同一角色在剧情中存在【显著外形差异的多个形态】（变身 / 年龄跨度如幼年↔成年 / 装备形态如机甲 / 兽化 / 重大造型改变等），必须拆成多个独立角色条目，用下划线区分形态：如 @主角_成年、@主角_机甲、@主角_兽化、@主角_婴儿。每个形态各自给出独立、互不混淆的 appearance（不要把多种形态的外形塞进同一条）。
- 仅【外形不变】的变化（情绪/表情/普通动作/换台词）不要拆分，沿用同一 @角色名。
- 拆分出的形态请在 note 里标注与本体的关系（如「主角的兽化形态」），便于分镜按当前形态选用对应的 @基名_形态。

【资产描述词规范（重要：必须完整，不要一句带过）】
- 硬约束：严禁使用括号；严禁「可能/大概/也许/类似」等模糊词；用确定、具体、可绘制的词；内容优雅得体，不写血腥/色情/擦边。
- 细节自动推演：原文对外形/服饰/场景描写简略时，按角色身份与整体美术风格合理补全为可绘制的具体细节。例：「白衣」→「云纹暗花白色丝绸长袍、领口镶银边」；「很帅」→「剑眉星目、鼻梁高挺、下颌线清晰」。补全要服务于一致性，不得臆造与剧情冲突的设定。
- 角色 appearance 必须按此结构写全（用顿号连缀成一句，不分行、不加括号）：性别、年龄（给具体数字）、面部（眼型如丹凤眼/桃花眼、眉形、唇形、肤质如冷白皮、妆容）、发色+发丝质感+发型+发饰材质、身材、服饰（材质如重工刺绣/绸缎/薄纱/皮革+主色+纹样+层级款式）、配饰（腰佩/武器/手持物）。相似角色必须用发色/瞳色/服饰主色调强制区分。
- 场景 desc：主体环境 + 光影氛围 + 若干具体陈设物（地点、时代、空间布局、材质质感）。
- 道具 desc：材质 + 颜色 + 物理属性（如反光/粗糙/通透）+ 形状 + 年代形制（防穿帮，如：古代青铜酒爵，非现代玻璃高脚杯）。

严格输出 JSON（不要任何额外文字），结构如下：
{
  "title": "作品标题（可推断）",
  "logline": "一句话梗概",
  "style": "整体美术风格（如：中国现代/3D动漫风格 等）",
  "characters": [{"name":"角色名（多形态用 基名_形态）","trigger":"@","appearance":"按上述结构写全：性别、年龄数字、面部五官+肤质+妆容、发色+发质+发型+发饰、身材、服饰材质+颜色+纹样+款式、配饰","note":"性格/戏剧功能；若为某角色的形态则注明关系"}],
  "scenes": [{"name":"场景名","trigger":"#","desc":"主体环境+光影氛围+具体陈设物+地点/时代/空间布局/材质"}],
  "props": [{"name":"道具名","trigger":"$","desc":"材质+颜色+物理属性+形状+年代形制（防穿帮，如：古代青铜酒爵，非现代高脚杯）","note":"戏剧功能/伏笔"}],
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
- "camera" 必须给出明确景别（远景/全景/中景/近景/特写）+ 机位/运动。相邻分镜尽量变换景别（如近景↔全景），用切镜头/景别切换推进，避免连续相同景别呆板。
- 衔接默认走切镜头：仅当剧情确实要求画面不间断时，才在 camera 或 handoff 里注明「长镜头」「一镜到底」「无缝衔接」等；否则不要写这些词，默认按可切镜头处理。
- 控制每个分镜的体量：单镜目标时长≈{{shot_target}}秒。纯画面/旁白按≈8字/秒折算，单镜台词+动作描述约{{shot_chars}}字；对白较多的镜头改按≈5字/秒折算（对白越多，单镜承载文字越少），避免一镜塞入过多对话造成"对话过载"。若某段台词/事件过长，拆成多个连续分镜，使每镜节奏适配约 3-15 秒。
- "duration"：按台词体量与情绪估算的镜头秒数（整数，3~15 秒），默认贴近目标时长 {{shot_target}} 秒。正常语速 5 字/秒；情绪急/怒/紧张说得更快（秒数更短），悲/沉/庄重说得更慢（秒数更长）。无台词的纯画面镜给≈{{shot_target}}秒。
- 避免「碎镜」：不要把仅几个字的台词或一个微小动作单独切成一镜。单镜内容应尽量接近目标时长 {{shot_target}} 秒；若某段（台词+动作）折算时长不足约目标的一半（≈{{shot_target}}秒/2），应与相邻、同场景同时空的内容合并为一镜（合并其动作与台词），除非剧情要求强切（如突变/反打/时空跳跃）。合并后 duration 取合并内容的合理总和（仍钳制 3~15 秒）。
- "action"：写成可直接拍摄的画面动作/事件本身（主体+动作+结果），简洁连贯；不要加「画面展示/镜头展示/画面呈现/本镜表现」等冗余引导词，也不要复述场景名或机位（另有字段）。
- "dialogue"：只填本镜真正说出口的台词/旁白原文，不带「台词：」「OS：」等前缀；无台词留空，不要把动作描写写进 dialogue。

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
  "duration": 镜头秒数(整数3~15),
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
**布光（去光污染）：** 角色设定图采用影棚柔光、均匀照明、结构清晰、材质本色还原；严禁逆光、强烈阴影、过曝、炫光、彩色环境光污染，确保后续分镜取色一致。

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

衔接总原则（重要）：默认用「切镜头 / 景别切换」衔接画面——本镜重新生成，不承接上一镜尾帧。
只有当本镜与上一镜确实是【必须连续的长镜头 / 一镜到底 / 无缝承接同一动作】时，才承接上一镜尾帧。
日常的同场景对话、反打、动作推进都应切镜头并变换景别（如上一镜近景→本镜中景/全景），
不要逐镜首尾帧相接（本管线的图生视频做不到精确首帧承接，强行相接只会跳帧+色差）。

仅输出 JSON（不要额外文字）：
{
  "scene_cut": true/false,            // 是否切换到全新时空（与上一镜不连续）
  "long_take": true/false,            // 本镜是否是必须连续的长镜头/一镜到底
  "use_tail_frame": true/false,       // 仅当 long_take=true 且同场景时才可为 true；其余一律 false
  "use_staging": true/false,          // 是否需要生成"站位图"统一人物位置/构图（人物站位延续且重要时 true）
  "use_director_board": true/false,   // 是否需要生成"导演分镜信息图"作为构图蓝本（复杂多镜调度时 true）
  "shot_size": "本镜建议景别（远景/全景/中景/近景/特写），尽量与上一镜不同",
  "camera_hint": "一句话运镜/景别指导：切镜头时说明换什么景别、长镜头时说明如何自然延续",
  "reason": "一句话说明判断依据"
}
判断准则：
- 切到新场景 / 时间跳跃 → scene_cut=true，long_take=false，use_tail_frame=false（用新景别建立环境）。
- 同一场景但只是普通对话/反打/动作推进 → scene_cut=false，long_take=false，use_tail_frame=false（切镜头+变换景别衔接）。
- 仅当明确「长镜头/一镜到底/无缝衔接/不切」、画面必须连续 → long_take=true，同场景时 use_tail_frame=true。
- 上一镜与本镜有相同在场人物且站位关系需保持 → use_staging=true。
- 本镜是一个新段落开端、需要整体构图规划 → use_director_board=true。

【上一镜状态】
{{prev_state}}

【本镜】
场景: {{scene}}
人物: {{characters}}
道具: {{props}}
动作: {{action}}
机位: {{camera}}
衔接接口: {{handoff}}"""

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

CONTINUITY_REVIEW = """你是动漫连续性复核员。下面给出相关图片与文字上下文，复核本镜与上一镜的衔接。

复核原则：本项目默认用「切镜头 / 景别切换」衔接，不追求逐镜首尾帧像素相接。
- 若本镜是切镜头/景别切换：重点看人物外形/服装是否一致、道具形制是否未穿帮、光线与整体风格是否统一、
  景别是否与上一镜有变化（避免雷同呆板）。不要因为"画面没有逐帧承接上一镜尾帧"就判不通过。
- 若本镜标注为必须连续的长镜头：才要求运动/构图/光线与上一镜尾帧自然延续、无跳切。
- 反向提醒：若发现本应切镜头的普通镜却生硬地首尾帧相接（重复、跳帧感、色差），在 issues 指出并建议改为景别切换。

仅输出 JSON（不要额外文字）：
{
  "pass": true/false,
  "score": 0-100,
  "issues": ["发现的不一致点（无则空数组）"],
  "suggestion": "若不通过，给出可操作的修正建议（改参考图/调提示词/换景别/重生站位图等）"
}

文字上下文：
{{context}}"""


# ── 视频生成提示词模板 ──────────────────────────────────────────────────────
# 把静态「图片提示词」升级为「视频提示词」：在画面描述之上整合 分镜信息（首帧锚定/
# 运镜景别/动作过程/情绪节奏+时长/台词）与 衔接信息（handoff 收尾接口/切镜头或长镜头
# 转场），并叠加防穿帮一致性守则。引擎已确定性地把这些字段合成为 {{dynamics}}
# （结构化、空字段自动省略），默认方案直接拼接即可、零额度、不额外调用 LLM；想要更强
# 控制可改写本方案，直接引用 {{first_frame}}/{{camera}}/{{action}}/{{transition}}/
# {{handoff}}/{{dialogue}}/{{emotion}}/{{duration}}/{{consistency}} 等粒度变量。
VIDEO_PROMPT = """{{image_prompt}}

【镜头动态】
{{dynamics}}"""

# ── 自适应分集模板（导入时启发式识别不到分隔符 → 介入 LLM 判断分集边界）──────────
EPISODE_SPLIT = """你是剧集编排师。下面是一篇剧本/小说按段落切好的【片段列表】，每行格式为「[序号] 段首文字」。
请通读后判断它应被划分成哪几「集」，并找出每一集的起始段落序号（start_seq）。
规则：
- 自适应识别分集边界：依据剧情段落/场景或时间跳跃/标题行（如「第一章」「第N集」「Episode N」）等线索综合判断；不同作品标记方式不同，灵活处理。
- 各集体量尽量均衡，避免出现过短的集；若全文确实只适合 1 集，就只返回 1 集。
- 集名：优先采用原文标题行原文；无标题时用「第N集」。
- start_seq 必须是片段列表中真实出现的序号；第 1 集的 start_seq 通常为列表首个序号。

仅输出 JSON（不要任何额外文字），结构：
{"episodes":[{"name":"第1集","start_seq":1},{"name":"第2集","start_seq":37}]}

【片段列表】
{{digest}}"""

# ── 逐镜提示词推理（点「AI推理」时调用，文本 LLM）──────────────────────────────
# 喂入：整体风格 + 本镜结构字段 + 目标时长 + 连续性(上一镜收尾) + 本镜关联资产说明，
# 产出干净规范的「图片(首帧静帧)」与「视频(镜头动态)」提示词，并剔除非画面元素。
SHOT_PROMPT_INFER = """你是资深动漫分镜与提示词工程师。请基于下列信息，为「单个分镜」推理生成用于 AI 出图与 AI 视频生成的提示词，并以严格 JSON 返回。

【整体风格】{{style}}
【本镜结构】
- 镜号：{{shot_no}}；目标时长：{{duration}} 秒
- 景别/机位：{{camera}}
- 场景：{{scene}}
- 出场角色：{{characters}}
- 关键道具：{{props}}
- 动作/事件：{{action}}
- 情绪：{{emotion}}
- 台词/对白（仅此为真正说出口的话，可能为空）：{{dialogue}}
【连续性·上一镜收尾(本镜需承接)】{{prev_handoff}}
【本镜收尾接口(交给下一镜)】{{handoff}}
【本镜关联资产（@名 → 说明；生成时这些 @ 会自动附带对应参考图）】
{{assets_desc}}

要求：
1) 引用统一用 @名（如 @主角、@丛林、@宝剑、@导演图、@站位图），并在需要处用简短说明指明作用（如「参考 @站位图 确定角色站位」）。库内 #/$ 分类符号不得出现在输出里。
2) 只保留可在画面中呈现的视觉信息；剔除非画面元素（不出声的心理活动、纯叙述旁白、作者解释、与画面无关的设定）。台词只在 video 的【台词/旁白】出现，且只用真正说出口的话；无台词则省略该段。
3) image（首帧静帧出图词）：一句到数句，描述起幅静帧——主体(@角色)+所在场景(@场景)+构图/景别+光线/色调+风格；不写运镜与动作过程。
4) video（视频生成词）：严格按以下分段，时间轴必须覆盖 0→{{duration}} 秒、动作均匀铺满、不浪费时长：
【首帧锚定·最高权重】起幅静帧（与 image 一致的主体+场景），定格为视频起幅
【运镜/景别】在 {{camera}} 基础上给出具体运镜
【动作过程·时间轴】把动作按 0→{{duration}}秒 分配：短镜单段写「0–{{duration}}秒：…」；≥6秒且动作较多时分 2–4 段，每段标注时间区间与（起势/核心/转折/收束）并写该段画面动作
【衔接】承接上一镜（{{prev_handoff}}）；说明转场（默认切镜头/景别切换；若为长镜头则写无缝衔接）
【台词/旁白】仅当有真实台词时写，并标注「声画同步，不出字幕」
【节奏】总时长约{{duration}}秒，{{emotion}}情绪，零删减
【防穿帮/一致性】{{consistency}}

严格输出 JSON（不要任何额外文字）：{"image": "…", "video": "…"}"""

DEFAULT_TEMPLATES = {
    "global_analysis": {
        "name": "全局分析（故事圣经）",
        "stage": "stage1",
        "body": GLOBAL_ANALYSIS,
        "variables": ["source_type", "full_text"],
        "version": 3,
    },
    "batch_decompose": {
        "name": "分批拆解（结构化分镜）",
        "stage": "stage2",
        "body": BATCH_DECOMPOSE,
        "variables": ["story_bible", "prev_handoff", "chunk", "shot_target", "shot_chars"],
        "version": 5,
    },
    "character_sheet": {
        "name": "人物参考图（三视图+四头像）",
        "stage": "asset",
        "body": CHARACTER_SHEET,
        "variables": ["style", "appearance"],
        "version": 2,
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
        "variables": ["prev_state", "scene", "characters", "props", "action", "camera", "handoff"],
        "version": 2,
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
        "version": 2,
    },
    "video_prompt": {
        "name": "视频生成提示词（镜头动态）",
        "stage": "video",
        "body": VIDEO_PROMPT,
        "variables": ["image_prompt", "dynamics", "first_frame", "action_timeline",
                       "transition", "handoff", "camera", "action", "emotion",
                       "duration", "dialogue", "scene", "characters", "props",
                       "consistency"],
        "version": 3,
    },
    "episode_split": {
        "name": "自适应分集（LLM 兜底）",
        "stage": "stage1",
        "body": EPISODE_SPLIT,
        "variables": ["digest"],
        "version": 1,
    },
    "shot_prompt_infer": {
        "name": "逐镜提示词推理（图片+视频）",
        "stage": "worktable",
        "body": SHOT_PROMPT_INFER,
        "variables": ["style", "shot_no", "duration", "camera", "scene",
                       "characters", "props", "action", "emotion", "dialogue",
                       "prev_handoff", "handoff", "assets_desc", "consistency"],
        "version": 1,
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


def _refresh_builtin_defaults(templates: dict) -> bool:
    """Bump the built-in `default` preset body when DEFAULT_TEMPLATES bumps its
    `version`. Only the engine-owned `default` preset is touched — user-added
    presets are never modified — so updated prompt logic ships to existing
    installs while preserving user customizations. Returns True if changed."""
    changed = False
    for key, v in DEFAULT_TEMPLATES.items():
        ver = v.get("version")
        if not ver or key not in templates:
            continue
        p = _find_preset(templates[key], "default")
        if p is None:
            continue
        if p.get("_v") != ver:
            p["body"] = v["body"]
            p["_v"] = ver
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
        _refresh_builtin_defaults(saved)
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
        if _refresh_builtin_defaults(saved):
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
    p["_v"] = DEFAULT_TEMPLATES[key].get("version")
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
