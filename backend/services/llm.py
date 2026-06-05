"""LLM service — OpenAI-compatible chat + model listing.

Targets a "大模型中转站" (relay): user supplies base_url + key in the Credential
Vault (category=llm). Supports listing models (GET /v1/models) and chat
completions, including multimodal (image) messages for the continuity review.
"""

import json
import logging
import re
from typing import Any, Optional

import requests as http

from ..core import credentials

logger = logging.getLogger("batch_studio")


class LLMError(Exception):
    pass


_JSON_UNSUPPORTED_RE = re.compile(
    r"response_format|json_object|unsupported|not support|不支持|无效|invalid",
    re.IGNORECASE,
)


def _resolve_creds(base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str]:
    if base_url and api_key:
        return base_url.strip().rstrip("/"), api_key.strip(), ""
    cred = credentials.get_default("llm")
    if not cred or not cred.get("base_url") or not cred.get("api_key"):
        raise LLMError("请先在设置→凭据库添加并启用一个【LLM】中转站 API")
    return cred["base_url"].strip().rstrip("/"), cred["api_key"].strip(), cred.get("model", "")


def _base(api_base: str) -> str:
    return api_base if api_base.endswith("/v1") else api_base + "/v1"


def list_models(base_url: Optional[str] = None, api_key: Optional[str] = None) -> list[str]:
    api_base, key, _ = _resolve_creds(base_url, api_key)
    url = f"{_base(api_base)}/models"
    resp = http.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if resp.status_code >= 400:
        raise LLMError(f"拉取模型列表失败 (HTTP {resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    items = data.get("data", data if isinstance(data, list) else [])
    models = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            models.append(it["id"])
        elif isinstance(it, str):
            models.append(it)
    return sorted(set(models))


def chat(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    response_json: bool = False,
    timeout: int = 180,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    api_base, key, default_model = _resolve_creds(base_url, api_key)
    model = model or default_model
    if not model:
        raise LLMError("未指定 LLM 模型，请在凭据库为该 API 设置默认模型或手动选择")
    url = f"{_base(api_base)}/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if response_json:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    resp = http.post(url, json=payload, headers=headers, timeout=timeout)
    if resp.status_code >= 400 and response_json and _JSON_UNSUPPORTED_RE.search(resp.text or ""):
        # Some OpenAI-compatible relays reject response_format even though the
        # model can still return JSON when the prompt asks for it. Retry without
        # that transport-level hint instead of failing the whole workflow.
        retry_payload = dict(payload)
        retry_payload.pop("response_format", None)
        logger.info("[LLM] response_format unsupported; retrying JSON prompt without it")
        resp = http.post(url, json=retry_payload, headers=headers, timeout=timeout)
    if resp.status_code >= 400:
        raise LLMError(f"LLM 调用失败 (HTTP {resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"LLM 返回结构异常: {str(data)[:300]}") from e


def chat_json(messages: list[dict], **kwargs) -> Any:
    """Chat expecting JSON output; tolerant of code-fenced responses."""
    raw = chat(messages, response_json=True, **kwargs)
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1] if txt.count("```") >= 2 else txt
        if txt.startswith("json"):
            txt = txt[4:]
        txt = txt.strip("`").strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        start = txt.find("{")
        start = start if start != -1 else txt.find("[")
        end = max(txt.rfind("}"), txt.rfind("]"))
        if start != -1 and end != -1:
            return json.loads(txt[start:end + 1])
        snippet = raw[:500].replace("\n", "\\n")
        raise LLMError(f"无法解析 LLM JSON 输出，请检查模型是否按模板返回严格 JSON。片段: {snippet}")
