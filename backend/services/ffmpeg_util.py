"""ffmpeg helpers for the continuity engine (Phase 5).

The single mechanic we need from ffmpeg is **tail-frame extraction**: grab the
last visible frame of a generated shot's video so it can be fed as the reference
image of the next shot (无缝衔接). We also expose probe_duration so the decision
engine can reason about timing.

Binary resolution order (so dev + frozen builds both work):
  1. BATCH_STUDIO_FFMPEG / BATCH_STUDIO_FFPROBE env override
  2. a bundled binary next to the executable / under BUNDLED_DIR (PyInstaller)
  3. the system PATH (dev machines / users who already have ffmpeg)
"""

import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from ..core.paths import APP_DIR, BUNDLED_DIR

logger = logging.getLogger("batch_studio")

_EXE = ".exe" if os.name == "nt" else ""


class FFmpegError(Exception):
    pass


def _find(tool: str, env_key: str) -> Optional[str]:
    override = os.environ.get(env_key)
    if override and Path(override).exists():
        return override
    # bundled (next to exe, or under PyInstaller temp / backend dir)
    for base in (APP_DIR, BUNDLED_DIR, APP_DIR / "bin", BUNDLED_DIR / "bin"):
        cand = Path(base) / f"{tool}{_EXE}"
        if cand.exists():
            return str(cand)
    # system PATH
    found = shutil.which(tool)
    return found


def ffmpeg_path() -> str:
    p = _find("ffmpeg", "BATCH_STUDIO_FFMPEG")
    if not p:
        raise FFmpegError("未找到 ffmpeg 可执行文件（开发机请安装 ffmpeg，打包版会内置）")
    return p


def ffprobe_path() -> Optional[str]:
    return _find("ffprobe", "BATCH_STUDIO_FFPROBE")


def is_available() -> bool:
    try:
        ffmpeg_path()
        return True
    except FFmpegError:
        return False


def probe_duration(video_path: str | Path) -> Optional[float]:
    """Return the video duration in seconds, or None if it can't be probed."""
    probe = ffprobe_path()
    video_path = str(video_path)
    if probe:
        try:
            out = subprocess.run(
                [probe, "-v", "quiet", "-print_format", "json", "-show_format", video_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30,
            )
            if out.returncode == 0:
                data = json.loads(out.stdout or "{}")
                dur = data.get("format", {}).get("duration")
                if dur is not None:
                    return float(dur)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ffmpeg] ffprobe 时长解析失败: {e}")
    return None


def extract_tail_frame(
    video_path: str | Path,
    out_path: str | Path,
    *,
    offset_from_end: float = 0.05,
) -> str:
    """Extract the last visible frame of *video_path* to *out_path* (PNG/JPG).

    We seek to (duration - offset_from_end) when the duration is known (more
    reliable than seeking to the very end, which can land past the last frame),
    and otherwise fall back to ``-sseof`` (seek-from-end).
    """
    ff = ffmpeg_path()
    video_path = str(video_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not Path(video_path).exists():
        raise FFmpegError(f"视频文件不存在: {video_path}")

    duration = probe_duration(video_path)
    # Read a short window at the very end and let -update 1 overwrite the output
    # for every decoded frame, so the file left behind is the *last* frame. This
    # is far more robust than seeking to an exact timestamp (which can land past
    # the final frame and produce an empty file).
    window = 3.0
    if duration and duration < window:
        window = max(duration, 0.2)
    cmd = [ff, "-y", "-sseof", f"-{window:.3f}", "-i", video_path,
           "-update", "1", "-q:v", "2", str(out_path)]

    logger.info(f"[ffmpeg] 抽取尾帧: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0 or not out_path.exists():
        # last resort: seek slightly before the known end and grab one frame
        if duration:
            seek = max(0.0, duration - max(offset_from_end, 0.2))
            cmd2 = [ff, "-y", "-ss", f"{seek:.3f}", "-i", video_path,
                    "-update", "1", "-frames:v", "1", "-q:v", "2", str(out_path)]
            proc = subprocess.run(cmd2, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0 or not out_path.exists():
            raise FFmpegError(f"尾帧抽取失败: {(proc.stderr or '')[-300:]}")
    return str(out_path)


def tail_frame_filename(shot_no: str) -> str:
    safe = (shot_no or uuid.uuid4().hex[:8]).replace("/", "_")
    return f"{safe}_tail.png"


def probe_image_size(path: str | Path) -> Optional[tuple[int, int]]:
    """Return (width, height) of an image/video first frame, or None."""
    probe = ffprobe_path()
    if not probe:
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            w, h = out.stdout.strip().split(",")[:2]
            return int(w), int(h)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ffmpeg] 图片尺寸探测失败: {e}")
    return None


def crop_b64_to_aspect(img_b64: str, ar_w: int, ar_h: int, *, tol: float = 0.01) -> str:
    """Center-crop a base64 image to the *exact* target aspect ratio (ar_w:ar_h).

    Used to keep every reference image fed to the video model at the same aspect
    ratio as the target video — a square/3:2 reference would otherwise be
    stretched by the model and cause scale jitter between continuity shots. We
    crop (never stretch) so geometry is preserved; if the image already matches
    the target ratio within *tol* it is returned unchanged (no re-encode).
    """
    import base64 as _b64

    if not img_b64 or ar_w <= 0 or ar_h <= 0:
        return img_b64
    target = ar_w / ar_h
    tmp_in = APP_DIR / f"_arnorm_{uuid.uuid4().hex[:8]}_in.png"
    tmp_out = APP_DIR / f"_arnorm_{uuid.uuid4().hex[:8]}_out.png"
    try:
        tmp_in.write_bytes(_b64.b64decode(img_b64))
        dims = probe_image_size(tmp_in)
        if dims:
            w, h = dims
            cur = w / h if h else target
            if abs(cur - target) <= tol:
                return img_b64  # already correct aspect → leave untouched
        ff = ffmpeg_path()
        # centered crop to the target ratio (crop defaults to centered)
        vf = (f"crop='if(gt(iw/ih,{target}),ih*{target},iw)':"
              f"'if(gt(iw/ih,{target}),ih,iw/{target})'")
        proc = subprocess.run([ff, "-y", "-i", str(tmp_in), "-vf", vf,
                               "-frames:v", "1", str(tmp_out)],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        if proc.returncode == 0 and tmp_out.exists():
            return _b64.b64encode(tmp_out.read_bytes()).decode()
        logger.warning(f"[ffmpeg] 参考图裁剪到 {ar_w}:{ar_h} 失败: {(proc.stderr or '')[-200:]}")
        return img_b64
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ffmpeg] 参考图比例归一化异常: {e}")
        return img_b64
    finally:
        for p in (tmp_in, tmp_out):
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def aspect_to_image_size(aspect_ratio: str) -> str:
    """Map a video aspect ratio to the nearest gpt-image supported size that
    shares the same orientation (so director/staging helper images are framed
    consistently with the target video instead of always square/landscape)."""
    ar = (aspect_ratio or "16:9").strip()
    portrait = {"9:16", "2:3", "3:4"}
    square = {"1:1"}
    if ar in square:
        return "1024x1024"
    if ar in portrait:
        return "1024x1536"
    return "1536x1024"  # 16:9 / 4:3 / 3:2 / 21:9 → landscape
