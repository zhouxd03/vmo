# Batch Anime Studio — 技术参数 & AI 后续维护文档

> 用途：本文件是「换会话/换窗口也能无缝接手」的交接文档。记录系统架构、关键参数、
> 本轮对话的关键决策与改动、以及**后续待开发/待修复清单**。新接手的 AI 或开发者
> 请先读本文件，再动代码。
>
> 维护约定：每次重大改动后，更新对应章节 + 末尾「变更记录」。

---

## 0. 一句话定位

把一篇小说/剧本，**连续、可控地批量**生成大量分镜画面与视频，导出到剪映。核心难点是
**跨分镜的连续性**（角色/场景/道具不漂移、镜与镜衔接不跳帧不色差），由「故事圣经 +
资产参考图 + 衔接策略 + AI 复核闸门」共同保障。单机桌面程序（PyInstaller 打包，内置
ffmpeg + WebView2 引导）。

- 仓库：`github.com/zhouxd03/vmo`（main 分支）
- Release：`v0.1.0`（`https://github.com/zhouxd03/vmo/releases/tag/v0.1.0`）
- 本地开发路径（本机）：`C:\Users\Administrator\work\batch-studio\`

---

## 1. 架构总览

```
前端 (Vue3 + naive-ui, Vite)  ── npm run build → 静态产物
        │  HTTP (同源)
        ▼
后端 (Flask, 单进程, threaded)  ── backend/app.py
        ├─ routes_*.py            REST 接口
        ├─ core/                  数据与配置（projects/assets/batches/settings/
        │                         story_state/prompt_templates/webview2）
        └─ services/              业务逻辑（脚本解析/分集/分镜/连续性/生成/
                                   错误策略/ffmpeg/图床/剪映导出/pacing 等）
        │
        ▼
