"""剪映 (JianYing / CapCut) draft export.

Turns the current episode's storyboard — each shot's chosen image/video plus its
subtitle text — into a 剪映 draft folder:

    <draft_name>/
        draft_content.json     # timeline: a video track + a subtitle text track
        draft_meta_info.json   # draft metadata 剪映 uses to list the draft
        media/                 # copied shot images / videos

The whole folder is zipped and returned to the caller. To open it, drop the
unzipped folder into 剪映's draft directory (Windows 默认:
``%USERPROFILE%/AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft``).

The schema follows 剪映 5.x / CapCut desktop (draft version 360000). 剪映
references media by absolute ``path``; we write the path of the copied media
inside the draft folder, so the draft opens in place. If the folder is moved,
剪映 may prompt to relink — point it at the bundled ``media/`` directory.
"""

import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from ..core.paths import OUTPUT_DIR, PROJECTS_DIR
from . import ffmpeg_util

# fallback media dimensions / durations
_DEF_W, _DEF_H = 1920, 1080
_IMG_DUR_US = 3_000_000          # photos default to 3s on the timeline
_DEF_VID_DUR_US = 5_000_000      # used when a video's duration can't be probed
_US = 1_000_000

_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _is_video(name: str) -> bool:
    return Path(name).suffix.lower() in _VIDEO_EXTS


def _now_us() -> int:
    return int(time.time() * _US)


# ── material builders ────────────────────────────────────────────────────────
def _video_material(mat_id: str, path: str, name: str, is_video: bool,
                    width: int, height: int, duration_us: int) -> dict:
    return {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": duration_us,
        "extra_type_option": 0,
        "formula_id": "",
        "freeze": None,
        "gameplay": None,
        "has_audio": is_video,
        "height": height,
        "id": mat_id,
        "intensifies_audio_path": "",
        "is_ai_generate_content": False,
        "is_copyright": False,
        "is_text_edit_overdub": False,
        "is_unified_beauty_mode": False,
        "local_id": "",
        "local_material_id": "",
        "material_id": "",
        "material_name": name,
        "material_url": "",
        "media_path": "",
        "object_locked": None,
        "path": path,
        "picture_from": "none",
        "picture_set_category_id": "",
        "picture_set_category_name": "",
        "request_id": "",
        "reverse_intensifies_audio_path": "",
        "reverse_path": "",
        "smart_motion": None,
        "source": 0,
        "source_platform": 0,
        "stable": None,
        "team_id": "",
        "type": "video" if is_video else "photo",
        "video_algorithm": {
            "algorithms": [], "complement_frame_config": None,
            "deflicker": None, "gameplay_configs": [], "motion_blur_config": None,
            "noise_reduction": None, "path": "", "quality_enhance": None,
            "time_range": None,
        },
        "width": width,
    }


def _text_material(mat_id: str, content: str) -> dict:
    # 剪映 stores the displayed string both as a rich-text JSON blob and plainly.
    rich = json.dumps({
        "text": content,
        "styles": [{
            "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}}},
            "font": {"id": "", "path": ""},
            "size": 8.0,
            "range": [0, len(content)],
        }],
    }, ensure_ascii=False)
    return {
        "add_type": 0,
        "alignment": 1,
        "background_alpha": 1.0,
        "background_color": "",
        "background_height": 0.14,
        "background_horizontal_offset": 0.0,
        "background_round_radius": 0.0,
        "background_style": 0,
        "background_vertical_offset": 0.0,
        "background_width": 0.14,
        "bold_width": 0.0,
        "border_alpha": 1.0,
        "border_color": "",
        "border_width": 0.08,
        "caption_template_info": None,
        "check_flag": 7,
        "combo_info": {"text_templates": []},
        "content": rich,
        "fixed_height": -1.0,
        "fixed_width": -1.0,
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": "",
        "font_path": "",
        "font_resource_id": "",
        "font_size": 8.0,
        "font_source_platform": 0,
        "font_title": "none",
        "font_url": "",
        "fonts": [],
        "force_apply_line_max_width": False,
        "global_alpha": 1.0,
        "group_id": "",
        "has_shadow": False,
        "id": mat_id,
        "initial_scale": 1.0,
        "is_rich_text": False,
        "italic_degree": 0,
        "ktv_color": "",
        "language": "",
        "layer_weight": 1,
        "letter_spacing": 0.0,
        "line_feed": 1,
        "line_max_width": 0.82,
        "line_spacing": 0.02,
        "multi_language_current": "none",
        "name": "",
        "original_size": [],
        "preset_category": "",
        "preset_category_id": "",
        "preset_has_set_alignment": False,
        "preset_id": "",
        "preset_index": 0,
        "preset_name": "",
        "recognize_task_id": "",
        "recognize_type": 0,
        "relevance_segment": [],
        "shadow_alpha": 0.9,
        "shadow_angle": -45.0,
        "shadow_color": "",
        "shadow_distance": 5.0,
        "shadow_point": {"x": 0.6, "y": -0.6},
        "shadow_smoothing": 0.45,
        "shape_clip_x": False,
        "shape_clip_y": False,
        "style_name": "",
        "sub_type": 0,
        "subtitle_keywords": None,
        "subtitle_template_original_fontsize": 0.0,
        "text_alpha": 1.0,
        "text_color": "#FFFFFF",
        "text_curve": None,
        "text_preset_resource_id": "",
        "text_size": 30,
        "text_to_audio_ids": [],
        "tts_auto_update": False,
        "type": "text",
        "typesetting": 0,
        "underline": False,
        "underline_offset": 0.22,
        "underline_width": 0.05,
        "use_effect_default_color": True,
        "words": {"end_time": [], "start_time": [], "text": []},
    }


