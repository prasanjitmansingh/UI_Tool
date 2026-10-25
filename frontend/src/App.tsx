import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  api, isStatic, type Direction, type GEdge, type GraphResponse, type Mode, type Platform, type PlatformId, type Stats,
} from "./api";
import { toFlow, type LineageNodeData } from "./layout";
import { LineageNode } from "./LineageNode";
import { SearchBox } from "./SearchBox";
import { DetailPanel } from "./DetailPanel";
import { TERMS } from "./terms";

const nodeTypes = { lineage: LineageNode };
const MINI: Record<string, string> = { table: "#2f6fed", view: "#8a4fd6", dataset: "#c77700", job: "#3f8a5f" };
const FALLBACK_PLATFORMS: Platform[] = [
  { id: "mainframe", label: "Mainframe", target: "Teradata", loader: "JCL jobs", configured: true },
  { id: "abinitio", label: "Ab Initio", target: "Hadoop / Hive", loader: "Ab Initio graphs", configured: true },
];

interface View { platform: PlatformId; node: string | null; mode: Mode; dir: Direction; depth: number }

function readHash(): View {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    platform: p.get("p") === "abinitio" ? "abinitio" : "mainframe",
    node: p.get("node") || null,
    mode: (p.get("mode") as Mode) || "table",
    dir: (p.get("dir") as Direction) || "both",
    depth: Number(p.get("depth")) || 3,
  };
}

/** Static demo: keep view state in memory; only a bare #mainframe / #abinitio picks the platform. */
function useMemoryState() {
  const [state, setState] = useState<View>(() => ({
    platform: location.hash === "#abinitio" ? "abinitio" : "mainframe",
    node: null, mode: "table", dir: "both", depth: 3,
  }));
  const set = useCallback((patch: Partial<View>) => setState((s) => ({ ...s, ...patch })), []);
  return [state, set] as const;
}

function useHashState() {
  const [state, setState] = useState(readHash);
  useEffect(() => {
    const on = () => setState(readHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const set = useCallback((patch: Partial<View>) => {
    const next = { ...readHash(), ...patch };
    const p = new URLSearchParams();
    p.set("p", next.platform);
    if (next.node) p.set("node", next.node);
    p.set("mode", next.mode); p.set("dir", next.dir); p.set("depth", String(next.depth));
    location.hash = p.toString();
  }, []);
  return [state, set] as const;
}

const useViewState = isStatic ? useMemoryState : useHashState;

function Canvas() {
  const [view, setView] = useViewState();
  const [platforms, setPlatforms] = useState<Platform[]>(FALLBACK_PLATFORMS);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [flow, setFlow] = useState<{ nodes: Node<LineageNodeData>[]; edges: Edge[] }>({ nodes: [], edges: [] });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selNode, setSelNode] = useState<string | null>(null);
  const [selEdge, setSelEdge] = useState<GEdge | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [showWarn, setShowWarn] = useState(false);
  const rf = useReactFlow();
  const terms = TERMS[view.platform];
  const platform = platforms.find((p) => p.id === view.platform) ?? FALLBACK_PLATFORMS[0];

  useEffect(() => { api.platforms().then(setPlatforms).catch(() => undefined); }, []);
  useEffect(() => {
    setStats(null); setShowWarn(false);
    api.stats(view.platform).then(setStats).catch(() => undefined);
  }, [view.platform]);

  const expand = useCallback((id: string, dir: "up" | "down") => setView({ node: id, dir, depth: 3 }), [setView]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    api.graph(view.platform, { node: view.node ?? undefined, direction: view.dir, depth: view.depth, mode: view.mode })
      .then((g) => {
        if (cancelled) return;
        setGraph(g);
        setFlow(toFlow(g, expand, TERMS[view.platform]));
        setSelNode(view.node); setSelEdge(null);
        window.setTimeout(() => rf.fitView({ padding: 0.2, duration: 250 }), 30);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e.message)); setGraph(null); setFlow({ nodes: [], edges: [] });
      })
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [view.platform, view.node, view.mode, view.dir, view.depth, expand, rf]);

  const onNodeClick: NodeMouseHandler = (_, n) => { setSelNode(n.id); setSelEdge(null); };
  const onNodeDoubleClick: NodeMouseHandler = (_, n) => setView({ node: n.id });

  const nodes = useMemo(() => flow.nodes.map((n) => ({ ...n, selected: n.id === selNode })), [flow.nodes, selNode]);
  const edges = useMemo(
    () => flow.edges.map((e) => ({ ...e, selected: e.id === selEdge?.id, animated: e.id === selEdge?.id })),
    [flow.edges, selEdge],
  );

  const doRescan = async () => {
    setLoading(true);
    try { setStats(await api.rescan(view.platform)); setView({}); } catch (e) { setError(String((e as Error).message)); }
    setLoading(false);
  };

  const counts = stats?.nodes ?? {};
  return (
    <div className="app">
      <div className="platformbar">
        <div className="brand">Data lineage{isStatic && <span className="demo-tag">Sample data</span>}</div>
        <div className="platforms" role="tablist" aria-label="Platform">
          {platforms.map((p) => (
            <button key={p.id} role="tab" aria-selected={view.platform === p.id}
              onClick={() => view.platform !== p.id && setView({ platform: p.id, node: null, mode: "table", dir: "both", depth: 3 })}>
              <span className="p-label">{p.label}</span>
              <span className="p-sub">{p.loader} → {p.target}</span>
            </button>
          ))}
        </div>
        <span className="spacer" />
        {stats && (
          <span className="counts muted small">
            {(counts.table ?? 0) + (counts.view ?? 0)} tables · {counts.job ?? 0} {terms.jobs.toLowerCase()} · {counts.dataset ?? 0} {terms.node.dataset.toLowerCase()}s
          </span>
        )}
      </div>
      <header>
        <SearchBox platform={view.platform} terms={terms}
          onPick={(n) => setView({ node: n.id, mode: n.type === "job" ? "job" : view.mode })} />
        <div className="seg" role="group" aria-label="View">
          {(["table", "job"] as Mode[]).map((m) => (
            <button key={m} aria-pressed={view.mode === m} onClick={() => setView({ mode: m })}>
              {m === "table" ? "Data flow" : terms.jobs}
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
        {!isStatic && <button className="ghost" onClick={doRescan} title="Re-read this platform's source files">Rescan</button>}
        {stats && stats.warnings.length > 0 && (
          <button className="warn" onClick={() => setShowWarn((s) => !s)} aria-expanded={showWarn} title="Parser warnings">
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
          {graph?.truncated && <div className="banner">Showing the 800 most connected objects — search for a table to focus.</div>}
          {!loading && graph && graph.nodes.length === 0 && (
            <div className="empty">
              {view.node ? "No lineage found for this selection." : `No lineage loaded for ${platform.label} yet — add files under its data folder and Rescan.`}
            </div>
          )}
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
            <span><i className="dot t-dataset" />{terms.node.dataset}</span>
            <span><i className="dot t-table" />{terms.node.table}</span>
            <span><i className="dot t-view" />{terms.node.view}</span>
            <span><i className="dot t-job" />{terms.node.job}</span>
            <span><i className="ln solid" />parsed</span>
            {view.platform === "mainframe"
              ? <><span><i className="ln dotted" />seen only in DBQL</span><span><i className="ln dashed" />inferred</span></>
              : <span><i className="ln dotted" />view definition</span>}
          </div>
          {loading && <div className="loading">Loading…</div>}
        </div>
        <DetailPanel
          platform={view.platform}
          platformLabel={platform.label}
          terms={terms}
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
