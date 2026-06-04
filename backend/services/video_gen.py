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


def _resolve_creds(base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str, str]:
    """Return (base_url, api_key, default_model, provider)."""
    if base_url and api_key:
        return base_url.strip().rstrip("/"), api_key.strip(), "", _detect_provider(base_url, "")
    cred = credentials.get_default("video")
    if not cred or not cred.get("base_url") or not cred.get("api_key"):
        raise VideoError("请先在设置→凭据库添加并启用一个【生视频】API（地址+Key 手动填写）")
    base = cred["base_url"].strip().rstrip("/")
    return base, cred["api_key"].strip(), cred.get("model", ""), _detect_provider(base, cred.get("provider", ""))


def _detect_provider(base_url: str, provider: str) -> str:
    """Pick the request-construction dialect. Explicit credential `provider`
    wins; otherwise sniff the base_url (Seedance hosts its API under
    /seedanceapi). Defaults to the OpenAI-compatible /v1/videos shape."""
    p = (provider or "").strip().lower()
    if p in ("seedance", "openai"):
        return p
    if "seedance" in (base_url or "").lower():
        return "seedance"
    return "openai"


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


def _stringify_upstream(value, limit: int = 1200) -> str:
    """Return a compact, readable upstream error string without truncating to
    useless fragments like ``{\\``. Providers often return nested error dicts,
    escaped JSON strings, or only ``fail_reason``; keep enough context for the
    user to fix credentials/params/prompt without opening raw logs."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        # Some relays return JSON as an escaped string; decode once if possible.
        if text.startswith("{") or text.startswith("["):
            try:
                import json
                return _stringify_upstream(json.loads(text), limit)
            except Exception:  # noqa: BLE001
                pass
        return text[:limit]
    if isinstance(value, dict):
        preferred = []
        for k in ("message", "msg", "detail", "reason", "fail_reason", "code", "type"):
            v = value.get(k)
            if v not in (None, ""):
                preferred.append(f"{k}={_stringify_upstream(v, limit)}")
        if preferred:
            return "；".join(preferred)[:limit]
    try:
        import json
        return json.dumps(value, ensure_ascii=False)[:limit]
    except Exception:  # noqa: BLE001
        return str(value)[:limit]


def _extract_error_fields(data: dict) -> tuple[str, str, str]:
    """Extract (message, code, type) from common relay/provider error shapes."""
    candidates = []
    if isinstance(data, dict):
        for k in ("fail_reason", "error", "message", "msg", "detail", "reason"):
            if data.get(k) not in (None, ""):
                candidates.append(data.get(k))
        for k in ("data", "result"):
            v = data.get(k)
            if isinstance(v, dict):
                for kk in ("fail_reason", "error", "message", "msg", "detail", "reason"):
                    if v.get(kk) not in (None, ""):
                        candidates.append(v.get(kk))
    msg_src = candidates[0] if candidates else data
    code = err_type = ""
    if isinstance(msg_src, dict):
        code = str(msg_src.get("code") or msg_src.get("error_code") or "")
        err_type = str(msg_src.get("type") or msg_src.get("error_type") or "")
    if not code and isinstance(data, dict):
        code = str(data.get("code") or data.get("error_code") or "")
    if not err_type and isinstance(data, dict):
        err_type = str(data.get("type") or data.get("error_type") or "")
    msg = _stringify_upstream(msg_src)
    return msg or "生成失败", code, err_type


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
    generate_audio: bool = False,
    watermark: bool = False,
) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise VideoError("请输入视频描述提示词")

    api_base, key, default_model, provider = _resolve_creds(base_url, api_key)
    ref_images_b64 = [b for b in (ref_images_b64 or []) if b]

    # safety net: a doubao-seedance model implies the Seedance dialect even if
    # the credential's provider field was left blank.
    if provider != "seedance" and (model or default_model or "").lower().startswith("doubao-seedance"):
        provider = "seedance"

    if provider == "seedance":
        return _generate_seedance(
            prompt, api_base=api_base, key=key, model=model or default_model,
            duration=duration, aspect_ratio=aspect_ratio, resolution=resolution,
            ref_images_b64=ref_images_b64, save_dir=save_dir,
            filename_prefix=filename_prefix, timeout=timeout,
            poll_interval=poll_interval, on_progress=on_progress,
            generate_audio=generate_audio, watermark=watermark,
        )

    model = model or default_model or "sora-v3-fast"

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
                msg, code, err_type = _extract_error_fields(data)
                raise VideoError(msg, code=code, err_type=err_type, raw=_stringify_upstream(data))
        except VideoError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Video] 轮询异常: {e}")
            continue
    raise VideoError(f"生成超时（{timeout}秒）")


# ── Seedance (doubao-seedance) provider ─────────────────────────────────────
# A different dialect from the OpenAI-style path above:
#   submit : POST {root}/seedanceapi/common/File/All  (multipart/form-data)
#            header `token`; reference images are uploaded as real `files` parts
#            (no image-host round-trip). Returns {code,data:{Id},success}.
#   query  : POST {root}/seedanceapi/user/DataIndex   (application/json)
#            {Id} -> {data:{Status,VideoUrl,Message,...}} (Status 0/1/2/3).
_SEEDANCE_ASPECTS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
_SEEDANCE_RES = {"480p", "720p", "1080p"}
_SEEDANCE_DEFAULT_MODEL = "doubao-seedance-2-0-fast-260128"


def _seedance_root(api_base: str) -> str:
    """Host root for Seedance, tolerant of a base_url that already includes the
    /seedanceapi prefix."""
    base = (api_base or "").rstrip("/")
    return base.split("/seedanceapi")[0].rstrip("/")


def _generate_seedance(
    prompt, *, api_base, key, model, duration, aspect_ratio, resolution,
    ref_images_b64, save_dir, filename_prefix, timeout, poll_interval,
    on_progress, generate_audio, watermark,
) -> dict:
    root = _seedance_root(api_base)
    model = model or _SEEDANCE_DEFAULT_MODEL
    dur = max(4, min(15, int(duration or 5)))          # Seedance allows 4-15s
    aspect = aspect_ratio if aspect_ratio in _SEEDANCE_ASPECTS else "16:9"
    res = resolution if resolution in _SEEDANCE_RES else "720p"

    form = {
        "model": model,
        "prompt": prompt,
        "duration": str(dur),
        "aspect_ratio": aspect,
        "resolution": res,
        "generate_audio": "true" if generate_audio else "false",
        "watermark": "true" if watermark else "false",
    }
    files = []
    for i, b in enumerate(ref_images_b64 or []):
        try:
            raw = base64.b64decode(b)
        except Exception as e:  # noqa: BLE001
            raise VideoError(f"第 {i+1} 张参考图数据无效") from e
        files.append(("files", (f"ref{i+1}.png", raw, "image/png")))

    mode = "图生视频" if files else "文生视频"
    submit_url = f"{root}/seedanceapi/common/File/All"
    logger.info(f"[Video][Seedance] 提交 {mode}: model={model} {dur}s {aspect} {res} refs={len(files)}")
    try:
        resp = http.post(submit_url, data=form, files=files or None,
                         headers={"token": key}, timeout=120)
    except Exception as e:  # noqa: BLE001
        raise VideoError(f"Seedance 提交请求失败: {e}") from e
    if resp.status_code >= 400:
        raise _api_error(resp, "Seedance 提交")
    body = _seedance_json(resp, "提交")
    task_id = ((body.get("data") or {}).get("Id"))
    if task_id in (None, "", 0):
        raise VideoError(f"Seedance 未返回任务ID: {str(body)[:300]}")

    if on_progress:
        on_progress(5)
    video_url = _poll_seedance(root, task_id, key, timeout, poll_interval, on_progress)
    if on_progress:
        on_progress(100)
    return _download(video_url, model, dur, save_dir, filename_prefix)


def _seedance_json(resp, where: str) -> dict:
    """Parse a Seedance JSON envelope and raise on business-level failure
    (code != 0 / success false), surfacing the server's message."""
    try:
        body = resp.json()
    except Exception as e:  # noqa: BLE001
        raise VideoError(f"Seedance {where} 返回非 JSON: {resp.text[:200]}") from e
    if not isinstance(body, dict):
        raise VideoError(f"Seedance {where} 返回格式异常: {str(body)[:200]}")
    if body.get("code") not in (0, None) or body.get("success") is False:
        msg = body.get("message") or "请求失败"
        raise VideoError(f"Seedance {where}失败: {msg}", code=str(body.get("code") or ""), raw=str(body)[:500])
    return body


