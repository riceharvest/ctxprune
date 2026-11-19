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
// but never rewritten. By model: on for claude-opus-* (Opus 5.5 probe passed), off otherwise; CTXPRUNE_DEDUP=0|1 overrides; CTXPRUNE_OFF=1: off for
// new results (results already sent keep their pinned text: off()).
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const SKIP = /^(read|edit|write|multiedit|multi_edit|apply_patch|patch|notebook.*)$/i;
const CLI = process.env.CTXPRUNE_CLI || `${homedir()}/ctxprune-v2/scripts/ctxprune_compress.py`;
const PY = process.env.CTXPRUNE_PYTHON || "python3";
const TIMEOUT_MS = Number(process.env.CTXPRUNE_PLUGIN_TIMEOUT_MS || 60000);   // compress call; past it: escaped fallback
const killSwitch = () => ["1", "true", "yes"].includes(process.env.CTXPRUNE_OFF || "");   // as lossless_hook.off()

// The model's provider + base URL (ctx.model), first seen in this process (one Pi session): a local / self-hosted model
// turns the `paths` rule off (lossless.paths_on; CTXPRUNE_PATHS=0|1 overrides); fixed for the session.
let TARGET = null;
let MODEL = null;   // the model of the latest call (ctx.model.id): decides cross-turn dedup per request
const DEDUP = new Map();   // model id -> the decision (ctxprune_compress.py --dedup-for: claude-opus-* on, else off)

export function noteTarget(ctx) {
  try {
    const m = ctx && ctx.model;
    if (!m) return;
    MODEL = typeof m.id === "string" ? m.id : (typeof m.name === "string" ? m.name : MODEL);   // latest (gate a)
    if (TARGET !== null) return;
    TARGET = [m.provider, m.baseUrl].filter((x) => typeof x === "string" && x).join(" ");
  } catch (e) { /* fail open: no target = hosted defaults */ }
}

function compress(text, command, tool, noModel) {
  return new Promise((resolve) => {
    const args = [CLI, `--command=${command || ""}`, `--tool=${tool || ""}`, "--harness", "pi"];   // `=`: a value starting with "-" is not a flag
    if (noModel) args.push("--no-model");
    try { const child = execFile(PY, args, {
      encoding: "utf8", timeout: TIMEOUT_MS, maxBuffer: 256 * 1024 * 1024,
      env: { ...process.env, CTXPRUNE_HARNESS: "pi", ...(TARGET ? { CTXPRUNE_TARGET: TARGET } : {}),
             ...(MODEL ? { CTXPRUNE_MODEL: MODEL } : {}) },   // the session's model: lossless.PATHS_WIN_MODELS
    }, (err, stdout) => resolve(!err && typeof stdout === "string" && stdout.length ? stdout : null));
      child.stdin.on("error", () => {});
      child.stdin.end(text);
    } catch (e) { resolve(null); }
  });
}

// H2 fail-open escape: lossless.escape_markers in JS (same line boundaries, invisible prefix class and marker prefix).
// One more leading backslash on every marker-shaped line; a no-op (same string) on text without one. Used wherever
// the compressor can't give an answer (timeout, crash, skipped as small, not shorter), so forged markers never reach
// the model raw. ~0.1 s per 5 MB.
const ESC_SEP = /(\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029])/;
const ESC_LINE = /^((?:[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\u{1bca0}-\u{1bca3}\u{1d173}-\u{1d17a}\u{e0000}-\u{e0fff}])*)(\\*)(\[(?:\/?ctxprune|\/?cp(?=[:\]= ])|\u2026))/iu;
function escapeMarkers(text) {
  if (typeof text !== "string" || !text.includes("[")) return text;
  const parts = text.split(ESC_SEP);   // capturing split: separators at the odd indices
  for (let k = 0; k < parts.length; k += 2) {
    // after U+0085 / U+2028 / U+2029 (legal raw inside a JSON string) two backslashes, so JSON stays valid
    if (parts[k].includes("[")) parts[k] = parts[k].replace(ESC_LINE, k && /^[\x85\u2028\u2029]$/.test(parts[k - 1]) ? "$1\\\\$2$3" : "$1\\$2$3");
  }
  return parts.join("");
}

