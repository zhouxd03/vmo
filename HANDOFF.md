# Batch Studio — 接力文档 (HANDOFF)

> 用途：换窗口/换会话后直接导入本文件即可无缝接力继续编程。最后更新：2026-06-03。

## 0. 一句话现状
桌面应用 Batch Studio v0.1.0（Vue3 前端 + FastAPI/pywebview 后端，打包 exe）。
当前在 PR #4 分支持续迭代「阶段二分镜拆解 + AI推理逐镜 + 资产并发生图 + 视频提示词规范化」。
**所有改动已 commit+push 到分支 `devin/1780466485-prompt-display-fix`（PR #4）。** 工作区无未提交源码改动（仅剩 `_tmp_*.ps1`/`_test_evidence/`/`*.md` 等本地脚手架，勿提交）。

## 1. 仓库 / 分支 / PR
- 仓库：`zhouxd03/vmo`（只看这个 repo）
- 当前分支：`devin/1780466485-prompt-display-fix`
- PR #4：https://github.com/zhouxd03/vmo/pull/4 （基于 PR #3；#3 合并后自动改基 main；仓库无 CI）
- 最新 commit：`20be51e fix: 资产并发线程安全+aerial持久化 / AI推理max_tokens / 场景鸟瞰图参考 / 分镜模式+手动拆解 / 原文映射 / 画面定义行预览 / subprocess隐藏窗口`
- Release v0.1.0 下载：https://github.com/zhouxd03/vmo/releases/download/v0.1.0/BatchStudio-v0.1.0-win64.zip
  - 更新 Release 资产需要 GitHub token（Contents 写权限）；Devin git 代理只代理 git 协议，api/uploads.github.com 走代理 404 → 上传 Release 必须用户提供 token。

## 2. 本地运行（Windows, 工作目录 `C:\Users\Administrator\work\batch-studio`）
```
# 后端+桌面（pywebview 窗口；绑定随机 localhost 端口，启动日志里看实际端口）
python run_desktop.py

# 仅前端构建
cd frontend && npm install && npm run build

# 打包 exe
pyinstaller (见仓库 .spec / 之前用的命令) → dist/BatchStudio-v0.1.0-win64.zip (~91MB)
```
CDP 调试：`http://localhost:29229`（脚本化浏览器）。桌面应用本身是 pywebview，不是 Chrome tab。

## 3. 实机凭据（用户已在 应用内 设置→凭据库 配置，存本机凭据库文件，重启不丢）
- **LLM 中转站**：`deepseek-v4-pro` @ `https://api.deepseek.com`（推理型模型！）
- **生图 API**：`gpt-image-2` @ `https://api.geeknow.top`
- 生视频 API：未配（本轮跳过视频生成测试）
- 测试项目：`测试分集小说`（3集；资产含 林夏/陈默/雨夜酒馆/黑色长柄伞）
- ⚠️ deepseek 是推理模型：`reasoning_content` 单独吃 `max_tokens`，逐镜推理已设 `max_tokens=6000`（batch_engine.py ~420），1400 会把 content 截空→JSON解析失败→回落。

## 4. 关键设计 / 数据流（务必先读，避免重复踩坑）
- **视频提示词由哪步生成**：打开工作台时引擎按模板**确定性合成**做即时预览/兜底（零额度，不调 LLM）；只有点 **AI推理 / AI推理全部** 才走**真·文本 LLM** 逐镜推理 `POST /infer_shot_prompt`，喂入①上一镜 handoff/尾帧/站位 ②本镜关联资产说明文本 ③结构字段+目标时长，剔除非画面元素，规范化输出。
- **阶段二分镜拆解**：两种模式——「自动(强制LLM)」走 `batch_decompose` 模板（无凭据则报错，不静默兜底）；「手动分镜」人工切段、LLM 逐段结构化填充。另有「分镜模式」下拉=按剧本类型的不同拆解模板（默认/对白密集/动作爽文/古风玄幻），可在模板页新增。改 LLM 模板务必 bump `version` 字段才会刷新。
- **资产并发生图**：`ThreadPoolExecutor`，并发数=设置项 `asset_gen_concurrency`(默认3)。`update_asset()` 用 `threading.RLock()` 串行化 project.json 读改写，白名单含 `aerial_image`/`ref_source`（曾因白名单漏字段导致鸟瞰图丢失）。
- **场景鸟瞰图**：场景资产除主图外再生成 `aerial_image`，作为 `scene_aerial` 参考角色（优先级 rank=2，紧随主场景图），用于固定空间/机位。
- **【画面定义】@图N**：`reference_set.compose_prompt()` 生成时**先 strip 旧定义块再重建**（幂等），按最终 `kept` 列表(含 b64) 顺序编号 @图1..N；返回的 `ref_images_b64` 与 @图N **同序**。预览用 `build_definition_block(require_b64=False)` 给近似。
- **缩略图**：仅对库中真实存在、且校验一致(能被调用)的资产渲染；不传给生成端，仅 @标签附加显示；@严格映射资产库标签，禁止臆造。

