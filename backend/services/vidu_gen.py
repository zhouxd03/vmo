"""Vidu web API adapter.

The credential api_key is treated as the Vidu web Cookie. Vidu's web workflow
uses a fixed official host, so base_url is intentionally ignored for this
provider.
"""

import base64
import hashlib
import logging
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests as http

try:
    from curl_cffi import requests as curl_http  # type: ignore
except Exception:  # noqa: BLE001
    curl_http = None

logger = logging.getLogger("batch_studio")

VIDU_BASE_URL = "https://service.vidu.cn/vidu/v1"
VIDU_UPLOAD_URL = "https://service.vidu.cn/tools/v1/files/uploads"

# UI label -> actual request values. Image generation uses both task type and
# settings.model_version; video generation writes the value to model_version.
VIDU_IMAGE_MODEL_OPTIONS = [
    {"label": "全能 Image", "value": "auto:3.2_image_2"},
    {"label": "全能 Image - 文生图", "value": "text2image:3.2_image_2"},
    {"label": "全能 Image - 参考生图", "value": "reference2image:3.2_image_2"},
]
VIDU_VIDEO_MODEL_OPTIONS = [
    {"label": "VIDU Q3", "value": "3.2"},
    {"label": "Vidu 3.1", "value": "3.1"},
]

_VIDU_USE_CURL = curl_http is not None


class ViduError(Exception):
    def __init__(self, message, *, code="", status=None, raw=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.raw = raw


def _root(_base_url: Optional[str] = None) -> str:
    return VIDU_BASE_URL


def _clip(value, limit: int = 500) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "..."


def _sanitize_cookie(cookie: str) -> str:
    """Normalize a copied browser cookie into a safe Cookie header value.

    Users often paste Set-Cookie exports that include attributes such as Path,
    Domain, SameSite, Secure, HttpOnly, or duplicated names from multiple
    domains. Vidu expects a plain request Cookie header: name=value; name=value.
    """
    text = str(cookie or "").replace("\r", ";").replace("\n", ";").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()

    attr_names = {
        "path", "domain", "expires", "max-age", "samesite", "secure",
        "httponly", "priority", "partitioned",
    }
    ordered: dict[str, str] = {}
    for part in text.split(";"):
        item = part.strip()
        if not item:
            continue
        if "=" not in item:
            if item.strip().lower() in attr_names:
                continue
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        lower = name.lower()
        if not name or lower in attr_names:
            continue
        if any(ch in name for ch in "\t \"'<>"):
            continue
        ordered[name] = value
    return "; ".join(f"{name}={value}" for name, value in ordered.items())


def _headers(cookie: str, content_type: Optional[str] = None) -> dict:
    safe_cookie = _sanitize_cookie(cookie)
    headers = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "zh",
        "cookie": safe_cookie,
        "origin": "https://www.vidu.cn",
        "priority": "u=1, i",
        "referer": "https://www.vidu.cn/",
        "sec-ch-ua": '"Microsoft Edge";v="124", "Chromium";v="124", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        ),
        "x-app-version": "-",
        "x-platform": "web",
        "x-request-id": uuid.uuid4().hex,
    }
    if content_type:
        headers["content-type"] = content_type
    return headers


def _is_edgeone_challenge_text(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "teojschallengesdk" in lower
        or "tencent_chaos_vm" in lower
        or "edgeone" in lower and "<html" in lower
    )


def _is_edgeone_challenge_response(resp) -> bool:
    content_type = str(getattr(resp, "headers", {}).get("Content-Type", "") or "").lower()
    text = str(getattr(resp, "text", "") or "")
    return ("text/html" in content_type or text.lstrip().lower().startswith("<!doctype html")) and _is_edgeone_challenge_text(text)


def _request(method: str, url: str, **kwargs):
    if _VIDU_USE_CURL:
        kwargs.setdefault("impersonate", "chrome")
        return curl_http.request(method, url, **kwargs)
    return http.request(method, url, **kwargs)


def _get(url: str, **kwargs):
    return _request("GET", url, **kwargs)


def _post(url: str, **kwargs):
    return _request("POST", url, **kwargs)


def _put(url: str, **kwargs):
    return _request("PUT", url, **kwargs)


