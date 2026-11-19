// ctxprune plugin for Cline (SDK runtime plugin, `afterTool`; shared by the Cline CLI, VS Code and JetBrains).
// Install: `cline plugin install <this file>` (design: docs/later-integrations.md). `afterTool` gets the tool result and
// whatever it returns replaces what the model sees. Lossless `safe` rules only (no session state -> no cross-turn
// dedup, no lossy). The text parts of the result go to scripts/ctxprune_compress.py --harness cline; the shorter text
// replaces them and every other field of the result object is copied (a bare string can be ignored, cf. copilot-cli
// #3361). Skipped: file read/edit/write tools and non-text results. Async execFile, never blocks the host.
// Fails open: error / timeout / not shorter -> original text with marker lines escaped. CTXPRUNE_OFF=1: untouched.
// UNVERIFIED LIVE: shape follows docs/later-integrations.md; tested in node with a fake afterTool context only.
import { execFile } from "node:child_process";
import { homedir } from "node:os";

const SKIP = /^(read|read_file|edit|edit_file|write|write_to_file|replace_in_file|multiedit|patch|apply_patch|todowrite|todoread)$/i;
const CLI = process.env.CTXPRUNE_CLI || `${homedir()}/ctxprune-v2/scripts/ctxprune_compress.py`;
const PY = process.env.CTXPRUNE_PYTHON || "python3";
const TIMEOUT_MS = Number(process.env.CTXPRUNE_PLUGIN_TIMEOUT_MS || 60000);
const MIN = Number(process.env.CTXPRUNE_MIN_CHARS || 0);
const killSwitch = () => ["1", "true", "yes"].includes(process.env.CTXPRUNE_OFF || "");

function compress(text, command, tool) {
  return new Promise((resolve) => {
    try {
      const child = execFile(PY, [CLI, `--command=${command || ""}`, `--tool=${tool || ""}`, "--harness", "cline"], {
        encoding: "utf8", timeout: TIMEOUT_MS, maxBuffer: 256 * 1024 * 1024,
        env: { ...process.env, CTXPRUNE_HARNESS: "cline" },
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

function standIn(tool, a) {
  a = a && typeof a === "object" ? a : {};
  if (typeof a.command === "string") return a.command;
  if (/grep|search/i.test(tool) && a.pattern) return ["rg", "-n", "--", String(a.pattern), a.path ? String(a.path) : ""].join(" ").trim();
  try { return `${tool} ${JSON.stringify(a)}`.slice(0, 500); } catch (e) { return tool; }
}


// result.content may be a string or an array of {type:"text",text} parts; returns the text or null (not plain text).
function textOf(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content) || !content.length || content.some((b) => !b || b.type !== "text" || typeof b.text !== "string")) return null;
  return content.map((b) => b.text).join("\n");
}

export async function rewrite(tool, args, result) {
  try {
    if (!result || result.isError || SKIP.test(String(tool || "")) || killSwitch()) return result;
    const field = "content" in result ? "content" : "output" in result ? "output" : null;
    if (!field) return result;
    const text = textOf(result[field]);
    if (!text) return result;
    let out = text.length < MIN ? null : await compress(text, standIn(String(tool || ""), args), String(tool || ""));
    if (!(out !== null && (out.length < text.length || escapedOnly(out, text)))) out = escapeMarkers(text);
    if (out === text) return result;
    return { ...result, [field]: typeof result[field] === "string" ? out : [{ type: "text", text: out }] };
  } catch (e) { return result; }   // fail open
}

// Only ONE plugin object is exported as default (the host loads the default export).
export default {
  name: "ctxprune",
  hooks: {
    afterTool: async (ctx) => {
      try {
        const r = ctx && (ctx.result ?? null);
        const t = await rewrite(ctx && (ctx.toolName ?? ctx.tool?.name ?? ctx.name), ctx && (ctx.input ?? ctx.args), r);
        return t === r ? undefined : { ...ctx, result: t };
      } catch (e) { return undefined; }
    },
  },
};
