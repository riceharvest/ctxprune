import asyncio

import ctxprune


class Obj:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)


def test_openai_tool_output_first_render_is_pinned_and_prefix_unchanged():
    seen = []
    client = Obj(chat=Obj(completions=Obj(create=lambda **kw: seen.append(kw) or kw)))
    wrapped = ctxprune.wrap_openai(client, render=lambda s: "first:" + s)
    prefix = [{"role": "system", "content": "same"}]
    wrapped.chat.completions.create(messages=prefix + [
        {"role": "tool", "tool_call_id": "call-1", "content": "alpha"}])
    wrapped.chat.completions.create(messages=prefix + [
        {"role": "tool", "tool_call_id": "call-1", "content": "changed"}])
    assert seen[0]["messages"][0] == seen[1]["messages"][0] == prefix[0]
    assert seen[0]["messages"][1]["content"] == seen[1]["messages"][1]["content"] == "first:alpha"


def test_openai_responses_and_anthropic_blocks():
    captured = []
    oai = ctxprune.wrap_openai(Obj(responses=Obj(create=lambda **kw: captured.append(kw))),
                               render=lambda s: "R" + s)
    oai.responses.create(input=[{"type": "function_call_output", "call_id": "c", "output": "x"}])
    assert captured[0]["input"][0]["output"] == "Rx"
    captured.clear()
    ant = ctxprune.wrap_anthropic(Obj(messages=Obj(create=lambda **kw: captured.append(kw))),
                                  render=lambda s: "R" + s)
    ant.messages.create(messages=[{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "u", "content": "y"}]}])
    assert captured[0]["messages"][0]["content"][0]["content"] == "Ry"


def test_litellm_callback_pins_and_is_safe_for_unknown_shapes():
    callback = ctxprune.LiteLLMCallback(render=lambda s: "R" + s)
    async def run():
        first = {"messages": [{"role": "tool", "tool_call_id": "z", "content": "one"}]}
        second = {"messages": [{"role": "tool", "tool_call_id": "z", "content": "two"}]}
        await callback.async_pre_call_hook(None, None, first, "completion")
        await callback.async_pre_call_hook(None, None, second, "completion")
        assert first["messages"][0]["content"] == second["messages"][0]["content"] == "Rone"
        unknown = {"prompt": "untouched"}
        assert await callback.async_pre_call_hook(None, None, unknown, "completion") is unknown
    asyncio.run(run())


def test_default_safe_render_is_lossless_stable_and_fast():
    import json, time, sys
    from ctxprune import sdk
    rows = json.dumps([{"id": i, "name": "n%d" % i, "ok": True} for i in range(200)], indent=2)
    seen = []
    c = ctxprune.wrap_openai(Obj(chat=Obj(completions=Obj(create=lambda **kw: seen.append(kw)))))
    msgs = [{"role": "system", "content": "s"},
            {"role": "tool", "tool_call_id": "t1", "content": rows}]
    c.chat.completions.create(messages=msgs, stream=True)
    first = seen[0]["messages"][1]["content"]
    sdk._load_lossless()
    assert json.loads(sdk._lossless.decode(first)) == json.loads(rows)  # values round-trip (JSON minify)
    t = time.perf_counter()
    for _ in range(100):
        c.chat.completions.create(messages=msgs)
    per = (time.perf_counter() - t) / 100
    assert all(s["messages"][1]["content"] == first for s in seen)  # 100% prefix stability
    assert per < 0.001, per  # cache hit path: <1 ms
    assert msgs[1]["content"] == rows  # input not mutated


def test_anthropic_text_block_list_content():
    seen = []
    a = ctxprune.wrap_anthropic(Obj(messages=Obj(create=lambda **kw: seen.append(kw))), render=lambda s: "R" + s)
    m = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u",
          "content": [{"type": "text", "text": "q"}]}]}]
    a.messages.create(messages=m)
    assert seen[0]["messages"][0]["content"][0]["content"][0]["text"] == "Rq"
