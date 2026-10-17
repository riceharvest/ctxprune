// ctxprune extension for Pi (@earendil-works/pi-coding-agent 1.3.x).
//   pi -e /abs/scripts/integrations/pi/ctxprune.ts ...      (or copy into ~/.pi/agent/extensions/)
// On every tool_result: shell out to scripts/ctxprune_compress.py (stdin text -> stdout compressed text) and return
// {content:[{type:"text",text}]} when it got shorter. Skipped: file read/edit/write tools (the agent edits against
// those exact bytes, PLAN §3.0), non-text content (images). Error results of non-bash tools get the lossless stage
// only (bash exiting non-zero is an ordinary output). Bash output Pi pre-truncated (2000 lines / 50 KB) is compressed from details.fullOutputPath when that file exists, with a footer naming the file; the result
// is used only if it is shorter than what Pi would have shown. structuredContent is cleared when present (Pi drops
// it when only content is replaced; docs: extensions.md, tool_result).
// Plain JS syntax on purpose (no type annotations): Pi loads it as TS, and `node --check` works on a .mjs copy.
// Env: CTXPRUNE_CLI (path to ctxprune_compress.py), CTXPRUNE_PYTHON (python3), plus the compressor's own env
// (CTXPRUNE_KEEP, CTXPRUNE_MIN_CHARS, CTXPRUNE_PORT, CTXPRUNE_LOG). Fails open: any error -> undefined (unchanged).
// Cross-turn dedup runs on the `context` event (before every LLM call), not at tool time: Pi compacts and switches
// branches (session_tree), so a reference written into a stored result could later point at an output that is no
// longer in context. The handler sends the request's tool calls and results in order to
// `ctxprune_compress.py --context=pi` (dedup.context_dedup: a repeated result or run of lines becomes
// `[ctxprune: lines a–b identical to output #k above (`cmd`)]`, pointing at an earlier result IN THAT REQUEST) and
// returns the rewritten messages for this call only (Pi passes the handler a clone; the session keeps the originals).
// Deterministic in the request prefix, so the prompt cache sees the same bytes. Read/edit/write results are counted
// but never rewritten. Opt-in: only with CTXPRUNE_DEDUP=1 (Qwen3.8 comprehension probe); CTXPRUNE_OFF=1: off for
// new results (results already sent keep their pinned text: off()).
import { execFile, execFileSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const SKIP = /^(read|edit|write|multiedit|multi_edit|apply_patch|patch|notebook.*)$/i;
const CLI = process.env.CTXPRUNE_CLI || `${homedir()}/ctxprune-v2/scripts/ctxprune_compress.py`;
const PY = process.env.CTXPRUNE_PYTHON || "python3";

// The model's provider + base URL (ctx.model), first seen in this process (one Pi session): a local / self-hosted model
// turns the `paths` rule off (lossless.paths_on; CTXPRUNE_PATHS=0|1 overrides); fixed for the session.
let TARGET = null;

export function noteTarget(ctx) {
  try {
    if (TARGET !== null) return;
    const m = ctx && ctx.model;
    if (!m) return;
    TARGET = [m.provider, m.baseUrl].filter((x) => typeof x === "string" && x).join(" ");
  } catch (e) { /* fail open: no target = hosted defaults */ }
}

function compress(text, command, tool, noModel) {
  try {
    const args = [CLI, `--command=${command || ""}`, `--tool=${tool || ""}`, "--harness", "pi"];   // `=`: a value starting with "-" is not a flag
    if (noModel) args.push("--no-model");
    const out = execFileSync(PY, args, {
      input: text, encoding: "utf8", timeout: 60000, maxBuffer: 256 * 1024 * 1024,
      env: { ...process.env, CTXPRUNE_HARNESS: "pi", ...(TARGET ? { CTXPRUNE_TARGET: TARGET } : {}) },
      stdio: ["pipe", "pipe", "ignore"],
    });
    return typeof out === "string" && out.length ? out : null;
  } catch (e) {
    return null;
  }
}

// H2: marker escaping adds one backslash per marker-shaped line, so a result can be LONGER and still must be used
// (a forged "[ctxprune: ...]" line in a file or web page would otherwise reach the model unescaped).
function escapedOnly(out, text) {
  // the line boundaries and invisible prefix chars of lossless.escape_markers (_LINE_SEP, INVISIBLE): a marker after \r,
  // \f or U+2028, or behind a bidi mark, is escaped too, and the plugin must not fall back to the unescaped text then
  const SEP = /\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]/;
  const INV = /^((?:[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\u{1bca0}-\u{1bca3}\u{1d173}-\u{1d17a}\u{e0000}-\u{e0fff}])*)\\/u;
  const a = out.split(SEP), b = text.split(SEP);
  if (a.length !== b.length) return false;
  let n = 0;
  for (let i = 0; i < a.length; i++) {
    if (a[i] === b[i]) continue;
    if (a[i].length !== b[i].length + 1 || a[i].replace(INV, "$1") !== b[i]) return false;
    n++;
  }
  return n > 0;
}

