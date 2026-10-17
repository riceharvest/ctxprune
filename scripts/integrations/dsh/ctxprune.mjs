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
import { execFileSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'ctxprune'

const HERE = dirname(fileURLToPath(import.meta.url))
const CLI = process.env.CTXPRUNE_CLI || join(HERE, '..', '..', 'ctxprune_compress.py')
const PY = process.env.CTXPRUNE_PYTHON || 'python3'
const MIN = Number(process.env.CTXPRUNE_MIN_CHARS || 2000)
const SKIP = /^(read|read_image|edit|write|multiedit|apply_patch|patch|notebook.*|todo.*|ask_user|present)$/i

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

function commandOf(args) {
  if (args && typeof args === 'object') {
    for (const k of ['command', 'cmd', 'pattern', 'query', 'url', 'path']) {
      if (typeof args[k] === 'string') return args[k]
    }
  }
  try { return JSON.stringify(args ?? {}).slice(0, 500) } catch { return '' }
}

export function apply(ctx) {
  ctx.on('tools/post-execute', async (exec, result, next) => {
    const decision = await next()
    try {
      if (decision.kind !== 'accept' || Object.hasOwn(decision, 'value') || SKIP.test(exec.name || '')) return decision
      const content = decision.content ?? result.content
      if (!Array.isArray(content) || content.length === 0 || content.some(b => b?.type !== 'text')) return decision
      const text = content.map(b => b.text ?? '').join('\n')
      if (text.length < MIN) return decision
      const out = execFileSync(PY, [CLI, `--command=${commandOf(exec.arguments)}`, `--tool=${exec.name || ''}`,   // `=`: "-x" stays a value
        '--harness', 'dsh'], {
        input: text, maxBuffer: 64 << 20, timeout: 60_000,
        env: { ...process.env, CTXPRUNE_HARNESS: 'dsh' }, stdio: ['pipe', 'pipe', 'ignore'],
      }).toString('utf8')
      if (!out || (out.length >= text.length && !escapedOnly(out, text))) return decision
      return { ...decision, kind: 'accept', content: [{ type: 'text', text: out }] }
    } catch {
      return decision
    }
  })
}
