import { useEffect, useState } from "react";
import { api, type GEdge, type Impact, type NodeDetail, type PlatformId, type Snippet } from "./api";
import type { Terms } from "./terms";

const ORIGIN_LABEL: Record<string, string> = {
  dbql: "observed (DBQL)", jcl: "JCL / script", scheduler: "scheduler", abinitio: "Ab Initio graph", metastore: "view definition",
};

function Evidence({ platform, edge }: { platform: PlatformId; edge: GEdge }) {
  const [snip, setSnip] = useState<Snippet | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setSnip(null); setErr(""); }, [edge.id]);
  const open = (file: string, line: number) => {
    setErr("");
    api.source(platform, file, line).then(setSnip).catch((e) => setErr(String(e.message)));
  };
  return (
    <section>
      <h3>Evidence</h3>
      <p className="muted small">{edge.source.replace(/^\w+:/, "")} → {edge.target.replace(/^\w+:/, "")}</p>
      <ul className="evidence">
        {edge.evidence.map((ev, i) => (
          <li key={i}>
            <span className={`pill ${ev.origin}`}>{ORIGIN_LABEL[ev.origin] ?? ev.origin}</span>
            <b>{ev.job}</b>{ev.step ? <span className="muted"> / {ev.step}</span> : null}
            {ev.confidence === "inferred" && <span className="pill inferred" title="Linked by position in the script, not by an explicit mapping">inferred</span>}
            {ev.executions ? <span className="muted"> · {ev.executions}× run</span> : null}
            <div>
              {ev.line && ev.origin !== "scheduler" ? (
                <button className="link mono" onClick={() => open(ev.file, ev.line!)}>{ev.file}:{ev.line}</button>
              ) : (
                <span className="mono muted">{ev.file}</span>
              )}
            </div>
          </li>
        ))}
      </ul>
      {err && <p className="error small">{err}</p>}
      {snip && (
        <pre className="snippet" aria-label={`${snip.file} excerpt`}>
          {snip.lines.map((l) => (
            <div key={l.n} className={l.n === snip.line ? "hl" : ""}>
              <span className="ln">{l.n}</span>{l.text || " "}
            </div>
          ))}
        </pre>
      )}
    </section>
  );
}

function summary(d: NodeDetail, imp: Impact, terms: Terms, platformLabel: string): string {
  const lines = [
    `[${platformLabel}] ${terms.node[d.type]} ${d.name}`,
    d.type !== "job" ? `Loaded by: ${d.loadedBy.join(", ") || "none found"}` : `Writes: ${d.jobOutputs.map((e) => e.target.replace(/^\w+:/, "")).join(", ") || "none found"}`,
    `Downstream objects affected (${imp.objects.length}):`,
    ...imp.objects.map((o) => `  - ${o.name} (${terms.node[o.type]}, ${o.distance} hop${o.distance === 1 ? "" : "s"})`),
    `${terms.jobs} affected (${imp.jobs.length}):`,
    ...imp.jobs.map((j) => `  - ${j.name} — ${j.reason}`),
  ];
  return lines.join("\n");
}