def _json_response(resp, where: str) -> dict:
    if _is_edgeone_challenge_response(resp):
        raise ViduError(
            "Vidu EdgeOne anti-bot challenge returned HTML. Refresh the Vidu Cookie from the browser, slow down requests, and retry.",
            code="vidu_edgeone_challenge",
            status=getattr(resp, "status_code", None),
            raw=str(getattr(resp, "text", "") or "")[:1000],
        )
    if resp.status_code >= 400:
        msg = resp.text[:500]
        code = ""
        try:
            body = resp.json()
            msg = str(body.get("message") or body.get("error") or body.get("reason") or msg)
            code = str(body.get("code") or body.get("reason") or "")
        except Exception:  # noqa: BLE001
            pass
        raise ViduError(
            f"Vidu {where} failed (HTTP {resp.status_code}): {msg}",
            code=code,
            status=resp.status_code,
            raw=resp.text[:1000],
        )
    try:
        body = resp.json()
    except Exception as e:  # noqa: BLE001
        raise ViduError(
            f"Vidu {where} returned non-JSON: {_clip(resp.text)}",
            status=resp.status_code,
            raw=resp.text[:1000],
        ) from e
    if not isinstance(body, dict):
        raise ViduError(f"Vidu {where} returned invalid JSON: {_clip(body)}", raw=str(body)[:1000])
    code = body.get("code")
    if code not in (None, 0, "0") and not body.get("id"):
        msg = body.get("message") or body.get("error") or body.get("reason") or "request failed"
        raise ViduError(f"Vidu {where} failed: {msg}", code=str(code), raw=str(body)[:1000])
    return body


def _image_size(raw: bytes) -> tuple[int, int]:
    try:
        from PIL import Image
        img = Image.open(BytesIO(raw))
        return img.size
    except Exception:  # noqa: BLE001
        return 1024, 1024


def _mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
        return "image/webp"
    return "image/jpeg"


def _upload_once(session: http.Session, cookie: str, raw: bytes, width: int, height: int) -> str:
    create = _post(
        VIDU_UPLOAD_URL,
        json={"scene": "vidu", "metadata": {"image-height": str(height), "image-width": str(width)}},
        headers=_headers(cookie, "application/json"),
        timeout=30,
    )
    info = _json_response(create, "upload create")
    upload_id = info.get("id")
    put_url = info.get("put_url")
    if not upload_id or not put_url:
        raise ViduError(f"Vidu upload create missing id/put_url: {_clip(info)}", raw=str(info)[:1000])

    put = _put(put_url, data=raw, headers={"Content-Type": _mime(raw)}, timeout=90)
    if put.status_code not in (200, 201, 204):
        raise ViduError(
            f"Vidu upload PUT failed (HTTP {put.status_code}): {_clip(put.text)}",
            status=put.status_code,
            raw=put.text[:1000],
        )

    finish = _put(
        f"{VIDU_UPLOAD_URL}/{upload_id}/finish",
        json={"etag": (put.headers.get("etag") or "").strip('"'), "id": upload_id},
        headers=_headers(cookie, "application/json"),
        timeout=30,
    )
    done = _json_response(finish, "upload finish")
    uri = done.get("uri")
    if not uri:
        raise ViduError(f"Vidu upload finish missing uri: {_clip(done)}", raw=str(done)[:1000])
    return uri


def _upload_image_pair(session: http.Session, cookie: str, img_b64: str) -> tuple[str, str, tuple[int, int]]:
    try:
        raw = base64.b64decode(img_b64.split(",", 1)[-1] if img_b64.startswith("data:") else img_b64)
    except Exception as e:  # noqa: BLE001
        raise ViduError("Vidu reference image base64 is invalid") from e
    width, height = _image_size(raw)
    src_uri = _upload_once(session, cookie, raw, width, height)
    time.sleep(0.2)
    content_uri = _upload_once(session, cookie, raw, width, height)
    return content_uri, src_uri, (width, height)


def _extract_task_id(obj) -> str:
    if isinstance(obj, dict):
        for key in ("id", "task_id", "taskId"):
            if obj.get(key):
                return str(obj[key])
        for key in ("data", "result"):
            nested = _extract_task_id(obj.get(key))
            if nested:
                return nested
    return ""


