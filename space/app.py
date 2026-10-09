"""ctxprune vs LLMLingua-2, side by side on your own text."""

import json

import gradio as gr
from llmlingua import PromptCompressor

from ctxprune import Compressor
from ctxprune.idcheck import survival

ours = Compressor("darioooooo0o/ctxprune-small", backend="onnx", onnx_file="onnx/model_quantized.onnx")
theirs = PromptCompressor(model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
                          use_llmlingua2=True, device_map="cpu")

EXAMPLES = {
    "Stripe-style error (JSON)": """{
  "id": "ord_8f3a2c91",
  "status": "failed",
  "customer": {"id": "cus_Q7xK2mP9", "email": "j.devries@example.nl"},
  "amount": 14950,
  "currency": "eur",
  "error": {"code": "card_declined", "decline_code": "insufficient_funds",
            "message": "Your card has insufficient funds. Ask the customer to use another payment method."},
  "retry_after": 3600
}""",
    "Server log": """2026-10-08T09:14:06.002Z ERROR [worker-3] job 5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a failed after 3 retries: ECONNREFUSED 10.0.4.17:5432
2026-10-08T09:14:06.118Z WARN  [scheduler] requeueing job 5f2d8e7a-1c4b-4e9a-b3d2-9a8f7e6c5b4a with backoff 120s (attempt 4 of 5)
2026-10-08T09:14:07.540Z INFO  [pool] connection pool for postgres://app@10.0.4.17:5432/orders recovered after 1.4s
2026-10-08T09:14:09.003Z ERROR [api] GET /v2/orders/ord_8f3a2c91 returned 503 in 2210ms (upstream timeout, trace=a71b0d4e9c)""",
    "Python traceback": """Traceback (most recent call last):
  File "/srv/app/lib/api_client.py", line 88, in fetch_page
    resp = self.session.get(url, timeout=self.timeout)
  File "/usr/lib/python3.12/site-packages/requests/sessions.py", line 602, in get
    return self.request("GET", url, **kwargs)
requests.exceptions.ConnectTimeout: HTTPSConnectionPool(host='api.example.com', port=443): Max retries exceeded with url: /v1/items?page=3 (Caused by ConnectTimeoutError(timeout=10.0))""",
    "Plain prose": """The committee met on Tuesday to review the proposal for the new cycling bridge. Most members agreed that the
current design was too expensive, but they were reluctant to delay the project again, because the city had already
secured a grant that expires at the end of next year. In the end they asked the engineers to come back within six weeks
with a cheaper version that keeps the same route.""",
}


def report(name, original, out):
    s = survival(original, out)
    kept = f"{s['protected'] - s['lost']}/{s['protected']}"
    lines = [f"**{name}**: {s['char_ratio']:.0%} of the text kept, identifiers intact: **{kept}**"]
    if s.get("json") is not None:
        lines.append("JSON still valid: " + ("yes" if s["json"] else "no"))
    if s["lost_examples"]:
        lines.append("Lost or mangled: " + ", ".join(f"`{x}`" for x in s["lost_examples"]))
    return "\n\n".join(lines)


def run(text, rate, protect):
    if not text.strip():
        return "", "", "", ""
    a = ours.compress(text, rate=rate, force_protected=protect)["text"]
    b = theirs.compress_prompt(text, rate=rate, force_tokens=["\n", "?"])["compressed_prompt"]
    return a, report("ctxprune-small", text, a), b, report("LLMLingua-2 (mBERT)", text, b)


with gr.Blocks(title="ctxprune") as demo:
    gr.Markdown(
        "# ctxprune: context compression for AI agents\n"
        "Paste a tool output, log, code or document. Both models delete the least useful words so an LLM reads fewer tokens. "
        "ctxprune is trained on agent text and never splits identifiers. "
        "[Model](https://huggingface.co/darioooooo0o/ctxprune-small) · [GitHub](https://github.com/riceharvest/ctxprune) · "
        "`pip install ctxprune`")
    with gr.Row():
        text = gr.Textbox(label="Input", lines=12, value=EXAMPLES["Stripe-style error (JSON)"])
    with gr.Row():
        rate = gr.Slider(0.2, 0.9, value=0.5, step=0.05, label="Fraction to keep")
        protect = gr.Checkbox(False, label="ctxprune: never drop IDs, numbers or paths")
        go = gr.Button("Compress", variant="primary")
    gr.Examples([[v] for v in EXAMPLES.values()], inputs=[text], label="Examples")
    with gr.Row():
        with gr.Column():
            out_a = gr.Textbox(label="ctxprune-small", lines=10)
            rep_a = gr.Markdown()
        with gr.Column():
            out_b = gr.Textbox(label="LLMLingua-2", lines=10)
            rep_b = gr.Markdown()
    go.click(run, [text, rate, protect], [out_a, rep_a, out_b, rep_b])
    demo.load(run, [text, rate, protect], [out_a, rep_a, out_b, rep_b])

if __name__ == "__main__":
    demo.launch()
