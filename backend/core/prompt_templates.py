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
    "bridge_context": {"desc": "连续性引擎生成的桥接变量，供下一镜提示词/站位图/导演图提炼引用", "sample": "桥接策略：站位图承接；承接重点：保持相对站位、左右关系和视线方向"},
    "chunk": {"desc": "本块剧本片段", "sample": "陈默从阴影中走出，两人对峙……"},
    "style": {"desc": "整体美术风格", "sample": "中国现代,3D动漫风格"},
    "appearance": {"desc": "角色外形/服装锚定", "sample": "20岁女大学生，长发微乱，浅色居家睡衣外搭针织开衫"},
    "voice": {"desc": "角色音色/声线锚定", "sample": "清亮少女音，语速偏快，情绪激动时尾音发颤"},
    "bio": {"desc": "人物小传/简介（身份、经历、性格、关系、叙事功能）", "sample": "外冷内热的女主，曾因一次失踪事件与主线阴谋相连，擅长观察细节，是推动调查线索的关键人物。"},
    "alias": {"desc": "角色别名/称呼（本名、外号、职称、代称等）", "sample": "林夏、夏姐、林队"},
    "first_appearance": {"desc": "首次出现信息（集数/镜头/章节等）", "sample": "第1集·S01-003"},
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
    "image_prompt": {"desc": "本镜图片提示词（风格+景别+首帧画面；不含对白，参考图编号由引擎另行注入）", "sample": "中国现代,3D动漫风格，过肩中景，废弃停车场，@林夏 与 @陈默 对峙，青铜酒爵在前景"},
    "dynamics": {"desc": "镜头动态（引擎按首帧/运镜/动作时间轴/台词旁白/衔接/节奏自动合成，可改）", "sample": "【首帧锚定·最高权重】@陈默、@林夏，位于@废弃停车场，定格为视频起幅静帧\n【运镜/景别】过肩中景\n【动作过程·时间轴】0–3秒（起势）：陈默从阴影中走出；3–6秒（核心动作）：两人对峙\n【台词/旁白】对白-@林夏：别过来（声画同步，不出字幕）\n【衔接】与上一镜切镜头/景别切换衔接，承接其人物状态与站位\n【节奏】总时长约6秒，紧张情绪，零删减、动作均匀铺满时间轴\n【防穿帮/一致性】镜头运动自然流畅、画面无跳帧；保持人物外形、服装与道具形制和参考图严格一致"},
    "action_timeline": {"desc": "动作时间轴（按目标时长把动作拆成起势/核心/转折/收束小节）", "sample": "0–3秒（起势）：陈默从阴影中走出；3–6秒（收束钩子）：两人对峙"},
    "dialogue": {"desc": "本镜声音内容（对白/画外旁白/内心OS；无则空）", "sample": "内心OS：有两脚！"},
    "emotion": {"desc": "本镜情绪", "sample": "紧张"},
    "duration": {"desc": "本镜时长（秒，按台词/情绪估算）", "sample": "6"},
    "consistency": {"desc": "一致性约束句（固定守则，可改）", "sample": "镜头运动自然流畅、画面无跳帧；保持人物外形、服装与道具形制和参考图一致"},
    "shot_target": {"desc": "单镜目标时长（秒，来自设置）", "sample": "10"},
    "shot_chars": {"desc": "单镜目标承载字数（按目标时长×8字/秒折算）", "sample": "80"},
    "first_frame": {"desc": "首帧动作锚定（主体+姿态/动作+场景，权重最高）", "sample": "陈默从阴影走出、半身前倾，位于废弃停车场"},
    "transition": {"desc": "本镜衔接/转场方式（引擎按 handoff 判定）", "sample": "与上一镜切镜头/景别切换衔接，承接其人物状态与站位"},
    "digest": {"desc": "剧本片段摘要（每行「[序号] 段首文字」，供 LLM 判断分集边界）", "sample": "[1] 第一章 雨夜\n[2] 林夏推开门……\n[37] 三个月后，城东"},
    "shot_text": {"desc": "人工划定的单个分镜原文片段（手动分镜模式）", "sample": "暴雨倾盆，密林深处泥地上，裹着破布的婴儿浑身黑色鳞片，仰面大哭。"},
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

【对白人物强制建档（最高优先级）】
- 凡是在原文中出现过真实对白、画外旁白、内心OS、吐槽、喊话、旁人转述台词或带引号发言的人物，都必须录入 characters，不得因为戏份少、当前集只出现一次、称呼临时、外形描写不足而遗漏。
- 若说话者只有临时称呼，如「黑衣人」「孩子甲」「村民乙」「旁白」「老板娘」「熊孩子头子」，也要先按该称呼建档；后文出现本名或身份揭示时，在 note 与 bio 中合并说明别名关系，避免长线剧情后段角色对不上。
- 群体台词可拆则拆：能从上下文判断具体说话者时必须拆成独立角色；确实无法拆分时，建立清晰的群体角色条目，如「熊孩子们」「围观村民」，并在 bio 中说明群体范围与叙事功能。
- 对白人物的 voice 不能为空。即使原文没有音色描写，也要根据年龄感、身份、性格、台词语气和剧情功能合理生成一条可复用音色词条。
- 跨集/长线识别时，优先用 bio、别名、关系、说话习惯和首次出场信息判断是否同一角色；不要把同一说话者在后续集重复建成新角色。

【角色多形态拆分（重要）】
- 若同一角色在剧情中存在【显著外形差异的多个形态】（变身 / 年龄跨度如幼年↔成年 / 装备形态如机甲 / 兽化 / 重大造型改变等），必须拆成多个独立角色条目，用下划线区分形态：如 @主角_成年、@主角_机甲、@主角_兽化、@主角_婴儿。每个形态各自给出独立、互不混淆的 appearance（不要把多种形态的外形塞进同一条）。
- 仅【外形不变】的变化（情绪/表情/普通动作/换台词）不要拆分，沿用同一 @角色名。
- 拆分出的形态请在 note 里标注与本体的关系（如「主角的兽化形态」），便于分镜按当前形态选用对应的 @基名_形态。
- 每个角色除了 appearance，还必须给出 bio（人物小传/简介）：身份、经历、性格、与其他角色的关系、在本作中的叙事功能。后续跨集再出现时，要优先按 bio 识别是否同一角色，避免重复建档。
- 若原文对同一角色存在多个称呼（本名/外号/代称/职称），请在 note 里统一标明，并在 bio 中写明别名关系，方便后续在别集继续识别。
- 每个角色必须给出 voice（音色/声线）：用一句可供视频对白/配音参考的中文短语描述，不要超过 40 字。包含声线年龄感、音色质感、语速/气息、情绪发声特点；例如「少女音，清亮偏软，急促时尾音发颤」或「低沉青年音，气息稳定，压怒时咬字发紧」。非人类角色也要给出拟人化声线，如「幼犬化少女音，尖细稚嫩，愤怒时带奶凶颤音」。