def _looks_like_media_url(value: str, exts: tuple[str, ...]) -> bool:
    text = (value or "").strip()
    if not text.startswith("http"):
        return False
    path = urlparse(text.split("?", 1)[0]).path.lower()
    return any(path.endswith(ext) for ext in exts)


def _extract_media_url(obj, exts: tuple[str, ...]) -> str:
    if isinstance(obj, str):
        return obj if _looks_like_media_url(obj, exts) else ""
    if isinstance(obj, list):
        for item in obj:
            nested = _extract_media_url(item, exts)
            if nested:
                return nested
        return ""
    if not isinstance(obj, dict):
        return ""

    for key in (
        "nomark_uri", "nomark_url", "uri", "url", "preview_uri", "preview_url",
        "download_uri", "download_url", "image_url", "imageUrl", "video_url",
        "videoUrl", "resource_url", "file_url", "result_url", "cover_url",
        "coverUrl",
    ):
        nested = _extract_media_url(obj.get(key), exts)
        if nested:
            return nested

    for value in obj.values():
        nested = _extract_media_url(value, exts)
        if nested:
            return nested
    return ""


def _status(obj: dict) -> str:
    for key in ("state", "status"):
        if obj.get(key):
            return str(obj.get(key)).lower()
    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
    for key in ("state", "status"):
        if data.get(key):
            return str(data.get(key)).lower()
    return ""


