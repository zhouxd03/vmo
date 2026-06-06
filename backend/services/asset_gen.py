"""Asset reference-image generation (Phase 3).

Renders the localized character/scene/prop sheet template, then calls the image
generation service. Saved under data/projects/<pid>/assets/.
"""

import logging
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from ..core import assets as assets_core
from ..core import projects
from ..core import prompt_templates as tpl
from ..core import settings as settings_store
from ..core.paths import PROJECTS_DIR
from . import image_gen

logger = logging.getLogger(__name__)

TEMPLATE_BY_TYPE = {
    "character": "character_sheet",
    "scene": "scene_sheet",
    "prop": "prop_sheet",
}


def _asset_dir(pid: str):
    d = PROJECTS_DIR / pid / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _asset_variables(project: dict, asset: dict) -> dict:
    bible = project.get("story_bible") or {}
    style = bible.get("style") or "中国现代，3D动漫风格"
    return {
        "name": asset.get("name") or "",
        "style": style,
        "appearance": asset.get("appearance") or asset.get("desc") or asset["name"],
        "desc": asset.get("desc") or asset.get("appearance") or asset["name"],
        "voice": asset.get("voice") or "",
    }


def build_prompt(project: dict, asset: dict) -> str:
    key = TEMPLATE_BY_TYPE[asset["type"]]
    return tpl.render(tpl.get_template_body(key), _asset_variables(project, asset))


def generate_ref_image(pid: str, asset_id: str, *, model: Optional[str] = None,
                       size: str = "1024x1024") -> dict:
    project = projects.get_project(pid)
    if not project:
        raise image_gen.GenerationError("项目不存在")
    asset = next((a for a in project.get("assets", []) if a["id"] == asset_id), None)
    if not asset:
        raise image_gen.GenerationError("资产不存在")

    prompt = build_prompt(project, asset)
    logger.info(
        "[Assets] generate ref image %s%s type=%s model=%s size=%s",
        asset.get("trigger", ""), asset.get("name", ""), asset.get("type"),
        model or "default", size,
    )
    result = image_gen.generate_image(
        prompt,
        model=model,
        size=size,
        save_dir=_asset_dir(pid),
        filename_prefix=f"{asset['type']}_{asset_id}",
    )
    patch = {"ref_image": result["filename"]}
    out = {"asset_id": asset_id, "filename": result["filename"], "prompt": prompt}

    assets_core.update_asset(pid, asset_id, patch)
    return out


_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def import_ref_image(pid: str, asset_id: str, src_path: str,
                     orig_name: str = "") -> dict:
    """Copy an external image file into the project's asset folder and bind it
    as the asset's reference image (persisted under data/projects/<pid>/assets/).

    Lets users supply their own reference art instead of generating it. The file
    is renamed deterministically so it survives re-imports and is project-scoped.
    """
    project = projects.get_project(pid)
    if not project:
        raise image_gen.GenerationError("项目不存在")
    asset = next((a for a in project.get("assets", []) if a["id"] == asset_id), None)
    if not asset:
        raise image_gen.GenerationError("资产不存在")
    src = Path(src_path)
    if not src.is_file():
        raise image_gen.GenerationError("找不到要导入的图片文件")
    ext = src.suffix.lower() or Path(orig_name).suffix.lower()
    if ext not in _IMG_EXT:
        raise image_gen.GenerationError(f"不支持的图片格式: {ext or '未知'}")
    fname = f"{asset['type']}_{asset_id}_import_{uuid.uuid4().hex[:6]}{ext}"
    dest = _asset_dir(pid) / fname
    shutil.copyfile(src, dest)
    assets_core.update_asset(pid, asset_id, {"ref_image": fname, "ref_source": "import"})
    return {"asset_id": asset_id, "filename": fname}