【资产描述词规范（重要：必须完整，不要一句带过）】
- 硬约束：严禁使用括号；严禁「可能/大概/也许/类似」等模糊词；用确定、具体、可绘制的词；内容优雅得体，不写血腥/色情/擦边。
- 细节自动推演：原文对外形/服饰/场景描写简略时，按角色身份与整体美术风格合理补全为可绘制的具体细节。例：「白衣」→「云纹暗花白色丝绸长袍、领口镶银边」；「很帅」→「剑眉星目、鼻梁高挺、下颌线清晰」。补全要服务于一致性，不得臆造与剧情冲突的设定。
- 角色 appearance 必须按此结构写全（用顿号连缀成一句，不分行、不加括号）：性别、年龄（给具体数字）、面部（眼型如丹凤眼/桃花眼、眉形、唇形、肤质如冷白皮、妆容）、发色+发丝质感+发型+发饰材质、身材、服饰（材质如重工刺绣/绸缎/薄纱/皮革+主色+纹样+层级款式）、配饰（腰佩/武器/手持物）。相似角色必须用发色/瞳色/服饰主色调强制区分。
- 场景 name 必须是稳定物理地点名，只写空间本体，不允许把时段、光线、天气、情绪或剧情状态写进名称。错误示例：医生办公室_深夜、走廊_清晨、深夜医院走廊；正确示例：医生办公室、医院走廊。时段与光线变化由场景参考图的光影子模块覆盖，不要为同一地点拆出多个时段场景。
- 场景 desc：主体环境 + 若干具体陈设物 + 地点/时代/空间布局/材质质感。若原文出现深夜、清晨、黄昏、雨夜等时段/光线信息，只能作为可变光影条件简要写入 desc，不能改变 name，也不能因此新增场景条目。
- 道具 desc：材质 + 颜色 + 物理属性（如反光/粗糙/通透）+ 形状 + 年代形制（防穿帮，如：古代青铜酒爵，非现代玻璃高脚杯）。

严格输出 JSON（不要任何额外文字），结构如下：
{
  "title": "作品标题（可推断）",
  "logline": "一句话梗概",
  "style": "整体美术风格（如：中国现代/3D动漫风格 等）",
  "characters": [{"name":"角色名（多形态用 基名_形态）","trigger":"@","appearance":"按上述结构写全：性别、年龄数字、面部五官+肤质+妆容、发色+发质+发型+发饰、身材、服饰材质+颜色+纹样+款式、配饰","voice":"音色/声线：声线年龄感+音色质感+语速/气息+情绪发声特点，不超过40字","bio":"人物小传/简介：身份、经历、性格、与其他角色的关系、在本作中的叙事功能；若为某角色的形态则注明关系","note":"性格/戏剧功能；若为某角色的形态则注明关系"}],
  "scenes": [{"name":"稳定物理地点名，不含时段/光线/天气/情绪","trigger":"#","desc":"主体环境+具体陈设物+地点/时代/空间布局/材质；时段/光线只作为可变条件简述"}],
  "props": [{"name":"道具名","trigger":"$","desc":"材质+颜色+物理属性+形状+年代形制（防穿帮，如：古代青铜酒爵，非现代高脚杯）","note":"戏剧功能/伏笔"}],
  "timeline": "时间线/事件顺序概述",
  "continuity_constraints": ["全局连续性约束（如：A 全程穿同一件外套；夜戏统一冷色光）"]
}

全文：
{{full_text}}"""

BATCH_DECOMPOSE = """你是资深动漫分镜师。基于下面的【故事圣经】与【上一块衔接摘要】，把【本块剧本片段】拆解为结构化分镜。

【输入边界】
- 本块内容必须使用统一的固定标记来定义边界：
  - `<<<分集标记n>>>`：分集边界，n 为分集编号。
  - `<<<分镜标记n>>>`：分镜边界，n 为分镜编号。
- 标记后的文本到下一个标记之前，都属于该标记块；批量工作台的「原文」会直接读取对应标记块，严禁把标记块之外的内容混入本镜。
- 空行、对白排版、段落换行都只是原文排版，不应自动等同于边界。
- 当原文中出现 `<<<分镜标记n>>>` 时，必须将其视为强分镜边界；当出现 `<<<分集标记n>>>` 时，必须将其视为强分集边界。
- 拆解时只处理这些标记块内的文本；不得把空行、段落换行或对白排版当成新的分镜/分集边界。

【命名与引用】
- 仅依据本块内容拆镜，但人物/场景/道具命名必须与故事圣经一致，并用触发符引用：人物 @名、场景 #名、道具 $名。
- 保持与上一块的连续性（承接"上一块衔接摘要"里的人物状态/站位/情绪/道具）；每个分镜给出"handoff"（下一段接口：本镜结束时人物最终姿态/站位/道具状态/情绪），供下一块承接。

【切分原则（连续性优先，避免过多/过少）】
- 以「一个镜头能不能一次拍完」为准：一个连贯动作或一段连续对话能在一个镜头内完成的，就放一个分镜，不要无谓拆开。
- 只有遇到【人物大幅位移/跨空间移动】（如从门口走到窗边、从屋内冲到屋外、远距离奔跑追逐）时，才拆成「起势镜（动作发起）→局部过渡镜（移动中段/特写局部）→落位镜（到达定位）」三段，避免瞬移、滑步、身体形变。
- "camera" 必须给出明确景别（远景/全景/中景/近景/特写）+ 机位/运动。相邻分镜尽量变换景别（近景↔全景），用切镜头/景别切换推进，避免连续相同景别呆板。
- 衔接默认走切镜头：仅当剧情确实要求画面不间断时，才在 camera 或 handoff 里注明「长镜头」「一镜到底」「无缝衔接」等；否则不要写这些词。

【时长与体量（让动作铺满单镜目标时长，且为运镜/动作留出时间）】
- 单镜目标时长≈{{shot_target}}秒。一个分镜的时长要同时容纳：台词朗读 + 运镜 + 动作完成。
- 纯画面/旁白按≈8字/秒折算，单镜台词+动作描述约{{shot_chars}}字。
- 对白较多的镜头改按≈5字/秒折算（对白越多，单镜承载文字越少），并且台词朗读时间不要占满整镜——至少留约 20%~30% 时长给运镜与动作；若一段台词读完就超过目标时长，按句意拆成多个连续分镜，避免「对白念不完」或「对话过载」。
- "duration"：按台词体量与情绪估算的镜头秒数（整数，3~15 秒），默认贴近目标时长 {{shot_target}} 秒。正常语速 5 字/秒；急/怒/紧张更快（秒数更短），悲/沉/庄重更慢（秒数更长）。无台词的纯画面镜给≈{{shot_target}}秒。
- 避免「碎镜」：不要把仅几个字的台词或一个微小动作单独切成一镜。若某段（台词+动作）折算时长不足约目标的一半（≈{{shot_target}}秒/2），应与相邻、同场景同时空的内容合并为一镜（合并其动作与台词），除非剧情要求强切（突变/反打/时空跳跃）。合并后 duration 取合并内容的合理总和（仍钳制 3~15 秒）。
- 避免「时长空心」：当 {{shot_target}} 接近 12~15 秒时，一个普通分镜必须包含足够演绎该时长的剧情推进、动作层次、反应节拍或环境互动；不要把只能演绎 5~7 秒的单一动作硬标成一镜。若内容不足，就向前/向后合并同场景连续文本；若内容过多，就按自然动作段落拆分。
- 普通剧情镜的 duration 不应明显低于目标时长：除非是明确的强切、闪回、反打特写、惊吓瞬间、转场空镜，否则 duration 至少贴近 {{shot_target}} 的 75%。目标 15 秒时，不要输出 6~8 秒的短镜作为常规镜头。
- 每个分镜必须能回答「这 {{shot_target}} 秒里观众持续看什么」：起幅状态、动作推进、人物反应、台词/旁白、收束钩子至少具备其中 2~3 项，避免只有一句台词或一个姿态。

【字段书写】
- "action"：写成可直接拍摄的画面动作/事件本身（主体+动作+结果），简洁连贯；不要加「画面展示/镜头展示/画面呈现/本镜表现」等冗余引导词，也不要复述场景名或机位（另有字段）。
- "dialogue"：只填本镜真正需要发声的内容，可包含真实对白、画外旁白、内心OS/吐槽；必须保留声源标记和说话人。
  - 真实对白必须写成「对白-@角色名：台词」，每一句都要带说话人；多句对白用换行或「；」分隔，但每句都必须重复标注说话人，严禁只写「对白：台词」。
  - 若原文没有明说谁开口，要根据上下文、在场 characters、上一句问答关系判断；确实无法判断时写「对白-@未知说话人：台词」，并把 @未知说话人 加入 characters，不能省略人物。
  - 画外旁白/内心OS/吐槽也要标注声源：如「画外旁白-@旁白：内容」「内心OS-@角色名：内容」「吐槽-@角色名：内容」。
  - 不出声的心理描写不要放入 dialogue。不要把动作描写写进 dialogue。
