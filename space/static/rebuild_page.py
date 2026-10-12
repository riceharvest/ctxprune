"""Re-render index.html from template.html, reusing the precomputed data already in index.html.

    python space/static/rebuild_page.py     (build.py recomputes that data and needs llmlingua)
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
old = (HERE / "index.html").read_text()
start = old.index("window.DATA = ") + len("window.DATA = ")
data = old[start:old.index("; const DATA = window.DATA;", start)]
json.loads(data)
(HERE / "index.html").write_text((HERE / "template.html").read_text().replace("__DATA__", data))
print("wrote", HERE / "index.html")
