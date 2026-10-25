"""Build the self-contained demo page: the built UI with lineage data embedded.

    cd frontend && npm run build && cd .. && python scripts/build_demo.py sample_data demo/lineage-explorer.html

The output is an HTML fragment (no <html>/<head>/<body>) as the Artifact host expects;
it also opens fine as a local file in a browser.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from lineage.export import export  # noqa: E402

FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
         "&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap")


def main(data_dir: str, out: str) -> None:
    dist = ROOT / "frontend" / "dist"
    html = (dist / "index.html").read_text()
    js_path = re.search(r'<script[^>]+src="/?(assets/[^"]+\.js)"', html).group(1)
    css_path = re.search(r'<link[^>]+href="/?(assets/[^"]+\.css)"', html).group(1)
    js = (dist / js_path).read_text().replace("</script", "<\\/script")
    css = (dist / css_path).read_text()
    data = json.dumps(export(Path(data_dir)), separators=(",", ":")).replace("</", "<\\/")
    page = f"""<title>Lineage Explorer</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="{FONTS}">
<style>{css}</style>
<div id="root"></div>
<script>window.__LINEAGE_DATA__ = {data};</script>
<script type="module">{js}</script>
"""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(page)
    print(f"wrote {out} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "sample_data"),
         sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "demo" / "lineage-explorer.html"))
