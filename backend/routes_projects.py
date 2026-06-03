"""Phase 2/3 routes: project import, two-stage LLM analysis, prompt templates,
asset library (@/#/$) and reference-image generation."""

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
    # ── projects ──
    @app.route("/api/projects", methods=["GET"])
    def list_projects():
        return jsonify(projects.list_projects())

    @app.route("/api/projects/<pid>", methods=["GET"])
    def get_project(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
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
        episodes = episode_splitter.split_into_episodes(text, file_type)
        if not episodes:
            return jsonify({"error": "未解析出任何片段，请检查文件格式"}), 400
        project = projects.create_project(name, episodes=episodes)
        return jsonify(project)

    # ── episodes (集) ──
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
            return jsonify({"error": "未解析出任何片段，请检查文件格式"}), 400
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
            return jsonify({"error": "没有可导出的分镜素材（请先生成图片或视频）"}), 400
        draft_name = body.get("draft_name") or f"{p.get('name', '项目')}_{ep.get('name', eid)}"
        try:
            zip_path = jianying_export.build_draft(pid, draft_name, items)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"剪映草稿导出失败: {e}"}), 500
        return send_file(str(zip_path), as_attachment=True,
                         download_name=zip_path.name, mimetype="application/zip")

    def _resolve_episode(pid, body):
        eid = (body or {}).get("episode_id") or projects.first_episode_id(pid)
        return eid, projects.get_episode(pid, eid)

    # ── story bible: manual edit of novel-level scalar fields ──
    # 风格({{style}})/标题/梗概可手动设定；手设后全流程沿用该值，且重新分析不覆盖。
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

    # ── stage 1: global analysis (per episode → merged into shared bible) ──
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
            return jsonify({"error": str(e)}), 502
        # union into the novel-level shared StoryBible (cross-episode sharing)
        merged = script_analysis.merge_bible(p.get("story_bible"), bible)
        projects.update_project(pid, {"story_bible": merged})
        projects.update_episode(pid, eid, {"stage": "analyzed"})
        return jsonify({"story_bible": merged, "episode_bible": bible})

    # ── stage 2: decompose (async, polled) ──
    @app.route("/api/projects/<pid>/decompose", methods=["POST"])
    def decompose_project(pid):
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        if not p.get("story_bible"):
            return jsonify({"error": "请先完成全局分析（Stage 1）"}), 400
        body = request.json or {}
        eid, ep = _resolve_episode(pid, body)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        model = body.get("model")
        ep_no = ep.get("idx", 1)
        # cross-episode handoff: seed with the previous episode's closing handoff
        prev_ep = projects.prev_episode(pid, eid)
        prev_handoff = None
        if prev_ep and prev_ep.get("shots"):
            prev_handoff = (prev_ep["shots"][-1] or {}).get("handoff")
        ctx = {**ep, "story_bible": p.get("story_bible")}

        def worker(job_id):
            def on_progress(done, total):
                jobs.update(job_id, progress=done, total=total,
                            message=f"拆解块 {done}/{total}")
            result = script_analysis.run_decompose(
                ctx, model=model, on_progress=on_progress,
                episode_no=ep_no, prev_handoff_init=prev_handoff)
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

    # ── assets (Phase 3: @/#/$) ──
    @app.route("/api/projects/<pid>/assets", methods=["GET"])
    def list_assets(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(assets_core.list_assets(pid))

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

    @app.route("/api/projects/<pid>/asset-image/<path:filename>", methods=["GET"])
    def serve_asset_image(pid, filename):
        return send_from_directory(PROJECTS_DIR / pid / "assets", filename)

    @app.route("/api/projects/<pid>/resolve", methods=["POST"])
    def resolve_mentions(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        text = (request.json or {}).get("text", "")
        return jsonify(assets_core.resolve_mentions(text, assets_core.list_assets(pid)))

    # ── batches (Phase 4) ──
    @app.route("/api/projects/<pid>/batches", methods=["GET"])
    def list_batches(pid):
        if not projects.get_project(pid):
            return jsonify({"error": "项目不存在"}), 404
        return jsonify(batches.list_batches(pid))

    @app.route("/api/projects/<pid>/shot_prompts", methods=["POST"])
    def shot_prompts(pid):
        """Preview editable image/video prompts for shots (no generation)."""
        p = projects.get_project(pid)
        if not p:
            return jsonify({"error": "项目不存在"}), 404
        body = request.json or {}
        _eid, ep = _resolve_episode(pid, body)
        if not ep:
            return jsonify({"error": "分集不存在"}), 404
        ctx = {**ep, "assets": p.get("assets", []),
               "story_bible": p.get("story_bible")}
        prompts = batch_engine.build_shot_prompts(ctx, body.get("shot_nos"))
        return jsonify({"prompts": prompts})

    def _apply_prompts(tasks, kind, overrides):
        """Resolve each task's final generation prompt:
        - explicit per-shot override (the worktable's edited text) wins;
        - else for video, render the user-editable `video_prompt` template
          (image description + 【镜头动态】) so engine-default video batches use
          the same video prompt the worktable shows;
        - else (image) keep the engine image prompt as-is."""
        for t in tasks:
            ov = (overrides or {}).get(t.get("shot_no"))
            if ov and ov.strip():
                t["prompt"] = ov.strip()
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
                return jsonify({"error": "请先在剧本解析完成分批拆解（Stage 2）"}), 400
            ctx = {**ep, "assets": p.get("assets", []),
                   "story_bible": p.get("story_bible")}
            tasks = batch_engine.build_tasks_from_shots(ctx, body.get("shot_nos"))
            _apply_prompts(tasks, kind, body.get("prompt_overrides") or {})
        if not tasks:
            return jsonify({"error": "没有可生成的任务"}), 400
        params = dict(body.get("params", {}) or {})
        if source != "manual":
            params.setdefault("episode_id", eid)
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
        batch_engine.pause(bid)
        return jsonify({"ok": True})

    @app.route("/api/projects/<pid>/batches/<bid>/retry", methods=["POST"])
    def retry_batch(pid, bid):
        if not batches.get_batch(pid, bid):
            return jsonify({"error": "批次不存在"}), 404
        batches.reset_failed(pid, bid)
        return jsonify({"job_id": _start_batch_job(pid, bid)})

    @app.route("/api/projects/<pid>/episode_batches", methods=["POST"])
    def create_episode_batches(pid):
        """Per-episode concurrent generation: one serial batch per episode, run
        with bounded cross-episode parallelism (单集串行 · 多集并发).

        body: {kind, episodes:[{episode_id, shot_nos?, prompt_overrides?}],
               params?, max_parallel?}
        """
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
            params = {**params_base, "episode_id": eid}
            try:
                # 单集串行：concurrency=1（含连续性链路）
                batch = batches.create_batch(
                    pid, kind, label, tasks, concurrency=1, params=params,
                    max_attempts=body.get("max_attempts", 3))
            except ValueError as e:
                skipped.append({"episode_id": eid, "reason": str(e)})
                continue
            created.append({"episode_id": eid, "batch_id": batch["id"], "total": len(tasks)})
        if not created:
            return jsonify({"error": "没有可生成的分集", "skipped": skipped}), 400
        job_id = episode_runner.start(pid, [c["batch_id"] for c in created], max_parallel)
        return jsonify({"created": created, "skipped": skipped,
                        "job_id": job_id, "max_parallel": max_parallel})

    @app.route("/api/output/<pid>/<bid>/<path:filename>", methods=["GET"])
    def serve_output(pid, bid, filename):
        return send_from_directory(OUTPUT_DIR / pid / bid, filename)

    # ── continuity engine (Phase 5) ──
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
        prev = body.get("prev_state")
        if prev is None:
            prev = story_state.get_current(pid)
        decision = continuity.decide_handoff(
            shot, prev, use_llm=bool(body.get("use_llm")), model=body.get("model"))
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
            return jsonify({"error": "缺少视频路径（video_path 或 bid+filename）"}), 400
        try:
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
        prev = body.get("prev_state") or story_state.get_current(pid)
        bible = (projects.get_project(pid) or {}).get("story_bible") or {}
        try:
            out = continuity.generate_staging(
                pid, shot, prev, style=bible.get("style", ""), model=body.get("model"),
                size=ffmpeg_util.aspect_to_image_size(body.get("aspect_ratio")),
                assets=(projects.get_project(pid) or {}).get("assets") or [])
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
        prev = body.get("prev_state") or story_state.get_current(pid)
        bible = (projects.get_project(pid) or {}).get("story_bible") or {}
        try:
            out = continuity.generate_director_board(
                pid, shot, prev, style=bible.get("style", ""), model=body.get("model"),
                size=ffmpeg_util.aspect_to_image_size(body.get("aspect_ratio")),
                assets=(projects.get_project(pid) or {}).get("assets") or [])
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
        prev = body.get("prev_state") or story_state.get_current(pid)
        try:
            out = continuity.ai_review(pid, shot, prev, model=body.get("model"))
        except llm_service.LLMError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
        return jsonify(out)

    @app.route("/api/projects/<pid>/continuity-image/<path:filename>", methods=["GET"])
    def serve_continuity_image(pid, filename):
        return send_from_directory(story_state.continuity_dir(pid), filename)

    # ── prompt templates ──
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
