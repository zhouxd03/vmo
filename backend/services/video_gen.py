"""Video generation service (adapted from shengtu /api/video/generate).

Key change vs shengtu: the API base URL is NOT hardcoded — it comes from the
Credential Vault (category=video) so the user provides base_url + key manually.
Submits a job, then polls until done, downloading the result locally.
"""

import base64
import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests as http

from ..core import credentials
from . import image_host

logger = logging.getLogger("batch_studio")


class VideoError(Exception):
    def __init__(self, message, *, code="", status=None, err_type="", raw=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.err_type = err_type
        self.raw = raw


def _api_error(resp, where="API") -> "VideoError":
    """Build a structured VideoError from an HTTP error response."""
    code = err_type = ""
    msg = resp.text[:300]
    try:
        body = resp.json()
        err = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(err, dict):
            code = str(err.get("code") or "")
            err_type = str(err.get("type") or "")
            msg = str(err.get("message") or msg)
    except Exception:  # noqa: BLE001
        pass
    return VideoError(f"{where} 错误 (HTTP {resp.status_code}): {msg}",
                      code=code, status=resp.status_code, err_type=err_type, raw=resp.text[:500])


def _resolve_creds(base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str]:
    if base_url and api_key:
        return base_url.strip().rstrip("/"), api_key.strip(), ""
    cred = credentials.get_default("video")
    if not cred or not cred.get("base_url") or not cred.get("api_key"):
        raise VideoError("请先在设置→凭据库添加并启用一个【生视频】API（地址+Key 手动填写）")
    return cred["base_url"].strip().rstrip("/"), cred["api_key"].strip(), cred.get("model", "")


def _extract_video_url(obj: dict) -> str:
    if not isinstance(obj, dict):
        return ""
    for k in ("video_url", "url", "result_url", "output_url", "download_url"):
        v = obj.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    # nested common shapes
    for k in ("data", "result", "output", "video"):
        v = obj.get(k)
        if isinstance(v, dict):
            nested = _extract_video_url(v)
            if nested:
                return nested
        if isinstance(v, list) and v and isinstance(v[0], dict):
            nested = _extract_video_url(v[0])
            if nested:
                return nested
    return ""


def _extract_task_id(obj: dict) -> str:
    for k in ("id", "task_id", "taskId", "request_id", "job_id"):
        v = obj.get(k)
        if v:
            return str(v)
    data = obj.get("data")
    if isinstance(data, dict):
        return _extract_task_id(data)
    return ""


def generate_video(
    prompt: str,
    *,
    model: Optional[str] = None,
    duration: int = 10,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    ref_images_b64: Optional[list[str]] = None,
    save_dir: str | Path,
    filename_prefix: str = "",
    timeout: int = 300,
    poll_interval: int = 5,
    on_progress: Optional[Callable[[int], None]] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise VideoError("请输入视频描述提示词")

    api_base, key, default_model = _resolve_creds(base_url, api_key)
    model = model or default_model or "sora-v3-fast"
    ref_images_b64 = [b for b in (ref_images_b64 or []) if b]

    def _to_url(b, idx, what):
        try:
            base64.b64decode(b)
        except Exception as e:  # noqa: BLE001
            raise VideoError(f"第 {idx+1} 张{what}数据无效") from e
        return image_host.upload(b)

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt,
        "seconds": str(duration),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }

    # 多模态软参考生视频：上一镜尾帧 / 导演图 / 站位图 / 资产图均以参考图
    # (reference_image_urls) 喂入，模型据此重渲染当前镜。注：本中转 seed-2.0fast
    # 不支持「首帧钉死」图生视频（实测输入图被静默丢弃），故统一走软参考。
    if ref_images_b64:
        payload["reference_image_urls"] = [_to_url(b, i, "参考图")
                                           for i, b in enumerate(ref_images_b64)]
        mode = "图生视频"
    else:
        mode = "文生视频"

    submit_url = f"{api_base}/v1/videos"
    logger.info(f"[Video] 提交 {mode}: model={model} {duration}s")
    resp = http.post(submit_url, json=payload, headers=headers, timeout=60)
    if resp.status_code >= 400:
        raise _api_error(resp, "提交")
    result = resp.json()

    video_url = _extract_video_url(result)
    if not video_url:
        task_id = _extract_task_id(result)
        if not task_id:
            raise VideoError(f"无法解析任务ID或视频URL: {str(result)[:300]}")
        video_url = _poll(api_base, task_id, key, timeout, poll_interval, on_progress)

    if on_progress:
        on_progress(100)
    return _download(video_url, model, duration, save_dir, filename_prefix)


def _poll(api_base, task_id, key, timeout, interval, on_progress) -> str:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    poll_url = f"{api_base}/v1/videos/{task_id}"
    elapsed = 0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            r = http.get(poll_url, headers=headers, timeout=30)
            if r.status_code >= 400:
                continue
            data = r.json()
            status = str(data.get("status", "")).lower()
            prog = data.get("progress", 0)
            if isinstance(prog, str) and prog.endswith("%"):
                prog = int(prog.replace("%", ""))
            elif isinstance(prog, (int, float)):
                prog = int(prog)
            else:
                prog = 0
            if on_progress:
                on_progress(prog)
            logger.info(f"[Video] 轮询 {elapsed}s: status={status} progress={prog}%")
            if status in ("completed", "done", "success", "finished"):
                url = _extract_video_url(data) or _extract_video_url(data.get("data", {}))
                if url:
                    return url
            elif status in ("failed", "error"):
                msg = data.get("fail_reason") or data.get("error") or data.get("message") or "生成失败"
                code = err_type = ""
                if isinstance(msg, dict):
                    code = str(msg.get("code") or "")
                    err_type = str(msg.get("type") or "")
                    msg = str(msg.get("message") or msg)
                raise VideoError(str(msg), code=code, err_type=err_type, raw=str(data)[:500])
        except VideoError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Video] 轮询异常: {e}")
            continue
    raise VideoError(f"生成超时（{timeout}秒）")


def _download(video_url, model, duration, save_dir, filename_prefix) -> dict:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    video_id = (filename_prefix + "_" if filename_prefix else "") + uuid.uuid4().hex[:8]
    filename = f"{video_id}_{model.replace('/', '_')}.mp4"
    filepath = save_dir / filename
    try:
        dl = http.get(video_url, timeout=180)
        dl.raise_for_status()
        filepath.write_bytes(dl.content)
        logger.info(f"[Video] 已保存 {filepath} ({len(dl.content)} bytes)")
        saved = str(filepath)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Video] 下载失败: {e}; 仅返回 URL")
        saved = ""
    return {
        "video_id": video_id,
        "filename": filename if saved else "",
        "filepath": saved,
        "video_url": video_url,
        "model": model,
        "duration": duration,
        "created_at": time.time(),
    }
