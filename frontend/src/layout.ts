import dagre from "@dagrejs/dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GEdge, GNode, GraphResponse } from "./api";
import type { Terms } from "./terms";

export const NODE_W = 230;
export const NODE_H = 58;

export type LineageNodeData = {
  node: GNode;
  isRoot: boolean;
  terms: Terms;
  onExpand: (id: string, dir: "up" | "down") => void;
};

function edgeLabel(e: GEdge): string {
  if (e.kind === "RUNS_AFTER") return "runs after";
  if (e.kind === "READS") return "reads";
  if (e.kind === "WRITES") return "writes";
  if (e.evidence.every((x) => x.origin === "metastore")) return "view";
  const [a, b, ...rest] = e.jobs;
  return [a, b].filter(Boolean).join(", ") + (rest.length ? ` +${rest.length}` : "");
}

export function toFlow(
  g: GraphResponse,
  onExpand: LineageNodeData["onExpand"],
  terms: Terms,
): { nodes: Node<LineageNodeData>[]; edges: Edge[] } {
  const dg = new dagre.graphlib.Graph();
  dg.setGraph({ rankdir: "LR", nodesep: 28, ranksep: 110, marginx: 20, marginy: 20 });
  dg.setDefaultEdgeLabel(() => ({}));
  g.nodes.forEach((n) => dg.setNode(n.id, { width: NODE_W, height: NODE_H }));
  g.edges.forEach((e) => dg.setEdge(e.source, e.target));
  dagre.layout(dg);

  const nodes: Node<LineageNodeData>[] = g.nodes.map((n) => {
    const p = dg.node(n.id);
    return {
      id: n.id,
      type: "lineage",
      position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 },
      width: NODE_W,
      height: NODE_H,
      data: { node: n, isRoot: n.id === g.root, terms, onExpand },
    };
  });

  const edges: Edge[] = g.edges.map((e) => {
    const onlyObserved = e.evidence.length > 0 && e.evidence.every((x) => x.origin === "dbql");
    const viewDef = e.evidence.length > 0 && e.evidence.every((x) => x.origin === "metastore");
    const inferred = e.evidence.length > 0 && e.evidence.every((x) => x.confidence === "inferred");
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      label: edgeLabel(e),
      data: { edge: e },
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      className: [e.kind === "RUNS_AFTER" ? "sched" : "", onlyObserved ? "observed" : "", viewDef ? "viewdef" : "", inferred ? "inferred" : ""]
        .filter(Boolean)
        .join(" "),
    };
  });
  return { nodes, edges };
}
