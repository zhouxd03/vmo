"""Project, asset, analysis, and generation routes."""

import os
import json

from flask import Flask, jsonify, request, send_file, send_from_directory

from .core import assets as assets_core
from .core import batches, jobs, projects
from .core import prompt_templates as tpl
from .core import settings as settings_store
from .core import story_state
from .core.paths import OUTPUT_DIR, PROJECTS_DIR
from .services import (asset_gen, batch_engine, continuity, episode_runner,
                       episode_splitter, ffmpeg_util, image_gen,
                       jianying_export, script_analysis, script_parser)
from .services import llm as llm_service


def register(app: Flask) -> None:
    # projects
    @app.route("/api/projects", methods=["GET"])
    def list_projects():
        return jsonify(projects.list_projects())

    @app.route("/api/projects/<pid>", methods=["GET"])
    def get_project(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(p)

    @app.route("/api/projects/<pid>/overview", methods=["GET"])
    def project_overview(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        # 按 episode_id + kind 聚合批次进度
        agg: dict = {}
        for b in batches.list_batches(pid):
            eid = b.get("episode_id")
            kind = b.get("kind", "image")
            slot = agg.setdefault(eid, {
                "image": {"done": 0, "total": 0, "error": 0},
                "video": {"done": 0, "total": 0, "error": 0},
            })
            if kind in slot:
                slot[kind]["done"] += b.get("done", 0)
                slot[kind]["total"] += b.get("total", 0)
                slot[kind]["error"] += b.get("error", 0)
        eps = []
        for ep in p.get("episodes", []):
            prog = agg.get(ep["id"], {
                "image": {"done": 0, "total": 0, "error": 0},
                "video": {"done": 0, "total": 0, "error": 0},
            })
            eps.append({
                "id": ep["id"],
                "idx": ep.get("idx"),
                "name": ep.get("name", ""),
                "stage": ep.get("stage", "imported"),
                "segment_count": len(ep.get("segments", [])),
                "shot_count": len(ep.get("shots", [])),
                "char_count": ep.get("char_count", 0),
                "progress": prog,
            })
        return jsonify({
            "id": p["id"],
            "name": p.get("name", ""),
            "episode_count": len(eps),
            "segment_count": sum(e["segment_count"] for e in eps),
            "shot_count": sum(e["shot_count"] for e in eps),
            "updated_at": p.get("updated_at"),
            "episodes": eps,
        })

    @app.route("/api/projects/<pid>", methods=["PATCH"])
    def rename_project(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "项目名称不能为空"}), 400
        p = projects.update_project(pid, {"name": name})
        return jsonify(p)

    @app.route("/api/projects/<pid>", methods=["DELETE"])
    def delete_project(pid):
        projects.delete_project(pid)
        return jsonify({"ok": True})

    @app.route("/api/projects/import", methods=["POST"])
    def import_project():
        body = request.json or {}
        text = body.get("text", "")
        file_type = body.get("file_type", "txt")
        name = body.get("name", "")
        if not text.strip():
            return jsonify({"error": "文本为空"}), 400
        use_llm = body.get("use_llm", True)
        llm_model = body.get("llm_model") or None
        split_meta: dict = {}
        episodes = episode_splitter.split_into_episodes(
            text, file_type, use_llm=use_llm, llm_model=llm_model, meta=split_meta)
        if not episodes:
            return jsonify({"error": "没有可导入的分集"}), 400
        project = projects.create_project(name, episodes=episodes)
        # 自适应分集结果，供前端 toast 提示
        project = dict(project)
        project["split"] = split_meta
        return jsonify(project)

    # episodes
    @app.route("/api/projects/<pid>/episodes", methods=["GET"])
    def list_episodes(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(projects.list_episodes(pid))

    @app.route("/api/projects/<pid>/episodes", methods=["POST"])
    def add_episode(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        text = body.get("text", "")
        if not text.strip():
            return jsonify({"error": "文本为空"}), 400
        parsed = script_parser.parse_script(text, body.get("file_type", "txt"))
        if not parsed["segments"]:
            return jsonify({"error": "没有可解析的内容"}), 400
        ep = projects.add_episode(pid, body.get("name", ""),
                                  parsed["source_type"], text, parsed)
        return jsonify(ep)

    @app.route("/api/projects/<pid>/episodes/reorder", methods=["POST"])
    def reorder_episodes(pid):
        order = (request.json or {}).get("order", [])
        if not projects.reorder_episodes(pid, order):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(projects.list_episodes(pid))

    @app.route("/api/projects/<pid>/episodes/<eid>", methods=["GET"])
    def get_episode(pid, eid):
        ep = projects.get_episode(pid, eid)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        return jsonify(ep)

    @app.route("/api/projects/<pid>/episodes/<eid>", methods=["POST"])
    def update_episode(pid, eid):
        # only allow editing safe metadata (e.g. rename) from the client
        body = request.json or {}
        patch = {k: v for k, v in body.items() if k in ("name",)}
        ep = projects.update_episode(pid, eid, patch)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        return jsonify(ep)

    @app.route("/api/projects/<pid>/episodes/<eid>", methods=["DELETE"])
    def delete_episode(pid, eid):
        projects.delete_episode(pid, eid)
        return jsonify({"ok": True})

    @app.route("/api/projects/<pid>/episodes/<eid>/export-jianying", methods=["POST"])
    def export_jianying(pid, eid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        ep = projects.get_episode(pid, eid)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        body = request.json or {}
        items = body.get("items") or []
        if not items:
            return jsonify({"error": "请选择导出片段"}), 400
        draft_name = body.get("draft_name") or f"{p.get('name', '项目')}_{ep.get('name', eid)}"
        try:
            zip_path = jianying_export.build_draft(pid, draft_name, items)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"剪映草稿导出失败: {e}"}), 500
        return send_file(str(zip_path), as_attachment=True,
                         download_name=zip_path.name, mimetype="application/zip")

    @app.route("/api/projects/<pid>/shot_material/select", methods=["POST"])
    def select_shot_material(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        eid = body.get("episode_id") or projects.first_episode_id(pid)
        shot_no = body.get("shot_no")
        if not eid or not shot_no:
            return jsonify({"error": "缺少 episode_id 或 shot_no"}), 400
        material = {
            "bid": body.get("bid"),
            "filename": body.get("filename"),
            "kind": body.get("kind"),
        }
        if not material["bid"] or not material["filename"]:
            return jsonify({"error": "缺少素材批次或文件名"}), 400
        selected = projects.update_shot_selected_material(pid, eid, shot_no, material)
        if not selected:
            return jsonify({"error": "分镜不存在或素材信息无效"}), 404
        return jsonify({"ok": True, "selected_material": selected})

    def _resolve_episode(pid, body):
        eid = (body or {}).get("episode_id") or projects.first_episode_id(pid)
        return eid, projects.get_episode(pid, eid)

    # story bible: manual edit of novel-level scalar fields
    # 标题、风格、梗概允许手动设定；后续流程沿用这些值，重新分析不覆盖。
    @app.route("/api/projects/<pid>/story_bible", methods=["PATCH"])
    def update_story_bible(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        bible = dict(p.get("story_bible") or {})
        for k in ("title", "logline", "style", "summary"):
            if k in body:
                bible[k] = (body.get(k) or "").strip()
        projects.update_project(pid, {"story_bible": bible})
        return jsonify({"story_bible": bible})

    # stage 1: global analysis (per episode merged into shared bible)
    def _story_bible_scalar_seed(existing: dict | None) -> dict:
        existing = existing or {}
        seed = {"characters": [], "scenes": [], "props": [], "continuity_constraints": []}
        for key in ("title", "logline", "style", "summary"):
            if existing.get(key):
                seed[key] = existing[key]
        return seed

    def _rebuild_story_bible_from_episodes(project: dict, current_eid: str,
                                           current_bible: dict) -> dict:
        merged = _story_bible_scalar_seed(project.get("story_bible"))
        used_any = False
        for ep_item in project.get("episodes", []) or []:
            ep_bible = current_bible if ep_item.get("id") == current_eid else ep_item.get("story_bible")
            if not ep_bible:
                continue
            merged = script_analysis.merge_bible(merged, ep_bible)
            used_any = True
        return merged if used_any else current_bible

    @app.route("/api/projects/<pid>/analyze", methods=["POST"])
    def analyze_project(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        eid, ep = _resolve_episode(pid, body)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        try:
            bible = script_analysis.run_global_analysis(ep, model=body.get("model"))
        except Exception as e:  # noqa: BLE001
            logger.exception("全局分析失败 pid=%s episode=%s", pid, eid)
            return jsonify({"error": str(e)}), 502
        # Replace this episode's analysis contribution, then rebuild the shared
        # StoryBible from per-episode bibles. This makes "重新分析" a real
        # overwrite instead of accumulating stale characters/scenes/props.
        merged = _rebuild_story_bible_from_episodes(p, eid, bible)
        projects.update_project(pid, {"story_bible": merged})
        story_state.clear_episode(pid, ep.get("idx", 1))
        projects.update_episode(pid, eid, {
            "story_bible": bible,
            "shots": [],
            "blocks": [],
            "stage": "analyzed",
        })
        return jsonify({"story_bible": merged, "episode_bible": bible})

    # stage 2: decompose (async, polled)
    @app.route("/api/projects/<pid>/decompose", methods=["POST"])
    def decompose_project(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        if not p.get("story_bible"):
            return jsonify({"error": "请先完成全局分析"}), 400
        body = request.json or {}
        eid, ep = _resolve_episode(pid, body)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        ep_stage = ep.get("stage") or "imported"
        ep_ready = bool(ep.get("story_bible")) or ep_stage in {"analyzed", "decomposed"} or bool(ep.get("shots"))
        if not ep_ready:
            return jsonify({"error": "请先分析当前分集，再进行分镜拆解"}), 400
        model = body.get("model")
        ep_no = ep.get("idx", 1)
        mode = (body.get("mode") or "auto").strip()
        template_preset = body.get("template_preset")  # 分镜模式预设
        manual_segments = body.get("manual_segments") or []
        if mode == "manual" and not [s for s in manual_segments if (s or "").strip()]:
            return jsonify({"error": "手动模式请至少填写一段内容"}), 400
        # cross-episode handoff: seed with the previous episode's closing handoff
        prev_ep = projects.prev_episode(pid, eid)
        prev_handoff = None
        if prev_ep and prev_ep.get("shots"):
            prev_handoff = (prev_ep["shots"][-1] or {}).get("handoff")
        ctx = {**ep, "story_bible": p.get("story_bible")}

        def worker(job_id):
            def on_progress(done, total):
                label = "结构化分镜" if mode == "manual" else "拆解块"
                jobs.update(job_id, progress=done, total=total,
                            message=f"{label} {done}/{total}")
            story_state.clear_episode(pid, ep_no)
            if mode == "manual":
                result = script_analysis.run_decompose_manual(
                    ctx, manual_segments, model=model, on_progress=on_progress,
                    episode_no=ep_no, prev_handoff_init=prev_handoff)
            else:
                result = script_analysis.run_decompose(
                    ctx, model=model, on_progress=on_progress,
                    episode_no=ep_no, prev_handoff_init=prev_handoff,
                    template_preset=template_preset)
            projects.update_episode(pid, eid, {
                "shots": result["shots"],
                "blocks": result["blocks"],
                "stage": "decomposed",
            })
            return {"shot_count": len(result["shots"]), "chunk_total": result["chunk_total"]}

        job_id = jobs.run_async("decompose", worker,
                                meta={"project": pid, "episode": eid})
        return jsonify({"job_id": job_id})

    @app.route("/api/jobs/<job_id>", methods=["GET"])
    def get_job(job_id):
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(job)

    # assets (Phase 3: @/#/$)
    @app.route("/api/projects/<pid>/assets", methods=["GET"])
    def list_assets(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(assets_core.list_assets(pid))

    @app.route("/api/assets/library", methods=["GET"])
    def global_asset_library():
        items = []
        current_pid = (request.args.get("exclude_project") or "").strip()
        for summary in projects.list_projects():
            spid = summary.get("id")
            if not spid or spid == current_pid:
                continue
            p = projects.get_project(spid)
            if not p:
                continue
            for a in p.get("assets", []) or []:
                if not a.get("ref_image"):
                    continue
                items.append({
                    "project_id": spid,
                    "project_name": p.get("name") or summary.get("name") or spid,
                    "asset_id": a.get("id"),
                    "type": a.get("type"),
                    "trigger": a.get("trigger"),
                    "name": a.get("name"),
                    "desc": a.get("desc") or "",
                    "appearance": a.get("appearance") or "",
                    "voice": a.get("voice") or "",
                    "ref_image": a.get("ref_image"),
                })
        return jsonify(items)

    @app.route("/api/projects/<pid>/assets", methods=["POST"])
    def add_asset(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        try:
            asset = assets_core.add_asset(
                pid,
                body.get("type", ""),
                body.get("name", ""),
                desc=body.get("desc", ""),
                appearance=body.get("appearance", ""),
                voice=body.get("voice", ""),
                role=body.get("role", "main"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(asset)

    @app.route("/api/projects/<pid>/assets/<aid>", methods=["POST"])
    def update_asset(pid, aid):
        asset = assets_core.update_asset(pid, aid, request.json or {})
        if not asset:
            return jsonify({"error": "资产不存在"}), 404
        return jsonify(asset)

    @app.route("/api/projects/<pid>/assets/<aid>", methods=["DELETE"])
    def delete_asset(pid, aid):
        assets_core.delete_asset(pid, aid)
        return jsonify({"ok": True})

    @app.route("/api/projects/<pid>/assets/seed", methods=["POST"])
    def seed_assets(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        if not p.get("story_bible"):
            return jsonify({"error": "请先完成全局分析（Stage 1）再导入资产"}), 400
        return jsonify(assets_core.seed_from_bible(pid))

    @app.route("/api/projects/<pid>/assets/<aid>/refimage", methods=["POST"])
    def gen_ref_image(pid, aid):
        body = request.json or {}
        try:
            out = asset_gen.generate_ref_image(
                pid, aid, model=body.get("model"), size=body.get("size", "1024x1024"))
        except image_gen.GenerationError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/assets/<aid>/refimage/start", methods=["POST"])
    def start_gen_ref_image(pid, aid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        asset = next((a for a in p.get("assets", []) if a.get("id") == aid), None)
        if not asset:
            return jsonify({"error": "资产不存在"}), 404
        body = request.json or {}
        model = body.get("model")
        size = body.get("size", "1024x1024")

        def worker(job_id):
            jobs.update(job_id, progress=0, total=1,
                        message=f"准备生成 {asset.get('trigger', '')}{asset.get('name', '')}")
            jobs.update(job_id, message="提交图片生成请求")
            out = asset_gen.generate_ref_image(
                pid, aid, model=model, size=size)
            jobs.update(job_id, progress=1, total=1, message="参考图已保存")
            return out

        job_id = jobs.run_async(
            "asset_ref_image", worker,
            meta={
                "project": pid,
                "asset": aid,
                "asset_name": asset.get("name", ""),
                "trigger": asset.get("trigger", ""),
                "model": model,
                "size": size,
            },
        )
        return jsonify({"job_id": job_id})

    @app.route("/api/projects/<pid>/assets/generate-missing", methods=["POST"])
    def gen_missing_ref_images(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        try:
            out = asset_gen.generate_missing(
                pid, model=body.get("model"), size=body.get("size", "1024x1024"),
                concurrency=body.get("concurrency"))
        except image_gen.GenerationError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/assets/generate-missing/start", methods=["POST"])
    def start_gen_missing_ref_images(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        pending = [a for a in p.get("assets", []) if not a.get("ref_image")]
        total = len(pending)
        model = body.get("model")
        size = body.get("size", "1024x1024")
        concurrency = body.get("concurrency")

        def worker(job_id):
            jobs.update(job_id, progress=0, total=total,
                        message=f"待补全 {total} 张参考图")

            def on_progress(done, all_count, item):
                name = item.get("name") or ""
                status = "完成" if item.get("status") == "ok" else "失败"
                jobs.update(job_id, progress=done, total=all_count,
                            message=f"{status} {name}（{done}/{all_count}）",
                            current=item)

            out = asset_gen.generate_missing(
                pid, model=model, size=size, concurrency=concurrency,
                on_progress=on_progress)
            jobs.update(job_id, progress=out.get("total", total),
                        total=out.get("total", total), message="参考图补全完成")
            return out

        job_id = jobs.run_async(
            "asset_missing_ref_images", worker,
            meta={"project": pid, "model": model, "size": size, "total": total},
        )
        return jsonify({"job_id": job_id, "total": total})

    @app.route("/api/projects/<pid>/assets/<aid>/import-image", methods=["POST"])
    def import_ref_image(pid, aid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "缺少上传文件"}), 400
        import tempfile
        suffix = "." + (f.filename.rsplit(".", 1)[-1] if "." in (f.filename or "") else "png")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            f.save(tmp.name)
            tmp.close()
            line_options = None
            raw_line_options = request.form.get("line_options") or ""
            if raw_line_options:
                try:
                    line_options = json.loads(raw_line_options)
                except json.JSONDecodeError:
                    line_options = None
            out = asset_gen.import_ref_image(
                pid, aid, tmp.name,
                orig_name=f.filename or "",
                purpose=request.form.get("purpose") or "import",
                base_image=request.form.get("base_image") or "",
                line_options=line_options,
            )
        except image_gen.GenerationError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return jsonify(out)

    @app.route("/api/projects/<pid>/assets/<aid>/restore-original", methods=["POST"])
    def restore_original_ref_image(pid, aid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        try:
            out = asset_gen.restore_original_ref_image(pid, aid)
        except image_gen.GenerationError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/assets/<aid>/import-library", methods=["POST"])
    def import_ref_image_from_library(pid, aid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        try:
            out = asset_gen.import_ref_image_from_asset(
                pid, aid,
                body.get("source_project_id") or "",
                body.get("source_asset_id") or "",
            )
        except image_gen.GenerationError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/asset-image/<path:filename>", methods=["GET"])
    def serve_asset_image(pid, filename):
        return send_from_directory(PROJECTS_DIR / pid / "assets", filename)

    @app.route("/api/projects/<pid>/resolve", methods=["POST"])
    def resolve_mentions(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        text = (request.json or {}).get("text", "")
        return jsonify(assets_core.resolve_mentions(text, assets_core.list_assets(pid)))

    # batches (Phase 4)
    @app.route("/api/projects/<pid>/batches", methods=["GET"])
    def list_batches(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(batches.list_batches(pid))

    @app.route("/api/projects/<pid>/shot_prompts", methods=["POST"])
    def shot_prompts(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "分集不存在"}), 404
        body = request.json or {}
        _eid, ep = _resolve_episode(pid, body)
        if not ep:
            return jsonify({"error": "项目不存在"}), 404
        ctx = {**ep, "_pid": pid, "assets": p.get("assets", []),
               "story_bible": p.get("story_bible")}
        prompts = batch_engine.build_shot_prompts(
            ctx, body.get("shot_nos"),
            include_saved=not bool(body.get("ignore_saved")),
            include_continuity_refs=bool(body.get("continuity")),
            manual_continuity=body.get("manual_continuity") if isinstance(body.get("manual_continuity"), dict) else None,
        )
        return jsonify({"prompts": prompts})

    @app.route("/api/projects/<pid>/shot_prompts/save", methods=["POST"])
    def save_shot_prompts(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        shot_no = body.get("shot_no")
        if not shot_no:
            return jsonify({"error": "缺少 shot_no"}), 400
        _eid, ep = _resolve_episode(pid, body)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        saved = projects.update_shot_prompts(pid, ep["id"], shot_no, {
            "image": body.get("image", ""),
            "video": body.get("video", ""),
            "source": body.get("source", "manual"),
        })
        if saved is None:
            return jsonify({"error": "分镜不存在"}), 404
        return jsonify({"ok": True, "prompt_overrides": saved})

    @app.route("/api/projects/<pid>/infer_shot_prompt", methods=["POST"])
    def infer_shot_prompt(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        shot_no = body.get("shot_no")
        if not shot_no:
            return jsonify({"error": "缺少 shot_no"}), 400
        _eid, ep = _resolve_episode(pid, body)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        ctx = {**ep, "_pid": pid, "assets": p.get("assets", []),
               "story_bible": p.get("story_bible")}
        try:
            res = batch_engine.infer_shot_prompt(ctx, shot_no, model=body.get("model"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            app.logger.exception("infer_shot_prompt failed: pid=%s episode=%s shot=%s", pid, ep.get("id"), shot_no)
            return jsonify({"error": f"提示词推理失败: {e}"}), 500
        saved = projects.update_shot_prompts(pid, ep["id"], shot_no, {
            "image": res.get("image", ""),
            "video": res.get("video", ""),
            "source": res.get("source", "llm"),
        })
        if saved is not None:
            res["persisted"] = True
            res["prompt_overrides"] = saved
        else:
            res["persisted"] = False
        return jsonify(res)

    def _apply_prompts(tasks, kind, overrides):
        # Resolve each task's final generation prompt.
        for t in tasks:
            ov = (overrides or {}).get(t.get("shot_no"))
            if ov and ov.strip():
                t["prompt"] = ov.strip()
            elif isinstance(t.get("prompt_overrides"), dict):
                saved = t["prompt_overrides"].get(kind)
                if isinstance(saved, str) and saved.strip():
                    t["prompt"] = saved.strip()
            elif kind == "video":
                t["prompt"] = batch_engine.build_video_prompt(t, t.get("prompt", ""))

    @app.route("/api/projects/<pid>/batches", methods=["POST"])
    def create_batch(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        kind = body.get("kind", "image")
        source = body.get("source", "shots")
        if source == "manual":
            asset_list = assets_core.list_assets(pid)
            tasks = []
            for i, line in enumerate(body.get("prompts", []) or []):
                line = (line or "").strip()
                if not line:
                    continue
                res = assets_core.resolve_mentions(line, asset_list)
                tasks.append({"shot_no": f"M{i+1:03d}", "seq": i + 1,
                              "prompt": res["text"], "materials": res["materials"]})
        else:
            eid, ep = _resolve_episode(pid, body)
            if not ep:
                return jsonify({"error": "分集不存在"}), 404
            if ep.get("stage") != "decomposed" and not ep.get("shots"):
                return jsonify({"error": "当前分集尚未拆解"}), 400
            ctx = {**ep, "assets": p.get("assets", []),
                   "story_bible": p.get("story_bible")}
            tasks = batch_engine.build_tasks_from_shots(ctx, body.get("shot_nos"))
            _apply_prompts(tasks, kind, body.get("prompt_overrides") or {})
        if not tasks:
            return jsonify({"error": "没有可生成的任务"}), 400
        params = dict(body.get("params", {}) or {})
        if source != "manual":
            params.setdefault("episode_id", eid)
            params.setdefault("episode_name", ep.get("name") or f"episode-{ep.get('idx', '')}")
        try:
            batch = batches.create_batch(
                pid, kind, body.get("name", ""), tasks,
                concurrency=body.get("concurrency", 2),
                params=params,
                max_attempts=body.get("max_attempts", 3),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(batch)

    @app.route("/api/projects/<pid>/batches/<bid>", methods=["GET"])
    def get_batch(pid, bid):
        b = batches.get_batch(pid, bid)
        if not b:
            return jsonify({"error": "批次不存在"}), 404
        return jsonify(b)

    @app.route("/api/projects/<pid>/batches/<bid>", methods=["DELETE"])
    def delete_batch(pid, bid):
        batches.delete_batch(pid, bid)
        return jsonify({"ok": True})

    @app.route("/api/projects/<pid>/batches/<bid>/tasks/<task_id>/result", methods=["DELETE"])
    def delete_batch_task_result(pid, bid, task_id):
        if not batches.get_batch(pid, bid):
            return jsonify({"error": "批次不存在"}), 404
        deleted = batches.delete_task_result(pid, bid, task_id)
        if not deleted:
            return jsonify({"error": "任务不存在或素材结果不存在"}), 404

        filename = deleted.get("filename") or ""
        file_deleted = False
        if filename:
            base = (OUTPUT_DIR / pid / bid).resolve()
            target = (base / filename).resolve()
            try:
                target.relative_to(base)
                if target.exists() and target.is_file():
                    target.unlink()
                    file_deleted = True
            except Exception:  # noqa: BLE001
                app.logger.warning("skip unsafe output delete: pid=%s bid=%s filename=%s", pid, bid, filename)

        cleared = projects.clear_selected_material(pid, bid, filename)
        return jsonify({"ok": True, "deleted": deleted, "file_deleted": file_deleted, "cleared_selected": cleared})

    @app.route("/api/projects/<pid>/batch_errors/dismiss", methods=["POST"])
    def dismiss_batch_errors(pid):
        body = request.json or {}
        items = body.get("items") or []
        if not isinstance(items, list) or not items:
            return jsonify({"error": "没有可清除的错误条目"}), 400
        grouped = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            bid = str(item.get("batch_id") or item.get("batchId") or "").strip()
            tid = str(item.get("task_id") or item.get("taskId") or "").strip()
            if not bid or not tid:
                continue
            grouped.setdefault(bid, []).append(tid)
        if not grouped:
            return jsonify({"error": "错误条目缺少批次或任务编号"}), 400
        total = 0
        updated = []
        for bid, task_ids in grouped.items():
            result = batches.dismiss_task_errors(pid, bid, task_ids)
            total += int(result.get("dismissed") or 0)
            if result.get("batch"):
                updated.append(result["batch"])
        return jsonify({"ok": True, "dismissed": total, "batches": updated})

    def _start_batch_job(pid, bid):
        def worker(job_id):
            def on_progress(done, total):
                jobs.update(job_id, progress=done, total=total, message=f"{done}/{total}")
            return batch_engine.run_batch(pid, bid, on_progress=on_progress)
        return jobs.run_async("batch", worker, meta={"project": pid, "batch": bid})

    @app.route("/api/projects/<pid>/batches/<bid>/start", methods=["POST"])
    def start_batch(pid, bid):
        if not batches.get_batch(pid, bid):
            return jsonify({"error": "批次不存在"}), 404
        return jsonify({"job_id": _start_batch_job(pid, bid)})

    @app.route("/api/projects/<pid>/batches/<bid>/pause", methods=["POST"])
    def pause_batch(pid, bid):
        if not batches.get_batch(pid, bid):
            return jsonify({"error": "批次不存在"}), 404
        batch_engine.pause(bid)
        batches.request_pause(pid, bid)
        batches.normalize_stopping(pid, bid)
        cancelled = batch_engine.cancel_doubao_tasks(pid, bid)
        return jsonify({"ok": True, "cancelled_doubao_tasks": cancelled})

    @app.route("/api/projects/<pid>/batches/<bid>/retry", methods=["POST"])
    def retry_batch(pid, bid):
        if not batches.get_batch(pid, bid):
            return jsonify({"error": "项目不存在"}), 404
        batches.reset_failed(pid, bid)
        return jsonify({"job_id": _start_batch_job(pid, bid)})

    @app.route("/api/projects/<pid>/episode_batches", methods=["POST"])
    def create_episode_batches(pid):
        # Per-episode concurrent generation.
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        kind = body.get("kind", "image")
        eps = body.get("episodes") or []
        if not eps:
            return jsonify({"error": "未选择任何分集"}), 400
        params_base = dict(body.get("params") or {})
        default_cap = settings_store.load_settings().get("max_parallel_episodes", 2)
        max_parallel = int(body.get("max_parallel") or default_cap)
        created, skipped = [], []
        for item in eps:
            eid = (item or {}).get("episode_id")
            ep = projects.get_episode(pid, eid)
            if not ep or (ep.get("stage") != "decomposed" and not ep.get("shots")):
                skipped.append({"episode_id": eid, "reason": "未拆解或不存在"})
                continue
            ctx = {**ep, "assets": p.get("assets", []),
                   "story_bible": p.get("story_bible")}
            tasks = batch_engine.build_tasks_from_shots(ctx, item.get("shot_nos"))
            _apply_prompts(tasks, kind, item.get("prompt_overrides") or {})
            if not tasks:
                skipped.append({"episode_id": eid, "reason": "无可生成分镜"})
                continue
            label = f"{ep.get('name', eid)}-并发{'生视频' if kind == 'video' else '生图'}"
            params = {**params_base, "episode_id": eid,
                      "episode_name": ep.get("name") or f"episode-{ep.get('idx', '')}"}
            try:
                # 单集串行：concurrency=1，保留连续性链路
                batch = batches.create_batch(
                    pid, kind, label, tasks, concurrency=1, params=params,
                    max_attempts=body.get("max_attempts", 3))
            except ValueError as e:
                skipped.append({"episode_id": eid, "reason": str(e)})
                continue
            created.append({"episode_id": eid, "batch_id": batch["id"], "total": len(tasks)})
        if not created:
            return jsonify({"error": "没有可创建的分集批次"}), 400
        job_id = episode_runner.start(pid, [c["batch_id"] for c in created], max_parallel)
        return jsonify({"created": created, "skipped": skipped,
                        "job_id": job_id, "max_parallel": max_parallel})

    @app.route("/api/output/<pid>/<bid>/<path:filename>", methods=["GET"])
    def serve_output(pid, bid, filename):
        return send_from_directory(OUTPUT_DIR / pid / bid, filename)

    # continuity engine (Phase 5)
    @app.route("/api/projects/<pid>/continuity", methods=["GET"])
    def get_continuity(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(story_state.get_state(pid))

    @app.route("/api/projects/<pid>/continuity/reset", methods=["POST"])
    def reset_continuity(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(story_state.reset(pid))

    @app.route("/api/projects/<pid>/continuity/decide", methods=["POST"])
    def decide_continuity(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        shot = body.get("shot") or {}
        with story_state.continuity_lock(pid):
            prev = body.get("prev_state")
            if prev is None:
                prev = story_state.get_current(pid)
            decision = continuity.decide_handoff(
                shot, prev, use_llm=bool(body.get("use_llm", True)), model=body.get("model"))
            if body.get("commit"):
                story_state.set_decision(pid, shot.get("shot_no", ""), decision)
        return jsonify(decision)

    @app.route("/api/projects/<pid>/continuity/tailframe", methods=["POST"])
    def tailframe_continuity(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        shot_no = body.get("shot_no", "")
        # video path: explicit path, or resolve from batch output
        video_path = body.get("video_path")
        if not video_path and body.get("bid") and body.get("filename"):
            video_path = str(OUTPUT_DIR / pid / body["bid"] / body["filename"])
        if not video_path:
            return jsonify({"error": "缺少视频路径"}), 400
        try:
            with story_state.continuity_lock(pid):
                out = continuity.extract_tail_frame(pid, shot_no, video_path)
        except ffmpeg_util.FFmpegError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/continuity/staging", methods=["POST"])
    def staging_continuity(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        shot = body.get("shot") or {}
        try:
            with story_state.continuity_lock(pid):
                prev = body.get("prev_state") or story_state.get_current(pid)
                bible = (projects.get_project(pid) or {}).get("story_bible") or {}
                bridge_data = body.get("bridge_data")
                if not isinstance(bridge_data, dict):
                    bridge_data = continuity.build_bridge_data(body.get("decision") or {}, shot, prev)
                bridge_context = body.get("bridge_context") or continuity.render_bridge_context(
                    bridge_data, purpose="staging")
                out = continuity.generate_staging(
                    pid, shot, prev, style=bible.get("style", ""), model=body.get("model"),
                    size=ffmpeg_util.aspect_to_image_size(body.get("aspect_ratio")),
                    assets=(projects.get_project(pid) or {}).get("assets") or [],
                    bridge_context=bridge_context)
        except image_gen.GenerationError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/continuity/director", methods=["POST"])
    def director_continuity(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        shot = body.get("shot") or {}
        try:
            with story_state.continuity_lock(pid):
                prev = body.get("prev_state") or story_state.get_current(pid)
                bible = (projects.get_project(pid) or {}).get("story_bible") or {}
                bridge_data = body.get("bridge_data")
                if not isinstance(bridge_data, dict):
                    bridge_data = continuity.build_bridge_data(body.get("decision") or {}, shot, prev)
                bridge_context = body.get("bridge_context") or continuity.render_bridge_context(
                    bridge_data, purpose="director")
                out = continuity.generate_director_board(
                    pid, shot, prev, style=bible.get("style", ""), model=body.get("model"),
                    size=ffmpeg_util.aspect_to_image_size(body.get("aspect_ratio")),
                    assets=(projects.get_project(pid) or {}).get("assets") or [],
                    bridge_context=bridge_context)
        except image_gen.GenerationError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/continuity/review", methods=["POST"])
    def review_continuity(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        shot = body.get("shot") or {}
        try:
            with story_state.continuity_lock(pid):
                prev = body.get("prev_state") or story_state.get_current(pid)
                out = continuity.ai_review(pid, shot, prev, model=body.get("model"))
        except llm_service.LLMError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/continuity-image/<path:filename>", methods=["GET"])
    def serve_continuity_image(pid, filename):
        return send_from_directory(story_state.continuity_dir(pid), filename)

    # prompt templates
    @app.route("/api/templates", methods=["GET"])
    def list_templates():
        return jsonify(tpl.load_templates(decorate=True))

    @app.route("/api/templates/<key>", methods=["POST"])
    def save_template(key):
        data = request.json or {}
        body = data.get("body", "")
        preset_id = data.get("preset_id")
        try:
            if preset_id:
                return jsonify(tpl.save_preset(key, preset_id, body))
            return jsonify(tpl.save_template(key, body))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/templates/<key>/presets", methods=["POST"])
    def add_preset(key):
        data = request.json or {}
        try:
            return jsonify(tpl.add_preset(
                key, data.get("name", ""), data.get("body", ""), data.get("base_id")
            ))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/templates/<key>/presets/<preset_id>", methods=["PATCH"])
    def update_preset(key, preset_id):
        data = request.json or {}
        try:
            if "name" in data:
                tpl.rename_preset(key, preset_id, data["name"])
            if "body" in data:
                return jsonify(tpl.save_preset(key, preset_id, data["body"]))
            return jsonify(tpl.load_templates(decorate=True)[key])
        except (ValueError, KeyError) as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/templates/<key>/presets/<preset_id>", methods=["DELETE"])
    def delete_preset(key, preset_id):
        try:
            return jsonify(tpl.delete_preset(key, preset_id))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/templates/<key>/active", methods=["POST"])
    def set_active_preset(key):
        preset_id = (request.json or {}).get("preset_id", "")
        try:
            return jsonify(tpl.set_active_preset(key, preset_id))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/templates/<key>/reset", methods=["POST"])
    def reset_template(key):
        try:
            return jsonify(tpl.reset_template(key))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/templates/<key>/preview", methods=["POST"])
    def preview_template(key):
        data = request.json or {}
        overrides = data.get("variables") or {}
        try:
            return jsonify({"rendered": tpl.preview_template(key, overrides, data.get("preset_id"))})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