def _video_segment(seg_id: str, mat_id: str, start_us: int, dur_us: int) -> dict:
    return {
        "cartoon": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "common_keyframes": [],
        "enable_adjust": True,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "extra_material_refs": [],
        "group_id": "",
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "id": seg_id,
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": mat_id,
        "render_index": 0,
        "responsive_layout": {
            "enable": False, "horizontal_pos_layout": 0,
            "size_layout": 0, "target_follow": "", "vertical_pos_layout": 0,
        },
        "reverse": False,
        "source_timerange": {"duration": dur_us, "start": 0},
        "speed": 1.0,
        "target_timerange": {"duration": dur_us, "start": start_us},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": 1.0,
    }


def _text_segment(seg_id: str, mat_id: str, start_us: int, dur_us: int) -> dict:
    return {
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": -0.78},
        },
        "common_keyframes": [],
        "enable_adjust": False,
        "extra_material_refs": [],
        "group_id": "",
        "id": seg_id,
        "is_placeholder": False,
        "keyframe_refs": [],
        "material_id": mat_id,
        "render_index": 14000,
        "responsive_layout": {
            "enable": False, "horizontal_pos_layout": 0,
            "size_layout": 0, "target_follow": "", "vertical_pos_layout": 0,
        },
        "source_timerange": None,
        "target_timerange": {"duration": dur_us, "start": start_us},
        "template_id": "",
        "template_scene": "default",
        "track_attribute": 0,
        "track_render_index": 14000,
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
        "volume": 1.0,
    }


def _draft_content(name: str, videos: list, texts: list,
                   video_segs: list, text_segs: list, total_us: int) -> dict:
    empty = []
    return {
        "canvas_config": {"height": _DEF_H, "ratio": "original", "width": _DEF_W},
        "color_space": 0,
        "config": {
            "adjust_mask_track_render_index": 0,
            "attachment_info": [],
            "combination_empty_track_render_index": 0,
            "export_range": None,
            "extract_audio_last_index": 0,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 0,
            "record_audio_last_index": 0,
            "sticker_max_index": 0,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None,
        "create_time": 0,
        "duration": total_us,
        "extra_info": None,
        "fps": 30.0,
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": _uid(),
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [], "audios": [], "effects": [], "filters": [],
            "handwrites": [], "stickers": [], "texts": [], "videos": [],
        },
        "last_modified_platform": {
            "app_id": 3704, "app_source": "lv", "app_version": "5.9.0",
            "device_id": "", "hard_disk_id": "", "mac_address": "", "os": "windows",
            "os_version": "",
        },
        "materials": {
            "ai_translates": empty, "audio_balances": empty, "audio_effects": empty,
            "audio_fades": empty, "audio_track_indexes": empty, "audios": empty,
            "beats": empty, "canvases": [{
                "album_image": "", "blur": 0.0, "color": "", "id": _uid(),
                "image": "", "image_id": "", "image_name": "", "source_platform": 0,
                "team_id": "", "type": "canvas_color",
            }], "chromas": empty, "color_curves": empty, "digital_humans": empty,
            "drafts": empty, "effects": empty, "flowers": empty, "green_screens": empty,
            "handwrites": empty, "hsl": empty, "images": empty,
            "log_color_wheels": empty, "loudnesses": empty, "manual_deformations": empty,
            "masks": empty, "material_animations": empty, "material_colors": empty,
            "multi_language_refs": empty, "placeholders": empty, "plugin_effects": empty,
            "primary_color_wheels": empty, "realtime_denoises": empty, "shapes": empty,
            "smart_crops": empty, "smart_relights": empty, "sound_channel_mappings": empty,
            "speeds": [{"curve_speed": None, "id": _uid(), "mode": 0, "speed": 1.0, "type": "speed"}],
            "stickers": empty, "tail_leaders": empty, "text_templates": empty,
            "texts": texts, "time_marks": empty, "transitions": empty,
            "video_effects": empty, "video_trackings": empty, "videos": videos,
            "vocal_beautifiers": empty, "vocal_separations": empty,
        },
        "mutable_config": None,
        "name": name,
        "new_version": "110.0.0",
        "platform": {
            "app_id": 3704, "app_source": "lv", "app_version": "5.9.0",
            "device_id": "", "hard_disk_id": "", "mac_address": "", "os": "windows",
            "os_version": "",
        },
        "relationships": [],
        "render_index_track_mode_on": True,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": [
            {
                "attribute": 0, "flag": 0, "id": _uid(),
                "is_default_name": True, "name": "", "segments": video_segs,
                "type": "video",
            },
            {
                "attribute": 0, "flag": 0, "id": _uid(),
                "is_default_name": True, "name": "", "segments": text_segs,
                "type": "text",
            },
        ],
        "update_time": 0,
        "version": 360000,
    }


