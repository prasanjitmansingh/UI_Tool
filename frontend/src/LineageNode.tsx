import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { LineageNodeData } from "./layout";

const ICON: Record<string, string> = { table: "▦", view: "◩", dataset: "▤", job: "⚙" };
const LABEL: Record<string, string> = { table: "Table", view: "View", dataset: "MF dataset", job: "Job" };

export function LineageNode({ data, selected }: NodeProps<Node<LineageNodeData>>) {
  const { node, isRoot, onExpand } = data;
  const temp = node.attrs.temporary === true;
  const parts = node.name.replace(/^VOLATILE:/, "").split(".");
  const short = node.type === "dataset" ? parts.slice(-2).join(".") : parts[parts.length - 1];
  const db = node.type === "dataset" ? parts.slice(0, -2).join(".") : parts.slice(0, -1).join(".");
  return (
    <div className={`lnode t-${node.type}${isRoot ? " root" : ""}${selected ? " sel" : ""}${temp ? " temp" : ""}`}
      title={node.name}>
      <Handle type="target" position={Position.Left} />
      {node.hasMoreUp && (
        <button className="more left nodrag" title="Show upstream" onClick={(e) => { e.stopPropagation(); onExpand(node.id, "up"); }}>‹</button>
      )}
      <div className="lnode-top">
        <span className="ico" aria-hidden>{ICON[node.type]}</span>
        <span className="kind">{temp ? "Volatile table" : LABEL[node.type]}</span>
      </div>
      <div className="lnode-name">{short}</div>
      {db && <div className="lnode-db">{db}</div>}
      {node.hasMoreDown && (
        <button className="more right nodrag" title="Show downstream" onClick={(e) => { e.stopPropagation(); onExpand(node.id, "down"); }}>›</button>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