- "seq"：本镜取材的【起始源片段序号】——即【本块剧本片段】中每行行首方括号里的数字 [N]。必须取自本块出现过的序号；不同分镜应对应它们各自取材的源片段，严禁把所有分镜都填同一个 seq。
- "seq_end"：本镜取材的【结束源片段序号】。若一个分镜由多段连续原文合并而来，填最后一段的序号；若只来自单段，则与 seq 相同。严禁省略跨段原文范围。
- 若源片段来自 `<<<分镜标记n>>>`，本镜应对应该标记块本身；不要跨到相邻分镜标记块。分集标记块只作为承上启下/分集起点，不要当作普通分镜内容吞并。

严格输出 JSON 数组（不要额外文字），每个元素：
{
  "seq": 源片段序号(整数，取自本块行首 [N]),
  "seq_end": 结束源片段序号(整数，单段时等于 seq),
  "scene": "#场景名",
  "characters": ["@角色名"],
  "props": ["$道具名"],
  "action": "动作/事件",
  "camera": "机位/景别/运动",
  "dialogue": "对白-@角色名：台词；对白-@另一角色：台词（画外旁白/内心OS同样标注声源；无则空）",
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

MANUAL_SHOT_STRUCTURE = """你是资深动漫分镜师。下面是【人工已划定好的单个分镜】对应的原文片段——分镜边界由人工决定，你【不要再拆分或合并】，只对这一个镜头做结构化字段填充。

【手动分镜方法（务必遵守）】
- 手动分镜时，请在每个你想固定成一个镜头的原文块前单独写一行 `<<<分镜标记n>>>`，n 为分镜编号。
- 标记后面的多行文本都归属于同一个分镜；中间可以保留换行、对白排版、动作分行、情绪分行，但这些**都不改变边界**。
- 如果同一镜头内部存在多次换行、段内排版或对白分段，只要没有再次出现下一个 `<<<分镜标记n>>>`，就仍然视为同一个分镜。
- 因此：**边界只看标记，不看换行。**

【边界标记】
- 人工分镜时，统一使用固定标记 `<<<分镜标记n>>>` 来标出边界。
- 本任务已给定单镜内容，因此你只负责填充字段，不要改写边界。

【命名与引用】
- 人物/场景/道具命名必须与故事圣经一致，并用触发符引用：人物 @名、场景 #名、道具 $名。
- 保持与上一镜的连续性（承接"上一镜衔接摘要"里的人物状态/站位/情绪/道具），并给出本镜 handoff（下一镜接口：本镜结束时人物最终姿态/站位/道具状态/情绪）。

【字段书写】
- "action"：写成可直接拍摄的画面动作/事件本身（主体+动作+结果），简洁连贯；不要加「画面展示/镜头展示」等冗余引导词，也不要复述场景名或机位。
- "camera"：给明确景别（远景/全景/中景/近景/特写）+ 机位/运动。
- "dialogue"：只填本镜真正需要发声的内容，可包含真实对白、画外旁白、内心OS/吐槽；必须保留声源标记和说话人。
  - 真实对白必须写成「对白-@角色名：台词」，每一句都要带说话人；多句对白用换行或「；」分隔，但每句都必须重复标注说话人，严禁只写「对白：台词」。
  - 若原文没有明说谁开口，要根据上下文、在场 characters、上一句问答关系判断；确实无法判断时写「对白-@未知说话人：台词」，并把 @未知说话人 加入 characters，不能省略人物。
  - 画外旁白/内心OS/吐槽也要标注声源：如「画外旁白-@旁白：内容」「内心OS-@角色名：内容」「吐槽-@角色名：内容」。无声音留空。
- "duration"：按台词体量与情绪估算的镜头秒数（整数 3~15），默认贴近 {{shot_target}} 秒；正常 5 字/秒，急/怒更快、悲/沉更慢；无台词的纯画面给≈{{shot_target}}秒。
- 若人工片段内容较短但目标时长为 {{shot_target}} 秒，不要只写一个瞬间动作；应在不加戏、不改边界的前提下，把同一片段内可见的起幅、动作推进、表情反应、停顿、收束状态写完整，使该镜能自然铺满目标时长。
- action 只保留可在画面中呈现的视觉信息；不出声的心理活动不要写进 action。若原文内心吐槽需要保留为声音，应写进 dialogue 并标注「内心OS」。

严格输出单个 JSON 对象（不要数组、不要额外文字）：
{
  "scene": "#场景名",
  "characters": ["@角色名"],
  "props": ["$道具名"],
  "action": "动作/事件",
  "camera": "机位/景别/运动",
  "dialogue": "对白-@角色名：台词；对白-@另一角色：台词（画外旁白/内心OS同样标注声源；无则空）",
  "emotion": "情绪",
  "duration": 镜头秒数(整数3~15),
  "key_elements": "画面关键元素",
  "handoff": "下一镜接口"
}

【故事圣经】
{{story_bible}}

【上一镜衔接摘要】
{{prev_handoff}}

【本镜原文片段（人工划定，勿再拆分/合并）】
{{shot_text}}"""

# ── 资产参考图模板（本地化重写，逻辑借鉴、非照搬）─────────────────────────────
CHARACTER_SHEET = """{{style}}，

生成一张横向 16:9「角色设定卡 / Character Card」信息板，画面清晰、专业、适合后续资产库审阅与视频生成引用。整体为暗色或中性摄影棚质感背景，细线分区，中文标注清楚可读；允许少量装饰性 UI 边框，但不要出现水印、平台 logo、乱码、无意义英文、重复文字。

【角色基础信息区（左侧）】
- 大标题：角色设定卡。
- 写清：名字：{{name}}；外号/称呼：根据角色描述提炼，未知则写「待定」；性别：根据角色设定判断；风格：从外形与气质中提炼 1 个短词。
- MBTI/性格：不要强行写四字母类型；用一句短句表达角色性格、气质、行为倾向与戏剧功能。
- 音色：{{voice}}；若为空则根据角色年龄感、气质和性格生成一句可供配音参考的音色词条。

【主视觉区】
- 角色 45°脸部超清晰写实特写：五官、发型、肤质、妆容、眼神清楚，作为角色识别主锚点。
- 人物全身正面与人物全身背面：完整不裁切，姿态自然，服装、发型、身材比例、鞋履、配饰与脸部特写保持同一角色一致。

【不同场景适用服装区】
横向展示 4 到 5 套同一角色的场景服装小卡，每套都标注「服装名称」与「启用条件」。服装只在角色原设合理范围内变化，脸、发型核心特征、身材比例、气质必须保持一致。
示例启用条件写法：日常休闲 / 好感度 < 30；舞台演出 / 舞蹈或比赛；职场精英 / 商务谈判或公司场景；运动活力 / 健身房或运动场景；晚宴礼服 / 宴会或重要剧情。

【动作与表情区】
- 自动随机触发的习惯性动作 1 个：给出动作名和简短说明，例如「撩发挑眉：紧张或自信时自然触发」。
- 经典夸张表情 3 个：每个表情有独立头像小图和中文标签，表情要戏剧化但不崩坏角色五官。

【技能区】
- 主要技能 3 个：每个技能包含小图标/小场景、技能名、短说明；技能应从角色身份、性格、剧情功能中合理推导，不要脱离角色设定乱加超能力。
- 大招 1 个：占据右下或底部较大画面，给出大招名和一句描述；视觉冲击强，但仍保持角色一致。

【一致性与排版要求】
- 所有人物图必须是同一名角色：脸型、五官、发色、发型、肤色、身材比例、核心服装元素和配饰不能漂移。
- 信息平铺在整张画面上，分区明确，文字不要压住人物关键部位；图片和文字比例均衡，不能只有大头或只有文字。
- 角色设定卡可读性优先：中文标签清晰，版面像游戏/动画企划设定页，而不是海报。

角色外形锚定：{{appearance}}
角色描述/小传：{{desc}}"""

CHARACTER_SHEET_LEGACY = """{{style}}，

画面为横向构图，纯净浅灰色或中性灰色摄影棚背景，整体排版清晰、对称、专业，无文字、无 logo、无水印、无 UI 元素、无边框、无网格线、无多余道具。
**画面左侧占据约 70% 主视觉区域：** 展示同一名原创虚构 3D 数字角色的标准全身三视图（正面 / 正左侧面 / 正背面全身站姿），人物完整不裁切、平视正交、自然标准站姿、双臂自然下垂。三视角必须 100% 统一：五官、脸型、发型、发色、发际线、肤色、体型、身高比例、服装版型与材质、鞋子、配饰完全一致。
**画面右侧占据约 30% 区域：** 以 2×2 等距网格感（不显示网格线）展示 4 张标准头部参考图（正面五官特写 / 俯视头顶发缝 / 正后方后脑勺 / 3-4 正侧方脸部）。所有头部视角五官、脸型、发型、发色、发际线、肤色、耳朵形状完全一致，完整清晰、无裁切遮挡。
**布光（去光污染）：** 角色设定图采用影棚柔光、均匀照明、结构清晰、材质本色还原；严禁逆光、强烈阴影、过曝、炫光、彩色环境光污染，确保后续分镜取色一致。

角色设定：{{appearance}}"""

SCENE_SHEET = """{{style}}，

场景资产设定板：生成一张可供后续分镜反复引用的「统一版式场景视觉圣经」，不是单张漂亮风景照。
必须严格参考“影视美术设定板 / 场景设计板”的排版，使用浅灰或中性灰底板、清晰分区、白色细分隔线，
并加入醒目的中文文字标注。文字必须清晰、端正、无乱码、无错别字，不要生成英文、logo、水印或 UI。

固定版式（所有背景图都必须一致，严格按以下区域排版）：
1. 顶部横向区域：标题文字「多角度展示区」。下方固定排列 4 张同一场景小图，分别在左下角标注
   「正视」「侧视」「俯视」「仰视」。四张图必须是同一地点、同一时代、同一空间布局，只改变视角。
2. 中部左侧竖向区域：标题文字「光影氛围区」。固定竖向排列 3 张同一场景小图，分别标注
   「白天」「黄昏」「夜晚」。只改变光线与色温，不改变建筑结构、道具位置和材质。
3. 中部中央最大区域：标题文字「主视觉区域」。放唯一主图，用超广角/远景建立镜头完整展示该场景。
   主图必须从地面到顶部、从左到右边界完整入画，不裁切主体建筑/地貌；前景—中景—远景层次清楚，
   能看出入口、主要活动区、陈设/道具相对位置、可行动路线、光源方向与材质基调。
4. 中部右侧区域：标题文字「配色参考区」。固定竖向排列 3 个大色块，并在右侧标注：
   「主色：____」「辅色：____」「点缀色：____」。横线处用符合场景的中文色名替换，例如
   工业灰、铁锈棕、帆布白、黄土色、木褐色、青灰色等。色块必须来自场景真实材质。
5. 底部横向区域：标题文字「细节特写区」。固定横向排列 4 张特写图，分别标注
   「关键道具」「材质纹理」「装饰元素」「局部细节」。特写内容展示关键陈设、墙面/地面/木材/金属/
   布料/石材等材质纹理、磨损、污渍、边角结构或可复用道具。

统一性要求：
- 整张图必须像一张专业场景设定板，分区稳定、文字清晰、信息密度高但不杂乱。
- 所有分区必须同一套场景、同一套空间结构、同一套材质与时代设定；不能把不同地点拼贴在一起。
- 版式必须稳定：顶部多角度，中部左光影，中部中央主视觉，中部右配色，底部细节特写；不要改成其它布局。
- 无人物、无动物、无剧情动作；只做背景/场景资产参考。
- 文字标注只用于设定板说明，不能遮挡场景主体和关键材质细节。

场景设定：{{desc}}"""

PROP_SHEET = """{{style}}，

道具参考图：中性背景下的单体道具特写，多角度清晰展示形制、年代、材质与细节，无人物、无文字、无水印。
严格遵循设定的年代与形制，避免年代/材质穿帮（如：古代青铜酒爵，绝非现代玻璃高脚杯）。

道具设定：{{desc}}"""

# ── 连续性引擎模板（Phase 5）─────────────────────────────────────────────────
HANDOFF_DECISION = """你是动漫分镜连续性导演兼剪辑师。你的任务不是“尽量连续”，而是判断两镜之间怎样衔接最像成熟影视作品：观众看得懂、情绪不断、空间不乱、人物与道具不穿帮。

工作方式：
1) 先读上一镜状态：它结束时人物在哪里、看向哪里、手里有什么、情绪停在什么点、镜头给观众留下了什么空间信息。
2) 再读本镜需求：本镜是继续同一个动作、换角度反打、情绪推进、信息揭示、动作爆发，还是进入新段落。
3) 最后选择桥接材料：只选择真正能帮下一镜成立的材料。不要为了“连续性”机械勾选；也不要在需要空间锚点时偷懒切镜头。

四种策略的专业用途：
- 切镜头 / 景别切换：适合普通反打、情绪推进、信息点转移、同场景换角度。它是影视剪辑里的正常衔接，不代表连续性差。
- 上一镜尾帧：只适合明确连续动作、长镜头、同一运动轨迹必须接上的情况。尾帧如果不是本镜自然起幅，不要用。
- 站位图：用于同一场景内维持人物左右关系、视线轴线、距离、遮挡、前后层次、道具相对位置。它是调度参考，不是成片视角。
- 导演图：用于复杂打斗、复杂运镜、多人/多道具调度、关键剧情转折、新段落构图蓝本。普通镜头不要滥用。

判断时必须考虑：
- 空间轴线：是否要维持 180 度关系、左右方位、视线方向、人物距离。
- 动作连续性：上一镜动作是否还没完成，本镜是否必须接同一动作的下一拍。
- 情绪节拍：本镜是延续上一镜情绪，还是需要切出一个新的强调点。
- 叙事清晰度：观众是否需要先重新建立环境/人物关系。
- 画面负担：参考图越多越容易互相牵制；只选真正必要的桥接材料。
- 生成风险：尾帧会强绑定起幅，站位图只锁空间关系，导演图锁复杂构图；按用途选择。

仅输出 JSON（不要额外文字）：
{
  "scene_cut": true/false,
  "long_take": true/false,
  "use_tail_frame": true/false,
  "use_staging": true/false,
  "use_director_board": true/false,
  "director_evidence": {
    "sufficient": true/false,
    "evidence": ["只有判定需要导演图时填写，列出2-4条具体证据，如：多人交叉运动、复杂运镜起止点、关键道具转移、剧情反转需要预构图"],
    "risk_if_no_director": "不用导演图会造成的具体风险；证据不足时写空字符串"
  },
  "shot_size": "建议景别，如：全景/中景/近景/特写/过肩/反打",
  "camera_hint": "具体到起幅与衔接动作的一句话，例如：从上一镜右侧人物视线方向切入过肩中景，保持左右关系但换景别推进情绪",
  "reason": "用专业但具体的一句话说明：为什么这样衔接最自然、避免什么穿帮或混乱"
}

硬性约束：
- 新场景/时间跳跃：scene_cut=true，use_tail_frame=false；除非本镜是复杂新段落，否则不用导演图。
- 非长镜头/非连续动作：不要使用尾帧。
- 同场景同人物但只是普通对话反打：优先切镜头或站位图，不要尾帧。
- 有两人以上对峙、移动、遮挡、视线轴线、道具交接：优先考虑站位图。
- 只有证据足够时才启用导演图：打斗/追逐/复杂推拉摇移/一镜到底/多人交叉调度/关键道具转移/关键剧情反转等必须至少命中明确证据；普通转身、仰拍、正面、眼神、普通换角度、普通对话反打，不得判为导演图。
- 如果 use_director_board=true，director_evidence.sufficient 必须为 true，evidence 至少写 2 条具体证据；如果证据不足，use_director_board=false，并说明改用站位图或普通切镜。
- 多种策略可组合，但组合必须有清楚用途；不要“全选”。

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
站位参考图（blocking reference）：基于随附的场景参考图和本镜信息，绘制本镜各人物在场景中的相对站位、朝向、视线轴线、镜头方向、移动路径与关键道具位置。保持与场景参考图的空间结构、入口、主要陈设、材质气氛一致；不要生成鸟瞰底图，不要照抄参考图视角，不要写文字标签，无水印。
本镜场景：{{scene}}
在场人物与站位：{{characters}}
需保持的空间/朝向关系（承接上一镜）：{{prev_blocking}}
桥接变量（只用于提炼站位逻辑，不写成画面文字）：{{bridge_context}}
道具位置：{{props}}"""

DIRECTOR_BOARD = """{{style}}，
导演分镜信息图（16:9 横版蓝图）：用于指导本段落生成，包含主场景定调、人物区（引用 @ 人物）、俯视调度站位、若干关键镜头分格、道具区（强调 $ 道具形制年代材质防穿帮）、灯光情绪标注、以及"下一段接口"（本段最后人物姿态/站位/道具状态/情绪）。严格承接上一段接口，避免上下割裂。
本段场景：{{scene}}
人物：{{characters}}
道具：{{props}}
上一段接口（必须承接）：{{prev_handoff}}
桥接变量（只提炼为构图、调度、动作路径，不原样写字）：{{bridge_context}}
本段动作概述：{{action}}"""

CONTINUITY_REVIEW = """你是动漫连续性复核员。下面给出相关图片与文字上下文，复核本镜与上一镜的衔接。

复核原则：
- 本项目默认用「切镜头 / 景别切换」衔接，不追求逐镜首尾帧像素相接。
- 若本镜是普通切镜头：重点看人物外形/服装是否一致、道具形制是否未穿帮、光线与整体风格是否统一、景别是否有合理变化。
- 若本镜标注为必须连续的长镜头：才要求运动/构图/光线与上一镜尾帧自然延续、无跳切。
- 不要因为普通镜头没有逐帧承接上一镜尾帧就判失败；真正的问题是本应切镜头却硬做首尾相接，导致跳帧、重复、色差或空间错乱。
- 复核建议要服务于可执行修正：换景别、改站位图、重生导演图、调整提示词。

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
VIDEO_PROMPT = """{{style}}

【剧情提要】
{{image_prompt}}

【镜头动态】
{{dynamics}}"""

# ── 自适应分集模板（导入时启发式识别不到分隔符 → 介入 LLM 判断分集边界）──────────
EPISODE_SPLIT = """你是剧集编排师。下面是一篇剧本/小说按段落切好的【片段列表】，每行格式为「[序号] 段首文字」。
请通读后判断它应被划分成哪几「集」，并找出每一集的起始段落序号（start_seq）。

【输入边界】
- 本任务使用固定分集标记 `<<<分集标记n>>>` 来表示集与集之间的边界，n 为分集编号。
- 若片段列表或原文中已经插入该标记，则必须优先按该标记划分分集。
- 若未插入该标记，再依据剧情段落/场景或时间跳跃/标题行（如「第一章」「第N集」「Episode N」）等线索综合判断。
- 不要把空行当作分集边界；空行只是排版。

规则：
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
SHOT_PROMPT_INFER = """你是资深动漫分镜与提示词工程师。请基于下列信息，为「单个分镜」生成更干净的出图词与视频词，并以严格 JSON 返回。

【输出原则】
- 尊重本软件生态：资产参考图、@图N 编号、站位图/导演图/首帧图的上传与裁剪由引擎处理；你只写可编辑提示词正文。
- 只保留本镜真正需要的字段，去掉与本次任务无关的剧情复述、无关设定、无关资产说明。
- 图片提示词只负责静帧构图与主体锚定；视频提示词只负责动作、衔接、节奏与收束。
- 连续性元素只保留最小必要集；桥接变量只用于提炼承接重点，不要原样复读，不要把分析过程写进最终提示词。
- 提示词应符合影视/动漫的自然规律：起幅清楚、动作顺滑、连续性明确，但不过度炫技、不乱切镜头。
- 不要输出「【画面定义】」「@图1」「@图2」「reference_image_urls」「【输出要求】」或任何给提示词工程师看的元说明；引擎会另行添加参考图编号。

【整体风格】{{style}}
【本镜结构】
- 镜号：{{shot_no}}；目标时长：{{duration}} 秒
- 景别/机位：{{camera}}
- 场景：{{scene}}
- 出场角色：{{characters}}
- 关键道具：{{props}}
- 动作/事件：{{action}}
- 情绪：{{emotion}}
- 台词/对白/旁白（可能为空）：{{dialogue}}
【上一镜收尾接口】{{prev_handoff}}
【连续性桥接变量】{{bridge_context}}
【本镜交给下一镜的接口】{{handoff}}
【本镜关联资产（仅保留真正会被画面使用的参考项）】
{{assets_desc}}

要求：
1) 引用统一用「对象名（@名）」轻量标注，且只能引用【本镜关联资产】或【连续性桥接变量】明确存在的对象；不得自造新 @名。库内 #/$ 分类符号不得出现在输出里。
   - 普通资产：写「角色名（@角色名）」「场景名（@场景名）」「道具名（@道具名）」，不要只写裸 @名。
   - 连续性素材：只有桥接变量明确选择尾帧/站位图/导演图时，才可写「首帧（@首帧）」「站位图（@站位图）」「导演图（@导演图）」；否则不要提。
2) 只保留可在画面中呈现的信息；真实台词、画外旁白、内心OS/吐槽只能放在 video 的台词/旁白段，不能放进 image；台词/旁白段必须原样保留说话人标记，例如「对白-@李阁：下一场是谁？」「内心OS-@雷晶：不能慌」，严禁改写成无主语的「对白：台词」。
3) image（首帧静帧出图词）：只写起幅静帧——主体「对象名（@名）」+场景「对象名（@名）」+构图/景别+光线/色调+情绪；不写运镜与动作过程，不写台词。
4) video（视频生成词）：严格按以下分段，时间轴必须覆盖 0→{{duration}} 秒，动作自然铺满，不要为了炫技写过多碎切：
【首帧锚定】起幅静帧（与 image 一致的主体+场景）
【运镜/景别】在 {{camera}} 基础上给出自然运镜；普通镜头优先切镜头/景别切换，只有确实需要连续动作时才写无缝衔接
【动作过程·时间轴】按 0→{{duration}} 秒分配：短镜单段写「0–{{duration}}秒：…」；≥6秒且动作较多时分 2–4 段，每段标注时间区间与（起势/核心/转折/收束）
【台词/旁白】仅当有声音内容时写；逐句保留说话人标记，格式为「对白-@角色名：台词」「画外旁白-@旁白：内容」「内心OS-@角色名：内容」；对白为角色开口，画外旁白/内心OS/吐槽为画外或内心音轨，不出字幕
【衔接】承接上一镜（{{prev_handoff}}），参考桥接变量（{{bridge_context}}）提炼必要承接点；写明本镜如何自然接到下一镜
【节奏】总时长约{{duration}}秒，{{emotion}}情绪，画面与动作保持顺滑
【防穿帮/一致性】{{consistency}}

严格输出 JSON（不要任何额外文字）：{"image": "…", "video": "…"}"""

SHOT_PROMPT_INFER_10S_TIMELINE = """你是资深短剧分镜节拍师。请只优化「单个分镜」视频词中的【动作过程·时间轴】这一段，并以严格 JSON 返回。

【硬性边界】
- 你只能输出可直接放在【动作过程·时间轴】后面的正文，不要输出 image，不要输出完整 video，不要输出【剧情提要】【首帧锚定】【运镜/景别】【台词/旁白】【衔接】【节奏】【防穿帮/一致性】等任何其他词段。
- 除【动作过程·时间轴】外，其他词段会继续使用系统兜底生成的内容；你不得改写、补写、评价或复述这些词段。
- 兜底视频提示词、兜底镜头动态变量源都只是只读上下文，不能被你“回写”或整体重写；你的结果会作为 AI 推理覆盖层单独保存。
- 只使用【本镜剧情原文】【本镜结构】【当前兜底时间轴】【兜底镜头动态变量源】【本镜关联资产】里已有的人物、场景、道具、动作和声音信息；不得新增剧情、地点、角色、道具、心理活动或未登记 @ 对象。
- 输出中的引用沿用「对象名（@名）」或原有 @ 名称，禁止出现 @图N、reference_image_urls、#/$ 分类符号。

【本镜剧情原文】
{{src_text}}
【本镜结构】
- 镜号：{{shot_no}}；目标时长：{{duration}} 秒
- 景别/机位：{{camera}}
- 场景：{{scene}}
- 出场角色：{{characters}}
- 关键道具：{{props}}
- 动作/事件：{{action}}
- 情绪：{{emotion}}
- 台词/对白/旁白（只作为动作节拍参考，不能改写台词）：{{dialogue}}
【上一镜收尾接口】{{prev_handoff}}
【连续性桥接变量】{{bridge_context}}
【本镜交给下一镜的接口】{{handoff}}
【本镜关联资产】
{{assets_desc}}
【兜底视频提示词（只读，不要复述，不要改写除时间轴外任何词段）】
{{fallback_video}}
【兜底镜头动态变量源（只读，用于理解首帧、运镜、衔接、节奏、防穿帮等源信息）】
{{fallback_dynamics}}
【当前兜底时间轴】
{{fallback_timeline}}

【10S模板本地化适配规则】
1) 输出目标：把本镜原文和兜底动态源重新设计成「10秒镜头组」级别的动作时间轴；最终只返回时间轴正文。不要输出场景/角色/物品/风格字段，因为本软件已由资产系统和兜底提示词处理。
2) 引用方式：原 10S 模板里的「@图片1/@图片2」在本软件中一律改为「对象名（@名）」或兜底里已有的 @名；禁止生成 @图N、@图片N、reference_image_urls、#/$ 分类符号。
3) 镜头数和时长：目标 {{duration}} 秒必须从 0 秒连续到 {{duration}} 秒。目标 10 秒时，慢节奏/情绪戏通常 3 镜，动作/冲突/信息密集通常 4 镜，极简反应可 2 镜，极复杂最多 5 镜；每镜一般 1–4.5 秒，爆点特写可 0.5–2 秒，但总和必须正好等于 {{duration}} 秒。
4) 推荐 10 秒节拍：
   - 3镜：0–3秒（镜头1·起势）→3–7秒（镜头2·核心动作）→7–10秒（镜头3·收束钩子）。
   - 4镜：0–2秒（镜头1·起势）→2–5秒（镜头2·核心动作）→5–8秒（镜头3·转折强化）→8–10秒（镜头4·收束钩子）。
   - 2镜：0–5秒（镜头1·起势）→5–10秒（镜头2·收束钩子）。
   - 5镜：前4镜承载起势/推进/转折/反应，第5镜 0.5–1.5 秒卡在表情、关键物或下一镜接口。
5) 每个镜头必须按 10S 模板的「镜头最小单位」落地，至少包含：对准谁、对准哪里、表情/状态、主体动作、跟随动作或连带反应、画面信息点、镜头位置与运动。不要只写“冲上去/对峙/转身”这种空动作。
6) 每个镜头建议格式：`0–2秒（镜头1·起势）：【景别+运镜方式·构图方式】主体与部位，表情/姿态，主体动作，跟随动作/连带反应，空间关系或关键物，光线来源与色彩，必要声效，情绪百分比。`
7) 镜头设计要吸收 10S 模板的部位库和商业短剧规则：
   - 情绪用眼睛、瞳孔、眼眶泪光、嘴角、下颌线、呼吸、喉结、手指细节表现。
   - 紧张用指节发白、攥衣角、脚尖后撤、鞋跟轻挪、吞咽、停顿表现。
   - 压迫关系用高低机位、前景遮挡、身体前倾、肩线、手部压制、距离变化表现。
   - 关键物先给道具特写或状态变化，再给角色反应；结尾钩子优先停在眼神骤变、关键物、动作结果或下一镜承接锚点。
8) 构图与光影必须服务剧情，不能泛泛写“电影感”：可用三分法、中心、对角线、框中框、引导线、留白、填满、前景压迫、层次纵深、S形/碎片/窥视构图；光线写清来源与方向，如侧逆光、顶侧光、暖柔光、冷月光、屏幕光、缝隙光、斑驳光、明暗交界、冷暖对撞。
9) 运镜和剪辑：用中文可执行术语，优先平移、推拉、轻摇、跟拍、过肩、低机位、俯拍/仰拍、快速切入、急停、轻微手持、弧线绕等；禁止输出 Crash In / Rack Focus / OTS / 焦段 / 光圈 / 景深参数等英文或器材参数。
10) 台词/旁白/OS只可来自 {{dialogue}}，不能新增或改写。若需要在某镜头中标注声音，可写【配音指令：声源｜状态｜语气特点｜台词原文】，台词原文必须与 {{dialogue}} 完全一致；也可以只写“声画同步，台词见【台词/旁白】”。不要把新台词塞进时间轴。
11) 声效可以写进时间轴，用于动作点和情绪点，例如脚步、布料摩擦、刀刃轻响、呼吸停顿、杯盘轻碰、环境骤静；禁止新增视频背景音乐，BGM 段由外层提示词处理。
12) 心理描写必须视觉化：把“害怕/震惊/犹豫/沉默/心寒”转成眼神、手、呼吸、站位、道具互动和动作停顿；禁止写“他想到/她意识到/内心觉得”。
13) 连续性：第一镜承接【首帧锚定/上一镜收尾接口】，中间镜头按可见动作结果推进，最后一镜落在【本镜交给下一镜的接口】或能被下一镜接住的视觉锚点。不得与兜底首帧、运镜、衔接、防穿帮冲突。
14) 文字密度：每个镜头 50–220 个中文字，1–3 句；整条 timeline 尽量少于 1000 字。宁可具体写可见动作和画面信息，也不要堆抽象形容词。
15) 禁用元素必须内化到时间轴：无字幕、无水印、无文字元素、无视频背景音乐；不得加入原文外的人、物、地点、动作、血腥/擦边/敏感尺度。

【输出格式强制】
- 只输出 JSON 对象，字段名只能是 timeline。
- timeline 内每个镜头都必须有连续时间区间和镜头序号，例如：
{"timeline": "0–2秒（镜头1·起势）：【全景缓慢推进·三分法纵深构图】…；2–5秒（镜头2·核心动作）：【中景侧跟·前景压迫构图】…；5–8秒（镜头3·转折强化）：【特写急停·填满构图】…；8–10秒（镜头4·收束钩子）：【大特写锁定·中心凝视构图】…"}"""

# ── 分镜模式（按剧本类型的内置拆解预设）──────────────────────────────────────
# 每个预设 = 基础 batch_decompose 提示词 + 该剧本类型的「分镜侧重」补充段，作为
# 内置可选方案随安装下发；用户仍可在「提示词模板 · 分批拆解」里新增/编辑自定义预设。
# 提升 _v 会刷新内置预设 body（与 default 同机制），不动用户自建预设。
_DECOMPOSE_STYLE_HINTS: dict[str, tuple[str, str]] = {
    "bd_dialogue": ("对白密集", """

【本模式侧重·对白密集剧】
- 以「说话回合」为切镜依据：一次连贯发言/一次问答为一镜，避免把一句台词拆到两镜。
- 严格按语速折算字数（≈5字/秒）确认台词能在 {{shot_target}} 秒内念完；念不完则拆，但拆点落在自然语气停顿处。
- 多用近景/特写表现表情与反应镜头（reaction shot），并为听者反应留出独立镜头。"""),
    "bd_action": ("动作爽文", """

【本模式侧重·动作爽文剧】
- 大位移/打斗动作拆成「起势→核心打击→落位/收束」连续镜头，防止瞬移、滑步、穿模。
- 镜头偏短、节奏快；台词极简，优先用动作与运镜叙事。
- 关键命中/转折给特写或慢镜头强调，机位强调冲击力（推/甩/跟随）。"""),
    "bd_guofeng": ("古风玄幻", """

【本模式侧重·古风玄幻剧】
- 注重环境氛围与法术/灵气特效的铺陈镜头，可给空镜/全景交代场景与时空。
- 服饰、法器、灵兽等道具一致性优先，命名严格沿用故事圣经触发符。
- 情绪渲染与转场可放缓节奏，留足运镜时间表现意境。"""),
}


def _builtin_decompose_presets() -> list[dict]:
    return [
        {"id": pid, "name": name, "body": BATCH_DECOMPOSE + suffix,
         "_v": DEFAULT_TEMPLATES["batch_decompose"]["version"]}
        for pid, (name, suffix) in _DECOMPOSE_STYLE_HINTS.items()
    ]


def _seed_decompose_presets(templates: dict) -> bool:
    """Ensure built-in 分镜模式 presets exist on batch_decompose, refreshing their
    body when the base template version bumps. User-added presets are untouched."""
    t = templates.get("batch_decompose")
    if not t or not isinstance(t.get("presets"), list):
        return False
    changed = False
    existing = {p.get("id"): p for p in t["presets"]}
    for bp in _builtin_decompose_presets():
        cur = existing.get(bp["id"])
        if cur is None:
            t["presets"].append(bp)
            changed = True
        elif cur.get("_v") != bp["_v"]:
            cur["body"] = bp["body"]
            cur["_v"] = bp["_v"]
            cur.setdefault("name", bp["name"])
            changed = True
    return changed


def _seed_character_presets(templates: dict) -> bool:
    """Keep the legacy character sheet available while the new card stays default."""
    t = templates.get("character_sheet")
    if not t or not isinstance(t.get("presets"), list):
        return False
    changed = False
    legacy = {
        "id": "legacy_turnaround",
        "name": "旧版三视图+四头像",
        "body": CHARACTER_SHEET_LEGACY,
        "_v": 1,
        "_builtin": True,
    }
    cur = _find_preset(t, legacy["id"])
    if cur is None:
        t["presets"].append(legacy)
        changed = True
    elif cur.get("_v") != legacy["_v"]:
        cur["body"] = legacy["body"]
        cur["_v"] = legacy["_v"]
        cur["_builtin"] = True
        cur.setdefault("name", legacy["name"])
        changed = True
    elif not cur.get("_builtin"):
        cur["_builtin"] = True
        changed = True
    if not t.get("active"):
        t["active"] = "default"
        changed = True
    return changed


def _seed_shot_infer_presets(templates: dict) -> bool:
    """Provide a 10S timeline-focused inference preset for backend overlay use."""
    t = templates.get("shot_prompt_infer")
    if not t or not isinstance(t.get("presets"), list):
        return False
    changed = False
    preset = {
        "id": "ten_second_timeline",
        "name": "10S时间轴优化",
        "body": SHOT_PROMPT_INFER_10S_TIMELINE,
        "_v": 5,
        "_builtin": True,
    }
    cur = _find_preset(t, preset["id"])
    if cur is None:
        t["presets"].append(preset)
        changed = True
    elif cur.get("_v") != preset["_v"]:
        cur["body"] = preset["body"]
        cur["_v"] = preset["_v"]
        cur["_builtin"] = True
        cur.setdefault("name", preset["name"])
        changed = True
    elif not cur.get("_builtin"):
        cur["_builtin"] = True
        changed = True
    return changed


DEFAULT_TEMPLATES = {
    "global_analysis": {
        "name": "全局分析（故事圣经）",
        "stage": "stage1",
        "body": GLOBAL_ANALYSIS,
        "variables": ["source_type", "full_text"],
        "version": 11,
    },
    "batch_decompose": {
        "name": "分批拆解（结构化分镜）",
        "stage": "stage2",
        "body": BATCH_DECOMPOSE,
        "variables": ["story_bible", "prev_handoff", "chunk", "shot_target", "shot_chars"],
        "version": 11,
    },
    "manual_shot_structure": {
        "name": "手动分镜·单镜结构化",
        "stage": "stage2",
        "body": MANUAL_SHOT_STRUCTURE,
        "variables": ["story_bible", "prev_handoff", "shot_text", "shot_target"],
        "version": 3,
    },
    "character_sheet": {
        "name": "人物参考图（角色设定卡）",
        "stage": "asset",
        "body": CHARACTER_SHEET,
        "variables": ["style", "name", "appearance", "desc", "voice"],
        "version": 3,
    },
    "scene_sheet": {
        "name": "场景参考图（多角度设定板）",
        "stage": "asset",
        "body": SCENE_SHEET,
        "variables": ["style", "desc"],
        "version": 5,
    },
    "prop_sheet": {
        "name": "道具参考图（防穿帮）",
        "stage": "asset",
        "body": PROP_SHEET,
        "variables": ["style", "desc"],
    },
    "handoff_decision": {
        "name": "衔接决策（切镜头/尾帧/站位图/导演图平权）",
        "stage": "continuity",
        "body": HANDOFF_DECISION,
        "variables": ["prev_state", "scene", "characters", "props", "action", "camera", "handoff"],
        "version": 3,
    },
    "staging_diagram": {
        "name": "站位图（俯视调度）",
        "stage": "continuity",
        "body": STAGING_DIAGRAM,
        "variables": ["style", "scene", "characters", "prev_blocking", "bridge_context", "props"],
        "version": 2,
    },
    "director_board": {
        "name": "导演分镜信息图",
        "stage": "continuity",
        "body": DIRECTOR_BOARD,
        "variables": ["style", "scene", "characters", "props", "prev_handoff", "bridge_context", "action"],
        "version": 2,
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
        "variables": ["style", "image_prompt", "dynamics", "first_frame", "action_timeline",
                       "transition", "handoff", "camera", "action", "emotion",
                       "duration", "dialogue", "scene", "characters", "props",
                       "bridge_context", "consistency"],
        "version": 9,
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
                       "prev_handoff", "bridge_context", "handoff", "assets_desc", "consistency"],
        "version": 9,
    },
}


