import type { NodeType, PlatformId } from "./api";

export interface Terms {
  node: Record<NodeType, string>;
  jobs: string; // name of the Jobs view
  searchHint: string;
}

export const TERMS: Record<PlatformId, Terms> = {
  mainframe: {
    node: { table: "Teradata table", view: "Teradata view", dataset: "MF dataset", job: "Mainframe job" },
    jobs: "Jobs",
    searchHint: "Find Teradata table, e.g. ORDER_FACT",
  },
  abinitio: {
    node: { table: "Hive table", view: "Hive view", dataset: "HDFS path", job: "Ab Initio graph" },
    jobs: "Graphs",
    searchHint: "Find Hive table, e.g. customer_dim",
  },
};
