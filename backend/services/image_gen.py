"""Image generation service (adapted from shengtu /api/generate).

Reusable function (no Flask coupling) so the Batch Engine can call it directly.
Credentials are pulled from the Credential Vault (category=image) unless an
explicit base_url/api_key is provided.
"""

import base64
import hashlib
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import requests as http

from ..core import credentials
from . import vidu_gen

logger = logging.getLogger("batch_studio")


class GenerationError(Exception):
    def __init__(self, message, *, code="", status=None, err_type="", raw=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.err_type = err_type
        self.raw = raw


def _api_error(resp, where="API"):
    """Build a structured GenerationError from an HTTP error response."""
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
    return GenerationError(f"{where} 错误 (HTTP {resp.status_code}): {msg}",
                           code=code, status=resp.status_code, err_type=err_type, raw=resp.text[:500])


def _resolve_creds(base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str, str]:
    """Return (api_base, api_key, default_model, provider)."""
    if base_url and api_key:
        return base_url.strip().rstrip("/"), api_key.strip(), "", "openai"
    cred = credentials.get_default("image")
    provider = (cred or {}).get("provider", "").strip().lower()
    if not cred or not cred.get("api_key") or (provider != "vidu" and not cred.get("base_url")):
        raise GenerationError("请先在设置→凭据库添加并启用一个【生图】API")
    return (cred.get("base_url") or "").strip().rstrip("/"), cred["api_key"].strip(), cred.get("model", ""), provider


def _normalize_base(api_base: str) -> str:
    if api_base.endswith("/v1"):
        return api_base[:-3]
    return api_base


_OPENAI_SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$", re.IGNORECASE)
_ASPECT_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


def _image_size_for_aspect(aspect: str, *, model: str = "") -> str:
    """Map local aspect-ratio presets to OpenAI-compatible image sizes."""
    text = str(aspect or "").strip()
    match = _ASPECT_RE.match(text)
    if not match:
        return "1024x1024"
    w, h = int(match.group(1)), int(match.group(2))
    if w <= 0 or h <= 0:
        return "1024x1024"
    if abs((w / h) - 1.0) < 0.04:
        return "1024x1024"

    model_name = (model or "").lower()
    if "dall-e-2" in model_name or "dalle-2" in model_name:
        return "1024x1024"
    if "dall-e-3" in model_name or "dalle-3" in model_name:
        return "1792x1024" if w > h else "1024x1792"
    return "1536x1024" if w > h else "1024x1536"


def _normalize_openai_image_size(size: str, *, model: str = "") -> str:
    """Normalize UI/Vidu-style size values before calling OpenAI-style APIs.

    Vidu accepts values such as ``16:9@1080p``. OpenAI-compatible image APIs
    usually expect ``widthxheight``; sending the local preset verbatim causes
    upstream "invalid size" errors. Exact ``widthxheight`` values are preserved.
    """
    text = str(size or "").strip()
    if not text:
        return "1024x1024"

    exact = _OPENAI_SIZE_RE.match(text)
    if exact:
        return f"{int(exact.group(1))}x{int(exact.group(2))}"

    # Drop optional sample/count suffixes used by some local providers.
    core = text.split("#", 1)[0].strip()
    if "@" in core:
        aspect = core.split("@", 1)[0].strip()
        return _image_size_for_aspect(aspect, model=model)
    if _ASPECT_RE.match(core):
        return _image_size_for_aspect(core, model=model)
    if core.lower() == "auto":
        return "auto"
    return "1024x1024"


def _parse_image_result(result: dict, timeout: int) -> str:
    """Extract base64 image data from a (possibly varied) API response."""
    items = result.get("data", [])
    if not items:
        if result.get("b64_json") or result.get("url") or result.get("b64"):
            items = [result]
        else:
            raise GenerationError("API 返回空结果，请检查模型是否支持")
    item = items[0]
    b64 = item.get("b64_json") or item.get("b64") or item.get("base64") or ""
    url = item.get("url", "")
    if b64 and b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    if not b64 and url:
        dl = http.get(url, timeout=min(timeout, 120))
        dl.raise_for_status()
        b64 = base64.b64encode(dl.content).decode("utf-8")
    if not b64:
        raise GenerationError("未获取到图片数据")
    return b64


def _clip(text: str, limit: int = 500) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "..."


def _ref_summaries(refs: list[str]) -> list[dict]:
    out = []
    for idx, b64 in enumerate(refs or [], start=1):
        try:
            raw = base64.b64decode(b64)
            out.append({
                "index": idx,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest()[:16],
            })
        except Exception:  # noqa: BLE001
            out.append({"index": idx, "bytes": 0, "sha256": "", "invalid": True})
    return out


def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1024x1024",
    quality: str = "auto",
    ref_images_b64: Optional[list[str]] = None,
    save_dir: str | Path,
    filename_prefix: str = "",
    timeout: int = 300,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise GenerationError("请输入描述提示词")

    api_base, key, default_model, provider = _resolve_creds(base_url, api_key)
    if provider == "vidu":
        timeout = max(300, int(timeout or 0))
        try:
            return vidu_gen.generate_image(
                prompt,
                base_url=api_base,
                cookie=key,
                model=model or default_model,
                size=size,
                ref_images_b64=ref_images_b64,
                save_dir=save_dir,
                filename_prefix=filename_prefix,
                timeout=timeout,
            )
        except vidu_gen.ViduError as e:
            raise GenerationError(str(e), code=e.code, status=e.status, err_type="vidu", raw=e.raw) from e

    api_base = _normalize_base(api_base)
    model = model or default_model or "gpt-image-1"
    requested_size = size
    size = _normalize_openai_image_size(size, model=model)
    if str(requested_size or "").strip() != size:
        logger.info(
            "[Generate] normalized image size provider=%s model=%s requested_size=%s api_size=%s",
            provider, model, requested_size, size,
        )
    ref_images_b64 = [b for b in (ref_images_b64 or []) if b]

    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    result: Optional[dict] = None

    if ref_images_b64:
        # ── 垫图模式: 先 multipart /v1/images/edits, 回退 JSON /v1/images/generations ──
        decoded = []
        for i, b in enumerate(ref_images_b64):
            try:
                base64.b64decode(b)
                decoded.append(b)
            except Exception as e:  # noqa: BLE001
                raise GenerationError(f"第 {i+1} 张参考图数据无效") from e

        logger.info(
            "[Generate] image-with-refs model=%s size=%s refs=%s timeout=%ss prompt_chars=%s prompt_sha256=%s prompt_preview=%s ref_summary=%s",
            model, size, len(decoded), timeout, len(prompt),
            hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            _clip(prompt, 800),
            json.dumps(_ref_summaries(decoded), ensure_ascii=False),
        )
        url_edits = f"{api_base}/v1/images/edits"
        form = {"model": model, "prompt": prompt, "n": "1", "size": size, "response_format": "b64_json"}
        if quality and quality != "auto":
            form["quality"] = quality
        multipart_error = ""
        for attempt in range(1, 3):
            if len(decoded) == 1:
                files = {"image": ("ref.png", base64.b64decode(decoded[0]), "image/png")}
            else:
                files = [("image", (f"ref_{i}.png", base64.b64decode(b), "image/png")) for i, b in enumerate(decoded)]
            try:
                logger.info(
                    "[Generate] multipart POST %s attempt=%s/2 refs=%s form=%s",
                    url_edits, attempt, len(decoded),
                    json.dumps({k: v for k, v in form.items() if k != "prompt"}, ensure_ascii=False),
                )
                resp = http.post(url_edits, data=form, files=files, headers=headers, timeout=timeout)
                if resp.status_code < 400:
                    try:
                        result = resp.json()
                    except Exception as e:  # noqa: BLE001
                        multipart_error = f"multipart 返回非 JSON: {_clip(resp.text, 500)}"
                        logger.warning("[Generate] %s", multipart_error)
                    else:
                        logger.info("[Generate] multipart 成功")
                        break
                else:
                    multipart_error = f"multipart HTTP {resp.status_code}: {_clip(resp.text, 500)}"
                    logger.warning("[Generate] %s", multipart_error)
                    if resp.status_code not in (502, 503, 504):
                        break
            except http.exceptions.Timeout as e:
                multipart_error = f"multipart 请求超时（{timeout}秒）: {e}"
                logger.warning("[Generate] %s", multipart_error)
                break
            except http.exceptions.ConnectionError as e:
                multipart_error = f"multipart 连接失败: {e}"
                logger.warning("[Generate] %s", multipart_error)
                break
            except Exception as e:  # noqa: BLE001
                multipart_error = f"multipart 异常: {e}"
                logger.warning("[Generate] %s", multipart_error)
                break
            if result is None and attempt < 2:
                time.sleep(2)

        if result is None:
            url_gen = f"{api_base}/v1/images/generations"
            jh = dict(headers, **{"Content-Type": "application/json"})
            payload = {"model": model, "prompt": prompt, "n": 1, "size": size, "response_format": "b64_json"}
            if quality and quality != "auto":
                payload["quality"] = quality
            payload["image"] = decoded[0] if len(decoded) == 1 else decoded
            logger.info(
                "[Generate] JSON fallback POST %s refs=%s payload=%s previous_error=%s",
                url_gen, len(decoded),
                json.dumps({k: v for k, v in payload.items() if k not in ("prompt", "image")}, ensure_ascii=False),
                multipart_error,
            )
            try:
                resp = http.post(url_gen, json=payload, headers=jh, timeout=timeout)
            except http.exceptions.Timeout as e:
                raise GenerationError(
                    f"垫图生图超时（{timeout}秒）；multipart 阶段：{multipart_error or '未返回'}",
                    code="image_timeout",
                    err_type="timeout",
                    raw=str(e),
                ) from e
            except http.exceptions.ConnectionError as e:
                raise GenerationError(
                    f"垫图生图连接失败；multipart 阶段：{multipart_error or '未返回'}",
                    code="image_connection_error",
                    err_type="network",
                    raw=str(e),
                ) from e
            if resp.status_code >= 400:
                err = _api_error(resp, "垫图 JSON 回退")
                err.raw = (
                    f"multipart={multipart_error or '未返回'}；"
                    f"json_fallback={err.raw or resp.text[:500]}"
                )
                raise err
            try:
                result = resp.json()
            except Exception as e:  # noqa: BLE001
                raise GenerationError(
                    f"垫图 JSON 回退返回非 JSON；multipart 阶段：{multipart_error or '未返回'}",
                    code="image_bad_json",
                    err_type="upstream",
                    raw=_clip(resp.text, 1000),
                ) from e
    else:
        # ── 纯文生图 ──
        url = f"{api_base}/v1/images/generations"
        jh = dict(headers, **{"Content-Type": "application/json"})
        payload = {"model": model, "prompt": prompt, "n": 1, "size": size, "response_format": "b64_json"}
        if quality and quality != "auto":
            payload["quality"] = quality
        resp = http.post(url, json=payload, headers=jh, timeout=timeout)
        if resp.status_code >= 400:
            raise _api_error(resp)
        result = resp.json()

    b64 = _parse_image_result(result, timeout)
    image_bytes = base64.b64decode(b64)
    if len(image_bytes) < 100:
        raise GenerationError(f"图片数据异常（仅 {len(image_bytes)} 字节）")

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    image_id = (filename_prefix + "_" if filename_prefix else "") + uuid.uuid4().hex[:8]
    filename = f"{image_id}_{model.replace('/', '_')}.png"
    filepath = save_dir / filename
    filepath.write_bytes(image_bytes)
    logger.info(f"[Generate] 已保存 {filepath} ({len(image_bytes)} bytes)")

    return {
        "image_id": image_id,
        "filename": filename,
        "filepath": str(filepath),
        "b64": b64,
        "model": model,
        "size": size,
        "created_at": time.time(),
    }