// H2: marker escaping adds one backslash per marker-shaped line, so a result can be LONGER and still must be used
// (a forged "[ctxprune: ...]" line in a file or web page would otherwise reach the model unescaped).
function escapedOnly(out, text) {
  // the line boundaries and invisible prefix chars of lossless.escape_markers (_LINE_SEP, INVISIBLE): a marker after \r,
  // \f or U+2028, or behind a bidi mark, is escaped too, and the plugin must not fall back to the unescaped text then
  const SEP = /(\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029])/;   // separators at odd indices
  const INV = /^((?:[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\u{1bca0}-\u{1bca3}\u{1d173}-\u{1d17a}\u{e0000}-\u{e0fff}])*)\\/u;
  const a = out.split(SEP), b = text.split(SEP);
  if (a.length !== b.length) return false;
  let n = 0;
  for (let i = 0; i < a.length; i++) {
    if (a[i] === b[i]) continue;
    if (i % 2) return false;
    const k = i && /^[\x85\u2028\u2029]$/.test(a[i - 1]) ? 2 : 1;   // escapeMarkers: two after an in-string separator
    let x = a[i];
    for (let j = 0; j < k; j++) x = x.replace(INV, "$1");
    if (a[i].length !== b[i].length + k || x !== b[i]) return false;
    n++;
  }
  return n > 0;
}

// One JSONL record in CTXPRUNE_LOG (the CLI's log; only when set), for decisions the plugin makes itself.
function logDecision(rec) {
  try {
    const p = process.env.CTXPRUNE_LOG;
    if (p) appendFileSync(p, JSON.stringify({ ts: Date.now() / 1000, ...rec }) + "\n");
  } catch (e) { /* logging only */ }
}

function standIn(tool, input) {
  const a = input && typeof input === "object" ? input : {};
  if (typeof a.command === "string") return a.command;
  if (/grep|search|rg/i.test(tool) && a.pattern) return ["rg", "-n", "--", String(a.pattern), a.path ? String(a.path) : ""].join(" ").trim();
  try { return `${tool} ${JSON.stringify(a)}`.slice(0, 500); } catch (e) { return tool; }
}

const HARNESS_NAME = "pi";

// Fail-safe for the request-time dedup (parity review 2026-10-18): what this plugin last sent for each rewritten
// result, so a CLI crash / timeout (or a missing context store) never reverts a result the provider already got
// deduped back to raw (a history edit: a cache miss, and a 400 on Anthropic 5.x models with preserved thinking). Key =
// sha256(conversation seed, call id, original text) -> the text sent. Results never rewritten have no entry: they
// were sent as the client has them, so the fallback leaves them alone. In memory, plus an append-only JSONL in the
// state dir (0600) so a restarted harness process keeps it; a line that can't be read is skipped (no pin = raw).
const SENT = new Map();
let SENT_LOADED = false;
const SENT_MAX = 200000;

function sentFile() {
  return join(process.env.CTXPRUNE_STATE || join(homedir(), ".cache", "ctxprune"), `context-sent-${HARNESS_NAME}.jsonl`);
}

function sentKey(seed, id, text) {
  return createHash("sha256").update(JSON.stringify([seed ?? null, id ?? null, text])).digest("hex");
}

function sentLoad() {
  if (SENT_LOADED) return;
  SENT_LOADED = true;
  try {
    const f = sentFile();
    if (!existsSync(f)) return;
    for (const line of readFileSync(f, "utf8").split("\n")) {
      if (!line) continue;
      try {
        const r = JSON.parse(line);
        if (r && typeof r.k === "string" && typeof r.t === "string" && !SENT.has(r.k)) SENT.set(r.k, r.t);
      } catch (e) { /* torn or corrupt line: no pin for it */ }
    }
  } catch (e) { /* unreadable file: no pins */ }
}