桌面外壳 (pywebview + WebView2)  ── run_desktop.py（frameless 自绘标题栏）
```

- **单进程托管**：前端 `npm run build` 出静态文件，由 Flask 直接托管（同源，无跨域）。
- **桌面壳**：`run_desktop.py` 起 Flask 线程 → 等 `/api/health` 就绪 → 开 pywebview 窗口
  （`gui="edgechromium"` 强制 WebView2，避免回退 MSHTML 白屏）。
- **数据落盘**：便携模式下 `data/`、`output/` 落在 exe 同级目录（非临时解包目录）。

### 1.1 数据结构（重要）

项目为**嵌套分集**结构：

```jsonc
{
  "id": "...",
  "story_bible": { "title", "logline", "style", "summary",
                   "characters": [...], "scenes": [...], "props": [...],
                   "continuity_constraints": [...] },
  "episodes": [
    { "name": "第1集", "source_type": "txt", "raw_text": "...",
      "parsed": { "segments": [ { "seq": 1, "text": "...", "time?": "..." } ],
                  "char_count": 1432 } }
  ]
}
```

- 分镜在**每集内**重新编号 `1..N`（非全局编号）。
- 前端读片段总数需**按 episodes 汇总**（历史 bug：旧代码读 `project.segments` → undefined → 报错）。

---

## 2. 关键产品参数（务必与代码保持一致）

| 参数 | 值 | 出处 | 说明 |
|---|---|---|---|
| 自动分集目标字数 | `TARGET_CHARS = 1440` | `services/episode_splitter.py` | ≈3 分钟/集，按 120字/15秒 ≈ 8字/秒 折算 |
| 末集并回阈值 | `MIN_TAIL_CHARS = 480` | 同上 | 尾集过短则并回上一集，避免极短尾集 |
| 分集标记识别 | 第X集/章/回/话/幕、EP/Episode/Chapter N | 同上 | **≥2 个标记**才按标记分集，否则按字数分 |
| 单分镜字数 | ~120 字/镜（≈15 秒） | `prompt_templates.batch_decompose` | 台词过多按情绪调语速 |
| 正常语速 | `BASE_RATE = 5.0` 字/秒 | `services/pacing.py` | |
| 情绪加速 | `×1.25`（急/怒/紧张/慌/惊/愤/激动/争吵/打斗/兴奋/催/喊/吼） | `pacing._FAST` | 秒数更短 |
| 情绪减速 | `×0.7`（悲/伤/哀/沉/庄/肃/平静/低落/沉思/深情/哽咽/缓/温柔/舒缓/凝重） | `pacing._SLOW` | 秒数更长 |
| 单镜时长钳制 | `[MIN_SEC=3, MAX_SEC=15]` 秒 | `pacing.py` | MAX=15 为视频模型单条上限 |
| 参考图画幅 | 居中裁剪到视频画幅（不拉伸） | `services/ffmpeg_util.crop_b64_to_aspect` | 防人物比例变形 |
| 导演图/站位图尺寸 | 随视频画幅取向（16:9→1536×1024 等） | `aspect_to_image_size` | 不再写死正方形 |

### 2.1 单镜时长算法（pacing.estimate_shot_seconds）

```
有台词： sec = 字数 / (5 × 情绪系数)        # 情绪系数 1.25/1.0/0.7
无台词： sec = clamp(动作字数/12, 3, 8)      # 中性默认
最终：   round(clamp(sec, 3, 15))            # 整数秒
```
该 `duration` 在 `script_analysis.py` 拆解时写入每个 shot，经 `batch_engine._shot_duration`
传给 `video_gen`（优先 shot 级，回退 batch 默认，钳制 [1,15]）。

---

## 3. 连续性 / 镜头衔接策略（本轮重点）

**背景结论（已实测）**：当前中转的 `seed-2.0fast` **不支持「首帧钉死」图生视频**——输入图被静默丢弃，
实际只做文生视频（请求 seed-2.0fast 却返回 `sora-v3-fast-*.mp4`，路由到了纯文生后端）。
因此「尾帧承接」在此端点做不到精确承接，强行用只会跳帧 + 色差。

**采用策略**：`services/continuity.decide_handoff_rule`

- 首镜 → 直接生成建场（多模态参考图建立角色/场景）。
- 同场景·普通镜 → **默认切镜头 / 景别切换**（重新生成，不承接尾帧）。
- 同场景·长镜头（命中「长镜头/一镜到底/无缝衔接」等标记）→ 才承接上一镜尾帧。
- 切场景 → 全新机位/景别建场。

返回字段：`scene_cut / use_tail_frame / use_staging / long_take / prefer_cut / camera_hint / reason`。

**AI 提示词同源**（避免只在代码事后改判）：
- `handoff_decision`：让 LLM 默认输出切镜/景别切换，仅长镜头 `use_tail_frame=true`，并输出
  `long_take / shot_size / camera_hint`。
- `continuity_review`（AI 复核闸门）：按「切镜/景别衔接是否自然」评判，不再因「未逐帧承接尾帧」判不过；
  反向提示「本该切镜却生硬首尾帧相接」。
- `batch_decompose`：要求 camera 给明确景别、相邻镜变换景别，仅必要时标注长镜头。

> 若未来更换为**真支持 Seedance-2 首尾帧**的端点：`video_gen` 的首帧双通道
> （`image_urls[0]` / `metadata.content[{role:first_frame}]`）代码思路是对的（本轮已回退，git 历史可查），
> 接回即可让长镜头承接生效。

---

## 4. 资产描述词规范（本轮强化，写在 `global_analysis` + 三张参考图模板）

**原则**
- 细节自动推演：原文简略时按身份与风格合理补全（「白衣」→「云纹暗花白色丝绸长袍，领口镶银边」）。
- 硬约束：严禁括号、严禁模糊词（可能/大概/也许/类似）；内容优雅得体，无血腥暴力色情。
- 结构化角色 appearance：性别 / 年龄数字 / 面部五官+肤质+妆容 / 发色+发质+发型+发饰 /
  身材 / 服饰材质+颜色+纹样+款式 / 配饰。
- 场景 desc：主体环境 + 光影氛围 + 具体陈设物 + 地点/时代/空间布局/材质。
- 道具 desc：材质 + 颜色 + 物理属性 + 形状 + 年代形制（防穿帮）。

**角色多形态拆分**（关键 bug 修复）
- 同一角色存在**显著外形差异**的多个形态（变身/年龄跨度/机甲/兽化）→ 拆成独立条目，
  用下划线区分：`@主角_成年`、`@主角_机甲`、`@主角_兽化`，每形态各自 appearance。
- **仅外形不变**的变化（情绪/表情/普通动作）**不拆**，仍用同一 `@主角`。

**去光污染（仅角色设定图）**：`character_sheet` 模板要求影棚柔光、均匀照明、结构清晰、材质本色还原；
严禁逆光/强阴影/过曝/炫光/彩色环境光污染。场景图仍可保留氛围/体积光。

**风格 `{{style}}` 变量**
- 可手动设定（前端故事圣经「风格」字段可编辑，PATCH `/api/projects/<pid>/story_bible`）。
- 留空则回退 AI 分析缺省值。重新分析不覆盖用户手设值。
- 全流程（资产图/导演图/视频提示词）统一吃这个值，不写死「3D国风」等具体画风。

---

## 5. 生成错误分类 + 智能重试（`services/error_policy.py`）

`classify(exc)` 按关键词 + HTTP 状态码判定，返回 `{category, code, status, retryable, abort_batch, friendly}`：

| 类别 | 可重试 | 中止整批 | 触发 | 处理 |
|---|---|---|---|---|
| `transient` | ✅ | ❌ | 超时/429/5xx/连接中断 | 递增退避重试（受 max_attempts） |
| `content` | ❌ | ❌ | 安全策略/违规/moderation/nsfw | 提示「请修改描述词」，同词重试必再败 |
| `fatal` | ❌ | ✅ | 401/403 密钥失效/额度耗尽 | 中止整批，剩余镜标「已跳过·未消耗调用」 |
| `param` | ❌ | ❌ | 400 时长/画幅非法 | 提示具体参数问题 |
| `unknown` | ✅ | ❌ | 其它 | 有限次重试 |

- `video_gen.py`/`image_gen.py`：解析 HTTP 错误体的 code/status/message，抛**结构化异常**（不再裸字符串）。
- `batch_engine._run_one`：重试闸门由分类器驱动 + 致命中止。
- 前端 `WorktableView.vue`：失败徽章悬停显示**友好可操作提示**（非裸 JSON）。

**原则**：不要「有错直接跳过」，而是按错误类型反馈；致命/内容类不空烧额度。

---

## 6. 打包 / 发布（Phase 7）

- `desktop.spec`（已纳入版本控制，原被 `*.spec` 忽略）：PyInstaller `--onedir`，内置
  ffmpeg 8.1.1 + ffprobe（经 `sys._MEIPASS` 解析），打包前端静态产物。
- WebView2：`backend/core/webview2.py` 首次运行检测 + 自动安装引导器（目标机缺 WebView2 会白屏）。
- 图标：`desktop_assets/app.ico`（多分辨率 16~256）。
- 数据：便携模式 `data/`、`output/` 落 exe 同级。
- 发行包**不含任何凭据**（用户在「设置/凭据库」自填）。
- `dist/`、`build/` **不入库**。

**构建步骤**
```bash
# 1. 前端
cd frontend && npm install && npm run build
# 2. 打包（仓库根目录）
pyinstaller desktop.spec
# 3. 压缩 dist/BatchStudio/ → BatchStudio-vX.Y.Z-win64.zip
# 4. 上传到 GitHub Release 资产
```

**本机开发运行（重要）**：本机 `python` 处于 **isolated 模式 (`-I`)**，忽略 `PYTHONPATH`/cwd。
`run_desktop.py` 顶部已加 `sys.path.insert(0, <repo root>)` 自举，故可直接：
```bash
cd C:/Users/Administrator/work/batch-studio && python run_desktop.py
```
（不要再依赖 `PYTHONPATH=.`，isolated 模式下无效。）

---

## 7. 关键文件地图

| 文件 | 职责 |
|---|---|
| `run_desktop.py` | 桌面入口：起 Flask + 等 health + 开 pywebview 窗口 + 窗口控制 API |
| `backend/app.py` | Flask app 工厂 |
| `backend/core/prompt_templates.py` | 所有 AI 提示词模板 + 版本刷新机制（只刷「default」预设，不动用户自建） |
| `backend/core/projects.py` | 项目 CRUD（含 episodes 嵌套） |
| `backend/core/webview2.py` | WebView2 运行时检测/安装 |
| `backend/routes_projects.py` | 项目接口（含 PATCH `/story_bible`） |
| `backend/services/episode_splitter.py` | 自动分集（标记优先，否则按字数） |
| `backend/services/pacing.py` | 单镜时长（语速×情绪） |
| `backend/services/script_analysis.py` | 两阶段 LLM：全局分析 + 分批拆解；写入每镜 duration |
| `backend/services/continuity.py` | 衔接决策（默认切镜/景别，长镜头才承接尾帧） |
| `backend/services/batch_engine.py` | 批量任务编排、重试闸门、按镜传 duration |
| `backend/services/error_policy.py` | 错误五分类 + 重试/中止策略 |
| `backend/services/video_gen.py` / `image_gen.py` | 生成调用 + 结构化错误 |
| `backend/services/ffmpeg_util.py` | 抽尾帧、画幅居中裁剪、画幅↔尺寸映射 |
| `backend/services/image_host.py` | 图床上传（catbox→litterbox→uguu→0x0→data 兜底） |
| `frontend/src/layouts/TitleBar.vue` | 自绘标题栏（`.pywebview-drag-region` 拖拽区 + 窗口控制） |
| `frontend/src/views/ImportView.vue` | 导入（触发自动分集） |
| `frontend/src/views/ScriptView.vue` | 剧本解析 + 故事圣经（含可编辑风格字段） |
| `frontend/src/views/WorktableView.vue` | 批量工作台（失败友好提示） |

模板版本（`DEFAULT_TEMPLATES`）：`global_analysis` v3、`batch_decompose` v3、`character_sheet` v2、
`scene_sheet`/`prop_sheet`/`handoff_decision`/`staging_diagram`/`director_board`/`continuity_review` v2。
**改提示词后务必 bump version**，老安装的 `templates.json` 才会自动刷新内置 default 预设。

---

## 8. 后续待开发 / 待修复清单（TODO，优先级从高到低）

### 8.1 [未完成] 窗口拖动（无边框标题栏）
- **现象**：用户报告打包版窗口拖不动。旧代码用了 Electron 的 `-webkit-app-region: drag`（pywebview 不认）。
- **本轮已改**：标题栏 `.brand`/`.spacer` 加 `.pywebview-drag-region`（pywebview 6.2.1 默认拖拽选择器）。
  机制：mousedown 命中拖拽区 → 监听 mousemove → 每次调 `window.move()`（经 JS↔Python 桥）。
- **已验证可用的部分**：JS↔Python API 桥正常（最小化按钮生效）；mousemove 事件确实送达 WebView2（侧栏 hover 高亮生效）。
- **仍存疑**：用**合成鼠标**拖拽无法稳定复现窗口移动（合成 mousemove 在按下态可能未被 WebView2 当作连续拖拽流）。
  **需在真机/打包后用真实鼠标验证**——按机制推断很可能已修好。
- **若真机仍拖不动的备选方案（更可靠）**：标题栏 `mousedown` → 调一个新 API，做 Win32
  `ReleaseCapture()` + `SendMessage(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)`，把拖动交给 OS 窗口管理器
  （与原生标题栏一致）。难点是取 pywebview 窗口的 hwnd（winforms 后端），可经 ctypes/win32 枚举或 pywebview 内部对象获取。
- 顺带：标题栏「最大化」按钮当前调 `toggle_fullscreen()`（全屏），建议改为 `maximize()/restore()` 切换更符合预期。

### 8.2 [未完成] 跨 tab 任务不中断 / 交互 UI 不失效
- **需求**：切换 tab/页面时，进行中的任务（分析/拆解/批量生成）不能中断、交互 UI 不能失效。
- **现状根因**：后端任务本就是独立线程（批量引擎线程池、分析 job），**服务端不受切页影响**；
  问题在前端——轮询写在各页面 `onMounted/onUnmounted`，**离开页面就清掉轮询** → 回来「看起来中断/进度丢失」。
- **方案**：把任务轮询与进度状态**上提到全局 Pinia store（App 级常驻）**，切 tab 不停轮询，
  回到页面自动续上实时进度与可交互状态。涉及 `WorktableView.vue`、`ScriptView.vue` 的轮询逻辑搬迁到 store。

### 8.3 [阻塞] 真支持图生视频首尾帧的端点
- 当前中转 seed-2.0fast 只做文生视频（见 §3）。换端点后接回首帧双通道即可让长镜头承接生效。**需用户决策/提供端点。**

### 8.4 [优化] 图床稳定性
- 公共图床（catbox/0x0/litterbox/uguu）会临时挂（412/503）。已做多家轮询 + data-URI 兜底，
  但上游视频模型是**服务端抓取 URL**，抓不到 data: URI 时图会被丢。首帧模式已加保护（无可用图床则报错不空烧）。
  建议长期方案：接一个稳定的对象存储（S3/OSS/R2）做图床。

---

## 9. 凭据 / 安全

- 视频/图像/LLM 凭据由用户在 app「设置/凭据库」自填，**不入库、不写死**。
- 历史问题：用户多次在聊天明文粘贴 GitHub token，**用完应在 `https://github.com/settings/tokens` 撤销**。
- 推 main 受保护：经代理直接推 main 会 403；用「推分支 + PR」流程。

---

## 10. 变更记录

- **2026-06（本轮）**：修复 3 个 bug（窗口拖动[返工中]、导入 reading 'length'、故事圣经字段缺失）；
  新增自动分集模块（`episode_splitter`）、单镜时长 pacing（语速×情绪）、风格可手设+AI 缺省、
  角色多形态拆分、资产描述词强化（结构化/推演/去光污染）；错误五分类+智能重试；参考图画幅居中裁剪；
  衔接策略默认切镜/景别（含 AI 提示词同源）；Phase 7 打包（PyInstaller + ffmpeg + WebView2 + 图标）；
  Release v0.1.0。`run_desktop.py` 加 isolated 模式 sys.path 自举。
- 待办见 §8。
