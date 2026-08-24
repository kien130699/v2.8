from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from facebook_app.config import settings
from facebook_app.db import get_setting


class LLMRouter:
    """Small OpenAI-compatible client with first-class 9Router support.

    Secrets stay in ENV. The selected model is a non-secret DB setting so it can
    be changed from the dashboard without restarting the server.
    """

    def _nine_configured(self) -> bool:
        return bool(settings.ninerouter_base_url and settings.ninerouter_api_key)

    def _generic_configured(self) -> bool:
        return bool(settings.llm_base_url and settings.llm_api_key)

    def configured(self) -> bool:
        # V2.8 policy: GPT/Gemini must go through 9Router only.
        return self._nine_configured()

    def provider_name(self) -> str:
        return "9router" if self._nine_configured() else "none"

    def _endpoint(self) -> tuple[str, str]:
        if self._nine_configured():
            return settings.ninerouter_base_url, settings.ninerouter_api_key
        raise RuntimeError("Chưa cấu hình 9ROUTER_API_KEY")

    def presets(self) -> list[dict[str, str]]:
        # 3.5/3.6 are intentionally configurable aliases: a user's 9Router may
        # expose them under a custom alias/provider prefix. /v1/models is the
        # source of truth and the dashboard can use any live model ID directly.
        candidates = [
            ("Gemini 3.1 Pro", settings.ninerouter_model_gemini_31),
            ("Gemini 3.5", settings.ninerouter_model_gemini_35),
            ("Gemini 3.6", settings.ninerouter_model_gemini_36),
            ("GPT 5.4 (Codex)", settings.ninerouter_model_gpt_54),
            ("GPT 5.4 (GitHub)", settings.ninerouter_model_gpt_54_gh),
            ("GPT 5.5", settings.ninerouter_model_gpt_55),
        ]
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for label, model_id in candidates:
            model_id = (model_id or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            out.append({"label": label, "id": model_id, "source": "preset"})
        return out

    def default_model(self) -> str:
        selected = get_setting("llm_selected_model", "").strip()
        if selected:
            return selected
        return settings.ninerouter_default_model or (self.presets()[0]["id"] if self.presets() else "")

    def resolve_model(self, model: str | None = None) -> str:
        value = (model or "").strip() or self.default_model().strip()
        if not value:
            raise RuntimeError("Chưa chọn LLM model")
        return value

    def live_models(self) -> list[dict[str, str]]:
        base, key = self._endpoint()
        r = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=min(settings.request_timeout_sec, 45),
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        out: list[dict[str, str]] = []
        for item in data:
            if isinstance(item, str):
                model_id = item
            elif isinstance(item, dict):
                model_id = str(item.get("id") or item.get("name") or "")
            else:
                continue
            if model_id:
                out.append({"id": model_id, "label": model_id, "source": "live"})
        out.sort(key=lambda x: x["id"].lower())
        return out

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)
        return content.strip()

    def chat(self, messages: list[dict[str, str]], model: str | None = None, *, temperature: float = 0.8, max_tokens: int | None = None) -> dict[str, Any]:
        base, key = self._endpoint()
        model_id = self.resolve_model(model)
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        started = time.perf_counter()
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=max(settings.request_timeout_sec, 90),
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise RuntimeError(f"LLM response không có choices: {str(data)[:500]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            # Some compatible gateways return content parts.
            content = "".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in content)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM response không có text content")
        return {"model": model_id, "content": content.strip(), "latency_ms": elapsed_ms, "raw": data}

    def chat_json(self, messages: list[dict[str, str]], model: str | None = None, *, temperature: float = 0.8) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.chat(messages, model, temperature=temperature)
        content = self._strip_code_fence(result["content"])
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            # Recover a top-level JSON object if the provider prepended a short sentence.
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(content[start : end + 1])
            else:
                raise ValueError(f"LLM không trả JSON hợp lệ: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("LLM JSON phải là object")
        return data, {"model": result["model"], "latency_ms": result["latency_ms"]}

    def test(self, model: str | None = None) -> dict[str, Any]:
        result = self.chat(
            [
                {"role": "system", "content": "Answer briefly."},
                {"role": "user", "content": "Trả lời đúng một câu tiếng Việt: Kết nối LLM hoạt động."},
            ],
            model,
            temperature=0.0,
            max_tokens=80,
        )
        return {
            "ok": True,
            "provider": self.provider_name(),
            "model": result["model"],
            "latency_ms": result["latency_ms"],
            "output": result["content"][:500],
        }


llm_router = LLMRouter()
