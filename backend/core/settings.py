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
    # 分镜：单个分镜的目标时长（秒）。拆解剧本时按此折算单镜承载的文字量——
    # 纯画面/旁白 ≈8字/秒（120字≈15秒），对白 ≈5字/秒；一镜对白越多则承载文字
    # 越少，避免"对话过载"。单镜估算时长也以此为中心，最终钳制到模型 15s 上限。
    "shot_target_seconds": 10,
    # llm: model used for multimodal continuity review (must support image input)
    "vision_model": "qwen3-vl-plus",
    # batch engine
    "max_concurrency": 3,
    "max_retries": 2,
    "task_interval_ms": 800,
    # per-episode concurrency: how many 分集 may generate at once. Each episode
    # runs serially内部 (concurrency=1) so memory stays bounded; raising this
    # runs more episodes in parallel at the cost of more RAM / API pressure.
    "max_parallel_episodes": 2,
    # 资产库「一键生成」并发数：同时生成多少张资产参考图。生图为 IO 密集（等中转
    # 返回），适度并发能显著加速；过高会增大 API 压力/限流，默认取稳妥值 3。
    "asset_gen_concurrency": 3,
    # reference images: hard cap on how many垫图 a single shot may feed the model.
    # When exceeded, lowest-priority images are dropped first, in the order
    # 导演图/首帧图 ＞ 角色图 ＞ 背景图 ＞ 配角图 ＞ 道具图.
    "max_reference_images": 4,
    # Video reference transport:
    # - auto: public URL first, then Data URL if all public hosts fail
    # - public_url: require a fetchable public URL
    # - data_url: send data:image/...;base64 directly when the relay supports it
    "video_reference_transport": "auto",
    # storage / theme
    "output_dir": str(OUTPUT_DIR),
    # 剪映/CapCut Windows 默认草稿目录。留空时只生成 zip，不自动写入剪映草稿库。
    "jianying_draft_dir": "",
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