def import_ref_image_from_asset(pid: str, asset_id: str, source_pid: str,
                                source_asset_id: str) -> dict:
    """Copy a reference image from another project's asset into this asset.

    The image is copied, not linked, so the current project remains self-contained
    even if the source project is renamed or deleted later.
    """
    target_project = projects.get_project(pid)
    source_project = projects.get_project(source_pid)
    if not target_project or not source_project:
        raise image_gen.GenerationError("项目不存在")
    target = next((a for a in target_project.get("assets", []) if a["id"] == asset_id), None)
    source = next((a for a in source_project.get("assets", []) if a["id"] == source_asset_id), None)
    if not target:
        raise image_gen.GenerationError("目标资产不存在")
    if not source:
        raise image_gen.GenerationError("素材库资产不存在")
    ref = source.get("ref_image")
    if not ref:
        raise image_gen.GenerationError("素材库资产没有参考图")
    src = _asset_dir(source_pid) / ref
    if not src.is_file():
        raise image_gen.GenerationError("素材库参考图文件不存在")
    ext = src.suffix.lower() or ".png"
    fname = f"{target['type']}_{asset_id}_library_{uuid.uuid4().hex[:6]}{ext}"
    dest = _asset_dir(pid) / fname
    shutil.copyfile(src, dest)
    assets_core.update_asset(pid, asset_id, {
        "ref_image": fname,
        "ref_source": "library",
    })
    return {
        "asset_id": asset_id,
        "filename": fname,
        "source_project_id": source_pid,
        "source_asset_id": source_asset_id,
    }


def _concurrency(override: Optional[int] = None) -> int:
    if override and override > 0:
        return int(override)
    try:
        n = int(settings_store.load_settings().get("asset_gen_concurrency", 3))
    except (TypeError, ValueError):
        n = 3
    return max(1, n)


def generate_missing(pid: str, *, model: Optional[str] = None,
                     size: str = "1024x1024",
                     concurrency: Optional[int] = None,
                     on_progress: Optional[Callable[[int, int, dict], None]] = None,
                     ) -> dict:
    """Batch-generate reference images for every asset that doesn't have one yet.

    Already-generated/imported assets are skipped, so running this again after a
    later episode adds new assets only fills the gaps (补全未生成的). Assets are
    generated concurrently (``settings.asset_gen_concurrency`` workers, overridable)
    so a large library finishes much faster; each asset is attempted independently —
    one failure never aborts the run — and per-asset errors are collected for the
    UI's error/retry display.
    """
    project = projects.get_project(pid)
    if not project:
        raise image_gen.GenerationError("项目不存在")
    pending = [a for a in project.get("assets", []) if not a.get("ref_image")]
    total = len(pending)
    generated, failed = [], []
    workers = min(_concurrency(concurrency), total) or 1
    done = 0
    lock = threading.Lock()

    def _one(a: dict) -> dict:
        try:
            out = generate_ref_image(pid, a["id"], model=model, size=size)
            return {"id": a["id"], "name": a["name"], "trigger": a.get("trigger", ""),
                    "status": "ok", "filename": out["filename"], "error": ""}
        except Exception as e:  # noqa: BLE001 — capture per-asset, keep going
            msg = str(e) or e.__class__.__name__
            logger.warning("[Assets] 一键生成失败 %s%s: %s",
                           a.get("trigger", ""), a.get("name", ""), msg)
            return {"id": a["id"], "name": a["name"], "trigger": a.get("trigger", ""),
                    "status": "error", "filename": "", "error": msg}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, a): a for a in pending}
        for fut in as_completed(futs):
            r = fut.result()
            if r["status"] == "ok":
                generated.append({"id": r["id"], "name": r["name"],
                                  "filename": r["filename"]})
            else:
                failed.append({"id": r["id"], "name": r["name"],
                               "trigger": r["trigger"], "error": r["error"]})
            with lock:
                done += 1
                cur = done
            if on_progress:
                on_progress(cur, total, {"id": r["id"], "name": r["name"],
                                         "status": r["status"], "error": r["error"]})
    return {
        "total": total,
        "generated": len(generated),
        "failed": failed,
        "items": generated,
        "skipped": len(project.get("assets", [])) - total,
        "concurrency": workers,
    }
