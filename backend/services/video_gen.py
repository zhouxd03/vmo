"""Video generation service (adapted from shengtu /api/video/generate).

Key change vs shengtu: the API base URL is not hardcoded; it comes from the
Credential Vault (category=video) so the user provides base_url + key manually.
Submits a job, then polls until done, downloading the result locally.
"""

import base64
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests as http

from ..core import credentials
from ..core import settings as settings_store
from . import image_host, vidu_gen

logger = logging.getLogger("batch_studio")


def _clip(text: str, limit: int = 240) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _safe_url(url: str) -> str:
    try:
        p = urlparse(url or "")
        if not p.scheme or not p.netloc:
            return url or ""
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:  # noqa: BLE001
        return url or ""


def _ref_digest(img_b64: str) -> dict:
    size = 0
    sha = ""
    try:
        raw = base64.b64decode(img_b64 or "")
        size = len(raw)
        sha = hashlib.sha256(raw).hexdigest()
    except Exception:  # noqa: BLE001
        size = len(img_b64 or "")
    return {"bytes": size, "sha256": sha[:16]}


def _json_keys(obj) -> list[str]:
    return sorted(obj.keys()) if isinstance(obj, dict) else []


def _response_summary(obj) -> dict:
    if not isinstance(obj, dict):
        return {"type": type(obj).__name__, "preview": _clip(str(obj), 300)}
    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
    error_value = obj.get("error") or data.get("Error") or data.get("error")
    return {
        "keys": _json_keys(obj),
        "data_keys": _json_keys(data),
        "task_id": _extract_task_id(obj),
        "video_url_present": bool(_extract_video_url(obj)),
        "status": obj.get("status") or data.get("Status") or data.get("status"),
        "progress": obj.get("progress") or data.get("Progress") or data.get("progress"),
        "code": obj.get("code"),
        "success": obj.get("success"),
        "message": _clip(obj.get("message") or obj.get("msg") or data.get("Message") or "", 240),
        "error": _clip(_stringify_upstream(error_value), 500) if error_value else "",
    }


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
    return VideoError(f"{where}错误 (HTTP {resp.status_code}): {msg}",
                      code=code, status=resp.status_code, err_type=err_type, raw=resp.text[:500])


