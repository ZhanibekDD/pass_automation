from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import ValidationError

from app.ai.config import AISettings
from app.ai.schemas import PageExtraction


class ModelResponseError(RuntimeError):
    pass


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_page_extraction(content: str | dict[str, Any]) -> PageExtraction:
    try:
        if isinstance(content, dict):
            return PageExtraction.model_validate(content)
        return PageExtraction.model_validate_json(_strip_json_fence(content))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ModelResponseError(
            "Vision-модель вернула ответ вне строгой JSON-схемы"
        ) from exc


class VisionProvider(ABC):
    def __init__(self, settings: AISettings):
        self.settings = settings

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def extract(self, *, image_bytes: bytes, prompt: str) -> PageExtraction:
        raise NotImplementedError


class DisabledVisionProvider(VisionProvider):
    def health(self) -> dict[str, Any]:
        return {
            "status": "disabled",
            "provider": self.settings.provider,
            "model": self.settings.model,
        }

    def extract(self, *, image_bytes: bytes, prompt: str) -> PageExtraction:
        raise RuntimeError("AI отключён. Установите AI_ENABLED=true")


class OllamaVisionProvider(VisionProvider):
    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.settings.request_timeout_seconds)

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{self.settings.base_url}/api/tags")
                response.raise_for_status()
                models = [
                    item.get("name")
                    for item in response.json().get("models", [])
                    if item.get("name")
                ]
            return {
                "status": "ok",
                "provider": "ollama",
                "model": self.settings.model,
                "model_available": any(
                    name == self.settings.model
                    or name.split(":", 1)[0] == self.settings.model.split(":", 1)[0]
                    for name in models
                ),
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "status": "unavailable",
                "provider": "ollama",
                "model": self.settings.model,
                "error": type(exc).__name__,
            }

    def extract(self, *, image_bytes: bytes, prompt: str) -> PageExtraction:
        payload = {
            "model": self.settings.model,
            "stream": False,
            "format": PageExtraction.model_json_schema(),
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }
            ],
        }
        with self._client() as client:
            response = client.post(f"{self.settings.base_url}/api/chat", json=payload)
            response.raise_for_status()
        try:
            content = response.json()["message"]["content"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelResponseError("Некорректный ответ Ollama") from exc
        return parse_page_extraction(content)


class VLLMVisionProvider(VisionProvider):
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.model_api_key:
            headers["Authorization"] = f"Bearer {self.settings.model_api_key}"
        return headers

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.settings.request_timeout_seconds,
            headers=self._headers(),
        )

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=5, headers=self._headers()) as client:
                response = client.get(f"{self.settings.base_url}/v1/models")
                response.raise_for_status()
                models = [
                    item.get("id")
                    for item in response.json().get("data", [])
                    if item.get("id")
                ]
            return {
                "status": "ok",
                "provider": "vllm",
                "model": self.settings.model,
                "model_available": self.settings.model in models,
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "status": "unavailable",
                "provider": "vllm",
                "model": self.settings.model,
                "error": type(exc).__name__,
            }

    def extract(self, *, image_bytes: bytes, prompt: str) -> PageExtraction:
        image_data = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.settings.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_data}"},
                        },
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "pass_document_extraction",
                    "strict": True,
                    "schema": PageExtraction.model_json_schema(),
                },
            },
        }
        with self._client() as client:
            response = client.post(
                f"{self.settings.base_url}/v1/chat/completions", json=payload
            )
            response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ModelResponseError(
                "Некорректный OpenAI-compatible ответ vLLM"
            ) from exc
        return parse_page_extraction(content)


def create_provider(settings: AISettings) -> VisionProvider:
    if not settings.enabled:
        return DisabledVisionProvider(settings)
    if settings.provider == "ollama":
        return OllamaVisionProvider(settings)
    return VLLMVisionProvider(settings)
