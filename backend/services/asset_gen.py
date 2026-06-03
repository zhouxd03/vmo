"""Asset reference-image generation (Phase 3).

Renders the localized character/scene/prop sheet template, then calls the image
generation service. Saved under data/projects/<pid>/assets/.
"""

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from ..core import assets as assets_core
from ..core import projects
from ..core import prompt_templates as tpl
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


def build_prompt(project: dict, asset: dict) -> str:
    bible = project.get("story_bible") or {}
    style = bible.get("style") or "中国现代，3D动漫风格"
    key = TEMPLATE_BY_TYPE[asset["type"]]
    body = tpl.get_template_body(key)
    variables = {
        "style": style,
        "appearance": asset.get("appearance") or asset.get("desc") or asset["name"],
        "desc": asset.get("desc") or asset.get("appearance") or asset["name"],
    }
    return tpl.render(body, variables)


def generate_ref_image(pid: str, asset_id: str, *, model: Optional[str] = None,
                       size: str = "1024x1024") -> dict:
    project = projects.get_project(pid)
    if not project:
        raise image_gen.GenerationError("项目不存在")
    asset = next((a for a in project.get("assets", []) if a["id"] == asset_id), None)
    if not asset:
        raise image_gen.GenerationError("资产不存在")

    prompt = build_prompt(project, asset)
    result = image_gen.generate_image(
        prompt,
        model=model,
        size=size,
        save_dir=_asset_dir(pid),
        filename_prefix=f"{asset['type']}_{asset_id}",
    )
    assets_core.update_asset(pid, asset_id, {"ref_image": result["filename"]})
    return {"asset_id": asset_id, "filename": result["filename"], "prompt": prompt}


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


def generate_missing(pid: str, *, model: Optional[str] = None,
                     size: str = "1024x1024",
                     on_progress: Optional[Callable[[int, int, dict], None]] = None,
                     ) -> dict:
    """Batch-generate reference images for every asset that doesn't have one yet.

    Already-generated/imported assets are skipped, so running this again after a
    later episode adds new assets only fills the gaps (补全未生成的). Each asset is
    attempted independently — one failure never aborts the run — and per-asset
    errors are collected for the UI's error/retry display.
    """
    project = projects.get_project(pid)
    if not project:
        raise image_gen.GenerationError("项目不存在")
    pending = [a for a in project.get("assets", []) if not a.get("ref_image")]
    total = len(pending)
    generated, failed = [], []
    for i, a in enumerate(pending):
        try:
            out = generate_ref_image(pid, a["id"], model=model, size=size)
            generated.append({"id": a["id"], "name": a["name"],
                              "filename": out["filename"]})
            status = "ok"
            err = ""
        except Exception as e:  # noqa: BLE001 — capture per-asset, keep going
            msg = str(e) or e.__class__.__name__
            failed.append({"id": a["id"], "name": a["name"],
                           "trigger": a.get("trigger", ""), "error": msg})
            status = "error"
            err = msg
            logger.warning("[Assets] 一键生成失败 %s%s: %s",
                           a.get("trigger", ""), a.get("name", ""), msg)
        if on_progress:
            on_progress(i + 1, total, {"id": a["id"], "name": a["name"],
                                       "status": status, "error": err})
    return {
        "total": total,
        "generated": len(generated),
        "failed": failed,
        "items": generated,
        "skipped": len(project.get("assets", [])) - total,
    }
