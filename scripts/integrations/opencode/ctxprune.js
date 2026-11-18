// ctxprune plugin for OpenCode (opencode-ai 1.19.x). Install: copy to ~/.config/opencode/plugins/ctxprune.js
// (global) or .opencode/plugins/ctxprune.js (project); files there load at startup (docs: opencode.ai/docs/plugins).
// "tool.execute.after": shell out to scripts/ctxprune_compress.py and set output.output to the compressed text when
// it got shorter. Tool args are not documented on the `after` input, so they are taken from input.args when present,
// else remembered from "tool.execute.before" (output.args) by callID. Skipped: file read/edit/write/patch tools (the
// agent edits against those exact bytes, PLAN §3.0). Env: CTXPRUNE_CLI, CTXPRUNE_PYTHON, plus the compressor's env.
// Fails open: any error -> output untouched. Only ONE export: OpenCode treats every exported function as a plugin.
// Cross-turn dedup runs on "experimental.chat.messages.transform" (the message list of every model call), not at tool
// time: OpenCode's prune shows old outputs as "[Old tool result content cleared]" and compaction drops the history
// before its summary, so a reference written into a stored output could later point at an output that is gone. The
// request's tool calls and results go in order to `ctxprune_compress.py --context=opencode` (dedup.context_dedup: a
// repeated output or run of lines becomes `[ctxprune: lines a–b identical to output #k above (`cmd`)]`, pointing at
// an earlier result IN THAT REQUEST); the rewrite replaces the part objects of this call's message list only (OpenCode
// reloads the list from storage for every step). Deterministic in the request prefix (prompt cache safe). read /
// edit / write / patch / todo outputs are counted but never rewritten. By model: on for claude-opus-* (Opus 5.5 probe passed), off otherwise; CTXPRUNE_DEDUP=0|1 overrides; CTXPRUNE_OFF=1: off for new results (results
// already sent keep their pinned text: off()).
import { execFile, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const SKIP = /^(read|edit|write|multiedit|patch|apply_patch|todowrite|todoread)$/i;
const CLI = process.env.CTXPRUNE_CLI || `${homedir()}/ctxprune-v2/scripts/ctxprune_compress.py`;
const PY = process.env.CTXPRUNE_PYTHON || "python3";
const TIMEOUT_MS = Number(process.env.CTXPRUNE_PLUGIN_TIMEOUT_MS || 60000);   // compress call; past it: escaped fallback
const killSwitch = () => ["1", "true", "yes"].includes(process.env.CTXPRUNE_OFF || "");   // as lossless_hook.off()

// The model's provider + base URL per session (chat.params), first seen: a local / self-hosted model turns the `paths`
// rule off (lossless.paths_on; CTXPRUNE_PATHS=0|1 overrides), and the choice stays fixed for the session.
const TARGETS = new Map();
const MODELS = new Map();   // sessionID -> the model of its latest call (chat.params): decides dedup per request
const DEDUP = new Map();    // model id -> the decision (ctxprune_compress.py --dedup-for: claude-opus-* on, else off)

function noteTarget(input) {
  try {
    const sid = input && input.sessionID;
    if (!sid) return;
    const prov = input.provider || {}, model = input.model || {};
    const mid = [model.id, model.modelID, model.api && model.api.id].find((x) => typeof x === "string" && x);
    if (mid) { MODELS.delete(sid); MODELS.set(sid, mid); }   // latest (safety gate a: a switch takes effect at once)
    if (MODELS.size > 4096) MODELS.delete(MODELS.keys().next().value);
    if (TARGETS.has(sid)) return;
    const parts = [model.providerID, prov.info && prov.info.id, prov.options && prov.options.baseURL,
                   model.options && model.options.baseURL, model.api && model.api.url];
    const t = parts.filter((x) => typeof x === "string" && x).join(" ");
    if (t) TARGETS.set(sid, t);
    if (TARGETS.size > 4096) TARGETS.delete(TARGETS.keys().next().value);
  } catch (e) { /* fail open: no target = hosted defaults */ }
}

function targetEnv(sessionID) {
  const t = sessionID && TARGETS.get(sessionID), m = sessionID && MODELS.get(sessionID);
  return { ...(t ? { CTXPRUNE_TARGET: t } : {}), ...(m ? { CTXPRUNE_MODEL: m } : {}) };   // model: PATHS_WIN_MODELS
}

function compress(text, command, tool, sessionID) {
  try {
    const out = execFileSync(PY, [CLI, `--command=${command || ""}`, `--tool=${tool || ""}`, "--harness", "opencode"], {   // `=`: "-x" commands stay values
      input: text, encoding: "utf8", timeout: TIMEOUT_MS, maxBuffer: 256 * 1024 * 1024,
      env: { ...process.env, CTXPRUNE_HARNESS: "opencode", ...targetEnv(sessionID) }, stdio: ["pipe", "pipe", "ignore"],
    });
    return typeof out === "string" && out.length ? out : null;
  } catch (e) {
    return null;
  }
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

function standIn(tool, a) {
  a = a && typeof a === "object" ? a : {};
  if (typeof a.command === "string") return a.command;
  if (/grep|search/i.test(tool) && a.pattern) return ["rg", "-n", "--", String(a.pattern), a.path ? String(a.path) : ""].join(" ").trim();
  try { return `${tool} ${JSON.stringify(a)}`.slice(0, 500); } catch (e) { return tool; }
}

const HARNESS_NAME = "opencode";

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

function contextDedup(events, seed, model) {
  return new Promise((resolve) => {
    try {
      const child = execFile(PY, [CLI, "--context=opencode"], {
        encoding: "utf8", timeout: 15000, maxBuffer: 512 * 1024 * 1024,
        env: { ...process.env, CTXPRUNE_HARNESS: "opencode" },
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
      child.stdin.end(JSON.stringify({ events, seed, model: model || null }));
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
function dedupOn(sid, model) {
  const v = (process.env.CTXPRUNE_DEDUP || "").toLowerCase();
  if (["1", "true", "yes", "on"].includes(v)) return true;
  if (["0", "false", "no", "off"].includes(v)) return false;
  if (!model) return false;
  if (DEDUP.has(model)) return DEDUP.get(model);
  let on = false;
  try {
    on = execFileSync(PY, [CLI, `--dedup-for=${model}`], { encoding: "utf8", timeout: 15000,
      stdio: ["ignore", "pipe", "ignore"] }).trim() === "1";
  } catch (e) { /* off */ }
  DEDUP.set(model, on);
  if (DEDUP.size > 4096) DEDUP.delete(DEDUP.keys().next().value);
  return on;
}

function off(sid, model) {
  const on = !["1", "true", "yes"].includes(process.env.CTXPRUNE_OFF || "") && dedupOn(sid, model);
  if (on) return false;
  try {
    return !existsSync(join(process.env.CTXPRUNE_STATE || join(homedir(), ".cache", "ctxprune"), "context-store.sqlite"));
  } catch (e) {
    return true;
  }
}

const CLEARED = "[Old tool result content cleared]";   // what OpenCode sends for a pruned (compacted) tool output

// The model-visible tool calls and results of one message list, in order (mirrors OpenCode's toModelMessages:
// failed assistant messages are dropped, a compacted output is sent as CLEARED). where[k] = [message, part] of
// result event k (rewritable ones only).
function contextEvents(messages) {
  const events = [], where = {};
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || !m.info || m.info.role !== "assistant" || !Array.isArray(m.parts)) continue;
    if (m.info.error && !(m.info.error.name === "MessageAbortedError")) continue;
    events.push({ t: "turn" });   // every assistant message, text-only too: dedup.context_dedup's history cut
    for (let j = 0; j < m.parts.length; j++) {
      const p = m.parts[j];
      if (!p || p.type !== "tool" || !p.state) continue;
      const st = p.state;
      if (st.status !== "completed" && st.status !== "error") continue;
      events.push({ t: "call", id: p.callID, name: p.tool, args: st.input ?? {} });
      if (st.status === "error") {
        if (typeof st.error === "string" && st.error) events.push({ t: "result", id: p.callID, name: p.tool, text: st.error, dedup: false });
        continue;
      }
      const cleared = Boolean(st.time && st.time.compacted);
      const text = cleared ? CLEARED : st.output;
      if (typeof text !== "string" || !text) continue;
      if (!cleared) where[events.length] = [i, j];
      events.push({ t: "result", id: p.callID, name: p.tool, text, dedup: !cleared });
    }
  }
  return { events, where };
}

export const CtxPrune = async () => {
  const pending = new Map();
  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      let msgs = null, events = null, where = null, seed = null;
      const apply = (out) => {
        for (const [k, text] of Object.entries(out)) {
          const w = where[k];
          if (!w || typeof text !== "string") continue;
          const m = msgs[w[0]], p = m.parts[w[1]];
          const parts = m.parts.slice();
          parts[w[1]] = { ...p, state: { ...p.state, output: text } };
          msgs[w[0]] = { ...m, parts };
        }
      };
      try {
        msgs = output && Array.isArray(output.messages) ? output.messages : null;
        const withSid = msgs && msgs.find((m) => m && m.info && m.info.sessionID);
        const sid = withSid ? withSid.info.sessionID : null;
        // The model of THIS call: OpenCode runs messages.transform before chat.params, so MODELS still holds the
        // previous call's model; the last user message carries the one this step uses (prompt loop: lastUser.model).
        // Without it a switch away from Opus deduped (and pinned) one more request's results (safety gate a).
        const user = msgs && [...msgs].reverse().find((m) => m && m.info && m.info.role === "user");
        const umodel = user && user.info.model && typeof user.info.model.modelID === "string" ? user.info.model.modelID : null;
        const asst = msgs && [...msgs].reverse().find((m) => m && m.info && m.info.role === "assistant" && m.info.modelID);
        const model = umodel || (sid && MODELS.get(sid)) || (asst ? asst.info.modelID : null);
        if (!msgs) return;
        ({ events, where } = contextEvents(msgs));
        const first = msgs.find((m) => m && m.info && m.info.role === "user");   // first-render pins: per conversation
        seed = first && Array.isArray(first.parts)
          ? JSON.stringify(first.parts.filter((p) => p && p.type === "text").map((p) => p.text)) : null;
        if (off(sid, model) || events.filter((e) => e.t === "result").length < 2) return apply(sentFallback(seed, events, where));
        const out = await contextDedup(events, seed, model);
        if (out === null) return apply(sentFallback(seed, events, where));   // CLI crash / timeout: last sent bytes
        sentRemember(seed, events, out);
        apply(out);
      } catch (e) {   // never raw for a result already sent rewritten
        try {
          if (msgs && events && where) apply(sentFallback(seed, events, where));
        } catch (e2) { /* the call goes out as the client built it */ }
      }
    },
    "chat.params": async (input) => { noteTarget(input); },
    "tool.execute.before": async (input, output) => {
      try {
        if (input && input.callID && output && output.args) pending.set(input.callID, output.args);
        if (pending.size > 1000) pending.clear();
      } catch (e) { /* fail open */ }
    },
    "tool.execute.after": async (input, output) => {
      try {
        const tool = String((input && input.tool) || "");
        const args = (input && input.args) || pending.get(input && input.callID);
        if (input && input.callID) pending.delete(input.callID);
        if (!tool || SKIP.test(tool) || !output || typeof output.output !== "string" || !output.output) return;
        if (killSwitch()) return;   // CTXPRUNE_OFF=1: untouched, like the Claude Code hook (no fail-open escape either)
        const out = compress(output.output, standIn(tool, args), tool, input && input.sessionID);
        if (out !== null && (out.length < output.output.length || escapedOnly(out, output.output))) output.output = out;
        else output.output = escapeMarkers(output.output);   // timeout / crash / not shorter: escaped, never raw
      } catch (e) {
        try {   // fail open, still escaped (a no-op on text without marker-shaped lines)
          if (output && typeof output.output === "string" && !SKIP.test(String((input && input.tool) || "")) && !killSwitch()) {
            output.output = escapeMarkers(output.output);
          }
        } catch (e2) { /* untouched */ }
      }
    },
  };
};