function sentRemember(seed, events, out) {
  try {
    sentLoad();
    const lines = [];
    for (const [k, text] of Object.entries(out)) {
      const e = events[k];
      if (!e || typeof text !== "string") continue;
      const key = sentKey(seed, e.id, e.text);
      if (SENT.get(key) === text) continue;
      SENT.set(key, text);
      lines.push(JSON.stringify({ k: key, t: text }));
    }
    while (SENT.size > SENT_MAX) SENT.delete(SENT.keys().next().value);
    if (lines.length) {
      mkdirSync(dirname(sentFile()), { recursive: true, mode: 0o700 });
      appendFileSync(sentFile(), lines.join("\n") + "\n", { mode: 0o600 });
    }
  } catch (e) { /* the memo is a safety net only */ }
}

// The CLI gave no answer: each result gets the text this plugin last sent for it, or stays as the client sent it.
function sentFallback(seed, events, where) {
  sentLoad();
  const out = {};
  for (const k of Object.keys(where)) {
    const e = events[k];
    const t = e && SENT.get(sentKey(seed, e.id, e.text));
    if (typeof t === "string" && t !== e.text) out[k] = t;
  }
  return out;
}

function contextDedup(events, seed) {
  return new Promise((resolve) => {
    try {
      const child = execFile(PY, [CLI, "--context=pi"], {
        encoding: "utf8", timeout: 15000, maxBuffer: 512 * 1024 * 1024,
        env: { ...process.env, CTXPRUNE_HARNESS: "pi" },
      }, (err, stdout) => {
        if (err) return resolve(null);   // crash / timeout: no answer (not "nothing to change")
        try {
          const r = JSON.parse(stdout);
          resolve(r && r.out && typeof r.out === "object" ? r.out : null);
        } catch (e) {
          resolve(null);
        }
      });
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify({ events, seed, model: MODEL }));
    } catch (e) {
      resolve(null);
    }
  });
}

// With dedup off or CTXPRUNE_OFF=1 the CLI still runs while first-render pins exist
// (<state>/context-store.sqlite): results already sent keep their deduped text and new ones go out unchanged, so a
// toggle never rewrites history the provider has cached (ctxprune_compress.context).
// Dedup goes by the current model, per request (claude-opus-* on; the allow-list is ctxprune_core.DEDUP_MODELS);
// CTXPRUNE_DEDUP=0|1 overrides.
export async function dedupOn() {
  const v = (process.env.CTXPRUNE_DEDUP || "").toLowerCase();
  if (["1", "true", "yes", "on"].includes(v)) return true;
  if (["0", "false", "no", "off"].includes(v)) return false;
  if (!MODEL) return false;
  if (DEDUP.has(MODEL)) return DEDUP.get(MODEL);
  const on = await new Promise((resolve) => {
    try {
      execFile(PY, [CLI, `--dedup-for=${MODEL}`], { encoding: "utf8", timeout: 15000 },
        (err, stdout) => resolve(!err && typeof stdout === "string" && stdout.trim() === "1"));
    } catch (e) { resolve(false); }
  });
  DEDUP.set(MODEL, on);
  return on;
}

