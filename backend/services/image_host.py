"""Public image hosting upload (adapted from shengtu).

Used to turn local/base64 images into public URLs for video APIs that require
reference_image_urls. Tries catbox -> 0x0 -> data URI fallback.
"""

import base64
import logging

import requests as http

logger = logging.getLogger("batch_studio")

SERVICES = ["catbox", "0x0", "datauri"]


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


def _to_0x0(img_bytes: bytes, filename: str = "image.png") -> str:
    resp = http.post(
        "https://0x0.st",
        files={"file": (filename, img_bytes, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.text.strip()
    if url.startswith("http"):
        return url
    raise ValueError(f"0x0.st 返回非 URL: {url[:100]}")


def upload(img_b64: str) -> str:
    img_bytes = base64.b64decode(img_b64)
    for service in SERVICES:
        try:
            if service == "catbox":
                url = _to_catbox(img_bytes)
            elif service == "0x0":
                url = _to_0x0(img_bytes)
            else:
                return f"data:image/png;base64,{img_b64}"
            logger.info(f"[ImageHost] {service} 上传成功: {url[:80]}")
            return url
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ImageHost] {service} 上传失败: {e}")
            continue
    return f"data:image/png;base64,{img_b64}"