# ── preset helpers ──────────────────────────────────────────────────────────
# Each template owns a list of `presets` (多套备选方案); one is `active`.
# Legacy stored templates carry a single `body` and are migrated on load.

def _default_template_shell(key: str) -> dict:
    v = DEFAULT_TEMPLATES.get(key, {})
    return {"name": v.get("name", key), "stage": v.get("stage", "")}


def _coerce_preset(raw: Any, idx: int = 0) -> dict:
    if isinstance(raw, dict):
        body = str(raw.get("body") or "")
        pid = str(raw.get("id") or ("default" if idx == 0 else uuid.uuid4().hex[:12]))
        name = str(raw.get("name") or ("默认方案" if pid == "default" else f"方案{idx + 1}"))
        out = {"id": pid, "name": name, "body": body}
        if "_v" in raw:
            out["_v"] = raw.get("_v")
        if raw.get("_builtin"):
            out["_builtin"] = True
        return out
    return {
        "id": "default" if idx == 0 else uuid.uuid4().hex[:12],
        "name": "默认方案" if idx == 0 else f"方案{idx + 1}",
        "body": str(raw or ""),
    }


def _coerce_template_entry(key: str, raw: Any) -> tuple[dict, bool]:
    """Normalize old/corrupt stored template entries to the preset schema."""
    changed = False
    base = _default_template_shell(key)
    if isinstance(raw, dict):
        t = dict(raw)
    elif isinstance(raw, list):
        t = {
            **base,
            "presets": [_coerce_preset(p, i) for i, p in enumerate(raw)],
            "active": "default",
        }
        changed = True
    elif isinstance(raw, str):
        t = {**base, "body": raw}
        changed = True
    else:
        t = dict(base)
        changed = True

    if not isinstance(t.get("name"), str) or not t.get("name"):
        t["name"] = base["name"]
        changed = True
    if not isinstance(t.get("stage"), str):
        t["stage"] = base["stage"]
        changed = True

    presets = t.get("presets")
    if isinstance(presets, list) and presets:
        clean = [_coerce_preset(p, i) for i, p in enumerate(presets)]
        if clean != presets:
            t["presets"] = clean
            changed = True
    return t, changed


