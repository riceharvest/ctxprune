"""Small, dependency-free adapters for SDK request bodies.

The wrappers operate on requests before they are sent. A tool result is rendered once
per tool call id and that rendering is reused for the lifetime of the wrapper. This
keeps a repeated result byte-for-byte stable across retries and later turns.
"""
from __future__ import annotations

from typing import Any


_lossless = None


def _load_lossless():
    global _lossless
    if _lossless is None:
        import sys
        from . import glue
        rt = glue.runtime_dir()
        if rt not in sys.path:
            sys.path.append(rt)
        import lossless
        _lossless = lossless
    return _lossless


def _safe_render(text: str) -> str:
    """Lossless (safe mode) rendering; any failure leaves the text unchanged."""
    try:
        return _load_lossless().compress(text)[0]
    except Exception:
        return text


class _Pinning:
    def __init__(self, render=None):
        self._render = render or _safe_render
        self._pinned: dict[str, str] = {}

    def _pin(self, key: Any, text: Any) -> Any:
        if not isinstance(key, str):
            return text
        if isinstance(text, list):  # Anthropic content blocks: pin each text block under key+index
            out = []
            for i, b in enumerate(text):
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                    b = dict(b, text=self._pin(f"{key}#{i}", b["text"]))
                out.append(b)
            return out
        if not isinstance(text, str):
            return text
        if key not in self._pinned:
            self._pinned[key] = self._render(text)
        return self._pinned[key]

    def messages(self, messages):
        """Copy and render OpenAI Chat Completions tool messages."""
        if not isinstance(messages, (list, tuple)):
            return messages
        result = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "tool":
                result.append(message)
                continue
            copied = dict(message)
            copied["content"] = self._pin(message.get("tool_call_id"), message.get("content"))
            result.append(copied)
        return result

    def anthropic_messages(self, messages):
        """Copy Anthropic messages and render tool_result blocks by tool_use_id."""
        if not isinstance(messages, (list, tuple)):
            return messages
        result = []
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                result.append(message)
                continue
            copied = dict(message)
            blocks = []
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    block = dict(block)
                    block["content"] = self._pin(block.get("tool_use_id"), block.get("content"))
                blocks.append(block)
            copied["content"] = blocks
            result.append(copied)
        return result

    def responses_input(self, value):
        """Copy OpenAI Responses API function_call_output items."""
        if not isinstance(value, (list, tuple)):
            return value
        result = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                item = dict(item)
                item["output"] = self._pin(item.get("call_id"), item.get("output"))
            result.append(item)
        return result


class _CreateProxy:
    def __init__(self, target, pinning, kind):
        self._target, self._pinning, self._kind = target, pinning, kind

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if name != "create" or not callable(value):
            return value

        def create(*args, **kwargs):
            if self._kind == "openai":
                if "messages" in kwargs:
                    kwargs = dict(kwargs, messages=self._pinning.messages(kwargs["messages"]))
                if "input" in kwargs:
                    kwargs = dict(kwargs, input=self._pinning.responses_input(kwargs["input"]))
            else:
                if "messages" in kwargs:
                    kwargs = dict(kwargs, messages=self._pinning.anthropic_messages(kwargs["messages"]))
            return value(*args, **kwargs)
        return create


class _ClientProxy:
    def __init__(self, client, kind, render=None):
        self._client, self._kind = client, kind
        self._pinning = _Pinning(render)

    def __getattr__(self, name):
        value = getattr(self._client, name)
        if name == "chat" and self._kind == "openai":
            return _NestedProxy(value, self._pinning, "openai")
        if name == "responses" and self._kind == "openai":
            return _NestedProxy(value, self._pinning, "openai")
        if name == "messages" and self._kind == "anthropic":
            return _NestedProxy(value, self._pinning, "anthropic")
        return value


class _NestedProxy:
    def __init__(self, target, pinning, kind):
        self._target, self._pinning, self._kind = target, pinning, kind

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if name == "completions" and self._kind == "openai":
            return _CreateProxy(value, self._pinning, "openai")
        if name == "create":
            return _CreateProxy(self._target, self._pinning, self._kind).create
        return value


def wrap_openai(client, *, render=None):
    """Wrap an OpenAI Python client; tool outputs are pinned by tool call id."""
    return _ClientProxy(client, "openai", render)


def wrap_anthropic(client, *, render=None):
    """Wrap an Anthropic Python client; tool results are pinned by tool use id."""
    return _ClientProxy(client, "anthropic", render)


class LiteLLMCallback:
    """LiteLLM-compatible ``CustomLogger`` callback for async_pre_call_hook payloads.

    Add an instance to ``litellm.callbacks``. It rewrites tool results in the
    request body and uses the same per-id first-render cache as the SDK wrappers.
    """
    def __init__(self, *, render=None):
        self._pinning = _Pinning(render)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if isinstance(data, dict):
            if "messages" in data:
                data["messages"] = self._pinning.messages(data["messages"])
            if "input" in data:
                data["input"] = self._pinning.responses_input(data["input"])
        return data
