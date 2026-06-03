"""Public image hosting upload (adapted from shengtu).

Used to turn local/base64 images into public URLs for video APIs. The upstream
video model FETCHES these URLs server-side, so a `data:` URI is NOT a usable
fallback for actual frame pinning (the upstream silently drops it) — we must
return a real public http(s) URL. We therefore try several independent hosts and
only fall back to a data URI as an absolute last resort (kept for soft-reference
compatibility, but it will not pin a first frame).

Order: catbox -> litterbox -> uguu -> 0x0 -> data URI.
"""

import base64
import logging

import requests as http

logger = logging.getLogger("batch_studio")

# real public-URL hosts first; "datauri" is a non-fetchable last resort
SERVICES = ["catbox", "litterbox", "uguu", "0x0", "datauri"]

_UA = {"User-Agent": "Mozilla/5.0 (batch-studio)"}


def _to_catbox(img_bytes: bytes, filename: str = "image.png") -> str:
    resp = http.post(
        "https://catbox.moe/user/api.php",
        data={"reqtype": "fileupload", "userhash": ""},
        files={"fileToUpload": (filename, img_bytes, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.text.strip()
    if url.startswith("http"):
        return url
    raise ValueError(f"catbox 返回非 URL: {url[:100]}")


def _to_litterbox(img_bytes: bytes, filename: str = "image.png") -> str:
    # temporary host (same operators as catbox); 1h is plenty for a render job
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


def upload(img_b64: str) -> str:
    img_bytes = base64.b64decode(img_b64)
    last_err = None
    for service in SERVICES:
        if service == "datauri":
            logger.warning("[ImageHost] 所有公共图床不可用，回退 data URI（注意：上游"
                           "可能无法抓取，首帧钉死会失效）")
            return f"data:image/png;base64,{img_b64}"
        try:
            url = _UPLOADERS[service](img_bytes)
            logger.info(f"[ImageHost] {service} 上传成功: {url[:80]}")
            return url
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"[ImageHost] {service} 上传失败: {e}")
            continue
    return f"data:image/png;base64,{img_b64}"