async function off() {
  const on = !["1", "true", "yes"].includes(process.env.CTXPRUNE_OFF || "") && await dedupOn();
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
      events.push({ t: "turn" });   // every assistant message, text-only too: dedup.context_dedup's history cut
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

export async function onContext(event, ctx) {
  let msgs = null, events = null, where = null, seed = null;
  const apply = (out) => {
    let changed = false;
    for (const [k, text] of Object.entries(out)) {
      const i = where[k];
      if (i === undefined || typeof text !== "string") continue;
      msgs[i] = { ...msgs[i], content: [{ ...msgs[i].content[0], text }] };
      changed = true;
    }
    return changed ? { messages: msgs } : undefined;
  };
  try {
    noteTarget(ctx);   // the session's model decides dedup (first seen)
    msgs = Array.isArray(event && event.messages) ? event.messages : null;
    if (!msgs) return undefined;
    ({ events, where } = contextEvents(msgs));
    const first = msgs.find((m) => m && m.role === "user");   // first-render pins are per conversation
    seed = first ? JSON.stringify(first.content) : null;
    if (await off() || events.filter((e) => e.t === "result").length < 2) return apply(sentFallback(seed, events, where));
    let out = await contextDedup(events, seed);
    if (out === null) return apply(sentFallback(seed, events, where));   // CLI crash / timeout: last sent bytes
    sentRemember(seed, events, out);
    let changed = false;
    for (const [k, text] of Object.entries(out)) {
      const i = where[k];
      if (i === undefined || typeof text !== "string") continue;
      msgs[i] = { ...msgs[i], content: [{ ...msgs[i].content[0], text }] };
      changed = true;
    }
    return changed ? { messages: msgs } : undefined;
  } catch (e) {
    try {   // never raw for a result already sent rewritten
      return msgs && events && where ? apply(sentFallback(seed, events, where)) : undefined;
    } catch (e2) {
      return undefined;
    }
  }
}

export default function (pi) {
  pi.on("context", onContext);
  pi.on("tool_result", async (event, ctx) => {
    try {
      noteTarget(ctx);
      const tool = String(event.toolName ?? event.tool ?? event.name ?? "");
      if (!tool || SKIP.test(tool)) return undefined;
      if (killSwitch()) return undefined;   // CTXPRUNE_OFF=1: untouched, like the Claude Code hook (no fail-open escape)
      // bash exiting non-zero (a failing test run) is an ordinary output: full pipeline. Other tool errors: lossless only.
      const noModel = Boolean(event.isError) && !/^bash$/i.test(tool);
      const parts = Array.isArray(event.content) ? event.content : [];
      if (!parts.length) return undefined;
      if (parts.some((p) => !p || p.type !== "text" || typeof p.text !== "string")) {   // images: escape the text parts
        const esc = parts.map((p) => (p && p.type === "text" && typeof p.text === "string" ? { ...p, text: escapeMarkers(p.text) } : p));
        return esc.some((p, i) => p !== parts[i] && p.text !== parts[i].text) ? { content: esc } : undefined;
      }
      const shown = parts.map((p) => p.text).join("\n");
      // every fail-open below (timeout, crash, unchanged, not shorter) shows Pi's own view escaped, never raw
      const fallback = () => {
        const esc = escapeMarkers(shown);
        if (esc === shown) return undefined;   // nothing marker-shaped: bytes untouched
        const r = { content: [{ type: "text", text: esc }] };
        if (event.structuredContent !== undefined) r.structuredContent = undefined;
        return r;
      };
      let text = shown;
      let footer = "";
      const full = event.details && event.details.fullOutputPath;
      if (typeof full === "string" && existsSync(full) && statSync(full).size < 8 * 1024 * 1024) {
        text = readFileSync(full, "utf8");
        footer = `\n[ctxprune: compressed from ${text.length} chars; the full output is in ${full}]`;
      }
      const out = await compress(text, standIn(tool, event.input ?? event.args), tool, noModel);
      if (out === null || out === text) return fallback();
      const final = out + footer;
      if (final.length >= shown.length && !(footer === "" && escapedOnly(out, text))) {
        // Pi showed its own capped view (fullOutputPath) and our result + footer is not shorter: declined by design.
        // One record for the health checker (episode_stats `declined_cap`), next to the CLI's own record.
        if (footer !== "") logDecision({ harness: "pi", tool, stage: "declined_cap", declined_cap: true,
                                         before: shown.length, after: shown.length, full: text.length, out: final.length });
        return fallback();
      }
      const res = { content: [{ type: "text", text: final }] };
      if (event.structuredContent !== undefined) res.structuredContent = undefined;
      return res;
    } catch (e) {
      try {
        if (killSwitch()) return undefined;
        const parts = Array.isArray(event.content) ? event.content : [];
        const shown = parts.map((p) => (p && typeof p.text === "string" ? p.text : "")).join("\n");
        const esc = parts.length && parts.every((p) => p && p.type === "text") ? escapeMarkers(shown) : shown;
        return esc !== shown ? { content: [{ type: "text", text: esc }] } : undefined;
      } catch (e2) {
        return undefined;
      }
    }
  });
}
