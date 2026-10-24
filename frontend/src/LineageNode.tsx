import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { LineageNodeData } from "./layout";

const ICON: Record<string, string> = { table: "▦", view: "◩", dataset: "▤", job: "⚙" };

export function LineageNode({ data, selected }: NodeProps<Node<LineageNodeData>>) {
  const { node, isRoot, terms, onExpand } = data;
  const temp = node.attrs.temporary === true;
  let short: string, db: string;
  if (node.type === "dataset" && node.name.startsWith("/")) {
    const segs = node.name.split("/").filter(Boolean); // HDFS path: last two segments up front
    short = segs.slice(-2).join("/");
    db = "/" + segs.slice(0, -2).join("/");
  } else {
    const parts = node.name.replace(/^VOLATILE:/, "").split(".");
    const keep = node.type === "dataset" ? 2 : 1; // MF dataset: last two qualifiers
    short = parts.slice(-keep).join(".");
    db = parts.slice(0, -keep).join(".");
  }
  return (
    <div className={`lnode t-${node.type}${isRoot ? " root" : ""}${selected ? " sel" : ""}${temp ? " temp" : ""}`}
      title={node.name}>
      <Handle type="target" position={Position.Left} />
      {node.hasMoreUp && (
        <button className="more left nodrag" title="Show upstream" onClick={(e) => { e.stopPropagation(); onExpand(node.id, "up"); }}>‹</button>
      )}
      <div className="lnode-top">
        <span className="ico" aria-hidden>{ICON[node.type]}</span>
        <span className="kind">{temp ? "Volatile table" : terms.node[node.type]}</span>
      </div>
      <div className="lnode-name">{short}</div>
      {db && db !== "/" && <div className="lnode-db">{db}</div>}
      {node.hasMoreDown && (
        <button className="more right nodrag" title="Show downstream" onClick={(e) => { e.stopPropagation(); onExpand(node.id, "down"); }}>›</button>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
