// ctxprune in the browser: a line-for-line port of ctxprune/atoms.py + compress.py (ONNX path).
// Needs a transformers.js tokenizer + token-classification model; nothing leaves the page.

const CJK = '぀-ヿ㐀-䶿一-鿿豈-﫿';
const W = `(?:(?![${CJK}])[\\p{L}\\p{N}_])`;
const WORD = `(?:--?|[/~.$@#])?${W}(?:(?:${W}|[.:/@%+#~=?&'\\-])*${W})?`;
const ATOM_RE = new RegExp(`(\\s*)(${WORD}|[${CJK}]|[^\\s])`, 'gu');
const CAPS = new Set(('THE AND FOR ANY NOT BUT ARE WAS YOU ALL WITH FROM THIS THAT WITHOUT WARRANTIES WARRANTY SOFTWARE KIND ' +
  'IMPLIED INCLUDING LIMITED COPYRIGHT OTHER LIABILITY USE BASIS CONDITIONS SUCH EVENT SHALL HOLDERS ' +
  'CONTRIBUTORS DAMAGES PURPOSE PARTICULAR FITNESS MERCHANTABILITY EXPRESS OUT CONNECTION ARISING ' +
  'WHETHER CONTRACT TORT OTHERWISE PROVIDED LICENSE NOTICE NOTE IMPORTANT').split(' '));

export function atomize(text) {
  const atoms = [];
  let pos = 0, m;
  ATOM_RE.lastIndex = 0;
  while ((m = ATOM_RE.exec(text))) {
    if (m.index !== pos) throw new Error('atomizer skipped text');
    atoms.push({ ws: m[1], text: m[2] });
    pos = m.index + m[0].length;
  }
  const tail = text.slice(pos);
  if (tail) atoms.push({ ws: tail, text: '' });
  return atoms;
}

const mergeWs = (a, b) => (a.includes('\n') || b.includes('\n')) ? '\n' : ((a || b) ? ' ' : '');

export function detok(atoms, keep) {
  const out = [];
  let pending = '';
  atoms.forEach((a, i) => {
    if (keep[i]) {
      let ws = a.ws;
      if (pending) ws = mergeWs(pending, a.ws);
      if (!out.length) ws = '';
      out.push(ws + a.text);
      pending = '';
    } else {
      pending = pending ? mergeWs(pending, a.ws) : (a.ws || ' ');
    }
  });
  return out.join('');
}