def _draft_meta(draft_id: str, name: str, root: str, total_us: int) -> dict:
    now = _now_us()
    return {
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "", "draft_enterprise_id": "",
            "draft_enterprise_name": "", "enterprise_material": [],
        },
        "draft_fold_path": root,
        "draft_id": draft_id,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_materials": [],
        "draft_name": name,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": str(Path(root).parent),
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": total_us,
    }


def _resolve_media(pid: str, item: dict) -> Optional[Path]:
    """Locate a shot material file from {bid, filename}."""
    fn = item.get("filename")
    bid = item.get("bid")
    if not fn or not bid:
        return None
    p = OUTPUT_DIR / pid / bid / fn
    return p if p.exists() else None


def build_draft(pid: str, draft_name: str, items: list) -> Path:
    """Build a 剪映 draft folder + zip from *items* and return the zip path.

    *items*: ordered list of {shot_no, bid, filename, subtitle}. Each contributes
    one clip on the video track and (when subtitle is non-empty) one subtitle on
    the text track, laid end-to-end.
    """
    safe_name = "".join(c for c in (draft_name or "batch-studio") if c not in '\\/:*?"<>|').strip() or "draft"
    base = OUTPUT_DIR / pid / "jianying"
    base.mkdir(parents=True, exist_ok=True)
    draft_dir = base / safe_name
    if draft_dir.exists():
        shutil.rmtree(draft_dir, ignore_errors=True)
    media_dir = draft_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    videos, texts, video_segs, text_segs = [], [], [], []
    cursor = 0
    used = 0
    for item in items:
        src = _resolve_media(pid, item)
        if not src:
            continue
        is_vid = _is_video(src.name)
        # copy media into the draft folder
        dst = media_dir / f"{item.get('shot_no') or used}_{src.name}"
        shutil.copy2(src, dst)
        if is_vid:
            dur = ffmpeg_util.probe_duration(dst)
            dur_us = int(dur * _US) if dur else _DEF_VID_DUR_US
        else:
            dur_us = _IMG_DUR_US

        mat_id = _uid()
        videos.append(_video_material(mat_id, str(dst), dst.name, is_vid,
                                      _DEF_W, _DEF_H, dur_us))
        video_segs.append(_video_segment(_uid(), mat_id, cursor, dur_us))

        subtitle = (item.get("subtitle") or "").strip()
        if subtitle:
            tid = _uid()
            texts.append(_text_material(tid, subtitle))
            text_segs.append(_text_segment(_uid(), tid, cursor, dur_us))

        cursor += dur_us
        used += 1

    if used == 0:
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise ValueError("本集没有可导出的素材（请先生成分镜图片或视频）")

    content = _draft_content(safe_name, videos, texts, video_segs, text_segs, cursor)
    meta = _draft_meta(content["id"], safe_name, str(draft_dir), cursor)
    (draft_dir / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_path = base / f"{safe_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in draft_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(base))
    return zip_path
