"""Public image hosting upload (adapted from shengtu).

Used to turn local/base64 images into public URLs for video APIs. The upstream
video model FETCHES these URLs server-side, so a `data:` URI is NOT a usable
fallback for actual frame pinning (the upstream silently drops it) — we must
return a real public http(s) URL. We therefore try several independent hosts and
only fall back to a data URI as an absolute last resort (kept for soft-reference
compatibility, but it will not pin a first frame).

Order: catbox -> uguu -> 0x0 -> litterbox -> data URI.
"""

import base64
import logging
import time
from urllib.parse import urlparse

import requests as http

logger = logging.getLogger("batch_studio")

# real public-URL hosts first; "datauri" is a non-fetchable last resort
SERVICES = ["catbox", "uguu", "0x0", "litterbox", "datauri"]

_UA = {"User-Agent": "Mozilla/5.0 (vmo-studio)"}
_UNHEALTHY_FOR_SECONDS = 600
_unhealthy_until: dict[str, float] = {}


def _is_unhealthy(service: str) -> bool:
    until = _unhealthy_until.get(service, 0)
    if until <= time.time():
        _unhealthy_until.pop(service, None)
        return False
    return True


def _mark_unhealthy(service: str, reason: Exception | str) -> None:
    _unhealthy_until[service] = time.time() + _UNHEALTHY_FOR_SECONDS
    logger.warning("[ImageHost] %s 暂时熔断 %ss: %s", service, _UNHEALTHY_FOR_SECONDS, reason)


def verify_public_url(url: str, *, timeout: int = 12) -> bool:
    """Best-effort fetchability check for a just-uploaded public image URL."""
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"不是可抓取的公网 URL: {str(url)[:80]}")
    headers = {**_UA, "Accept": "image/*,*/*"}
    try:
        resp = http.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code in (403, 405) or resp.status_code >= 500:
            raise RuntimeError(f"HEAD {resp.status_code}")
        resp.raise_for_status()
        return True
    except Exception as head_err:  # noqa: BLE001
        try:
            resp = http.get(url, headers={**headers, "Range": "bytes=0-63"},
                            timeout=timeout, allow_redirects=True, stream=True)
            resp.raise_for_status()
            next(resp.iter_content(chunk_size=16), b"")
            return True
        except Exception as get_err:  # noqa: BLE001
            raise RuntimeError(f"URL 预验证失败: HEAD={head_err}; GET={get_err}") from get_err


def verify_uploaded_image(url: str, expected_bytes: bytes, *, timeout: int = 20) -> bool:
    """Fetch the uploaded URL and verify it is byte-identical to the source."""
    verify_public_url(url, timeout=min(timeout, 12))
    resp = http.get(url, headers={**_UA, "Accept": "image/*,*/*"},
                    timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    got = resp.content
    if got != expected_bytes:
        import hashlib
        raise RuntimeError(
            "uploaded image verification mismatch: "
            f"expected_bytes={len(expected_bytes)} got_bytes={len(got)} "
            f"expected_sha256={hashlib.sha256(expected_bytes).hexdigest()} "
            f"got_sha256={hashlib.sha256(got).hexdigest()}"
        )
    return True


def _to_catbox(img_bytes: bytes, filename: str = "image.png") -> str:
    resp = http.post(
        "https://catbox.moe/user/api.php",
        data={"reqtype": "fileupload"},
        files={"fileToUpload": (filename, img_bytes, "image/png")},
        headers=_UA,
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.text.strip()
    if url.startswith("http"):
        return url
    raise ValueError(f"catbox 返回非 URL: {url[:100]}")


def _to_litterbox(img_bytes: bytes, filename: str = "image.png") -> str:
    # Temporary host (same operators as catbox). Keep it behind permanent
    # public hosts because some video providers cannot fetch short-lived links
    # reliably from their server-side task runner.
    resp = http.post(
        "https://litterbox.catbox.moe/resources/internals/api.php",
        data={"reqtype": "fileupload", "time": "1h"},
        files={"fileToUpload": (filename, img_bytes, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.text.strip()
    if url.startswith("http"):
        return url
    raise ValueError(f"litterbox 返回非 URL: {url[:100]}")


def _to_uguu(img_bytes: bytes, filename: str = "image.png") -> str:
    resp = http.post(
        "https://uguu.se/upload",
        files={"files[]": (filename, img_bytes, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    url = (data.get("files") or [{}])[0].get("url", "")
    if url.startswith("http"):
        return url
    raise ValueError(f"uguu 返回非 URL: {str(data)[:100]}")


def _to_0x0(img_bytes: bytes, filename: str = "image.png") -> str:
    resp = http.post(
        "https://0x0.st",
        files={"file": (filename, img_bytes, "image/png")},
        headers=_UA,
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.text.strip()
    if url.startswith("http"):
        return url
    raise ValueError(f"0x0.st 返回非 URL: {url[:100]}")


_UPLOADERS = {
    "catbox": _to_catbox,
    "litterbox": _to_litterbox,
    "uguu": _to_uguu,
    "0x0": _to_0x0,
}


def _service_from_url(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if host.endswith("catbox.moe"):
        if host.startswith("litterbox."):
            return "litterbox"
        return "catbox"
    if host.endswith("uguu.se"):
        return "uguu"
    if host.endswith("0x0.st"):
        return "0x0"
    return ""


def mark_urls_unhealthy(urls: list[str], reason: Exception | str) -> None:
    """Temporarily avoid hosts whose URLs the upstream video service rejected."""
    seen: set[str] = set()
    for url in urls or []:
        service = _service_from_url(url)
        if service and service not in seen:
            seen.add(service)
            _mark_unhealthy(service, reason)


def upload(img_b64: str, *, allow_data_uri: bool = True, verify: bool = True) -> str:
    img_bytes = base64.b64decode(img_b64)
    last_err = None
    for service in SERVICES:
        if service == "datauri":
            if not allow_data_uri:
                raise RuntimeError(f"all public image hosts failed; no fetchable URL available: {last_err}")
            logger.warning("[ImageHost] 所有公共图床不可用，回退 data URI（注意：上游"
                           "可能无法抓取，首帧钉死会失效）")
            return f"data:image/png;base64,{img_b64}"
        if _is_unhealthy(service):
            logger.info("[ImageHost] %s 已临时熔断，跳过", service)
            continue
        try:
            url = _UPLOADERS[service](img_bytes)
            if verify:
                verify_uploaded_image(url, img_bytes)
            logger.info(f"[ImageHost] {service} 上传成功: {url[:80]}")
            return url
        except Exception as e:  # noqa: BLE001
            last_err = e
            _mark_unhealthy(service, e)
            logger.warning(f"[ImageHost] {service} 上传失败: {e}")
            continue
    return f"data:image/png;base64,{img_b64}"
