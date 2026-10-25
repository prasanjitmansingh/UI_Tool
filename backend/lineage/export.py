"""Export every platform's lineage graph (and the source files its evidence cites) as JSON.

The static demo page embeds this so it runs without the API:

    python -m lineage.export ../sample_data > demo.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .platforms import PLATFORMS


def export(data_dir: Path) -> dict:
    out = {"platforms": [], "data": {}}
    for p in PLATFORMS.values():
        root = data_dir / p.id
        g = p.build(root)
        files = sorted({ev["file"] for e in g.edges.values() for ev in e.evidence if ev.get("file")})
        out["platforms"].append({"id": p.id, "label": p.label, "target": p.target, "loader": p.loader,
                                 "configured": root.is_dir()})
        out["data"][p.id] = {
            "nodes": [n.to_dict() for n in g.nodes.values()],
            "edges": [e.to_dict() for e in g.edges.values()],
            "stats": g.stats(),
            "files": {f: (root / f).read_text(encoding="utf-8", errors="replace")
                      for f in files if (root / f).is_file()},
        }
    return out


if __name__ == "__main__":
    json.dump(export(Path(sys.argv[1] if len(sys.argv) > 1 else "../sample_data")), sys.stdout)