## 5. ⚠️ 下次接力第一优先项（用户最新需求，未完成）
**「定义行 @图N 必须 = 实际发送请求里的图片数组顺序/值，显示=发送，杜绝错乱」**
- 用户担心：预览显示「@图1 是 林夏」只是展示标签，可能与真正发送给生成端的参考图数组顺序/集合不一致（例如预览没含 场景鸟瞰图/道具，实际请求里这些图会插入并移位 @图N）。
- 现状（已验证一致的部分）：**生成链路**已一致 —— `compose_prompt`(reference_set.py:189-218) 用同一个有序 `kept` 重建定义并产出 `ref_images_b64`，`ref_images_b64[N-1]` 即 @图N 指代的同一张图。
- **要做**：让**预览**的 @图N 完全对齐**实际发送**的顺序与集合（含 scene_aerial、道具、导演图/站位图等所有真正随附的图），即预览也走与生成相同的 `cap()/_finalize_refs` 排序逻辑。验证：预览里的「@图1 是 X」编号/对象，与点生成后真正 POST 的 `ref_images_b64` 数组逐位一致。
- 相关文件：`backend/services/reference_set.py`（`cap` 优先级、`compose_prompt`、`build_definition_block`、`material_role`、`scene_aerial`）、`backend/services/batch_engine.py`（`_finalize_refs`、`_asset_ref_items` aerial 注入 ~459-492、`_with_preview_def`/`_preview_definition_block`、infer_shot_prompt ~416-421）、`frontend/src/views/WorktableView.vue`（预览渲染/缩略图）。

## 6. 待办清单（按优先级）
1. **(P0)** 预览【画面定义】@图N 与实际发送图片数组严格对齐（见 §5）。
2. (P1) 实机 E2E 复测（凭据已配）：阶段二自动/手动 LLM 拆解 → 资产并发生图+鸟瞰图 → AI推理逐镜（source=llm，非 fallback）→ 定义行/缩略图/原文映射 全部 PASS，录屏断言。
3. (P1) 原文与分镜错位：已改为内容匹配定位 src_text；用户反馈仍偶有错位，需用「测试分集小说」实测复核映射键。
4. (P2) 重打包 exe + 用户给 token 后刷新 Release v0.1.0。
5. 用户更早提的批次需求（尚未全部验收）：多集连续继承去重、窗口自由拉伸(已修 ctypes 64位 lParam)、日志调试 tab(已加)、资产外部导入持久化、导出剪映仅选中入轨、单分镜多视频仅留选中。

## 7. 已修 BUG 备忘
- 资产并发丢 `aerial_image`：`assets.py` 加 `RLock` + 白名单补 `aerial_image`/`ref_source`。
- AI推理回落：deepseek 推理模型 `max_tokens` 1400→6000。
- 生视频黑框闪烁：所有 `subprocess`/`ffmpeg`/`ffprobe` 加 `CREATE_NO_WINDOW`（ffmpeg_util.py / video_gen.py）。
- 窗口不能拉伸：ctypes 64位把 `lParam` 当 32位致 WNDPROC 崩溃 → 按指针宽度修正。
- col1 双符号 `##`/`@@`：模板与字段各加一层前缀，去重。

## 8. 约束 / 用户偏好
- 全程不打断、按推荐默认自动推进（用户原话：「完成所有任务前不要询问」「全程不打断你」）。
- 改 AI 模板必 bump version。
- 不提交 plans/todos/截图/`_tmp_*`/`_test_evidence` 等脚手架到仓库。
- ⚠️ 用户多次在聊天明文发 GitHub token（`ghp_…N0rtZHo`），提醒其轮换；如要免手动给 token 自动刷新 Release，建议存 fine-grained PAT secret（仅 `zhouxd03/vmo` Contents 读写）。
