// In-browser version of the lineage API for the self-contained demo page.
// Mirrors backend/lineage/graph.py over data exported by `python -m lineage.export`.
import type {
  Direction, GEdge, GNode, GraphResponse, Impact, Mode, NodeDetail, Platform, PlatformId, Snippet, Stats,
} from "./api";

export interface StaticPlatformData { nodes: GNode[]; edges: GEdge[]; stats: Stats; files: Record<string, string> }
export interface StaticBundle { platforms: Platform[]; data: Record<PlatformId, StaticPlatformData> }

class StaticGraph {
  nodes = new Map<string, GNode>();
  edges: GEdge[];
  constructor(private d: StaticPlatformData) {
    d.nodes.forEach((n) => this.nodes.set(n.id, n));
    this.edges = d.edges;
  }

  viewEdges(mode: Mode) {
    const kinds = mode === "table" ? ["FLOW"] : ["READS", "WRITES", "RUNS_AFTER"];
    return this.edges.filter((e) => kinds.includes(e.kind));
  }

  lineage(root: string, direction: Direction, depth: number, mode: Mode): GraphResponse {
    const edges = this.viewEdges(mode);
    const down = new Map<string, GEdge[]>(), up = new Map<string, GEdge[]>();
    for (const e of edges) {
      (down.get(e.source) ?? down.set(e.source, []).get(e.source)!).push(e);
      (up.get(e.target) ?? up.set(e.target, []).get(e.target)!).push(e);
    }
    const keepNodes = new Map<string, number>([[root, 0]]);
    const keepEdges = new Map<string, GEdge>();
    const bfs = (adj: Map<string, GEdge[]>, forward: boolean) => {
      const seen = new Set([root]);
      const q: [string, number][] = [[root, 0]];
      while (q.length) {
        const [id, d] = q.shift()!;
        if (d >= depth) continue;
        for (const e of adj.get(id) ?? []) {
          const other = forward ? e.target : e.source;
          keepEdges.set(e.id, e);
          if (!keepNodes.has(other)) keepNodes.set(other, d + 1);
          if (!seen.has(other)) { seen.add(other); q.push([other, d + 1]); }
        }
      }
    };
    if (direction !== "down") bfs(up, false);
    if (direction !== "up") bfs(down, true);
    return {
      root, mode,
      nodes: [...keepNodes].map(([id, distance]) => ({
        ...this.nodes.get(id)!, distance,
        hasMoreUp: (up.get(id) ?? []).some((e) => !keepEdges.has(e.id)),
        hasMoreDown: (down.get(id) ?? []).some((e) => !keepEdges.has(e.id)),
      })),
      edges: [...keepEdges.values()],
    };
  }

  overview(mode: Mode): GraphResponse {
    const edges = this.viewEdges(mode);
    const ids = new Set(edges.flatMap((e) => [e.source, e.target]));
    return {
      root: null, mode, truncated: false,
      nodes: [...ids].sort().map((id) => ({ ...this.nodes.get(id)!, distance: 0, hasMoreUp: false, hasMoreDown: false })),
      edges,
    };
  }

  search(q: string, types?: string[], limit = 25): GNode[] {
    const query = q.trim().toUpperCase();
    const all = [...this.nodes.values()].filter((n) => !types || types.includes(n.type));
    if (!query) return all.sort((a, b) => a.name.toUpperCase().localeCompare(b.name.toUpperCase())).slice(0, limit);
    return all
      .filter((n) => n.name.toUpperCase().includes(query) || n.id.toUpperCase().includes(query))
      .map((n) => {
        const hay = n.name.toUpperCase(), tail = hay.split(".").pop()!;
        const rank = hay === query || tail === query ? 0 : hay.startsWith(query) || tail.startsWith(query) ? 1 : 2;
        return { n, rank, len: hay.length };
      })
      .sort((a, b) => a.rank - b.rank || a.len - b.len)
      .slice(0, limit)
      .map((x) => x.n);
  }

  detail(id: string): NodeDetail | null {
    const n = this.nodes.get(id);
    if (!n) return null;
    const of = (kind: string, pred: (e: GEdge) => boolean) => this.edges.filter((e) => e.kind === kind && pred(e));
    const isJob = n.type === "job";
    const jobsOf = (es: GEdge[]) => [...new Set(es.flatMap((e) => e.evidence.map((ev) => ev.job)))].sort();
    return {
      ...n,
      loadedBy: jobsOf(of("WRITES", (e) => e.target === id)),
      readBy: jobsOf(of("READS", (e) => e.source === id)),
      upstream: of("FLOW", (e) => e.target === id),
      downstream: of("FLOW", (e) => e.source === id),
      jobInputs: isJob ? of("READS", (e) => e.target === id) : [],
      jobOutputs: isJob ? of("WRITES", (e) => e.source === id) : [],
      predecessors: of("RUNS_AFTER", (e) => e.target === id).map((e) => e.source),
      successors: of("RUNS_AFTER", (e) => e.source === id).map((e) => e.target),
      impact: this.impact(id),
    };
  }

