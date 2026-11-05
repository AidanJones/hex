"""LLM backends, stdlib only (urllib). Anthropic by default; any
OpenAI-compatible endpoint (Ollama, LM Studio, llama.cpp) also works."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


def _post(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"{e.code}: {e.read()[:300]!r}") from e
    except urllib.error.URLError as e:
        raise LLMError(str(e)) from e


class AnthropicLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or os.environ.get("HEX_MODEL", "claude-sonnet-5-5")
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise LLMError("Set ANTHROPIC_API_KEY in .env (or pick another HEX_BACKEND).")

    def complete(self, system: str, user: str, max_tokens: int = 900) -> str:
        r = _post("https://api.anthropic.com/v1/messages",
                  {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                  {"model": self.model, "max_tokens": max_tokens,
                   # Static system prompt is cached, so repeated calls are cheap.
                   "system": [{"type": "text", "text": system,
                               "cache_control": {"type": "ephemeral"}}],
                   "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in r.get("content", []))


THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_thinking(text: str) -> str:
    """Reasoning models (MiniMax M2.x, Qwen, DeepSeek…) may inline <think> blocks."""
    text = THINK_RE.sub("", text)
    if "<think>" in text:            # unterminated: model ran out of tokens mid-thought
        text = text.split("<think>")[0]
    return text.strip()


class OpenAICompatLLM:
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 key_env: str = "OPENAI_API_KEY", min_tokens: int = 0,
                 extra: dict | None = None) -> None:
        self.model = model or os.environ.get("HEX_MODEL", "llama3.1")
        self.base = (base_url or os.environ.get("HEX_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self.key = os.environ.get(key_env, "none")
        self.min_tokens = min_tokens      # thinking models need headroom beyond the answer
        self.extra = extra or {}

    def complete(self, system: str, user: str, max_tokens: int = 900) -> str:
        r = _post(f"{self.base}/chat/completions", {"authorization": f"Bearer {self.key}"},
                  {"model": self.model, "max_tokens": max(max_tokens, self.min_tokens),
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}], **self.extra})
        return strip_thinking(r["choices"][0]["message"].get("content") or "")


def minimax(model: str | None = None) -> OpenAICompatLLM:
    if not os.environ.get("MINIMAX_API_KEY"):
        raise LLMError("Set MINIMAX_API_KEY in .env.")
    return OpenAICompatLLM(
        model or os.environ.get("HEX_MODEL", "MiniMax-M2.5"),
        # Global endpoint; mainland-China accounts use https://api.minimaxi.com/v1
        os.environ.get("HEX_BASE_URL", "https://api.minimax.io/v1"),
        key_env="MINIMAX_API_KEY",
        min_tokens=4000,
        extra={"reasoning_split": True},   # put thinking in a separate field, not the answer
    )


def make_llm(backend: str, model: str | None = None):
    if backend == "anthropic":
        return AnthropicLLM(model)
    if backend == "minimax":
        return minimax(model)
    return OpenAICompatLLM(model)
