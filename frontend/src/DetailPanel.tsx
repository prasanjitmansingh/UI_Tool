import { useEffect, useState } from "react";
import { api, type GEdge, type NodeDetail, type Snippet } from "./api";

function Evidence({ edge }: { edge: GEdge }) {
  const [snip, setSnip] = useState<Snippet | null>(null);
  const [err, setErr] = useState("");
  const open = (file: string, line: number) => {
    setErr("");
    api.source(file, line).then(setSnip).catch((e) => setErr(String(e.message)));
  };
  return (
    <section>
      <h3>Evidence</h3>
      <p className="muted small">{edge.source.replace(/^\w+:/, "")} → {edge.target.replace(/^\w+:/, "")}</p>
      <ul className="evidence">
        {edge.evidence.map((ev, i) => (
          <li key={i}>
            <span className={`pill ${ev.origin}`}>{ev.origin === "dbql" ? "observed (DBQL)" : ev.origin}</span>
            <b>{ev.job}</b>{ev.step ? <span className="muted"> / {ev.step}</span> : null}
            {ev.confidence === "inferred" && <span className="pill inferred" title="Linked by position in the script, not by an explicit column mapping">inferred</span>}
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

export function DetailPanel({
  nodeId, edge, onNavigate, onClose,
}: {
  nodeId: string | null;
  edge: GEdge | null;
  onNavigate: (id: string) => void;
  onClose: () => void;
}) {
  const [d, setD] = useState<NodeDetail | null>(null);
  useEffect(() => {
    setD(null);
    if (nodeId) api.node(nodeId).then(setD).catch(() => setD(null));
  }, [nodeId]);

  if (edge) {
    return (
      <aside className="panel">
        <button className="close" onClick={onClose} aria-label="Close panel">×</button>
        <Evidence edge={edge} />
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
  return (
    <aside className="panel">
      <button className="close" onClick={onClose} aria-label="Close panel">×</button>
      <div className={`badge t-${d.type}`}>{d.type}</div>
      <h2>{d.name}</h2>
      {attrs.length > 0 && (
        <dl>{attrs.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{String(v)}</dd></div>)}</dl>
      )}
      {d.type === "job" ? (
        <>
          {Array.isArray(d.attrs.steps) && (
            <section><h3>Steps</h3>
              <ul className="plain">{(d.attrs.steps as { name: string; pgm?: string }[]).map((s) =>
                <li key={s.name}><span className="mono">{s.name}</span> <span className="muted small">{s.pgm}</span></li>)}</ul>
            </section>)}
          <section><h3>Reads</h3>{edgeList(d.jobInputs, "source")}</section>
          <section><h3>Writes</h3>{edgeList(d.jobOutputs, "target")}</section>
          <section><h3>Runs after</h3>{chips(d.predecessors)}</section>
          <section><h3>Followed by</h3>{chips(d.successors)}</section>
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