def _task_error_message(data: dict) -> str:
    if not isinstance(data, dict):
        return str(data)[:300]
    for key in ("message", "error", "reason", "fail_reason", "err_msg", "error_message"):
        value = data.get(key)
        if value:
            return str(value)
    for key in ("data", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            msg = _task_error_message(nested)
            if msg:
                return msg
    return str(data)[:300]


def _text_prompt_item(prompt: str) -> dict:
    return {
        "type": "text",
        "content": prompt,
        "negative": False,
        "recaption": "",
        "selected_region": None,
        "src_img": "",
        "src_imgs": [],
        "preset": False,
        "src_content": "",
        "line_art_pos": "unspecified",
        "name": "",
        "cover_uri": "",
    }


def _image_prompt_item(content_uri: str, src_uri: str, width: int, height: int, index: int) -> dict:
    return {
        "type": "image",
        "content": content_uri,
        "negative": False,
        "recaption": "",
        "selected_region": {
            "top_left": {"x": 0, "y": 0},
            "bottom_right": {"x": int(width), "y": int(height)},
        },
        "src_img": "",
        "src_imgs": [src_uri],
        "preset": False,
        "src_content": "",
        "line_art_pos": "unspecified",
        "name": f"图{index}",
        "cover_uri": "",
    }


def _material_prompt_item(content_uri: str, name: str) -> dict:
    return {
        "type": "material",
        "content": content_uri,
        "negative": False,
        "recaption": "",
        "selected_region": None,
        "src_img": "",
        "src_imgs": [],
        "preset": False,
        "src_content": "",
        "line_art_pos": "unspecified",
        "name": name,
        "material": {
            "id": "",
            "version": "0",
            "contents": [content_uri],
            "creator_id": "",
            "share_id": "0",
            "modality": "image",
        },
        "cover_uri": "",
    }


def _base_input(prompts: list[dict]) -> dict:
    return {
        "creation_id": "0",
        "prompts": prompts,
        "lang": "zh",
        "enhance": True,
        "skip_moderation": False,
        "seed": "0",
        "multi_image_boost": False,
        "lora": None,
        "invisible_watermark": None,
        "editor_mode": "normal",
        "with_audio": False,
        "lip_sync": False,
        "remix_mode": "unspecified",
    }


def _base_settings(*, model_version: str, resolution: str = "1080p", aspect_ratio: str = "",
                   duration: int = 0, use_trial: bool = True) -> dict:
    return {
        "style": "",
        "duration": int(duration or 0),
        "aspect_ratio": aspect_ratio or "",
        "model": "",
        "model_version": model_version,
        "resolution": resolution or "1080p",
        "movement_amplitude": "auto",
        "schedule_mode": "normal",
        "sample_count": 1,
        "use_trial": bool(use_trial),
        "is_locked": False,
        "transition": "unspecified",
        "with_audio": False,
        "lip_sync": False,
        "frame_durations": [],
        "image_count": 0,
        "codec": "h265",
        "retain_original_audio": False,
        "biz_scene": "unspecified",
        "voice_lang": "",
        "voice_id": "",
        "speed": 0,
        "volume": 0,
        "grid_count": 0,
        "subtitle_enable": False,
    }


def test_cookie(base_url: Optional[str], cookie: str) -> dict:
    root = _root(base_url)
    resp = _get("https://service.vidu.cn/iam/v1/users/me", headers=_headers(cookie), timeout=30)
    if resp.status_code == 404:
        resp = _get(f"{root}/tasks/count?states=created&states=processing", headers=_headers(cookie), timeout=30)
    return {"ok": True, "user": _json_response(resp, "auth test")}


def create_task(base_url: Optional[str], cookie: str, payload: dict) -> str:
    root = _root(base_url)
    logger.info("[Vidu] submit task type=%s model_version=%s", payload.get("type"), payload.get("settings", {}).get("model_version"))
    resp = _post(f"{root}/tasks", json=payload, headers=_headers(cookie, "application/json"), timeout=60)
    data = _json_response(resp, "task submit")
    task_id = _extract_task_id(data)
    if not task_id:
        raise ViduError(f"Vidu submit did not return task id: {_clip(data)}", raw=str(data)[:1000])
    return task_id


def poll_task(base_url: Optional[str], cookie: str, task_id: str, *, timeout: int, interval: int,
              media_exts: tuple[str, ...], on_progress=None, on_stage=None) -> tuple[str, dict]:
    root = _root(base_url)
    endpoints = [
        f"{root}/tasks/state?id={task_id}",
        f"{root}/tasks/{task_id}",
        f"{root}/tasks/{task_id}/state",
    ]
    start = time.time()
    last = {}
    while time.time() - start < timeout:
        time.sleep(max(3, interval))
        for endpoint in endpoints:
            try:
                resp = _get(endpoint, headers=_headers(cookie), timeout=30)
                if resp.status_code >= 400:
                    continue
                data = _json_response(resp, "task poll")
            except Exception as e:  # noqa: BLE001
                logger.warning("[Vidu] poll failed endpoint=%s error=%s", endpoint, e)
                continue

            last = data
            media_url = _extract_media_url(data, media_exts)
            status = _status(data)
            elapsed = int(time.time() - start)
            progress = min(95, 10 + int(elapsed / max(1, timeout) * 85))
            if on_progress:
                on_progress(progress)
            if on_stage:
                on_stage("polling", f"Vidu {status or 'processing'}", progress)
            logger.info("[Vidu] poll task=%s status=%s media=%s", task_id, status, bool(media_url))

            if media_url and status in ("", "success", "completed", "done", "finished"):
                return media_url, data
            if status in ("failed", "error", "failure"):
                raise ViduError(f"Vidu task failed: {_task_error_message(data)}", raw=str(data)[:1000])

    raise ViduError(f"Vidu generation timeout after {timeout}s", code="vidu_timeout", raw=str(last)[:1000])


def _download(url: str, save_dir: str | Path, filename: str, *, cookie: str = "") -> tuple[str, bytes]:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    filepath = save_dir / filename
    resp = _get(url, headers=_headers(cookie), timeout=(20, 240))
    if resp.status_code >= 400:
        resp = _get(url, timeout=(20, 240))
    if resp.status_code >= 400 or not resp.content:
        raise ViduError(f"Vidu media download failed (HTTP {resp.status_code})", status=resp.status_code)
    filepath.write_bytes(resp.content)
    return str(filepath), resp.content


def generate_video(prompt: str, *, base_url: str, cookie: str, model: str = "", duration: int = 5,
                   aspect_ratio: str = "16:9", resolution: str = "1080p",
                   ref_images_b64: Optional[list[str]] = None, save_dir: str | Path,
                   filename_prefix: str = "", timeout: int = 600, poll_interval: int = 10,
                   on_progress=None, on_stage=None, generate_audio: bool = False) -> dict:
    session = http.Session()
    ref_items = []
    for img_b64 in (ref_images_b64 or [])[:4]:
        content_uri, src_uri, (width, height) = _upload_image_pair(session, cookie, img_b64)
        ref_items.append((content_uri, src_uri, width, height))

    model_version = _video_model_value(model)
    task_type = "character2video" if ref_items else "text2video"
    submit_prompt = _vidu_material_prompt_text(prompt, len(ref_items))
    prompts = [_text_prompt_item(submit_prompt)]
    if task_type == "character2video":
        prompts.extend(
            _image_prompt_item(content_uri, src_uri, width, height, idx)
            for idx, (content_uri, src_uri, width, height) in enumerate(ref_items, start=1)
        )
    payload = {
        "type": task_type,
        "input": _base_input(prompts),
        "settings": _base_settings(
            model_version=model_version,
            resolution=resolution or "1080p",
            aspect_ratio=aspect_ratio or "16:9",
            duration=int(duration or 5),
            use_trial=True,
        ),
    }
    if task_type == "character2video":
        payload["settings"]["model"] = "vidu"
        payload["settings"]["transition"] = "pro"
        payload["settings"]["codec"] = "h264"
    if generate_audio:
        payload["input"]["with_audio"] = True
        payload["settings"]["with_audio"] = True

    task_id = create_task(base_url, cookie, payload)
    if on_progress:
        on_progress(5)
    media_url, status_data = poll_task(
        base_url, cookie, task_id, timeout=timeout, interval=poll_interval,
        media_exts=(".mp4", ".mov", ".webm"), on_progress=on_progress, on_stage=on_stage,
    )
    video_id = (filename_prefix + "_" if filename_prefix else "") + uuid.uuid4().hex[:8]
    filename = f"{video_id}_vidu-{model_version}.mp4"
    filepath, content = _download(media_url, save_dir, filename, cookie=cookie)
    if on_progress:
        on_progress(100)
    return {
        "video_id": video_id,
        "filename": filename,
        "filepath": filepath,
        "video_url": media_url,
        "model": f"vidu-{model_version}",
        "duration": duration,
        "bytes": len(content),
        "vidu_task_id": task_id,
        "vidu_status": status_data,
        "created_at": time.time(),
    }


def generate_image(prompt: str, *, base_url: str, cookie: str, model: str = "",
                   size: str = "1024x1024", ref_images_b64: Optional[list[str]] = None,
                   save_dir: str | Path, filename_prefix: str = "", timeout: int = 300) -> dict:
    session = http.Session()
    requested_type, model_version = _image_model_value(model)
    ref_items = []
    for img_b64 in (ref_images_b64 or [])[:5]:
        content_uri, src_uri, (width, height) = _upload_image_pair(session, cookie, img_b64)
        ref_items.append((content_uri, src_uri, width, height))

    task_type = "reference2image" if ref_items else "text2image"
    if requested_type == "text2image":
        task_type = "text2image"
        ref_items = []
    elif requested_type == "reference2image" and ref_items:
        task_type = "reference2image"

    aspect_ratio, resolution, sample_count = _parse_image_params(size)
    prompts = [_text_prompt_item(prompt)]
    if task_type == "reference2image":
        prompts.extend(
            _image_prompt_item(content_uri, src_uri, width, height, idx)
            for idx, (content_uri, src_uri, width, height) in enumerate(ref_items, start=1)
        )
    payload = {
        "type": task_type,
        "input": _base_input(prompts),
        "settings": _base_settings(
            model_version=model_version,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=0,
            use_trial=False,
        ),
    }
    payload["settings"]["sample_count"] = sample_count
    task_id = create_task(base_url, cookie, payload)
    media_url, status_data = poll_task(
        base_url, cookie, task_id, timeout=timeout, interval=8,
        media_exts=(".png", ".jpg", ".jpeg", ".webp"), on_progress=None, on_stage=None,
    )
    image_id = (filename_prefix + "_" if filename_prefix else "") + uuid.uuid4().hex[:8]
    filename = f"{image_id}_vidu.png"
    filepath, content = _download(media_url, save_dir, filename, cookie=cookie)
    b64 = base64.b64encode(content).decode("utf-8")
    return {
        "image_id": image_id,
        "filename": filename,
        "filepath": filepath,
        "b64": b64,
        "model": f"{task_type}:{model_version}",
        "size": size,
        "vidu_task_id": task_id,
        "vidu_status": status_data,
        "sha256": hashlib.sha256(content).hexdigest()[:16],
        "created_at": time.time(),
    }


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = str(size or "1024x1024").lower().split("x", 1)
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 1024, 1024


def _parse_image_params(size: str) -> tuple[str, str, int]:
    """Parse legacy image sizes and Vidu UI values.

    Supported Vidu-style values:
      - 16:9@1080p, 9:16@2K, 1:1@4K
      - optional sample suffix, e.g. 16:9@1080p#4
    """
    text = str(size or "").strip()
    sample_count = 1
    if "#" in text:
        text, count = text.rsplit("#", 1)
        try:
            sample_count = max(1, min(4, int(count)))
        except Exception:  # noqa: BLE001
            sample_count = 1

    allowed_aspects = {"16:9", "9:16", "1:1", "4:3", "3:4"}
    allowed_resolutions = {"1080p", "2k", "4k"}
    if "@" in text:
        aspect, resolution = [part.strip() for part in text.split("@", 1)]
        if aspect not in allowed_aspects:
            aspect = "16:9"
        res_key = resolution.lower()
        resolution = "2k" if res_key == "2k" else "4k" if res_key == "4k" else "1080p"
        if resolution not in allowed_resolutions:
            resolution = "1080p"
        return aspect, resolution, sample_count

    if text in allowed_aspects:
        return text, "1080p", sample_count

    width, height = _parse_size(text)
    return _aspect_ratio(width, height), "1080p", sample_count


def _vidu_material_prompt_text(prompt: str, ref_count: int) -> str:
    text = prompt or ""
    if ref_count <= 0:
        return text
    for idx in range(1, ref_count + 1):
        tag = f"[@图{idx}]"
        if tag in text:
            continue
        if f"[@{idx}]" in text:
            text = text.replace(f"[@{idx}]", tag)
            continue
        if f"@{idx}" in text:
            text = text.replace(f"@{idx}", tag)
            continue
        if f"@图{idx}" in text:
            text = text.replace(f"@图{idx}", tag)
            continue
        text = f"{tag}{text}" if idx == 1 else f"{text} {tag}"
    return text


def _aspect_ratio(width: int, height: int) -> str:
    if width > height * 1.5:
        return "16:9"
    if height > width * 1.5:
        return "9:16"
    if width > height:
        return "4:3"
    if height > width:
        return "3:4"
    return "1:1"


def _video_model_value(model: str) -> str:
    text = str(model or "").strip().lower()
    aliases = {
        "": "3.2",
        "vidu 3.2": "3.2",
        "vidu-3.2": "3.2",
        "v3.2": "3.2",
        "3.2": "3.2",
        "vidu 3.1": "3.1",
        "vidu-3.1": "3.1",
        "v3.1": "3.1",
        "3.1": "3.1",
    }
    return aliases.get(text, model or "3.2")


def _image_model_value(model: str) -> tuple[str, str]:
    text = str(model or "").strip().lower()
    aliases = {
        "": ("auto", "3.2_image_2"),
        "auto": ("auto", "3.2_image_2"),
        "auto:3.2_image_2": ("auto", "3.2_image_2"),
        "vidu all-purpose image": ("auto", "3.2_image_2"),
        "vidu reference to image": ("reference2image", "3.2_image_2"),
        "reference to image": ("reference2image", "3.2_image_2"),
        "reference2image": ("reference2image", "3.2_image_2"),
        "reference2image:3.2_image_2": ("reference2image", "3.2_image_2"),
        "vidu text to image": ("text2image", "3.2_image_2"),
        "text to image": ("text2image", "3.2_image_2"),
        "text2image": ("text2image", "3.2_image_2"),
        "text2image:3.2_image_2": ("text2image", "3.2_image_2"),
        "reference2image:3.1": ("reference2image", "3.1"),
        "text2image:3.1": ("text2image", "3.1"),
    }
    if text in aliases:
        return aliases[text]
    if ":" in text:
        task_type, version = text.split(":", 1)
        if task_type in {"auto", "text2image", "reference2image"} and version:
            return task_type, version
    return "auto", "3.2_image_2"
