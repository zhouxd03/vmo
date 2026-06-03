"""Image generation service (adapted from shengtu /api/generate).

Reusable function (no Flask coupling) so the Batch Engine can call it directly.
Credentials are pulled from the Credential Vault (category=image) unless an
explicit base_url/api_key is provided.
"""

import base64
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

import requests as http

from ..core import credentials

logger = logging.getLogger("batch_studio")


class GenerationError(Exception):
    pass


def _resolve_creds(base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str]:
    """Return (api_base, api_key, default_model)."""
    if base_url and api_key:
        return base_url.strip().rstrip("/"), api_key.strip(), ""
    cred = credentials.get_default("image")
    if not cred or not cred.get("base_url") or not cred.get("api_key"):
        raise GenerationError("请先在设置→凭据库添加并启用一个【生图】API")
    return cred["base_url"].strip().rstrip("/"), cred["api_key"].strip(), cred.get("model", "")


def _normalize_base(api_base: str) -> str:
    if api_base.endswith("/v1"):
        return api_base[:-3]
    return api_base


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


def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1024x1024",
    quality: str = "auto",
    ref_images_b64: Optional[list[str]] = None,
    save_dir: str | Path,
    filename_prefix: str = "",
    timeout: int = 180,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise GenerationError("请输入描述提示词")

    api_base, key, default_model = _resolve_creds(base_url, api_key)
    api_base = _normalize_base(api_base)
    model = model or default_model or "gpt-image-1"
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

        url_edits = f"{api_base}/v1/images/edits"
        if len(decoded) == 1:
            files = {"image": ("ref.png", base64.b64decode(decoded[0]), "image/png")}
        else:
            files = [("image", (f"ref_{i}.png", base64.b64decode(b), "image/png")) for i, b in enumerate(decoded)]
        form = {"model": model, "prompt": prompt, "n": "1", "size": size, "response_format": "b64_json"}
        if quality and quality != "auto":
            form["quality"] = quality
        try:
            resp = http.post(url_edits, data=form, files=files, headers=headers, timeout=timeout)
            if resp.status_code < 400:
                result = resp.json()
            else:
                logger.warning(f"[Generate] multipart 失败 HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Generate] multipart 异常: {e}")

        if result is None:
            url_gen = f"{api_base}/v1/images/generations"
            jh = dict(headers, **{"Content-Type": "application/json"})
            payload = {"model": model, "prompt": prompt, "n": 1, "size": size, "response_format": "b64_json"}
            if quality and quality != "auto":
                payload["quality"] = quality
            payload["image"] = decoded[0] if len(decoded) == 1 else decoded
            resp = http.post(url_gen, json=payload, headers=jh, timeout=timeout)
            if resp.status_code >= 400:
                raise GenerationError(f"API 错误: {resp.text[:300]}")
            result = resp.json()
    else:
        # ── 纯文生图 ──
        url = f"{api_base}/v1/images/generations"
        jh = dict(headers, **{"Content-Type": "application/json"})
        payload = {"model": model, "prompt": prompt, "n": 1, "size": size, "response_format": "b64_json"}
        if quality and quality != "auto":
            payload["quality"] = quality
        resp = http.post(url, json=payload, headers=jh, timeout=timeout)
        if resp.status_code >= 400:
            raise GenerationError(f"API 错误 (HTTP {resp.status_code}): {resp.text[:300]}")
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
