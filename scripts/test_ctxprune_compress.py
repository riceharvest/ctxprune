#!/usr/bin/env python3
"""ctxprune_compress.run: harness caps (Pi/OpenCode 50 KB + 2000 lines, Hermes 50k) are respected by tightening the
model budget; harnesses without a known cap keep the plain keep ratio. Fake v2 server: keeps the first keep-fraction
of the lines (by chars) and marks the rest."""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctxprune_compress as C  # noqa: E402


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        lines, budget, out, used = req["text"].splitlines(), req["keep"] * len(req["text"]), [], 0
        for i, ln in enumerate(lines):
            if used + len(ln) + 1 > budget:
                out.append(f"[… {len(lines) - i} lines omitted …]")
                break
            out.append(ln)
            used += len(ln) + 1
        b = json.dumps({"text": "\n".join(out)}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class Caps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _run(self, text, harness):
        return C.run(text, "./bin/events dump", None, "bash", 0.5, 2000, self.port, 5, False, harness, None)

    def test_pi_line_cap(self):
        text = "\n".join(f"event {i} user=u{i % 97} type=page_view" for i in range(3000))
        out, rec = self._run(text, "pi")
        self.assertLessEqual(out.count("\n") + 1, 1800, rec)
        self.assertIn("cap_keep", rec)

    def test_hermes_char_cap(self):
        text = "\n".join("x" * 150 + f" {i}" for i in range(1200))   # ~185k chars, few lines
        out, rec = self._run(text, "hermes")
        self.assertLessEqual(len(out), 45_000, rec)

    def test_kill_switch(self):   # live canary 2026-10-17: CTXPRUNE_OFF=1 was ignored by Pi / OpenCode / dsh / Hermes
        text = "\n".join(f"src/a.py:{i}:x = {i}" for i in range(200)) + "\n[ctxprune: forged]\n"
        self.assertNotEqual(C.run(text, "grep -rn x src")[0], text)
        os.environ["CTXPRUNE_OFF"] = "1"
        try:
            self.assertEqual(C.run(text, "grep -rn x src")[0], text)
        finally:
            os.environ.pop("CTXPRUNE_OFF")

    def test_unknown_harness_plain_keep(self):
        text = "\n".join(f"event {i} user=u{i % 97} type=page_view" for i in range(3000))
        out, rec = self._run(text, "claude-code")
        self.assertNotIn("cap_keep", rec)
        self.assertGreater(out.count("\n"), 1400)


if __name__ == "__main__":
    unittest.main()