def seedance_user_info(base_url: str, api_key: str) -> dict:
    """Query Seedance account balances (token / fast token / int'l seconds).
    Credit-free — used as the connectivity/auth test for a Seedance credential."""
    root = _seedance_root((base_url or "").strip().rstrip("/"))
    headers = {"token": (api_key or "").strip(), "Content-Type": "application/json"}
    resp = http.post(f"{root}/seedanceapi/user/UserIndex", json={"Id": 0},
                     headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise _api_error(resp, "Seedance 鉴权")
    return (_seedance_json(resp, "鉴权").get("data") or {})


def _poll_seedance(root, task_id, key, timeout, interval, on_progress) -> str:
    query_url = f"{root}/seedanceapi/user/DataIndex"
    headers = {"token": key, "Content-Type": "application/json"}
    elapsed = 0
    interval = max(3, interval)
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            r = http.post(query_url, json={"Id": task_id}, headers=headers, timeout=30)
            if r.status_code >= 400:
                continue
            data = (r.json() or {}).get("data") or {}
            status = data.get("Status")
            logger.info(f"[Video][Seedance] 轮询 {elapsed}s: status={status} ({data.get('StatusText','')})")
            if status == 2:                              # 已完成
                url = data.get("VideoUrl") or ""
                if url:
                    return url
            elif status == 3:                            # 失败
                raise VideoError(data.get("Message") or "Seedance 生成失败", raw=str(data)[:500])
            else:                                        # 0 待处理 / 1 处理中
                if on_progress:
                    on_progress(40 if status == 1 else 15)
        except VideoError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Video][Seedance] 轮询异常: {e}")
            continue
    raise VideoError(f"Seedance 生成超时（{timeout}秒）")


def _download(video_url, model, duration, save_dir, filename_prefix) -> dict:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    video_id = (filename_prefix + "_" if filename_prefix else "") + uuid.uuid4().hex[:8]
    filename = f"{video_id}_{model.replace('/', '_')}.mp4"
    filepath = save_dir / filename
    try:
        # stream to disk in chunks so the whole video never sits in RAM at once
        # (keeps memory bounded when several episodes generate concurrently)
        size = 0
        with http.get(video_url, timeout=180, stream=True) as dl:
            dl.raise_for_status()
            with open(filepath, "wb") as fh:
                for chunk in dl.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fh.write(chunk)
                        size += len(chunk)
        logger.info(f"[Video] 已保存 {filepath} ({size} bytes)")
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
