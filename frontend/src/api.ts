export type NodeType = "table" | "view" | "dataset" | "job";
export type Mode = "table" | "job";
export type Direction = "up" | "down" | "both";
export type PlatformId = "mainframe" | "abinitio";

export interface Platform {
  id: PlatformId;
  label: string;
  target: string;
  loader: string;
  configured: boolean;
}

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
  origin: "jcl" | "dbql" | "scheduler" | "abinitio" | "metastore";
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
  impact: Impact | null;
}

export interface Impact {
  id: string;
  objects: { id: string; name: string; type: NodeType; distance: number }[];
  jobs: { id: string; name: string; reason: string }[];
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
  platforms: () => get<Platform[]>("platforms"),
  graph: (platform: PlatformId, p: { node?: string; direction: Direction; depth: number; mode: Mode }) =>
    get<GraphResponse>("graph", { platform, ...p }),
  search: (platform: PlatformId, q: string, type?: string) => get<GNode[]>("search", { platform, q, type }),
  node: (platform: PlatformId, id: string) => get<NodeDetail>("node", { platform, id }),
  stats: (platform: PlatformId) => get<Stats>("stats", { platform }),
  source: (platform: PlatformId, file: string, line: number) => get<Snippet>("source", { platform, file, line }),
  rescan: async (platform: PlatformId) => {
    const res = await fetch(`/api/rescan?platform=${platform}`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    return (await res.json()) as Stats;
  },
};