def _coerce_templates_root(saved: Any) -> tuple[dict, bool]:
    """Normalize top-level legacy/corrupt templates.json data."""
    if isinstance(saved, dict):
        return dict(saved), False
    out: dict = {}
    changed = True
    if isinstance(saved, list):
        for item in saved:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("id") or "")
            if key:
                out[key] = item
    return out, changed


_DIALOGUE_HANDOFF_ORDER_RE = re.compile(
    r"(?ms)^(?P<handoff>\s*【衔接】[^\n]*(?:\n(?!\s*【)[^\n]*)*)\n"
    r"(?P<dialogue>\s*【台词/旁白】[^\n]*(?:\n(?!\s*【)[^\n]*)*)"
)


def _dialogue_before_handoff(body: str) -> str:
    """Move dialogue/voice blocks before handoff blocks inside prompt templates."""
    return _DIALOGUE_HANDOFF_ORDER_RE.sub(r"\g<dialogue>\n\g<handoff>", body or "")


def _migrate_dialogue_handoff_order(templates: dict) -> bool:
    """Keep persisted presets aligned with the current video section order."""
    changed = False
    for key in ("video_prompt", "shot_prompt_infer"):
        t = templates.get(key)
        if not isinstance(t, dict):
            continue
        for p in t.get("presets", []):
            if not isinstance(p, dict):
                continue
            body = str(p.get("body") or "")
            new_body = _dialogue_before_handoff(body)
            if new_body != body:
                p["body"] = new_body
                changed = True
    return changed


