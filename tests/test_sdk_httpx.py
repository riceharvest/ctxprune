"""wrap_openai / wrap_anthropic against the real SDK clients over httpx.MockTransport (no network).

Skipped when httpx / openai / anthropic are not installed."""
import json

import pytest

import ctxprune

httpx = pytest.importorskip("httpx")

OAI_REPLY = {"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
             "choices": [{"index": 0, "finish_reason": "stop",
                          "message": {"role": "assistant", "content": "ok"}}]}
ANT_REPLY = {"id": "m1", "type": "message", "role": "assistant", "model": "m", "stop_reason": "end_turn",
             "stop_sequence": None, "content": [{"type": "text", "text": "ok"}],
             "usage": {"input_tokens": 1, "output_tokens": 1}}


def _http(sdk):
    """Newer anthropic releases use httpx2, older ones and openai use httpx."""
    mod = getattr(sdk, "_base_client", None)
    try:
        import httpx2
        if "httpx2" in repr(getattr(mod, "httpx", "")) or sdk.__name__ == "anthropic" and not _uses_httpx(sdk):
            return httpx2
    except ImportError:
        pass
    return httpx


def _uses_httpx(sdk):
    try:
        sdk.Anthropic(api_key="k", http_client=httpx.Client())
        return True
    except TypeError:
        return False


def _transport(bodies, reply, http=httpx):
    def handler(request):
        bodies.append(json.loads(request.content))
        return http.Response(200, json=reply)
    return http.MockTransport(handler)


def test_wrap_openai_over_mock_transport_pins_tool_output_bytes():
    openai = pytest.importorskip("openai")
    bodies = []
    client = openai.OpenAI(api_key="k", http_client=httpx.Client(transport=_transport(bodies, OAI_REPLY)))
    w = ctxprune.wrap_openai(client, render=lambda s: "R:" + s)
    prefix = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}
    w.chat.completions.create(model="m", messages=prefix + [call, {"role": "tool", "tool_call_id": "call_1", "content": "alpha"}])
    w.chat.completions.create(model="m", messages=prefix + [call, {"role": "tool", "tool_call_id": "call_1", "content": "ALTERED"}])
    assert len(bodies) == 2
    assert bodies[0]["messages"][-1]["content"] == bodies[1]["messages"][-1]["content"] == "R:alpha"
    # whole request prefix is byte-identical between the two turns
    assert json.dumps(bodies[0], sort_keys=True) == json.dumps(bodies[1], sort_keys=True)
    # an unwrapped client sends the raw value (proves the wrapper, not the SDK, changed it)
    raw = []
    c2 = openai.OpenAI(api_key="k", http_client=httpx.Client(transport=_transport(raw, OAI_REPLY)))
    c2.chat.completions.create(model="m", messages=prefix + [call, {"role": "tool", "tool_call_id": "call_1", "content": "alpha"}])
    assert raw[0]["messages"][-1]["content"] == "alpha"


def test_wrap_openai_responses_over_mock_transport():
    openai = pytest.importorskip("openai")
    bodies = []
    reply = {"id": "r", "object": "response", "created_at": 1, "model": "m", "output": [], "status": "completed",
             "parallel_tool_calls": True, "tool_choice": "auto", "tools": []}
    client = openai.OpenAI(api_key="k", http_client=httpx.Client(transport=_transport(bodies, reply)))
    w = ctxprune.wrap_openai(client, render=lambda s: "R:" + s)
    w.responses.create(model="m", input=[{"type": "function_call_output", "call_id": "c9", "output": "beta"}])
    assert bodies[0]["input"][0]["output"] == "R:beta"


def test_wrap_anthropic_over_mock_transport_pins_tool_result_bytes():
    anthropic = pytest.importorskip("anthropic")
    bodies = []
    h = _http(anthropic)
    client = anthropic.Anthropic(api_key="k", http_client=h.Client(transport=_transport(bodies, ANT_REPLY, h)))
    w = ctxprune.wrap_anthropic(client, render=lambda s: "R:" + s)

    def msgs(text):
        return [{"role": "user", "content": "go"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "f", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": text}]}]
    w.messages.create(model="m", max_tokens=8, messages=msgs("alpha"))
    w.messages.create(model="m", max_tokens=8, messages=msgs("ALTERED"))
    assert bodies[0]["messages"][2]["content"][0]["content"] == "R:alpha"
    assert json.dumps(bodies[0], sort_keys=True) == json.dumps(bodies[1], sort_keys=True)


def test_wrap_anthropic_streaming_passes_through():
    anthropic = pytest.importorskip("anthropic")
    bodies = []
    h = _http(anthropic)
    def handler(request):
        bodies.append(json.loads(request.content))
        return h.Response(200, headers={"content-type": "text/event-stream"}, content=b"")
    client = anthropic.Anthropic(api_key="k", http_client=h.Client(transport=h.MockTransport(handler)))
    w = ctxprune.wrap_anthropic(client, render=lambda s: "R:" + s)
    w.messages.create(model="m", max_tokens=8, stream=True, messages=[
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "s1", "content": "x"}]}])
    assert bodies[0]["stream"] is True
    assert bodies[0]["messages"][0]["content"][0]["content"] == "R:x"
