"""Generation error classification & retry policy.

Maps any exception raised by the image/video generation services into a
structured, user-actionable error so the batch engine can decide *whether to
retry* (instead of blindly retrying — which wastes paid quota on errors that
can never succeed, e.g. a content-safety rejection) and so the UI can show a
friendly, actionable message instead of raw JSON.

Categories
----------
- ``transient``: temporary (network / timeout / 5xx / rate-limit). **Retryable.**
- ``content`` : prompt rejected by content/safety policy. **Not retryable**
                (same prompt will fail again) — user must edit the prompt.
- ``fatal``   : credential invalid / quota or balance exhausted. **Not retryable**
                and aborts the rest of the batch (otherwise every remaining shot
                fails the same way and burns nothing useful).
- ``param``   : bad request parameters (duration/aspect/resolution). Not retryable.
- ``unknown`` : unclassified. Retried a limited number of times (could be a blip).
"""

from __future__ import annotations

import json
import re

import requests as http

# ── keyword tables (lowercased substring match on code/type/message) ──────────
_CONTENT_KEYS = (
    "safety", "content_policy", "content policy", "sensitive", "moderation",
    "violat", "nsfw", "prohibited", "blocked", "safety_rejected",
    "内容触发", "安全策略", "敏感", "违规", "审核", "拒绝",
)
_FATAL_KEYS = (
    "invalid_api_key", "invalid api key", "unauthorized", "authentication",
    "permission", "forbidden", "insufficient_quota", "insufficient quota",
    "insufficient_balance", "insufficient balance", "exceeded your current quota",
    "insufficient_user_quota", "quota", "billing", "no balance", "out of credit", "account",
    "密钥", "鉴权", "未授权", "无权限", "额度", "余额", "欠费", "配额",
)
_TRANSIENT_KEYS = (
    "timeout", "timed out", "temporarily", "temporary", "try again",
    "rate limit", "ratelimit", "too many requests", "overloaded", "busy",
    "connection", "reset by peer", "bad gateway", "service unavailable",
    "gateway timeout", "upstream_unavailable", "fail_to_fetch_task",
    "reference_image_upload_failed",
    "task not found", "任务查询", "任务获取", "超时", "稍后", "重试", "繁忙", "限流", "网络",
)
_PARAM_KEYS = (
    "invalid_reference_inputs", "reference_inputs", "invalid_request_error",
    "too many reference", "maximum reference", "reference image limit",
    "reference images exceeds", "reference images exceed",
    "参考图片最多支持", "最多支持 4 张", "最多支持4张",
    "参考图片数量", "参考图数量", "参考图片数", "参考图数",
    "超过当前视频接口上限", "超过上限", "最多支持",
)

_META = {
    "transient": {"retryable": True, "abort_batch": False,
                  "hint": "网络/服务暂时不可用，已自动重试"},
    "content":   {"retryable": False, "abort_batch": False,
                  "hint": "提示词触发内容/安全策略，请修改描述词后重试（重试同样的提示词会再次失败）"},
    "fatal":     {"retryable": False, "abort_batch": True,
                  "hint": "凭据无效或额度/余额耗尽，请到设置→凭据库检查 Key 与额度；已中止本批后续生成以免浪费调用"},
    "param":     {"retryable": False, "abort_batch": False,
                  "hint": "请求参数非法（时长/画幅/分辨率等），请调整后重试"},
    "unknown":   {"retryable": True, "abort_batch": False,
                  "hint": "未知错误，已做有限次重试"},
}


def _status_category(status: int | None) -> str | None:
    if status is None:
        return None
    if status in (408, 425, 429) or 500 <= status <= 599:
        return "transient"
    if status in (401, 403, 402):
        return "fatal"
    if status == 400:
        return "param"  # may be refined to "content" by keyword match
    return None


def _text_of(exc: Exception) -> str:
    parts = [str(exc)]
    for attr in ("code", "err_type", "raw"):
        v = getattr(exc, attr, None)
        if v:
            parts.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return " ".join(parts).lower()


def _match(text: str, keys) -> bool:
    return any(k in text for k in keys)


def classify(exc: Exception) -> dict:
    """Return a structured error dict for *exc*.

    Keys: ``category``, ``code``, ``status``, ``retryable``, ``abort_batch``,
    ``message`` (friendly, actionable), ``detail`` (raw upstream text).
    """
    code = (getattr(exc, "code", "") or "").strip()
    status = getattr(exc, "status", None)
    detail = str(exc)
    text = _text_of(exc)

    # 1) hard network-layer exceptions → always transient
    if isinstance(exc, (http.Timeout, http.ConnectionError)):
        category = "transient"
    else:
        # 2) keyword wins over status (a 400 can be a content rejection)
        if _match(text, _PARAM_KEYS):
            category = "param"
        elif _match(text, _CONTENT_KEYS):
            category = "content"
        elif _match(text, _FATAL_KEYS):
            category = "fatal"
        elif _match(text, _TRANSIENT_KEYS):
            category = "transient"
        else:
            category = _status_category(status) or "unknown"

    meta = _META[category]
    friendly = _friendly_message(category, code, detail)
    return {
        "category": category,
        "code": code or _code_from_status(status),
        "status": status,
        "retryable": meta["retryable"],
        "abort_batch": meta["abort_batch"],
        "message": friendly,
        "hint": meta["hint"],
        "detail": detail[:500],
    }


def _code_from_status(status: int | None) -> str:
    return f"http_{status}" if status else ""


_CATEGORY_LABEL = {
    "transient": "服务暂时不可用",
    "content": "内容安全拒绝",
    "fatal": "凭据/额度错误",
    "param": "参数错误",
    "unknown": "未知错误",
}


def _friendly_message(category: str, code: str, detail: str) -> str:
    text = " ".join(str(x or "") for x in (code, detail)).lower()
    if _match(text, _FATAL_KEYS) and any(k in text for k in ("quota", "balance", "余额", "额度")):
        return "[额度不足] 视频服务预扣费失败或账户余额不足；请充值/更换视频凭据后重试。"
    if "missing_video_url" in text:
        return "[视频结果链接缺失] 上游显示生成完成，但没有返回可下载视频链接；已停止等待，请查看错误详情中的原始返回。"
    if _match(text, _PARAM_KEYS):
        return "[图生视频参数错误] 参考图数量超过当前视频接口的实际上限；为避免少图提交，系统不会自动丢图，请在设置里调低「总参考图上限」后重试。"
    if "fail_to_fetch_task" in text:
        return "[图生视频参考图抓取失败] 视频服务未能抓取已上传的参考图 URL；请重试，或更换可被上游访问的图床/视频接口。"
    if "reference_image_upload_failed" in text:
        return "[图生视频参考图上传失败] 无法生成可被视频服务公网抓取的参考图 URL；请检查网络或更换图床/视频接口。"
    label = _CATEGORY_LABEL[category]
    # surface a short upstream snippet (strip noisy JSON braces if present)
    snippet = detail.strip()
    m = re.search(r'"message"\s*:\s*"([^"]+)"', snippet)
    if m:
        snippet = m.group(1)
    snippet = snippet[:160]
    tail = f"｜{snippet}" if snippet else ""
    return f"[{label}]{tail}"
