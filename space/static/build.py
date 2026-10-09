"""Precompute real ctxprune and LLMLingua-2 outputs for the static demo page (HF static Spaces are free).

    python space/static/build.py   ->  space/static/index.html
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from app import EXAMPLES, ours, theirs  # noqa: E402

from ctxprune.align import align  # noqa: E402
from ctxprune.atoms import atomize, is_protected  # noqa: E402
from ctxprune.idcheck import survival  # noqa: E402

data = []
for name, text in EXAMPLES.items():
    atoms = atomize(text)
    ex = {"name": name, "atoms": [[a.ws, a.text, is_protected(a.text)] for a in atoms], "runs": {}}
    for rate in (0.5, 0.33):
        outs = {
            "ours": ours.compress(text, rate=rate)["text"],
            "ours_protect": ours.compress(text, rate=rate, force_protected=True)["text"],
            "theirs": theirs.compress_prompt(text, rate=rate, force_tokens=["\n", "?"])["compressed_prompt"],
        }
        # LLMLingua-2 again at the size of ctxprune's ID-safe output, so that toggle compares like for like
        target = len(outs["ours_protect"]) / max(1, len(text))
        tries = [theirs.compress_prompt(text, rate=r / 100, force_tokens=["\n", "?"])["compressed_prompt"]
                 for r in range(20, 100, 2)]
        outs["theirs_matched"] = min(tries, key=lambda o: abs(len(o) / max(1, len(text)) - target))
        for key, out in outs.items():
            s = survival(text, out)
            want = {a.text for a in atoms if is_protected(a.text)}
            have = {a.text for a in atomize(out)}
            ex["runs"][f"{key}@{rate}"] = {
                "keep": align(atoms, out).keep, "lost": sorted(want - have), "out": out,
                "ratio": s["char_ratio"], "ids": [s["protected"] - s["lost"], s["protected"]], "json": s.get("json"),
            }
    data.append(ex)

html = (HERE / "template.html").read_text().replace("__DATA__", json.dumps(data))
(HERE / "index.html").write_text(html)
print("wrote", HERE / "index.html", len(html) // 1024, "KB")
