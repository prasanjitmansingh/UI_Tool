import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, type Direction, type GEdge, type GraphResponse, type Mode, type Stats } from "./api";
import { toFlow, type LineageNodeData } from "./layout";
import { LineageNode } from "./LineageNode";
import { SearchBox } from "./SearchBox";
import { DetailPanel } from "./DetailPanel";

const nodeTypes = { lineage: LineageNode };
const MINI: Record<string, string> = { table: "#2f6fed", view: "#8a4fd6", dataset: "#c77700", job: "#3f8a5f" };

function useHashState() {
  const read = () => {
    const p = new URLSearchParams(location.hash.slice(1));
    return {
      node: p.get("node") || null,
      mode: (p.get("mode") as Mode) || "table",
      dir: (p.get("dir") as Direction) || "both",
      depth: Number(p.get("depth")) || 3,
    };
  };
  const [state, setState] = useState(read);
  useEffect(() => {
    const on = () => setState(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const set = useCallback((patch: Partial<ReturnType<typeof read>>) => {
    const next = { ...read(), ...patch };
    const p = new URLSearchParams();
    if (next.node) p.set("node", next.node);
    p.set("mode", next.mode); p.set("dir", next.dir); p.set("depth", String(next.depth));
    location.hash = p.toString();
  }, []);
  return [state, set] as const;
}

function Canvas() {
  const [view, setView] = useHashState();
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [flow, setFlow] = useState<{ nodes: Node<LineageNodeData>[]; edges: Edge[] }>({ nodes: [], edges: [] });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selNode, setSelNode] = useState<string | null>(null);
  const [selEdge, setSelEdge] = useState<GEdge | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [showWarn, setShowWarn] = useState(false);
  const rf = useReactFlow();

  const refreshStats = useCallback(() => api.stats().then(setStats).catch(() => undefined), []);
  useEffect(() => { refreshStats(); }, [refreshStats]);

  const expand = useCallback((id: string, dir: "up" | "down") => setView({ node: id, dir, depth: 3 }), [setView]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    api.graph({ node: view.node ?? undefined, direction: view.dir, depth: view.depth, mode: view.mode })
      .then((g) => {
        if (cancelled) return;
        setGraph(g);
        setFlow(toFlow(g, expand));
        setSelNode(view.node); setSelEdge(null);
        window.setTimeout(() => rf.fitView({ padding: 0.2, duration: 250 }), 30);
      })
      .catch((e) => !cancelled && setError(String(e.message)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [view.node, view.mode, view.dir, view.depth, expand, rf]);

  const onNodeClick: NodeMouseHandler = (_, n) => { setSelNode(n.id); setSelEdge(null); };
  const onNodeDoubleClick: NodeMouseHandler = (_, n) => setView({ node: n.id });

  const nodes = useMemo(
    () => flow.nodes.map((n) => ({ ...n, selected: n.id === selNode })),
    [flow.nodes, selNode],
  );
  const edges = useMemo(
    () => flow.edges.map((e) => ({ ...e, selected: e.id === selEdge?.id, animated: e.id === selEdge?.id })),
    [flow.edges, selEdge],
  );

  const doRescan = async () => {
    setLoading(true);
    try { setStats(await api.rescan()); setView({}); } catch (e) { setError(String((e as Error).message)); }
    setLoading(false);
  };

  return (
    <div className="app">
      <header>
        <h1>Teradata lineage</h1>
        <SearchBox onPick={(n) => setView({ node: n.id, mode: n.type === "job" ? "job" : view.mode })} />
        <div className="seg" role="group" aria-label="View">
          {(["table", "job"] as Mode[]).map((m) => (
            <button key={m} aria-pressed={view.mode === m} onClick={() => setView({ mode: m })}>
              {m === "table" ? "Data flow" : "Jobs"}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Direction">
          {([["up", "Upstream"], ["both", "Both"], ["down", "Downstream"]] as [Direction, string][]).map(([d, l]) => (
            <button key={d} aria-pressed={view.dir === d} disabled={!view.node} onClick={() => setView({ dir: d })}>{l}</button>
          ))}
        </div>
        <label className="depth">Depth
          <select value={view.depth} disabled={!view.node} onChange={(e) => setView({ depth: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 8, 12].map((d) => <option key={d}>{d}</option>)}
          </select>
        </label>
        <span className="spacer" />
        {view.node && <button className="ghost" onClick={() => setView({ node: null })}>Show all</button>}
        <button className="ghost" onClick={doRescan} title="Re-read JCL, scripts, scheduler, DBQL and DBC exports">Rescan</button>
        {stats && stats.warnings.length > 0 && (
          <button className="warn" onClick={() => setShowWarn((s) => !s)} aria-expanded={showWarn}>
            ⚠ {stats.warnings.length}
          </button>
        )}
      </header>

      {showWarn && stats && (
        <div className="warnings" role="region" aria-label="Parser warnings">
          <ul>{stats.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}

      <main>
        <div className="canvas">
          {error && <div className="banner error">{error}</div>}
          {graph?.truncated && <div className="banner">Showing the 800 most connected objects — search for one to focus.</div>}
          {!loading && graph && graph.nodes.length === 0 && <div className="empty">No lineage found for this selection.</div>}
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            onNodeDoubleClick={onNodeDoubleClick}
            onEdgeClick={(_, e) => { setSelEdge((e.data as { edge: GEdge }).edge); setSelNode(null); }}
            onPaneClick={() => { setSelNode(null); setSelEdge(null); }}
            nodesConnectable={false}
            minZoom={0.1}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={22} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(n) => MINI[(n.data as LineageNodeData).node.type]} nodeStrokeWidth={0} />
          </ReactFlow>
          <div className="legend" aria-label="Legend">
            <span><i className="dot t-dataset" />MF dataset</span>
            <span><i className="dot t-table" />Table</span>
            <span><i className="dot t-view" />View</span>
            <span><i className="dot t-job" />Job</span>
            <span><i className="ln solid" />parsed from JCL/scripts</span>
            <span><i className="ln dotted" />seen only in DBQL</span>
            <span><i className="ln dashed" />inferred</span>
          </div>
          {loading && <div className="loading">Loading…</div>}
        </div>
        <DetailPanel
          nodeId={selNode}
          edge={selEdge}
          onNavigate={(id) => setView({ node: id, mode: id.startsWith("job:") ? "job" : view.mode })}
          onClose={() => { setSelNode(null); setSelEdge(null); }}
        />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  );
}
