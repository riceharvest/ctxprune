import base64
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

import ctxprune  # noqa: E402
from ctxprune import cli  # noqa: E402
from ctxprune.notebook import extract_code_cells, render_notebook, unescape_notebook_diff  # noqa: E402


class NotebookTests(unittest.TestCase):
    def test_renders_cells_outputs_and_stubs_png(self):
        code = "x = 1\nprint(x)\n"
        nb = {
            "cells": [
                {"cell_type": "markdown", "source": ["# Hello\n", "world"]},
                {
                    "cell_type": "code",
                    "source": code,
                    "outputs": [
                        {"text": "ok\n"},
                        {"data": {"image/png": base64.b64encode(b"x" * 49152).decode()}},
                    ],
                },
                {"cell_type": "raw", "source": "raw text"},
            ]
        }
        result = render_notebook(nb)
        self.assertIn("# %% [markdown]\n# Hello\nworld", result)
        self.assertIn("# %%\n" + code, result)
        self.assertIn("# %% output\nok\n", result)
        self.assertIn("# [image/png 48KB elided]", result)
        self.assertIn("# %% [raw]\nraw text", result)
        self.assertEqual(extract_code_cells(nb), [code])
        self.assertEqual(extract_code_cells(result), [code])

    def test_caps_stream_text_and_lines(self):
        result = render_notebook(
            {"cells": [{"cell_type": "code", "source": "pass\n", "outputs": [{"text": "x" * 30}]}]},
            max_output_chars=10,
        )
        self.assertIn("x" * 10 + "\n[output truncated; 30 chars total]", result)

        lines_20 = "".join(f"row {i}\n" for i in range(20))
        result_lines = render_notebook(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": "for i in range(20): print(i)",
                        "outputs": [
                            {"output_type": "stream", "name": "stdout", "text": lines_20[:40]},
                            {"output_type": "stream", "name": "stdout", "text": lines_20[40:]},
                        ],
                    }
                ]
            },
            max_output_lines=12,
        )
        self.assertIn("row 0\n", result_lines)
        self.assertIn("row 7\n", result_lines)
        self.assertIn("[... 8 lines omitted (20 lines total)]", result_lines)
        self.assertIn("row 19\n", result_lines)
        self.assertNotIn("row 10\n", result_lines)

    def test_exact_code_cell_fidelity_with_edge_cases(self):
        cells_src = [
            "import numpy as np\nx = np.arange(10)",
            "def f(a):\n    return a + 1\n",
            "",
            "print('done')\n\n",
        ]
        nb = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "cells": [
                {"cell_type": "markdown", "source": ["Intro with fake header:\n", "# %%\n", "# %% output\n"]},
                {"cell_type": "code", "source": cells_src[0], "outputs": [{"output_type": "stream", "name": "stdout", "text": ["# %%\n", "hi\n"]}]},
                {"cell_type": "code", "source": cells_src[1], "outputs": [{"output_type": "error", "ename": "ValueError", "evalue": "bad value"}]},
                {"cell_type": "code", "source": cells_src[2], "outputs": []},
                {
                    "cell_type": "code",
                    "source": cells_src[3],
                    "outputs": [
                        {
                            "output_type": "display_data",
                            "data": {
                                "application/vnd.jupyter.widget-view+json": {"model_id": "abc", "version_major": 2},
                                "text/html": ["<div>" + "x" * 2048 + "</div>"],
                                "text/plain": ["Widget()"],
                            },
                        }
                    ],
                },
            ],
        }
        rendered = render_notebook(json.dumps(nb))
        self.assertEqual(extract_code_cells(nb), cells_src)
        self.assertEqual(extract_code_cells(rendered), cells_src)
        self.assertIn("# %% error\nValueError: bad value", rendered)
        self.assertIn("# [text/html 2KB elided]", rendered)
        self.assertIn("# [application/vnd.jupyter.widget-view+json ", rendered)

    def test_unescapes_only_notebook_diff_json_strings_and_folds_counters(self):
        diff = (
            "diff --git a/demo.ipynb b/demo.ipynb\n"
            "--- a/demo.ipynb\n"
            "+++ b/demo.ipynb\n"
            "@@ -1,4 +1,4 @@\n"
            '-   "execution_count": 1,\n'
            '+   "execution_count": 2,\n'
            '-    "print(\\"old\\")\\n",\n'
            '+    "print(\\"new\\")\\n",\n'
            "@@ -10,3 +10,3 @@\n"
            '   "cell_type": "code",\n'
            '-  "execution_count": 3,\n'
            '+  "execution_count": 4,\n'
            '   "metadata": {},\n'
            "diff --git a/demo.py b/demo.py\n"
            "@@ -1 +1 @@\n"
            '-"keep\\n"\n'
        )
        result = unescape_notebook_diff(diff)
        self.assertIn('-print("old")\n', result)
        self.assertIn('+print("new")\n', result)
        self.assertIn("~|execution_count -1 +1\n", result)
        self.assertNotIn('"cell_type": "code"', result)
        self.assertIn('-"keep\\n"\n', result)

    def test_accepts_json_and_cli_roundtrip(self):
        raw_json = json.dumps({"cells": [{"cell_type": "code", "source": ["print(1)", "\n"]}]})
        self.assertIn("# %%\nprint(1)\n", ctxprune.render_notebook(raw_json))
        with tempfile.NamedTemporaryFile("w", suffix=".ipynb", delete=False) as tf:
            tf.write(raw_json)
            path = tf.name
        try:
            buf = io.StringIO()
            with mock.patch.object(sys, "stdout", buf):
                self.assertEqual(cli.main(["notebook", path]), 0)
            self.assertEqual(extract_code_cells(buf.getvalue()), ["print(1)\n"])
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