function ImpactSection({ d, terms, platformLabel, onNavigate }: {
  d: NodeDetail; terms: Terms; platformLabel: string; onNavigate: (id: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [manual, setManual] = useState<string | null>(null);
  useEffect(() => { setManual(null); }, [d.id]);
  const imp = d.impact;
  if (!imp) return null;
  const copy = async () => {
    const text = summary(d, imp, terms, platformLabel);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setManual(text); // clipboard refused: show the text selected so it can be copied by hand
    }
  };
  return (
    <section className="impact">
      <div className="impact-head">
        <h3>Downstream impact</h3>
        <button className="ghost small" onClick={copy}>{copied ? "Copied" : "Copy summary"}</button>
      </div>
      <div className="kpis">
        <div><b>{imp.objects.filter((o) => o.type !== "dataset").length}</b><span>tables / views</span></div>
        <div><b>{imp.objects.filter((o) => o.type === "dataset").length}</b><span>{terms.node.dataset.toLowerCase()}s</span></div>
        <div><b>{imp.jobs.length}</b><span>{terms.jobs.toLowerCase()}</span></div>
      </div>
      {manual !== null && (
        <textarea id="impact-summary" className="manual-copy" readOnly value={manual} rows={6}
          aria-label="Impact summary" ref={(el) => el?.select()} />
      )}
      {imp.objects.length === 0 && imp.jobs.length === 0 && <p className="muted small">Nothing downstream — no dependants found.</p>}
      {imp.objects.length > 0 && (
        <ul className="plain">{imp.objects.map((o) => (
          <li key={o.id}>
            <span className={`dot t-${o.type}`} /> <button className="link" onClick={() => onNavigate(o.id)}>{o.name}</button>
            <span className="muted small"> · {o.distance} hop{o.distance === 1 ? "" : "s"}</span>
          </li>))}
        </ul>
      )}
      {imp.jobs.length > 0 && (
        <div className="chips" style={{ marginTop: 8 }}>
          {imp.jobs.map((j) => <button key={j.id} className="chip" title={j.reason} onClick={() => onNavigate(j.id)}>{j.name}</button>)}
        </div>
      )}
    </section>
  );
}

export function DetailPanel({
  platform, platformLabel, terms, nodeId, edge, onNavigate, onClose,
}: {
  platform: PlatformId;
  platformLabel: string;
  terms: Terms;
  nodeId: string | null;
  edge: GEdge | null;
  onNavigate: (id: string) => void;
  onClose: () => void;
}) {
  const [d, setD] = useState<NodeDetail | null>(null);
  useEffect(() => {
    setD(null);
    if (nodeId) api.node(platform, nodeId).then(setD).catch(() => setD(null));
  }, [platform, nodeId]);

  if (edge) {
    return (
      <aside className="panel">
        <button className="close" onClick={onClose} aria-label="Close panel">×</button>
        <Evidence platform={platform} edge={edge} />
      </aside>
    );
  }
  if (!nodeId || !d) return null;
  const chips = (ids: string[]) => ids.length
    ? <div className="chips">{ids.map((j) => <button key={j} className="chip" onClick={() => onNavigate(j.startsWith("job:") ? j : `job:${j}`)}>{j.replace(/^job:/, "")}</button>)}</div>
    : <p className="muted small">None found</p>;
  const edgeList = (es: GEdge[], end: "source" | "target") => es.length
    ? <ul className="plain">{es.map((e) => (
        <li key={e.id}>
          <button className="link" onClick={() => onNavigate(e[end])}>{e[end].replace(/^\w+:/, "")}</button>
          <span className="muted small"> via {e.jobs.join(", ") || "—"}</span>
        </li>))}</ul>
    : <p className="muted small">None found</p>;
  const attrs = Object.entries(d.attrs).filter(([k, v]) => v !== undefined && v !== null && k !== "steps" && typeof v !== "object");
  const isJob = d.type === "job";
  return (
    <aside className="panel">
      <button className="close" onClick={onClose} aria-label="Close panel">×</button>
      <div className={`badge t-${d.type}`}>{terms.node[d.type]}</div>
      <h2>{d.name}</h2>
      {attrs.length > 0 && (
        <dl>{attrs.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{String(v)}</dd></div>)}</dl>
      )}
      <ImpactSection d={d} terms={terms} platformLabel={platformLabel} onNavigate={onNavigate} />
      {isJob ? (
        <>
          <section><h3>Reads</h3>{edgeList(d.jobInputs, "source")}</section>
          <section><h3>Writes</h3>{edgeList(d.jobOutputs, "target")}</section>
          <section><h3>Runs after</h3>{chips(d.predecessors)}</section>
          <section><h3>Followed by</h3>{chips(d.successors)}</section>
          {Array.isArray(d.attrs.steps) && (
            <section><h3>{platform === "abinitio" ? "Components" : "Steps"}</h3>
              <ul className="plain">{(d.attrs.steps as { name: string; pgm?: string }[]).map((s) =>
                <li key={s.name}><span className="mono">{s.name}</span> <span className="muted small">{s.pgm}</span></li>)}</ul>
            </section>)}
        </>
      ) : (
        <>
          <section><h3>Loaded by</h3>{chips(d.loadedBy)}</section>
          <section><h3>Read by</h3>{chips(d.readBy)}</section>
          <section><h3>Direct sources</h3>{edgeList(d.upstream, "source")}</section>
          <section><h3>Direct targets</h3>{edgeList(d.downstream, "target")}</section>
        </>
      )}
    </aside>
  );
}
