import { useEffect, useRef, useState } from "react";
import { api, type GNode, type PlatformId } from "./api";
import type { Terms } from "./terms";

type Scope = "tables" | "jobs" | "datasets" | "all";
const SCOPE_TYPES: Record<Scope, string | undefined> = {
  tables: "table,view", jobs: "job", datasets: "dataset", all: undefined,
};

/** Table search for one platform. With an empty query it lists tables to browse. */
export function SearchBox({ platform, terms, onPick }: { platform: PlatformId; terms: Terms; onPick: (n: GNode) => void }) {
  const [q, setQ] = useState("");
  const [scope, setScope] = useState<Scope>("tables");
  const [hits, setHits] = useState<GNode[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const timer = useRef<number>();

  useEffect(() => { setQ(""); setHits([]); setOpen(false); }, [platform]);

  useEffect(() => {
    if (!open) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api.search(platform, q, SCOPE_TYPES[scope]).then((h) => { setHits(h); setActive(0); }).catch(() => setHits([]));
    }, 150);
    return () => window.clearTimeout(timer.current);
  }, [q, scope, platform, open]);

  const pick = (n: GNode) => { setQ(""); setOpen(false); onPick(n); };
  const scopeLabel: Record<Scope, string> = {
    tables: "Tables", jobs: terms.jobs, datasets: terms.node.dataset + "s", all: "All",
  };

  return (
    <div className="search" onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
      <div className="search-row">
        <input
          value={q}
          placeholder={scope === "tables" ? terms.searchHint : `Search ${scopeLabel[scope].toLowerCase()}…`}
          aria-label="Search table"
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, hits.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Enter" && hits[active]) pick(hits[active]);
            else if (e.key === "Escape") setOpen(false);
          }}
        />
        <select value={scope} aria-label="Search in" onChange={(e) => { setScope(e.target.value as Scope); setOpen(true); }}>
          {(Object.keys(scopeLabel) as Scope[]).map((s) => <option key={s} value={s}>{scopeLabel[s]}</option>)}
        </select>
      </div>
      {open && (
        <ul className="hits" role="listbox">
          {!q.trim() && hits.length > 0 && <li className="none">Browse {scopeLabel[scope].toLowerCase()} — type to filter</li>}
          {hits.length === 0 && <li className="none">{q.trim() ? "No matches on this platform" : "Nothing to browse"}</li>}
          {hits.map((h, i) => (
            <li key={h.id} role="option" aria-selected={i === active} className={i === active ? "active" : ""}
              onMouseDown={(e) => { e.preventDefault(); pick(h); }}>
              <span className={`dot t-${h.type}`} />
              <span className="hit-name">{h.name}</span>
              <span className="hit-type">{terms.node[h.type]}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
