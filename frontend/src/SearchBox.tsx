import { useEffect, useRef, useState } from "react";
import { api, type GNode } from "./api";

export function SearchBox({ onPick }: { onPick: (n: GNode) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<GNode[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const timer = useRef<number>();

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!q.trim()) { setHits([]); return; }
    timer.current = window.setTimeout(() => {
      api.search(q).then((h) => { setHits(h); setActive(0); }).catch(() => setHits([]));
    }, 150);
    return () => window.clearTimeout(timer.current);
  }, [q]);

  const pick = (n: GNode) => { setQ(""); setOpen(false); onPick(n); };

  return (
    <div className="search">
      <input
        value={q}
        placeholder="Search tables, views, datasets, jobs…"
        aria-label="Search lineage objects"
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter" && hits[active]) pick(hits[active]);
          else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && q.trim() && (
        <ul className="hits" role="listbox">
          {hits.length === 0 && <li className="none">No matches</li>}
          {hits.map((h, i) => (
            <li key={h.id} role="option" aria-selected={i === active} className={i === active ? "active" : ""}
              onMouseDown={(e) => { e.preventDefault(); pick(h); }}>
              <span className={`dot t-${h.type}`} />
              <span className="hit-name">{h.name}</span>
              <span className="hit-type">{h.type}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