def _migrate(templates: dict) -> bool:
    """In-place upgrade legacy single-`body` templates to the preset model.
    Returns True if anything changed."""
    changed = False
    for key in list(templates.keys()):
        t, coerced = _coerce_template_entry(key, templates.get(key))
        if coerced:
            templates[key] = t
            changed = True
        if "presets" not in t or not isinstance(t.get("presets"), list) or not t["presets"]:
            body = t.pop("body", "") or DEFAULT_TEMPLATES.get(key, {}).get("body", "")
            t["presets"] = [{"id": "default", "name": "默认方案", "body": body}]
            t["active"] = "default"
            t.pop("variables", None)  # was derived; recomputed on decorate
            changed = True
        if not t.get("active") or not any(p.get("id") == t["active"] for p in t["presets"] if isinstance(p, dict)):
            t["active"] = t["presets"][0]["id"]
            changed = True
    if _migrate_dialogue_handoff_order(templates):
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
    return next((p for p in t.get("presets", []) if isinstance(p, dict) and p.get("id") == preset_id), None)


def _active_preset(t: dict) -> dict:
    presets = t.get("presets")
    if not isinstance(presets, list) or not presets:
        body = str(t.pop("body", "") or "")
        presets = [{"id": "default", "name": "默认方案", "body": body}]
        t["presets"] = presets
        t["active"] = "default"
        t.pop("variables", None)
    return _find_preset(t, t.get("active")) or presets[0]


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
                "is_builtin": p["id"] == "default" or bool(p.get("_builtin")),
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
    saved, root_changed = _coerce_templates_root(saved)
    if not saved:
        saved = {}
        for k, v in DEFAULT_TEMPLATES.items():
            saved[k] = {"name": v["name"], "stage": v["stage"]}
        _migrate(saved)
        _refresh_builtin_defaults(saved)
        _seed_decompose_presets(saved)
        _seed_character_presets(saved)
        _seed_shot_infer_presets(saved)
        write_json(TEMPLATES_FILE, saved)
    else:
        changed = root_changed
        # merge in any new default keys without clobbering user edits
        for k, v in DEFAULT_TEMPLATES.items():
            if k not in saved:
                saved[k] = {"name": v["name"], "stage": v["stage"]}
                changed = True
        if _migrate(saved):
            changed = True
        if _refresh_builtin_defaults(saved):
            changed = True
        if _seed_decompose_presets(saved):
            changed = True
        if _seed_character_presets(saved):
            changed = True
        if _seed_shot_infer_presets(saved):
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
    p = _find_preset(t, preset_id)
    if not p:
        raise ValueError(f"未知预设: {preset_id}")
    if p.get("id") == "default" or p.get("_builtin"):
        raise ValueError("内置方案不能删除，可切换使用或另存为新方案后编辑")
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


