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
// edit / write / patch / todo outputs are counted but never rewritten. Opt-in: only with CTXPRUNE_DEDUP=1 (Qwen3.8 comprehension probe); CTXPRUNE_OFF=1: off.
import { execFile, execFileSync } from "node:child_process";
import { homedir } from "node:os";

const SKIP = /^(read|edit|write|multiedit|patch|apply_patch|todowrite|todoread)$/i;
const CLI = process.env.CTXPRUNE_CLI || `${homedir()}/ctxprune-v2/scripts/ctxprune_compress.py`;
const PY = process.env.CTXPRUNE_PYTHON || "python3";

// The model's provider + base URL per session (chat.params), first seen: a local / self-hosted model turns the `paths`
// rule off (lossless.paths_on; CTXPRUNE_PATHS=0|1 overrides), and the choice stays fixed for the session.
const TARGETS = new Map();

export function noteTarget(input) {
  try {
    const sid = input && input.sessionID;
    if (!sid || TARGETS.has(sid)) return;
    const prov = input.provider || {}, model = input.model || {};
    const parts = [model.providerID, prov.info && prov.info.id, prov.options && prov.options.baseURL,
                   model.options && model.options.baseURL, model.api && model.api.url];
    const t = parts.filter((x) => typeof x === "string" && x).join(" ");
    if (t) TARGETS.set(sid, t);
    if (TARGETS.size > 4096) TARGETS.delete(TARGETS.keys().next().value);
  } catch (e) { /* fail open: no target = hosted defaults */ }
}

export function targetEnv(sessionID) {
  const t = sessionID && TARGETS.get(sessionID);
  return t ? { CTXPRUNE_TARGET: t } : {};
}

function compress(text, command, tool, sessionID) {
  try {
    const out = execFileSync(PY, [CLI, `--command=${command || ""}`, `--tool=${tool || ""}`, "--harness", "opencode"], {   // `=`: "-x" commands stay values
      input: text, encoding: "utf8", timeout: 60000, maxBuffer: 256 * 1024 * 1024,
      env: { ...process.env, CTXPRUNE_HARNESS: "opencode", ...targetEnv(sessionID) }, stdio: ["pipe", "pipe", "ignore"],
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

function standIn(tool, a) {
  a = a && typeof a === "object" ? a : {};
  if (typeof a.command === "string") return a.command;
  if (/grep|search/i.test(tool) && a.pattern) return ["rg", "-n", "--", String(a.pattern), a.path ? String(a.path) : ""].join(" ").trim();
  try { return `${tool} ${JSON.stringify(a)}`.slice(0, 500); } catch (e) { return tool; }
}

function contextDedup(events) {
  return new Promise((resolve) => {
    try {
      const child = execFile(PY, [CLI, "--context=opencode"], {
        encoding: "utf8", timeout: 15000, maxBuffer: 512 * 1024 * 1024,
        env: { ...process.env, CTXPRUNE_HARNESS: "opencode" },
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
      child.stdin.end(JSON.stringify({ events }));
    } catch (e) {
      resolve({});
    }
  });
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
      try {
        const msgs = output && Array.isArray(output.messages) ? output.messages : null;
        if (!msgs || ["1", "true", "yes"].includes(process.env.CTXPRUNE_OFF || "") || !["1", "true", "yes", "on"].includes((process.env.CTXPRUNE_DEDUP || "").toLowerCase())) return;   // dedup is opt-in
        const { events, where } = contextEvents(msgs);
        if (events.filter((e) => e.t === "result").length < 2) return;
        const out = await contextDedup(events);
        for (const [k, text] of Object.entries(out)) {
          const w = where[k];
          if (!w || typeof text !== "string") continue;
          const m = msgs[w[0]], p = m.parts[w[1]];
          const parts = m.parts.slice();
          parts[w[1]] = { ...p, state: { ...p.state, output: text } };
          msgs[w[0]] = { ...m, parts };
        }
      } catch (e) { /* fail open: the call goes out unchanged */ }
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
        const out = compress(output.output, standIn(tool, args), tool, input && input.sessionID);
        if (out !== null && (out.length < output.output.length || escapedOnly(out, output.output))) output.output = out;
      } catch (e) { /* fail open */ }
    },
  };
};
