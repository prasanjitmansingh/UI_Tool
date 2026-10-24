export type NodeType = "table" | "view" | "dataset" | "job";
export type Mode = "table" | "job";
export type Direction = "up" | "down" | "both";

export interface GNode {
  id: string;
  type: NodeType;
  name: string;
  attrs: Record<string, unknown>;
  distance?: number;
  hasMoreUp?: boolean;
  hasMoreDown?: boolean;
}

export interface Evidence {
  job: string;
  step?: string;
  file: string;
  line?: number;
  origin: "jcl" | "dbql" | "scheduler";
  confidence?: string;
  scriptKind?: string;
  executions?: number;
}

export interface GEdge {
  id: string;
  kind: "FLOW" | "READS" | "WRITES" | "RUNS_AFTER";
  source: string;
  target: string;
  jobs: string[];
  evidence: Evidence[];
}

export interface GraphResponse {
  root: string | null;
  mode: Mode;
  truncated?: boolean;
  nodes: GNode[];
  edges: GEdge[];
}

export interface NodeDetail extends GNode {
  loadedBy: string[];
  readBy: string[];
  upstream: GEdge[];
  downstream: GEdge[];
  jobInputs: GEdge[];
  jobOutputs: GEdge[];
  predecessors: string[];
  successors: string[];
}

export interface Stats {
  nodes: Record<string, number>;
  edges: Record<string, number>;
  sources: Record<string, number>;
  warnings: string[];
}

export interface Snippet {
  file: string;
  line: number;
  start: number;
  lines: { n: number; text: string }[];
}

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) qs.set(k, String(v));
  const res = await fetch(`/api/${path}?${qs}`);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const api = {
  graph: (p: { node?: string; direction: Direction; depth: number; mode: Mode }) =>
    get<GraphResponse>("graph", p),
  search: (q: string) => get<GNode[]>("search", { q }),
  node: (id: string) => get<NodeDetail>("node", { id }),
  stats: () => get<Stats>("stats"),
  source: (file: string, line: number) => get<Snippet>("source", { file, line }),
  rescan: async () => {
    const res = await fetch("/api/rescan", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    return (await res.json()) as Stats;
  },
};
