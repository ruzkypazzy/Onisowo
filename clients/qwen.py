"""
Qwen LLM client — OpenAI-compatible wrapper for Qwen 3.6-plus / Qwen 3.6-flash.

Qwen tokens only work with Alibaba Cloud APIs (per Bitget hackathon rules).
Uses the Bitget-provided Qwen proxy at https://hackathon.bitgetops.com/v1
or direct Alibaba Cloud at https://api.minimax.io/v1.

Models:
- qwen3.6-plus (recommended, smarter)
- qwen3.6-flash (faster, cheaper)
"""

import os
import json
import logging
from typing import Any, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)


class QwenClient:
    """Qwen LLM client. OpenAI-compatible. Reads from env vars."""

    DEFAULT_BASE_URL = "https://hackathon.bitgetops.com/v1"
    DEFAULT_MODEL = "qwen3.6-plus"
    DEFAULT_FAST_MODEL = "qwen3.6-flash"
    DEFAULT_TIMEOUT = 30  # seconds
    DEFAULT_MAX_TOKENS = 2000

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("BITGET_QWEN_API_KEY", "")
        self.base_url = base_url or os.environ.get("QWEN_BASE_URL", self.DEFAULT_BASE_URL)
        self.model = model or os.environ.get("QWEN_MODEL", self.DEFAULT_MODEL)

        if not self.api_key:
            raise ValueError(
                "Qwen API key missing. Set BITGET_QWEN_API_KEY env var."
            )

        # Initialize OpenAI-compatible client
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.DEFAULT_TIMEOUT,
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> dict[str, Any]:
        """Make a chat completion call.

        messages: [{"role": "user"|"system"|"assistant", "content": "..."}]
        tools: optional list of tool schemas (OpenAI function-calling format)
        Returns: {"content": "...", "tool_calls": [...], "usage": {...}}
        """
        kwargs = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error(f"Qwen API error: {e}")
            raise

        message = resp.choices[0].message

        return {
            "content": message.content or "",
            "tool_calls": [tc.model_dump() for tc in (message.tool_calls or [])],
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                "total_tokens": resp.usage.total_tokens if resp.usage else 0,
            },
        }

    def is_healthy(self) -> bool:
        """Quick health check. Returns True if Qwen API is reachable."""
        try:
            resp = self.chat(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                model=self.DEFAULT_FAST_MODEL,  # use flash for ping
            )
            return bool(resp.get("content"))
        except Exception as e:
            logger.warning(f"Qwen health check failed: {e}")
            return False
