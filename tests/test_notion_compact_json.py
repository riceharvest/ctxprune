"""Notion MCP compact JSON (own_research:notion-compact-json-zero): savings, exact round trip, no field lost.
Run: python3 -m pytest tests/test_notion_compact_json.py   (fixtures: tests/gen_notion_fixtures.py)"""
import glob, json, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
from lossless import compress, decode  # noqa: E402
from lossless2 import _est, _enc_jsondefs, encode, decode as dec2, JSON_RULES  # noqa: E402

FIX = sorted(glob.glob(os.path.join(HERE, "fixtures", "notion", "*.json")))


def leaves(o, out):
    """Every id / url / title / plain_text / content string the next tool call may read."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("id", "url", "public_url", "plain_text", "content", "name", "href", "database_id", "page_id",
                     "next_cursor", "request_id", "created_time", "last_edited_time") and isinstance(v, str):
                out.add(v)
            leaves(v, out)
    elif isinstance(o, list):
        for v in o:
            leaves(v, out)
    return out


@mock.patch.dict(os.environ, {"CTXPRUNE_JSONDEFS": "1"})   # jsondefs is opt-in in 0.5 (decision t_ae33ce38, Option A)
class Notion(unittest.TestCase):
    def test_default_off(self):          # default output has no jsondefs rule: byte-identical to 0.4.1
        with mock.patch.dict(os.environ, {"CTXPRUNE_JSONDEFS": ""}):
            for p in FIX:
                _, st = compress(open(p).read(), None)
                self.assertNotIn("l2_jsondefs", st["rules"], p)

    def test_fixtures_present(self):
        self.assertEqual({os.path.basename(p) for p in FIX},
                         {"search.json", "page.json", "block_children.json", "database_query.json"})

    def test_roundtrip_and_no_field_lost(self):
        for p in FIX:
            text = open(p).read()
            self.assertNotIn("\n", text)                      # compact, as the MCP server prints it
            out, st = compress(text, None)
            self.assertEqual(decode(out), text, p)
            want = leaves(json.loads(text), set())
            self.assertGreater(len(want), 5)
            # the compressed text itself (what the model reads) still holds every value, verbatim
            missing = [v for v in want if v not in out and json.dumps(v, ensure_ascii=False)[1:-1] not in out]
            self.assertEqual(missing, [], (p, missing[:3]))

    def test_savings_target(self):
        tb = ta = 0
        for p in FIX:
            text = open(p).read()
            out, _ = compress(text, None)
            tb, ta = tb + _est(text), ta + _est(out)
            if not p.endswith("page.json"):                   # the 1 KB single page has almost nothing repeated
                self.assertLess(_est(out), 0.85 * _est(text), p)
        self.assertLess(ta, 0.85 * tb)

    def test_search_mixed_records_fire_jsondefs(self):      # page + database in one list: no common key order, no rows
        out, st = compress(open(os.path.join(HERE, "fixtures", "notion", "search.json")).read(), None)
        self.assertEqual(st["rules"], ["l2_jsondefs"])

    def test_defs_is_exact_on_odd_input(self):
        ann = '{"bold":false,"italic":false,"strikethrough":false,"underline":false,"code":false,"color":"default"}'
        for t in (json.dumps({"a": [json.loads(ann)] * 5}, separators=(",", ":")),
                  '{"x":"@1@","a":' + ann + ',"b":' + ann + ',"c":' + ann + "}",       # token already in the text
                  '{"x":"[cp:rows #1]","a":' + ann + ',"b":' + ann + "}",
                  '{"a":' + ann + ',"b":' + ann + '}\n'):
            e, _ = encode(t, JSON_RULES)
            self.assertEqual(dec2(e), t)
        self.assertEqual(_enc_jsondefs('{"a":1}'), '{"a":1}')
        e, _ = encode('{"x":"@1@","a":' + ann + ',"b":' + ann + ',"c":' + ann + "}", JSON_RULES)
        self.assertNotIn("[cp:defs", e)                      # unsafe token -> left alone

    def test_never_larger_and_idempotent(self):
        for p in FIX:
            text = open(p).read()
            out, _ = compress(text, None)
            self.assertLessEqual(len(out), len(text))
            self.assertEqual(encode(out, JSON_RULES)[0], out)   # marked text is left alone


if __name__ == "__main__":
    unittest.main()