  impact(id: string): Impact | null {
    const n = this.nodes.get(id);
    if (!n) return null;
    const flowDown = new Map<string, string[]>(), writers = new Map<string, Set<string>>(), sched = new Map<string, string[]>();
    for (const e of this.edges) {
      if (e.kind === "FLOW") (flowDown.get(e.source) ?? flowDown.set(e.source, []).get(e.source)!).push(e.target);
      else if (e.kind === "WRITES") (writers.get(e.target) ?? writers.set(e.target, new Set()).get(e.target)!).add(e.source);
      else if (e.kind === "RUNS_AFTER") (sched.get(e.source) ?? sched.set(e.source, []).get(e.source)!).push(e.target);
    }
    const start = n.type === "job" ? this.edges.filter((e) => e.kind === "WRITES" && e.source === id).map((e) => e.target) : [id];
    const dist = new Map<string, number>(start.map((s) => [s, n.type === "job" ? 1 : 0]));
    const q = [...start];
    while (q.length) {
      const cur = q.shift()!;
      for (const nxt of flowDown.get(cur) ?? []) if (!dist.has(nxt)) { dist.set(nxt, dist.get(cur)! + 1); q.push(nxt); }
    }
    dist.delete(id);
    const jobs = new Map<string, string>();
    for (const obj of dist.keys()) for (const j of writers.get(obj) ?? []) if (j !== id && !jobs.has(j)) jobs.set(j, "loads an affected object");
    if (n.type === "job") {
      const seen = new Set([id]); const sq = [id];
      while (sq.length) {
        const cur = sq.shift()!;
        for (const nxt of sched.get(cur) ?? []) if (!seen.has(nxt)) { seen.add(nxt); sq.push(nxt); jobs.set(nxt, "scheduled after this job"); }
      }
    }
    const name = (i: string) => this.nodes.get(i)!.name;
    return {
      id,
      objects: [...dist].sort((a, b) => a[1] - b[1] || name(a[0]).localeCompare(name(b[0])))
        .map(([i, d]) => ({ id: i, name: name(i), type: this.nodes.get(i)!.type, distance: d })),
      jobs: [...jobs].sort((a, b) => name(a[0]).localeCompare(name(b[0]))).map(([j, reason]) => ({ id: j, name: name(j), reason })),
    };
  }

  source(file: string, line: number, context = 8): Snippet {
    const text = this.d.files[file];
    if (text === undefined) throw new Error("404 File not found");
    const lines = text.split(/\r?\n/);
    if (lines[lines.length - 1] === "") lines.pop();
    const start = Math.max(1, line - context), end = Math.min(lines.length, line + context);
    const out = [];
    for (let k = start; k <= end; k++) out.push({ n: k, text: lines[k - 1] });
    return { file, line, start, lines: out };
  }
}

export function makeStaticApi(bundle: StaticBundle) {
  const graphs = new Map<PlatformId, StaticGraph>();
  const g = (p: PlatformId) => {
    if (!graphs.has(p)) graphs.set(p, new StaticGraph(bundle.data[p]));
    return graphs.get(p)!;
  };
  const ok = <T,>(v: T) => Promise.resolve(v);
  return {
    platforms: () => ok(bundle.platforms),
    graph: (platform: PlatformId, p: { node?: string; direction: Direction; depth: number; mode: Mode }) => {
      const gr = g(platform);
      if (!p.node) return ok(gr.overview(p.mode));
      if (!gr.nodes.has(p.node)) return Promise.reject(new Error(`404 Unknown node ${p.node}`));
      return ok(gr.lineage(p.node, p.direction, p.depth, p.mode));
    },
    search: (platform: PlatformId, q: string, type?: string) => ok(g(platform).search(q, type ? type.split(",") : undefined)),
    node: (platform: PlatformId, id: string) => {
      const d = g(platform).detail(id);
      return d ? ok(d) : Promise.reject(new Error(`404 Unknown node ${id}`));
    },
    stats: (platform: PlatformId) => ok(bundle.data[platform].stats),
    source: (platform: PlatformId, file: string, line: number) => {
      try { return ok(g(platform).source(file, line)); } catch (e) { return Promise.reject(e); }
    },
    rescan: (platform: PlatformId) => ok(bundle.data[platform].stats),
  };
}
