/** Native, deterministic subset of ctxprune's lossless safe path.
 * Rules only fire when their inverse can recover the original text.
 */

const separators = /(\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029])/;
const invisible = "\\u00ad\\u034f\\u061c\\u115f\\u1160\\u17b4\\u17b5\\u180b-\\u180f\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u206f\\u3164\\ufe00-\\ufe0f\\ufeff\\uffa0\\ufff0-\\ufff8\\u{1bca0}-\\u{1bca3}\\u{1d173}-\\u{1d17a}\\u{e0000}-\\u{e0fff}";
const marker = String.raw`\[(?:/?ctxprune|/?cp(?=[:\]= ])|…)`;
const markerLine = new RegExp(`^([${invisible}]*)(\\\\*)(${marker})`, "iu");
const invisiblePrefix = new RegExp(`^[${invisible}]*`, "u");
const markerAtStart = new RegExp(`^${marker}`, "iu");

function eachLine(text: string, fn: (line: string, double: boolean) => string): string {
  if (!text.includes("[")) return text;
  const parts = text.split(separators);
  for (let i = 0; i < parts.length; i += 2) {
    if (parts[i].includes("[")) parts[i] = fn(parts[i], i > 0 && /^[\x85\u2028\u2029]$/.test(parts[i - 1]));
  }
  return parts.join("");
}

/** Escape forged ctxprune markers, matching Python lossless.escape_markers. */
export function escapeMarkers(text: string): string {
  return eachLine(text, (line, double) => line.replace(markerLine, (_m, lead, slashes, body) => lead + (double ? "\\\\" : "\\") + slashes + body));
}

/** Undo marker escaping; used by decode(). */
export function unescapeMarkers(text: string): string {
  return eachLine(text, (line, double) => {
    const lead = line.match(invisiblePrefix)?.[0] ?? "";
    let i = lead.length;
    while (line[i] === "\\") i++;
    if (i === lead.length || (double && i - lead.length < 2) || !markerAtStart.test(line.slice(i))) return line;
    return line.slice(0, i - 1) + line.slice(i);
  });
}

export interface LosslessResult { text: string; rules: string[]; charsBefore: number; charsAfter: number }

/** Current conservative safe rules: reversible marker escaping and JSON whitespace compaction. */
export function compress(text: string): LosslessResult {
  let output = escapeMarkers(text);
  const rules = output === text ? [] : ["esc"];
  if (!text.startsWith("\ufeff") && /^[\s]*[\[{]/.test(output)) {
    const compact = compactJson(output);
    if (compact !== output && compact.length < output.length) {
      output = compact;
      rules.push("json_compact");
    }
  }
  return { text: output, rules, charsBefore: text.length, charsAfter: output.length };
}

/** JSON stringify/parse compaction preserves JSON values, but is deliberately limited to ordinary JSON values. */
function compactJson(input: string): string {
  try {
    JSON.parse(input); // validation only; retain original key order, duplicate keys, escapes and numeric spellings
    let compact = "", inString = false, escaped = false;
    for (const ch of input) {
      if (inString) {
        compact += ch;
        if (escaped) escaped = false;
        else if (ch === "\\") escaped = true;
        else if (ch === '"') inString = false;
      } else if (ch === '"') { inString = true; compact += ch; }
      else if (!/[\t\n\r ]/.test(ch)) compact += ch;
    }
    return compact;
  } catch { return input; }
}

/** Expand this package's reversible encoding. One-way noise cleanup is intentionally not performed. */
export function decode(text: string): string { return unescapeMarkers(text); }

export interface OnnxOptions { modelPath: string; intraOpNumThreads?: number }
export interface OnnxLineScorer {
  score(inputIds: bigint[] | number[], attentionMask?: bigint[] | number[]): Promise<Float32Array>;
  close(): Promise<void>;
}

/** Load ONNX Runtime lazily so lossless-only consumers install no runtime dependency. */
export async function createOnnxLineScorer(options: OnnxOptions): Promise<OnnxLineScorer> {
  let ort: any;
  try { ort = await import("onnxruntime-node"); }
  catch { throw new Error("Install optional peer dependency onnxruntime-node to use ONNX scoring"); }
  const session = await ort.InferenceSession.create(options.modelPath, {
    executionProviders: ["cpu"],
    intraOpNumThreads: options.intraOpNumThreads ?? 1,
  });
  return {
    async score(inputIds, attentionMask = inputIds.map(() => 1)) {
      const ids = BigInt64Array.from(inputIds, BigInt);
      const mask = BigInt64Array.from(attentionMask, BigInt);
      const result = await session.run({
        input_ids: new ort.Tensor("int64", ids, [1, ids.length]),
        attention_mask: new ort.Tensor("int64", mask, [1, mask.length]),
      });
      const logits = result.logits.data as Float32Array;
      const n = logits.length / 2;
      const probs = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        const a = logits[i * 2], b = logits[i * 2 + 1];
        const max = Math.max(a, b), ea = Math.exp(a - max), eb = Math.exp(b - max);
        probs[i] = eb / (ea + eb);
      }
      return probs;
    },
    async close() { await session.release?.(); },
  };
}
