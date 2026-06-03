"""Asset reference-image generation (Phase 3).

Renders the localized character/scene/prop sheet template, then calls the image
generation service. Saved under data/projects/<pid>/assets/.
"""

from typing import Optional

from ..core import assets as assets_core
from ..core import projects
from ..core import prompt_templates as tpl
from ..core.paths import PROJECTS_DIR
from . import image_gen

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
