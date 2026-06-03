"""Global application settings (schema-driven on the frontend)."""

from .paths import SETTINGS_FILE, OUTPUT_DIR
from .store import read_json, write_json

DEFAULTS = {
    # generation defaults
    "image_size": "1024x1024",
    "image_quality": "auto",
    "image_timeout": 180,
    "video_aspect_ratio": "16:9",
    "video_resolution": "720p",
    "video_duration": 10,
    "video_timeout": 300,
    # llm: model used for multimodal continuity review (must support image input)
    "vision_model": "qwen3-vl-plus",
    # batch engine
    "max_concurrency": 3,
    "max_retries": 2,
    "task_interval_ms": 800,
    # reference images: hard cap on how many垫图 a single shot may feed the model.
    # When exceeded, lowest-priority images are dropped first, in the order
    # 导演图/首帧图 ＞ 角色图 ＞ 背景图 ＞ 配角图 ＞ 道具图.
    "max_reference_images": 8,
    # storage / theme
    "output_dir": str(OUTPUT_DIR),
    "theme": "dark",
    "accent": "#21fe84",
}


def load_settings() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(read_json(SETTINGS_FILE, {}))
    return cfg


def save_settings(patch: dict) -> dict:
    cfg = load_settings()
    for k, v in (patch or {}).items():
        if k in DEFAULTS and v is not None:
            cfg[k] = v
    write_json(SETTINGS_FILE, cfg)
    return cfg