function standIn(tool, input) {
  const a = input && typeof input === "object" ? input : {};
  if (typeof a.command === "string") return a.command;
  if (/grep|search|rg/i.test(tool) && a.pattern) return ["rg", "-n", "--", String(a.pattern), a.path ? String(a.path) : ""].join(" ").trim();
  try { return `${tool} ${JSON.stringify(a)}`.slice(0, 500); } catch (e) { return tool; }
}

function contextDedup(events, seed) {
  return new Promise((resolve) => {
    try {
      const child = execFile(PY, [CLI, "--context=pi"], {
        encoding: "utf8", timeout: 15000, maxBuffer: 512 * 1024 * 1024,
        env: { ...process.env, CTXPRUNE_HARNESS: "pi" },
      }, (err, stdout) => {
        if (err) return resolve({});
        try {
          const r = JSON.parse(stdout);
          resolve(r && r.out && typeof r.out === "object" ? r.out : {});
        } catch (e) {
          resolve({});
        }
      });
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify({ events, seed }));
    } catch (e) {
      resolve({});
    }
  });
}

// Dedup is opt-in. With dedup off or CTXPRUNE_OFF=1 the CLI still runs while first-render pins exist
// (<state>/context-store.sqlite): results already sent keep their deduped text and new ones go out unchanged, so a
// toggle never rewrites history the provider has cached (ctxprune_compress.context).
function off() {
  const on = !["1", "true", "yes"].includes(process.env.CTXPRUNE_OFF || "") &&
    ["1", "true", "yes", "on"].includes((process.env.CTXPRUNE_DEDUP || "").toLowerCase());
  if (on) return false;
  try {
    return !existsSync(join(process.env.CTXPRUNE_STATE || join(homedir(), ".cache", "ctxprune"), "context-store.sqlite"));
  } catch (e) {
    return true;
  }
}

// The tool calls and results of one request, in order; where[k] = the message index of result event k.
export function contextEvents(messages) {
  const events = [], where = {};
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || typeof m !== "object" || !Array.isArray(m.content)) continue;
    if (m.role === "assistant") {
      for (const b of m.content) {
        if (b && b.type === "toolCall") events.push({ t: "call", id: b.id, name: b.name, args: b.arguments ?? {} });
      }
    } else if (m.role === "toolResult") {
      const texts = m.content.filter((p) => p && p.type === "text" && typeof p.text === "string");
      if (!texts.length) continue;
      where[events.length] = i;
      events.push({ t: "result", id: m.toolCallId, name: m.toolName, text: texts.map((p) => p.text).join("\n"),
                    dedup: m.content.length === 1 });   // a single text part (no images): rewritable
    }
  }
  return { events, where };
}

export async function onContext(event) {
  try {
    const msgs = Array.isArray(event && event.messages) ? event.messages : null;
    if (!msgs || off()) return undefined;
    const { events, where } = contextEvents(msgs);
    if (events.filter((e) => e.t === "result").length < 2) return undefined;
    const first = msgs.find((m) => m && m.role === "user");   // first-render pins are per conversation
    const out = await contextDedup(events, first ? JSON.stringify(first.content) : null);
    let changed = false;
    for (const [k, text] of Object.entries(out)) {
      const i = where[k];
      if (i === undefined || typeof text !== "string") continue;
      msgs[i] = { ...msgs[i], content: [{ ...msgs[i].content[0], text }] };
      changed = true;
    }
    return changed ? { messages: msgs } : undefined;
  } catch (e) {
    return undefined;
  }
}

export default function (pi) {
  pi.on("context", onContext);
  pi.on("tool_result", async (event, ctx) => {
    try {
      noteTarget(ctx);
      const tool = String(event.toolName ?? event.tool ?? event.name ?? "");
      if (!tool || SKIP.test(tool)) return undefined;
      // bash exiting non-zero (a failing test run) is an ordinary output: full pipeline. Other tool errors: lossless only.
      const noModel = Boolean(event.isError) && !/^bash$/i.test(tool);
      const parts = Array.isArray(event.content) ? event.content : [];
      if (!parts.length || parts.some((p) => !p || p.type !== "text" || typeof p.text !== "string")) return undefined;
      const shown = parts.map((p) => p.text).join("\n");
      let text = shown;
      let footer = "";
      const full = event.details && event.details.fullOutputPath;
      if (typeof full === "string" && existsSync(full) && statSync(full).size < 8 * 1024 * 1024) {
        text = readFileSync(full, "utf8");
        footer = `\n[ctxprune: compressed from ${text.length} chars; the full output is in ${full}]`;
      }
      const out = compress(text, standIn(tool, event.input ?? event.args), tool, noModel);
      if (out === null || out === text) return undefined;
      const final = out + footer;
      if (final.length >= shown.length && !(footer === "" && escapedOnly(out, text))) return undefined;
      const res = { content: [{ type: "text", text: final }] };
      if (event.structuredContent !== undefined) res.structuredContent = undefined;
      return res;
    } catch (e) {
      return undefined;
    }
  });
}
