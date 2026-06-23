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
    DEFAULT_TIMEOUT = 60  # seconds (was 30; bumped to give the agentic pick loop enough headroom)
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
            err_str = str(e).lower()
            # On timeout or transient errors, retry once with the fast model
            is_timeout = "timeout" in err_str or "timed out" in err_str
            is_transient = "502" in err_str or "503" in err_str or "504" in err_str
            if is_timeout or is_transient:
                logger.warning(f"Qwen {model or self.model} hit {err_str[:60]}; retrying with {self.DEFAULT_FAST_MODEL}")
                try:
                    kwargs["model"] = self.DEFAULT_FAST_MODEL
                    # Reduce max_tokens for the retry — flash is faster
                    kwargs["max_tokens"] = min(kwargs.get("max_tokens", 2000), 800)
                    resp = self.client.chat.completions.create(**kwargs)
                except Exception as e2:
                    logger.error(f"Qwen fast-model retry also failed: {e2}")
                    raise
            else:
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
