// ctxprune for DeepSeek Harness (dsh, @deepseek-ai/dsh): a `tools/post-execute` waterfall listener that compresses
// tool-result text through scripts/ctxprune_compress.py (same CLI and settings as the Pi / OpenCode plugins:
// CTXPRUNE_KEEP, CTXPRUNE_MIN_CHARS, CTXPRUNE_PORT, CTXPRUNE_LOG). Registered without `prepend`, so dsh's own
// spill-policy (prepended, outermost) sees the compressed text and only spills what is still over its cap.
// Skipped: file read/edit/write tools (the agent edits against exact bytes), non-text content, blocked or value
// decisions. Fails open: any error -> the decision unchanged.
// Load it with a profile patch:  - insert: [{ id: ctxprune, name: '/abs/path/ctxprune.mjs' }]
// No cross-turn dedup here (the Hermes / Pi / OpenCode plugins dedup each model request from its message list): dsh
// has no request-local message transform (`agent/request` "cannot mutate messages", `agent/pre-step` only sees the
// messages entering the step), and its compaction keeps a verbatim tail, summarizes the span before it and trims
// the middle of big results, so a reference written into a result at tool time could later point at an output that
// is gone. For dedup, route dsh through ctxprune_proxy (it dedups every request the same way).
import { execFile } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'ctxprune'

const HERE = dirname(fileURLToPath(import.meta.url))
const CLI = process.env.CTXPRUNE_CLI || join(HERE, '..', '..', 'ctxprune_compress.py')
const PY = process.env.CTXPRUNE_PYTHON || 'python3'
const TIMEOUT_MS = Number(process.env.CTXPRUNE_PLUGIN_TIMEOUT_MS || 60000)   // compress call; past it: escaped fallback
const killSwitch = () => ['1', 'true', 'yes'].includes(process.env.CTXPRUNE_OFF || '')   // as lossless_hook.off()
const MIN = Number(process.env.CTXPRUNE_MIN_CHARS || 2000)
const SKIP = /^(read|read_image|edit|write|multiedit|apply_patch|patch|notebook.*|todo.*|ask_user|present)$/i

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

function commandOf(args) {
  if (args && typeof args === 'object') {
    for (const k of ['command', 'cmd', 'pattern', 'query', 'url', 'path']) {
      if (typeof args[k] === 'string') return args[k]
    }
  }
  try { return JSON.stringify(args ?? {}).slice(0, 500) } catch { return '' }
}

// decision with its text replaced by the escaped text, or the decision itself when nothing was marker-shaped
function escaped(decision, text) {
  const esc = escapeMarkers(text)
  return esc === text ? decision : { ...decision, kind: 'accept', content: [{ type: 'text', text: esc }] }
}

// The model route + id of the agent's calls, read (never changed) on `agent/request`: a local / self-hosted route
// (provider name like vllm / ollama / local) turns the `paths` rule off (lossless.paths_on), and the model id feeds
// lossless.PATHS_WIN_MODELS, as CTXPRUNE_TARGET / CTXPRUNE_MODEL do for the Pi / OpenCode / Hermes plugins. The route
// is fixed at its first sight (one dsh process); the model is the latest call's. A user-set CTXPRUNE_TARGET wins
// (a route name alone can't show a localhost baseURL).
let TARGET = null
let MODEL = null
function noteCall(cfg) {
  try {
    if (cfg && typeof cfg.model === 'string' && cfg.model) MODEL = cfg.model
    if (TARGET === null && cfg && typeof cfg.provider === 'string' && cfg.provider) TARGET = cfg.provider
  } catch { /* fail open: hosted defaults */ }
}
function targetEnv() {
  return { ...(TARGET && !process.env.CTXPRUNE_TARGET ? { CTXPRUNE_TARGET: TARGET } : {}),
           ...(MODEL && !process.env.CTXPRUNE_MODEL ? { CTXPRUNE_MODEL: MODEL } : {}) }
}

export function apply(ctx) {
  try {
    ctx.on('agent/request', async (_payload, next) => {
      const cfg = await next()
      noteCall(cfg)
      return cfg   // the same config object: never a switch
    })
  } catch { /* an older dsh without agent/request: hosted defaults */ }
  ctx.on('tools/post-execute', async (exec, result, next) => {
    const decision = await next()
    let fallbackText = null
    try {
      if (decision.kind !== 'accept' || Object.hasOwn(decision, 'value') || SKIP.test(exec.name || '')) return decision
      if (killSwitch()) return decision   // CTXPRUNE_OFF=1: untouched, like the Claude Code hook (no fail-open escape)
      const content = decision.content ?? result.content
      if (!Array.isArray(content) || content.length === 0 || content.some(b => b?.type !== 'text')) return decision
      const text = content.map(b => b.text ?? '').join('\n')
      fallbackText = text
      if (text.length < MIN) return escaped(decision, text)   // under the minimum: escape only, no compress
      const out = await new Promise((resolve, reject) => {
        const child = execFile(PY, [CLI, `--command=${commandOf(exec.arguments)}`, `--tool=${exec.name || ''}`,
          '--harness', 'dsh'], { encoding: 'utf8', maxBuffer: 64 << 20, timeout: TIMEOUT_MS,
          env: { ...process.env, CTXPRUNE_HARNESS: 'dsh', ...targetEnv() } }, (err, stdout) => err ? reject(err) : resolve(stdout))
        child.stdin.on('error', () => {})
        child.stdin.end(text)
      })
      if (!out || (out.length >= text.length && !escapedOnly(out, text))) return escaped(decision, text)
      return { ...decision, kind: 'accept', content: [{ type: 'text', text: out }] }
    } catch {
      try {   // timeout / crash: escaped, never raw (a no-op on text without marker-shaped lines)
        return fallbackText === null ? decision : escaped(decision, fallbackText)
      } catch {
        return decision
      }
    }
  })
}