def get_preset_body(key: str, preset_id: str) -> str:
    """Return a specific preset body without changing the user's active preset."""
    templates = load_templates()
    if key not in templates:
        if key not in DEFAULT_TEMPLATES:
            raise ValueError(f"未知模板: {key}")
        v = DEFAULT_TEMPLATES[key]
        templates[key] = {"name": v["name"], "stage": v["stage"], "presets": [], "active": "default"}
    t = templates[key]
    p = _find_preset(t, preset_id)
    if not p:
        raise ValueError(f"未知预设: {preset_id}")
    return str(p.get("body") or "")


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


def get_template_body(key: str, preset_id: str | None = None) -> str:
    """Body of a preset (active by default) — what the engine actually uses.

    *preset_id* lets a caller pick a specific 分镜模式/方案 for one run without
    changing the persisted active preset; unknown ids fall back to active.
    """
    templates = load_templates()
    if key not in templates:
        if key not in DEFAULT_TEMPLATES:
            raise ValueError(f"未知模板: {key}")
        v = DEFAULT_TEMPLATES[key]
        templates[key] = {"name": v["name"], "stage": v["stage"], "body": v["body"]}
    t = templates[key]
    if "body" not in t and (not isinstance(t.get("presets"), list) or not t.get("presets")):
        t["body"] = DEFAULT_TEMPLATES.get(key, {}).get("body", "")
    p = (_find_preset(t, preset_id) if preset_id else None) or _active_preset(t)
    return p.get("body", "")