def _data_url(img_b64: str, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{img_b64}"


def _bearer_header(api_key: str) -> str:
    key = (api_key or "").strip()
    return key if key.lower().startswith("bearer ") else f"Bearer {key}"


def _openai_video_base(api_base: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _video_reference_transport() -> str:
    value = str(settings_store.load_settings().get("video_reference_transport", "data_url") or "data_url").strip().lower()
    if value in {"auto", "public_url", "data_url"}:
        return value
    return "data_url"


def _video_size(aspect_ratio: str, resolution: str) -> str:
    try:
        aw, ah = [int(x) for x in str(aspect_ratio or "16:9").split(":", 1)]
    except Exception:  # noqa: BLE001
        aw, ah = 16, 9
    res = str(resolution or "720p").strip().lower()
    short = 1080 if res == "1080p" else 480 if res == "480p" else 720
    if aw >= ah:
        return f"{int(round(short * aw / ah))}x{short}"
    return f"{short}x{int(round(short * ah / aw))}"


def _is_reference_fetch_error(exc: VideoError) -> bool:
    text = " ".join(
        str(x or "") for x in (exc, exc.code, exc.err_type, exc.raw)
    ).lower()
    return "fail_to_fetch_task" in text


def _resolve_creds(base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str, str]:
    """Return (base_url, api_key, default_model, provider)."""
    if base_url and api_key:
        return base_url.strip().rstrip("/"), api_key.strip(), "", _detect_provider(base_url, "")
    cred = credentials.get_default("video")
    provider = (cred or {}).get("provider", "").strip().lower()
    if not cred or not cred.get("api_key") or (provider != "vidu" and not cred.get("base_url")):
        raise VideoError("请先在设置中添加并启用一个视频生成 API（地址 + Key）。")
    base = (cred.get("base_url") or "").strip().rstrip("/")
    return base, cred["api_key"].strip(), cred.get("model", ""), _detect_provider(base, provider)


def _detect_provider(base_url: str, provider: str) -> str:
    """Pick the request-construction dialect. Explicit credential `provider`
    wins; otherwise sniff the base_url (Seedance hosts its API under
    /seedanceapi). Defaults to the OpenAI-compatible /v1/videos shape."""
    p = (provider or "").strip().lower()
    if p in ("seedance", "seedance_web", "openai", "vidu"):
        return p
    base = (base_url or "").lower()
    if "/user/data" in base or "119.45.158.223:8618" in base or "119.45.233.77:8963" in base:
        return "seedance_web"
    if (
        "seedance" in base
        or "119.45.252.34:8618" in base
    ):
        return "seedance"
    return "openai"


_VIDEO_URL_RE = re.compile(r"https?://[^\s\"'<>]+?\.(?:mp4|mov|webm)(?:\?[^\s\"'<>]*)?", re.I)
_REF_TOKEN_LINE_RE = re.compile(r"(?m)^@图(\d+)\s+")


def _looks_like_video_url(value: str) -> bool:
    text = (value or "").strip()
    if not text.startswith("http"):
        return False
    lower = text.lower()
    return bool(_VIDEO_URL_RE.search(lower))


def _extract_video_url(obj) -> str:
    if isinstance(obj, str):
        text = obj.strip()
        if _looks_like_video_url(text):
            return text
        match = _VIDEO_URL_RE.search(text)
        return match.group(0) if match else ""
    if isinstance(obj, list):
        for item in obj:
            nested = _extract_video_url(item)
            if nested:
                return nested
        return ""
    if not isinstance(obj, dict):
        return ""

    for key in (
        "video_url", "videoUrl", "VideoUrl", "url", "result_url", "resultUrl",
        "output_url", "outputUrl", "download_url", "downloadUrl", "file_url", "fileUrl",
    ):
        nested = _extract_video_url(obj.get(key))
        if nested:
            return nested

    for value in obj.values():
        nested = _extract_video_url(value)
        if nested:
            return nested
    return ""


def _seedance_status_code(value) -> int | None:
    """Normalize Seedance status values.

    The web API usually returns integers, but some relays stringify fields.
    Treating "2" as non-completed makes the app miss a finished VideoUrl and
    eventually mark the task as timed out.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(str(value).strip())
    except Exception:  # noqa: BLE001
        return None


def _extract_task_id(obj: dict) -> str:
    for k in ("id", "Id", "ID", "task_id", "taskId", "request_id", "job_id"):
        v = obj.get(k)
        if v:
            return str(v)
    data = obj.get("data")
    if isinstance(data, dict):
        return _extract_task_id(data)
    return ""


def _bind_body_asset_tags_to_image_tokens(prompt: str, ref_meta: Optional[list[dict]] = None) -> str:
    text = prompt or ""
    for idx, meta in enumerate(ref_meta or [], start=1):
        name = str((meta or {}).get("name") or "").strip()
        if not name:
            continue
        image_token = f"@image{idx}"
        escaped_name = re.escape(name)
        escaped_tag = re.escape(f"@{name}")
        text = re.sub(
            rf"{escaped_name}[（(]\s*{escaped_tag}\s*[）)]",
            f"{name}（{image_token}）",
            text,
        )
        text = re.sub(
            rf"{escaped_name}[（(]\s*@(?:图|圖片|图片){idx}\s*[）)]",
            f"{name}（{image_token}）",
            text,
        )
        text = re.sub(escaped_tag, f"{name}（{image_token}）", text)
    return text


def _strip_definition_heading_note(text: str) -> str:
    return re.sub(r"【画面定义】（[^）]*）", "【画面定义】", text or "")


def _adapt_seedance_prompt(prompt: str, ref_meta: Optional[list[dict]] = None) -> str:
    """Seedance receives images as multipart ``files`` parts.

    The submitted prompt uses one provider-facing reference system only:
    @imageN, where N is the multipart files upload order. Local review can keep
    @图N, but the upstream request should not mix @图N / 图片N / @imageN.
    """
    text = _strip_definition_heading_note(prompt or "")
    text = text.replace(
        "@图N 按 reference_image_urls 数组顺序编号",
        "@imageN 按 multipart files 上传顺序编号",
    )
    text = text.replace(
        "仅说明随附参考图的对应关系",
        "仅说明随附参考图的对应关系；@imageN 即第N个上传的 multipart files",
    )
    text = text.replace("各 @图 对象", "各 @image 对象")
    out = []
    for line in text.splitlines():
        match = re.match(r"^@图(\d+)\s+", line)
        if match:
            idx = match.group(1)
            line = re.sub(rf"^@图{idx}\s+", f"@image{idx} ", line, count=1)
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"@图(\d+)\b", r"@image\1", text)
    return _bind_body_asset_tags_to_image_tokens(_strip_definition_heading_note(text), ref_meta)


def _should_adapt_json_reference_tokens(model: str, api_base: str) -> bool:
    """Some OpenAI-compatible relays expose Seed/Seedance video models through
    /v1/videos, but still bind reference images by @imageN tokens.
    Keep the local @图N review format, and adapt only the submitted prompt."""
    text = f"{model or ''} {api_base or ''}".lower()
    return any(
        marker in text
        for marker in (
            "seed-",
            "seedance",
            "doubao-seed",
            "64.81.112.180:3000",
        )
    )


def _adapt_json_reference_prompt(prompt: str, ref_meta: Optional[list[dict]] = None) -> str:
    text = _strip_definition_heading_note(prompt or "")
    text = text.replace(
        "@图N 按 reference_image_urls 数组顺序编号",
        "@imageN 按 reference_image_urls 数组顺序编号",
    )
    text = text.replace(
        "仅说明随附参考图的对应关系",
        "仅说明随附参考图的对应关系；@imageN 即 reference_image_urls 数组中的第N张图片",
    )
    text = text.replace("各 @图 对象", "各 @image 对象")
    out = []
    for line in text.splitlines():
        match = re.match(r"^@图(\d+)\s+", line)
        if match:
            idx = match.group(1)
            line = re.sub(rf"^@图{idx}\s+", f"@image{idx} ", line, count=1)
        out.append(line)
    text = "\n".join(out)

    # Body text generated by the app uses human-readable asset tags such as
    # @萧云. Seed-style JSON relays bind uploaded refs through @imageN, so keep
    # the name readable while attaching the provider token: 萧云（@image1）.
    return _bind_body_asset_tags_to_image_tokens(_strip_definition_heading_note(text), ref_meta)


_UNBOUND_ASSET_TAG_RE = re.compile(r"@(?!(?:image(?:N|\d+)?|图\d+)\b)([\u4e00-\u9fffA-Za-z_][\w\u4e00-\u9fff·-]{0,40})")


def _unbound_reference_tags(prompt: str) -> list[str]:
    seen = []
    for match in _UNBOUND_ASSET_TAG_RE.finditer(prompt or ""):
        tag = f"@{match.group(1)}"
        if tag not in seen:
            seen.append(tag)
    return seen


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
    poll_interval: int = 10,
    on_progress: Optional[Callable[[int], None]] = None,
    on_stage: Optional[Callable[[str, str, Optional[int]], None]] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    generate_audio: bool = False,
    watermark: bool = False,
    ref_meta: Optional[list[dict]] = None,
    on_refs_prepared: Optional[Callable[[list[dict], list[str]], None]] = None,
    on_submit_response: Optional[Callable[[dict], None]] = None,
) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise VideoError("请输入视频描述提示词")

    api_base, key, default_model, provider = _resolve_creds(base_url, api_key)
    ref_images_b64 = [b for b in (ref_images_b64 or []) if b]
    ref_meta = list(ref_meta or [])

    def _stage(stage: str, label: str, progress: Optional[int] = None) -> None:
        if on_stage:
            on_stage(stage, label, progress)

    # safety net: a doubao-seedance model implies the Seedance dialect even if
    # the credential's provider field was left blank.
    if provider != "seedance" and (model or default_model or "").lower().startswith("doubao-seedance"):
        provider = "seedance"

    if provider == "vidu":
        try:
            return vidu_gen.generate_video(
                prompt,
                base_url=api_base,
                cookie=key,
                model=model or default_model,
                duration=duration,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                ref_images_b64=ref_images_b64,
                save_dir=save_dir,
                filename_prefix=filename_prefix,
                timeout=timeout,
                poll_interval=poll_interval,
                on_progress=on_progress,
                on_stage=on_stage,
                generate_audio=generate_audio,
            )
        except vidu_gen.ViduError as e:
            raise VideoError(str(e), code=e.code, status=e.status, err_type="vidu", raw=e.raw) from e

    if provider == "seedance_web" or "/user/data" in api_base.lower():
        return _generate_seedance_web_data(
            prompt, api_base=api_base, key=key, model=model or default_model,
            duration=duration, aspect_ratio=aspect_ratio, resolution=resolution,
            ref_images_b64=ref_images_b64, save_dir=save_dir,
            filename_prefix=filename_prefix, timeout=timeout,
            poll_interval=poll_interval, on_progress=on_progress, on_stage=on_stage,
            generate_audio=generate_audio, watermark=watermark,
            ref_meta=ref_meta, on_refs_prepared=on_refs_prepared,
            on_submit_response=on_submit_response,
        )
    if provider == "seedance":
        return _generate_seedance(
            prompt, api_base=api_base, key=key, model=model or default_model,
            duration=duration, aspect_ratio=aspect_ratio, resolution=resolution,
            ref_images_b64=ref_images_b64, save_dir=save_dir,
            filename_prefix=filename_prefix, timeout=timeout,
            poll_interval=poll_interval, on_progress=on_progress, on_stage=on_stage,
            generate_audio=generate_audio, watermark=watermark,
            ref_meta=ref_meta, on_refs_prepared=on_refs_prepared,
            on_submit_response=on_submit_response,
        )

    model = model or default_model or "sora-v3-fast"
    json_ref_token_adapter = False
    submit_prompt = prompt
    if ref_images_b64 and _should_adapt_json_reference_tokens(model, api_base):
        submit_prompt = _adapt_json_reference_prompt(prompt, ref_meta)
        json_ref_token_adapter = submit_prompt != prompt
        unbound_tags = _unbound_reference_tags(submit_prompt)
        if unbound_tags:
            raise VideoError(
                "视频提示词仍引用了未随请求上传的参考对象："
                + "、".join(unbound_tags)
                + "。请在设置中调高「总参考图上限」或取消这些对象引用；系统不会少图提交。",
                code="unbound_reference_tags",
                err_type="reference_binding",
                raw=json.dumps({
                    "unbound_tags": unbound_tags,
                    "ref_meta": ref_meta,
                }, ensure_ascii=False),
            )
    ref_transport = _video_reference_transport()
    ref_url_cache: dict[str, list[str]] = {}
    ref_url_mapping: list[dict] = []

    def _to_url(b, idx, what, transport=None):
        transport = transport or ref_transport
        try:
            base64.b64decode(b)
        except Exception as e:  # noqa: BLE001
            raise VideoError(f"第 {idx + 1} 张{what}数据无效") from e
        if transport == "data_url":
            return _data_url(b)
        try:
            _stage("uploading_refs", f"预上传并验证参考图 {idx + 1}/{len(ref_images_b64)}", 2)
            return image_host.upload(b, allow_data_uri=False, verify=True)
        except Exception as e:  # noqa: BLE001
            raise VideoError(
                f"图生视频参考图上传失败：无法生成可供视频服务抓取的公网图片 URL（{e}）",
                code="reference_image_upload_failed",
                err_type="image_host",
                raw=str(e),
            ) from e

    def _prepare_ref_urls(transport):
        if not ref_images_b64:
            return []
        if transport in ref_url_cache:
            return ref_url_cache[transport]
        if transport == "data_url":
            _stage("uploading_refs", "准备 Base64 参考图", 2)
        else:
            _stage("uploading_refs", "预上传并验证参考图", 2)
        urls = [_to_url(b, i, "参考图", transport) for i, b in enumerate(ref_images_b64)]
        mapping = []
        for idx, url in enumerate(urls):
            meta = ref_meta[idx] if idx < len(ref_meta) else {}
            provider_tokens = [f"@image{idx + 1}"] if json_ref_token_adapter else []
            mapping.append({
                "index": idx + 1,
                "reference_image_urls_index": idx,
                "token": meta.get("token") or f"@图{idx + 1}",
                "provider_token": provider_tokens[0] if provider_tokens else "",
                "provider_tokens": provider_tokens,
                "role": meta.get("role") or "unknown",
                "name": meta.get("name") or "",
                "filename": meta.get("filename") or "",
                "sha256": meta.get("sha256") or "",
                "host": "data-url" if transport == "data_url" else urlparse(url).netloc,
            })
        ref_url_mapping[:] = mapping
        ref_digests = [_ref_digest(b) for b in ref_images_b64]
        logger.info(
            "[Video][Request] refs prepared transport=%s count=%s mapping=%s digests=%s",
            transport, len(urls), mapping, ref_digests,
        )
        if on_refs_prepared:
            on_refs_prepared(mapping, [] if transport == "data_url" else urls)
        ref_url_cache[transport] = urls
        return urls

    headers = {"Authorization": _bearer_header(key), "Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": submit_prompt,
        "seconds": str(duration),
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "size": _video_size(aspect_ratio, resolution),
    }

    # 多模态软参考生视频：上一镜尾帧 / 导演图 / 站位图 / 资产图均作为
    # reference_image_urls 传入，模型按数组顺序与画面定义中的 @图N 对齐。
    submit_url = f"{_openai_video_base(api_base)}/videos"

    def _attach_refs(transport):
        if ref_images_b64:
            payload["reference_image_urls"] = _prepare_ref_urls(transport)
            return "图生视频"
        payload.pop("reference_image_urls", None)
        return "文生视频"

    def _submit(transport_label):
        mode = _attach_refs(transport_label)
        _stage("submitting", "请求中", 8)
        ref_urls = payload.get("reference_image_urls") or []
        payload_summary = {
            "keys": _json_keys(payload),
            "model": payload.get("model"),
            "seconds": payload.get("seconds"),
            "aspect_ratio": payload.get("aspect_ratio"),
            "resolution": payload.get("resolution"),
            "size": payload.get("size"),
            "prompt_chars": len(payload.get("prompt") or ""),
            "prompt_sha256": hashlib.sha256((payload.get("prompt") or "").encode("utf-8")).hexdigest()[:16],
            "prompt_preview": _clip(payload.get("prompt") or "", 2000),
            "reference_token_adapter": "seed-json" if json_ref_token_adapter else "",
            "reference_image_urls_count": len(ref_urls),
            "reference_transport": transport_label,
            "reference_bindings": [
                {
                    "reference_image_urls_index": m.get("reference_image_urls_index", i),
                    "index": m.get("index", i + 1),
                    "token": m.get("token") or f"@图{i + 1}",
                    "provider_tokens": m.get("provider_tokens") or [],
                    "role": m.get("role") or "",
                    "name": m.get("name") or "",
                    "sha256": m.get("sha256") or "",
                }
                for i, m in enumerate(ref_url_mapping)
            ],
            "reference_value_types": [
                "data_url" if str(u).startswith("data:") else _safe_url(str(u))
                for u in ref_urls
            ],
        }
        logger.info(
            f"[Video] 提交 {mode}: model={model} {duration}s "
            f"{aspect_ratio} {resolution} size={payload.get('size')} "
            f"refs={len(payload.get('reference_image_urls') or [])} "
            f"transport={transport_label} "
            f"ref_token_adapter={'seed-json' if json_ref_token_adapter else 'none'}"
        )
        logger.info(
            "[Video][Request] POST %s provider=openai-compatible mode=%s payload=%s",
            _safe_url(submit_url), mode, json.dumps(payload_summary, ensure_ascii=False),
        )
        resp = http.post(submit_url, json=payload, headers=headers, timeout=60)
        logger.info(
            "[Video][Response] POST %s status=%s content_type=%s bytes=%s",
            _safe_url(submit_url), resp.status_code, resp.headers.get("Content-Type", ""),
            len(resp.content or b""),
        )
        if resp.status_code >= 400:
            raise _api_error(resp, "提交")
        data = resp.json()
        logger.info(
            "[Video][Response] submit json=%s",
            json.dumps(_response_summary(data), ensure_ascii=False),
        )
        return data

    actual_transport = ref_transport
    try:
        result = _submit("data_url" if ref_transport == "auto" else ref_transport)
        if ref_transport == "auto":
            actual_transport = "data_url"
    except VideoError as exc:
        if ref_transport != "auto" or not _is_reference_fetch_error(exc):
            raise
        logger.warning("[Video] data_url reference rejected in auto mode; retrying with public_url")
        actual_transport = "public_url"
        result = _submit("public_url")
    if on_submit_response:
        on_submit_response(result if isinstance(result, dict) else {"raw": result})

    video_url = _extract_video_url(result)
    if not video_url:
        task_id = _extract_task_id(result)
        if not task_id:
            raise VideoError(f"无法解析任务 ID 或视频 URL: {str(result)[:300]}")
        _stage("waiting", "等待中", 10)
        video_url = _poll(api_base, task_id, key, timeout, poll_interval, on_progress, on_stage)

    if on_progress:
        on_progress(99)
    _stage("downloading", "下载视频中", 99)
    out = _download(video_url, model, duration, save_dir, filename_prefix)
    if ref_url_mapping:
        out["reference_image_mapping"] = ref_url_mapping
        out["reference_image_urls"] = [] if actual_transport == "data_url" else list(ref_url_cache.get(actual_transport) or [])
    out["reference_transport"] = actual_transport
    if on_progress:
        on_progress(100)
    return out


def _poll(api_base, task_id, key, timeout, interval, on_progress, on_stage=None) -> str:
    headers = {"Authorization": _bearer_header(key), "Content-Type": "application/json"}
    poll_url = f"{_openai_video_base(api_base)}/videos/{task_id}"
    logger.info(
        "[Video][Request] poll start GET %s task_id=%s timeout=%ss interval=%ss",
        _safe_url(poll_url), task_id, timeout, interval,
    )
    elapsed = 0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            r = http.get(poll_url, headers=headers, timeout=30)
            logger.info(
                "[Video][Poll] GET %s elapsed=%ss status_code=%s bytes=%s",
                _safe_url(poll_url), elapsed, r.status_code, len(r.content or b""),
            )
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
            if on_stage:
                on_stage("polling", "等待中", prog)
            logger.info(f"[Video] 轮询 {elapsed}s: status={status} progress={prog}%")
            logger.info(
                "[Video][Poll] json=%s",
                json.dumps(_response_summary(data), ensure_ascii=False),
            )
            if status in ("completed", "done", "success", "finished"):
                url = _extract_video_url(data)
                if url:
                    logger.info("[Video] completed, video url extracted; downloading result")
                    return url
                raw = _stringify_upstream(data)
                logger.error("[Video] task completed without video url: %s", raw[:500])
                raise VideoError(
                    "视频任务已完成，但上游没有返回可下载的视频链接",
                    code="missing_video_url",
                    err_type="upstream",
                    raw=raw,
                )
            elif status in ("failed", "error"):
                msg, code, err_type = _extract_error_fields(data)
                raise VideoError(msg, code=code, err_type=err_type, raw=_stringify_upstream(data))
        except VideoError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Video] 轮询异常: {e}")
            continue
    raise VideoError(f"生成超时（{timeout}秒）")


# Seedance (doubao-seedance) provider
# A different dialect from the OpenAI-style path above:
#   submit : POST {root}/seedanceapi/common/File/All  (multipart/form-data)
#            header `token`; reference images are uploaded as real `files` parts
#            (no image-host round-trip). Returns {code,data:{Id},success}.
#   query  : POST {root}/seedanceapi/user/DataIndex   (application/json)
#            {Id} -> {data:{Status,VideoUrl,Message,...}} (Status 0/1/2/3).
_SEEDANCE_ASPECTS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
_SEEDANCE_RES = {"480p", "720p", "1080p"}
_SEEDANCE_MAX_IMAGES = 9
SEEDANCE_MODELS = [
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-2-0-260128-1",
    "doubao-seedance-2-0-260128-2",
    "doubao-seedance-2-0-260128-3",
]
_SEEDANCE_DEFAULT_MODEL = SEEDANCE_MODELS[0]


def _seedance_root(api_base: str) -> str:
    """Host root for Seedance, tolerant of a base_url that already includes the
    /seedanceapi prefix."""
    base = (api_base or "").rstrip("/")
    if "119.45.233.77:8963" in base:
        return "http://119.45.158.223:8618"
    return base.split("/seedanceapi")[0].rstrip("/")


def _generate_seedance_web_data(
    prompt, *, api_base, key, model, duration, aspect_ratio, resolution,
    ref_images_b64, save_dir, filename_prefix, timeout, poll_interval,
    on_progress, on_stage, generate_audio, watermark,
    ref_meta=None, on_refs_prepared=None, on_submit_response=None,
) -> dict:
    """Seedance web-console compatible path.

    The provider's web page uploads media to OSS and then submits
    /seedanceapi/user/Data with a JSON `Data` field:
    { text, model, images, imagesAsset, content:[text, image_url...] }.
    We reuse our existing verified public-image upload path instead of
    introducing a new COS dependency, then keep DataIndex polling/download.
    """
    root = _seedance_root(api_base)
    model = model or _SEEDANCE_DEFAULT_MODEL
    dur = max(4, min(15, int(duration or 5)))
    aspect = aspect_ratio if aspect_ratio in _SEEDANCE_ASPECTS else "16:9"
    res = resolution if resolution in _SEEDANCE_RES else "720p"
    if not ref_images_b64:
        raise VideoError("Seedance 网页端任务要求至少上传 1 张参考图；当前未拿到可上传图片，已停止请求。")
    if len(ref_images_b64) > _SEEDANCE_MAX_IMAGES:
        raise VideoError(
            f"Seedance 接口最多支持 {_SEEDANCE_MAX_IMAGES} 张参考图，当前准备上传 {len(ref_images_b64)} 张；"
            "系统不会少图提交，请减少引用或调整参考图上限。",
            code="too_many_reference_images",
            err_type="seedance",
        )

    if on_stage:
        on_stage("uploading_refs", "预上传并验证参考图", 2)
    ref_urls: list[str] = []
    ref_mapping: list[dict] = []
    for idx, b64 in enumerate(ref_images_b64 or []):
        try:
            base64.b64decode(b64)
        except Exception as e:  # noqa: BLE001
            raise VideoError(f"第 {idx + 1} 张参考图数据无效") from e
        try:
            url = image_host.upload(b64, allow_data_uri=False, verify=True)
        except Exception as e:  # noqa: BLE001
            raise VideoError(
                f"Seedance 网页端需要可公网访问的参考图 URL，第 {idx + 1} 张上传失败：{e}",
                code="reference_image_upload_failed",
                err_type="image_host",
                raw=str(e),
            ) from e
        meta = (ref_meta or [])[idx] if idx < len(ref_meta or []) else {}
        ref_urls.append(url)
        ref_mapping.append({
            "index": idx + 1,
            "token": meta.get("token") or f"@图{idx + 1}",
            "provider_token": f"@image{idx + 1}",
            "provider_tokens": [f"@image{idx + 1}"],
            "role": meta.get("role") or "unknown",
            "name": meta.get("name") or "",
            "filename": meta.get("filename") or f"ref{idx + 1}.png",
            "sha256": meta.get("sha256") or "",
            "host": urlparse(url).netloc,
        })
    logger.info("[Video][SeedanceWeb] refs prepared: %s", ref_mapping)
    if on_refs_prepared:
        on_refs_prepared(ref_mapping, ref_urls)

    prompt = _adapt_seedance_prompt(prompt, ref_meta)
    unbound_tags = _unbound_reference_tags(prompt)
    if unbound_tags:
        raise VideoError(
            "视频提示词引用了未随请求上传的对象："
            + "、".join(unbound_tags)
            + "。为避免少图或错图，已停止提交；请提高参考图上限，或取消这些对象引用后重试。",
            code="unbound_reference_tags",
            err_type="param",
            raw=json.dumps({"unbound_tags": unbound_tags, "reference_mapping": ref_mapping}, ensure_ascii=False),
        )

    content = [{"type": "text", "text": prompt}]
    for url in ref_urls:
        content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    data_payload = {
        "token": "",
        "model": model,
        "images": ref_urls,
        "imagesAsset": ["" for _ in ref_urls],
        "videos": [],
        "audio": [],
        "text": prompt,
        "generate_audio": bool(generate_audio),
        "ratio": aspect,
        "duration": dur,
        "watermark": bool(watermark),
        "resolution": res,
        "disable_face_mask": False,
        "content": content,
    }
    submit_payload = {
        "Prompt": prompt,
        "Pormat": prompt,
        "Data": json.dumps(data_payload, ensure_ascii=False),
        "Model": model,
        "ImagesAsset": data_payload["imagesAsset"],
    }
    submit_url = f"{root}/seedanceapi/user/Data"
    if on_stage:
        on_stage("submitting", "请求中", 8)
    logger.info(
        "[Video][SeedanceWeb] submit 图生视频: model=%s %ss %s %s refs=%s",
        model, dur, aspect, res, len(ref_urls),
    )
    logger.info(
        "[Video][Request] POST %s provider=seedance-web json keys=%s ref_urls=%s prompt_chars=%s prompt_sha256=%s prompt_preview=%s",
        _safe_url(submit_url),
        _json_keys(submit_payload),
        json.dumps([_safe_url(u) for u in ref_urls], ensure_ascii=False),
        len(prompt or ""),
        hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16],
        _clip(prompt or "", 2000),
    )
    try:
        resp = http.post(
            submit_url,
            json=submit_payload,
            headers={"token": key, "Content-Type": "application/json"},
            timeout=120,
        )
    except Exception as e:  # noqa: BLE001
        raise VideoError(f"Seedance 网页端提交请求失败: {e}") from e
    logger.info(
        "[Video][Response] POST %s status=%s content_type=%s bytes=%s",
        _safe_url(submit_url), resp.status_code, resp.headers.get("Content-Type", ""),
        len(resp.content or b""),
    )
    if resp.status_code >= 400:
        raise _api_error(resp, "Seedance 网页端提交")
    body = _seedance_json(resp, "网页端提交")
    logger.info("[Video][Response] seedance-web submit json=%s", json.dumps(_response_summary(body), ensure_ascii=False))
    if on_submit_response:
        on_submit_response(body)
    task_id = ((body.get("data") or {}).get("Id"))
    if task_id in (None, "", 0):
        raise VideoError(f"Seedance 网页端未返回任务 ID: {str(body)[:300]}")
    if on_progress:
        on_progress(5)
    if on_stage:
        on_stage("waiting", "等待中", 10)
    poll_result = _poll_seedance(root, task_id, key, timeout, poll_interval, on_progress, on_stage)
    video_url = poll_result["video_url"]
    if on_progress:
        on_progress(99)
    if on_stage:
        on_stage("downloading", "下载视频中", 99)
    out = _download(video_url, model, dur, save_dir, filename_prefix)
    out["seedance_status"] = poll_result.get("status_data") or {}
    out["seedance_usage"] = poll_result.get("usage") or {}
    out["reference_image_mapping"] = ref_mapping
    out["reference_image_urls"] = ref_urls
    out["reference_transport"] = "seedance-web-data"
    if on_progress:
        on_progress(100)
    return out


def _generate_seedance(
    prompt, *, api_base, key, model, duration, aspect_ratio, resolution,
    ref_images_b64, save_dir, filename_prefix, timeout, poll_interval,
    on_progress, on_stage, generate_audio, watermark,
    ref_meta=None, on_refs_prepared=None, on_submit_response=None,
) -> dict:
    root = _seedance_root(api_base)
    model = model or _SEEDANCE_DEFAULT_MODEL
    dur = max(4, min(15, int(duration or 5)))          # Seedance allows 4-15s
    aspect = aspect_ratio if aspect_ratio in _SEEDANCE_ASPECTS else "16:9"
    res = resolution if resolution in _SEEDANCE_RES else "720p"
    if not ref_images_b64:
        raise VideoError("Seedance 图生视频接口要求至少上传 1 张参考图；当前未拿到可上传图片，已停止请求。")
    if len(ref_images_b64) > _SEEDANCE_MAX_IMAGES:
        raise VideoError(
            f"Seedance 接口文档限制最多 {_SEEDANCE_MAX_IMAGES} 张参考图，当前准备上传 {len(ref_images_b64)} 张；"
            "请在设置中降低单镜参考图上限或取消部分连续性/资产引用，系统不会自动丢图提交。",
            code="too_many_reference_images",
            err_type="seedance",
        )

    files = []
    ref_mapping: list[dict] = []
    file_digests = []
    for i, b in enumerate(ref_images_b64 or []):
        try:
            raw = base64.b64decode(b)
        except Exception as e:  # noqa: BLE001
            raise VideoError(f"第 {i + 1} 张参考图数据无效") from e
        files.append(("files", (f"ref{i+1}.png", raw, "image/png")))
        file_digests.append({
            "index": i + 1,
            "field": "files",
            "filename": f"ref{i+1}.png",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()[:16],
        })
    if files:
        for idx, _b in enumerate(ref_images_b64 or []):
            meta = (ref_meta or [])[idx] if idx < len(ref_meta or []) else {}
            ref_mapping.append({
                "index": idx + 1,
                "token": meta.get("token") or f"@图{idx + 1}",
                "provider_token": f"@image{idx + 1}",
                "provider_tokens": [f"@image{idx + 1}"],
                "role": meta.get("role") or "unknown",
                "name": meta.get("name") or "",
                "filename": meta.get("filename") or f"ref{idx + 1}.png",
                "sha256": meta.get("sha256") or "",
                "host": "seedance-multipart",
            })
        logger.info("[Video][Seedance] multipart refs prepared: %s", ref_mapping)
        if on_refs_prepared:
            on_refs_prepared(ref_mapping, [])

    prompt = _adapt_seedance_prompt(prompt, ref_meta)
    unbound_tags = _unbound_reference_tags(prompt)
    if unbound_tags:
        raise VideoError(
            "视频提示词引用了未随请求上传的对象："
            + "、".join(unbound_tags)
            + "。为避免少图或错图，已停止提交；请提高参考图上限，或取消这些对象引用后重试。",
            code="unbound_reference_tags",
            err_type="param",
            raw=json.dumps({"unbound_tags": unbound_tags, "reference_mapping": ref_mapping}, ensure_ascii=False),
        )
    form = {
        "model": model,
        "prompt": prompt,
        "duration": str(dur),
        "aspect_ratio": aspect,
        "resolution": res,
        "generate_audio": "true" if generate_audio else "false",
        "watermark": "true" if watermark else "false",
    }

    mode = "图生视频" if files else "文生视频"
    submit_url = f"{root}/seedanceapi/common/File/All"
    if on_stage:
        on_stage(
            "uploading_refs" if files else "submitting",
            "上传图片中" if files else "请求中",
            2 if files else 8,
        )
    logger.info(f"[Video][Seedance] 提交 {mode}: model={model} {dur}s {aspect} {res} refs={len(files)}")
    logger.info(
        "[Video][Request] POST %s provider=seedance multipart form=%s files=%s prompt_chars=%s prompt_sha256=%s prompt_preview=%s",
        _safe_url(submit_url),
        json.dumps({k: v for k, v in form.items() if k != "prompt"}, ensure_ascii=False),
        json.dumps(file_digests, ensure_ascii=False),
        len(prompt or ""),
        hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16],
        _clip(prompt or "", 2000),
    )
    try:
        resp = http.post(submit_url, data=form, files=files or None,
                         headers={"token": key}, timeout=120)
    except Exception as e:  # noqa: BLE001
        raise VideoError(f"Seedance 提交请求失败: {e}") from e
    logger.info(
        "[Video][Response] POST %s status=%s content_type=%s bytes=%s",
        _safe_url(submit_url), resp.status_code, resp.headers.get("Content-Type", ""),
        len(resp.content or b""),
    )
    if resp.status_code >= 400:
        raise _api_error(resp, "Seedance 提交")
    body = _seedance_json(resp, "提交")
    logger.info(
        "[Video][Response] seedance submit json=%s",
        json.dumps(_response_summary(body), ensure_ascii=False),
    )
    if on_submit_response:
        on_submit_response(body)
    task_id = ((body.get("data") or {}).get("Id"))
    if task_id in (None, "", 0):
        raise VideoError(f"Seedance 未返回任务 ID: {str(body)[:300]}")

    if on_progress:
        on_progress(5)
    if on_stage:
        on_stage("waiting", "等待中", 10)
    poll_result = _poll_seedance(root, task_id, key, timeout, poll_interval, on_progress, on_stage)
    video_url = poll_result["video_url"]
    if on_progress:
        on_progress(99)
    if on_stage:
        on_stage("downloading", "下载视频中", 99)
    out = _download(video_url, model, dur, save_dir, filename_prefix)
    out["seedance_status"] = poll_result.get("status_data") or {}
    out["seedance_usage"] = poll_result.get("usage") or {}
    if ref_mapping:
        out["reference_image_mapping"] = ref_mapping
        out["reference_image_urls"] = []
        out["reference_transport"] = "seedance-multipart"
    if on_progress:
        on_progress(100)
    return out


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
    Credit-free; used as the connectivity/auth test for a Seedance credential."""
    root = _seedance_root((base_url or "").strip().rstrip("/"))
    headers = {"token": (api_key or "").strip(), "Content-Type": "application/json"}
    resp = http.post(f"{root}/seedanceapi/user/UserIndex", json={"Id": 0},
                     headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise _api_error(resp, "Seedance 鉴权")
    return (_seedance_json(resp, "鉴权").get("data") or {})


def _poll_seedance(root, task_id, key, timeout, interval, on_progress, on_stage=None) -> dict:
    query_url = f"{root}/seedanceapi/user/DataIndex"
    headers = {"token": key, "Content-Type": "application/json"}
    elapsed = 0
    last_data = {}
    interval = max(10, interval)
    logger.info(
        "[Video][Request] seedance poll start POST %s task_id=%s timeout=%ss interval=%ss",
        _safe_url(query_url), task_id, timeout, interval,
    )
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            r = http.post(query_url, json={"Id": task_id}, headers=headers, timeout=30)
            logger.info(
                "[Video][Poll] POST %s elapsed=%ss status_code=%s bytes=%s payload=%s",
                _safe_url(query_url), elapsed, r.status_code, len(r.content or b""),
                json.dumps({"Id": task_id}, ensure_ascii=False),
            )
            if r.status_code >= 400:
                continue
            body = _seedance_json(r, "轮询")
            logger.info(
                "[Video][Poll] seedance json=%s",
                json.dumps(_response_summary(body), ensure_ascii=False),
            )
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            last_data = data
            raw_status = data.get("Status")
            status = _seedance_status_code(raw_status)
            status_text = str(data.get("StatusText") or "")
            message = str(data.get("Message") or "")
            logger.info(
                "[Video][Seedance] poll %ss: status=%s normalized=%s (%s) token=%s deduct=%s duration=%s message=%s",
                elapsed, raw_status, status, status_text, data.get("UseToken"), data.get("DeductToken"),
                data.get("UseDuration"), message,
            )
            if status == 2:                              # completed
                url = _extract_video_url(data) or _extract_video_url(body)
                if url:
                    logger.info(
                        "[Video][Seedance] completed task_id=%s video_url=%s",
                        task_id, _safe_url(url),
                    )
                    return {
                        "video_url": url,
                        "status_data": data,
                        "usage": {
                            "UseToken": data.get("UseToken"),
                            "DeductToken": data.get("DeductToken"),
                            "UseDuration": data.get("UseDuration"),
                        },
                    }
                raise VideoError(
                    "Seedance 任务已完成，但接口没有返回 VideoUrl",
                    code="missing_video_url",
                    err_type="seedance",
                    raw=_stringify_upstream(data),
                )
            elif status == 3:                            # failed
                raise VideoError(
                    message or status_text or "Seedance 生成失败",
                    code=str(body.get("code") or ""),
                    err_type="seedance",
                    raw=_stringify_upstream(data),
                )
            else:                                        # 0 pending / 1 processing
                if status == 1:
                    progress = min(90, 40 + int(max(0, elapsed - interval) / max(1, timeout) * 55))
                    label = status_text or "生成中"
                else:
                    progress = min(35, 12 + int(elapsed / max(1, timeout) * 23))
                    label = status_text or "待处理"
                label = f"{label}（已等待 {elapsed}s）"
                if on_progress:
                    on_progress(progress)
                if on_stage:
                    on_stage("polling", label, progress)
        except VideoError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Video][Seedance] 轮询异常: {e}")
            continue
    raise VideoError(
        f"Seedance 生成超时（{timeout}秒）：上游最后状态为 "
        f"{last_data.get('Status')}（{last_data.get('StatusText') or '未知'}）。"
        "如果一直是待处理，通常是上游队列未调度或当前模型没有进入执行。",
        code="seedance_timeout",
        err_type="seedance",
        raw=_stringify_upstream(last_data),
    )


def _download(video_url, model, duration, save_dir, filename_prefix) -> dict:
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    video_id = (filename_prefix + "_" if filename_prefix else "") + uuid.uuid4().hex[:8]
    filename = f"{video_id}_{model.replace('/', '_')}.mp4"
    filepath = save_dir / filename
    tmp_path = filepath.with_name(f"{filepath.name}.{uuid.uuid4().hex[:8]}.part")
    last_error = None
    for attempt in range(1, 4):
        size = 0
        try:
            tmp_path.unlink(missing_ok=True)
            logger.info("[Video] downloading result (attempt %s/3): %s", attempt, video_url)
            with http.get(video_url, timeout=(20, 180), stream=True) as dl:
                dl.raise_for_status()
                expected = int(dl.headers.get("Content-Length") or 0)
                with open(tmp_path, "wb") as fh:
                    for chunk in dl.iter_content(chunk_size=1 << 20):
                        if chunk:
                            fh.write(chunk)
                            size += len(chunk)
                    fh.flush()
                if size <= 0:
                    raise VideoError("video download returned empty content", code="video_download_empty")
                if expected and size < expected:
                    raise VideoError(
                        f"video download incomplete ({size}/{expected} bytes)",
                        code="video_download_incomplete",
                    )
            tmp_path.replace(filepath)
            logger.info("[Video] saved %s (%s bytes)", filepath, size)
            saved = str(filepath)
            break
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.warning("[Video] download attempt %s failed: %s", attempt, e)
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            if attempt < 3:
                time.sleep(1.5 * attempt)
    else:
        raise VideoError(
            f"视频已生成但下载落盘失败：{last_error}",
            code="video_download_failed",
            err_type="download",
            raw=str(last_error),
        )
    return {
        "video_id": video_id,
        "filename": filename,
        "filepath": saved,
        "video_url": video_url,
        "model": model,
        "duration": duration,
        "created_at": time.time(),
    }