export function isProtected(atom) {
  if (!atom || !/[\p{L}\p{N}]/u.test(atom)) return false;
  if (/\p{Nd}/u.test(atom)) return true;
  const core = atom.replace(/^[-/~.$@#]+/, '');
  if (/[_/.:@=]/.test(core)) return true;
  if (/^[0-9a-fA-F]{7,}$/.test(core)) return true;
  const upper = core === core.toUpperCase() && core !== core.toLowerCase();
  if ([...core].length >= 3 && upper && !CAPS.has(core)) return true;
  if (/[a-z][A-Z]|[A-Z]{2}[a-z]/.test(core)) return true;
  return atom !== core;
}

// Tokenize exactly like the Python `tokenizers` library does for this tokenizer (Gemma vocab,
// Metaspace with split=true, byte fallback), with UTF-16 offsets. transformers.js alone ignores
// split=true, which changes ids around runs of spaces.
export function makeEncoder(tok) {
  const t = tok._tokenizer;
  const json = tok._tokenizerJSON;
  const vocab = json.model.vocab;
  const added = json.added_tokens.map(a => a.content).sort((a, b) => b.length - a.length);
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const addedRe = new RegExp(added.map(esc).join('|'), 'g');
  const addedId = Object.fromEntries(json.added_tokens.map(a => [a.content, a.id]));
  const byteRe = /^<0x([0-9A-F]{2})>$/;
  const dec = new TextDecoder('utf-8', { fatal: true });

  function segment(text, base, ids, offs) {
    if (!text) return;
    let norm = text.replace(/ /g, '▁');
    let shift = 0;
    if (!norm.startsWith('▁')) { norm = '▁' + norm; shift = 1; }
    const from = offs.length;
    let start = 0;
    for (let i = 1; i <= norm.length; i++) {
      if (i < norm.length && norm[i] !== '▁') continue;
      const piece = norm.slice(start, i);
      let pos = start, bytes = [], byteStart = null, pend = [];
      for (const s of t.model.encode([piece])) {
        ids.push(vocab[s]);
        const m = byteRe.exec(s);
        if (!m) { offs.push([pos, pos + s.length]); pos += s.length; continue; }
        if (byteStart === null) byteStart = pos;
        bytes.push(parseInt(m[1], 16));
        pend.push(offs.length); offs.push(null);
        let ch = null;
        try { ch = dec.decode(new Uint8Array(bytes)); } catch (e) { /* incomplete char */ }
        if (ch !== null) {
          for (const k of pend) offs[k] = [byteStart, byteStart + ch.length];
          pos = byteStart + ch.length; bytes = []; byteStart = null; pend = [];
        }
      }
      start = i;
    }
    for (let k = from; k < offs.length; k++) {
      let [s, e] = offs[k] || [0, 0];
      s = Math.max(0, s - shift) + base; e = Math.max(0, e - shift) + base;
      if (e === s && s < base + text.length) e = s + 1; // zero-width tokens borrow the next char, like Python
      offs[k] = [s, e];
    }
  }

  return function encode(text) {
    const ids = [], offs = [];
    let last = 0, m;
    addedRe.lastIndex = 0;
    while ((m = addedRe.exec(text))) {
      segment(text.slice(last, m.index), last, ids, offs);
      ids.push(addedId[m[0]]); offs.push([m.index, m.index + m[0].length]);
      last = m.index + m[0].length;
    }
    segment(text.slice(last), last, ids, offs);
    return { ids, offs };
  };
}

export class Compressor {
  // model: transformers.js PreTrainedModel (token classification); Tensor: transformers.js Tensor class
  constructor(tok, model, Tensor, { maxLen = 512, batchSize = 8 } = {}) {
    this.tok = tok; this.model = model; this.Tensor = Tensor;
    this.encode = makeEncoder(tok);
    this.maxLen = maxLen; this.batchSize = batchSize;
    this.bos = tok.bos_token_id ?? 2; this.eos = tok.eos_token_id ?? 1; this.pad = tok.pad_token_id ?? 0;
  }

  async atomScores(text, onProgress) {
    const atoms = atomize(text);
    let full = '';
    const spans = atoms.map(a => { full += a.ws; const s = full.length; full += a.text; return [s, full.length]; });
    const owner = new Int32Array(full.length).fill(-1);
    spans.forEach(([s, e], i) => { for (let c = s; c < e; c++) owner[c] = i; });
    const { ids, offs } = this.encode(full);
    const tokAtom = offs.map(([s, e]) => { for (let c = s; c < Math.min(e, owner.length); c++) if (owner[c] >= 0) return owner[c]; return -1; });
    const tokSets = offs.map(([s, e]) => {
      const seen = [];
      for (let c = s; c < Math.min(e, owner.length); c++) { const a = owner[c]; if (a >= 0 && seen[seen.length - 1] !== a) seen.push(a); }
      return seen;
    });
    const win = this.maxLen - 2;
    const windows = [];
    let start = 0;
    while (start < ids.length) {
      let end = Math.min(start + win, ids.length);
      if (end < ids.length) {
        let cut = end;
        while (cut > start + 1 && tokAtom[cut] === tokAtom[cut - 1]) cut--;
        end = cut > start + 1 ? cut : end;
      }
      windows.push([start, end]);
      start = end;
    }
    const probs = new Float32Array(ids.length);
    for (let b = 0; b < windows.length; b += this.batchSize) {
      const batch = windows.slice(b, b + this.batchSize);
      const seqs = batch.map(([s, e]) => [this.bos, ...ids.slice(s, e), this.eos]);
      const width = Math.max(...seqs.map(q => q.length));
      const idData = new BigInt64Array(seqs.length * width);
      const maskData = new BigInt64Array(seqs.length * width);
      seqs.forEach((q, r) => {
        for (let j = 0; j < width; j++) {
          idData[r * width + j] = BigInt(j < q.length ? q[j] : this.pad);
          maskData[r * width + j] = j < q.length ? 1n : 0n;
        }
      });
      const out = await this.model({
        input_ids: new this.Tensor('int64', idData, [seqs.length, width]),
        attention_mask: new this.Tensor('int64', maskData, [seqs.length, width]),
      });
      const L = out.logits.data; // [B, width, 2]
      batch.forEach(([s, e], r) => {
        for (let j = 0; j < e - s; j++) {
          const k = (r * width + j + 1) * 2;
          const a = L[k], c = L[k + 1], mx = Math.max(a, c);
          const ea = Math.exp(a - mx), ec = Math.exp(c - mx);
          probs[s + j] = ec / (ea + ec);
        }
      });
      if (onProgress) onProgress(Math.min(1, (b + batch.length) / windows.length));
    }
    const sums = new Float64Array(atoms.length), hits = new Int32Array(atoms.length);
    tokSets.forEach((set, i) => { for (const a of set) { sums[a] += probs[i]; hits[a]++; } });
    const scores = Array.from(sums, (s, i) => hits[i] ? s / hits[i] : 0);
    return { atoms, scores };
  }

  select(atoms, scores, rate, protect = false) {
    const len = s => [...s].length;
    const counts = atoms.map(a => len(a.text) + (a.ws ? 1 : 0));
    const keep = atoms.map(a => !!a.text && protect && isProtected(a.text));
    const total = counts.reduce((x, y) => x + y, 0);
    const budget = Math.ceil(rate * total);
    let used = 0;
    keep.forEach((k, i) => { if (k) used += counts[i]; });
    const order = [...atoms.keys()].sort((i, j) => scores[j] - scores[i] || i - j);
    for (const i of order) {
      if (used >= budget) break;
      if (!keep[i] && atoms[i].text) { keep[i] = true; used += counts[i]; }
    }
    return { text: detok(atoms, keep), keep, kept: used, total };
  }

  async compress(text, rate = 0.5, protect = false) {
    const { atoms, scores } = await this.atomScores(text);
    return this.select(atoms, scores, rate, protect);
  }
}
