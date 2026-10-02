import type { VaultDoc } from "../types.ts";

export const NODE_W = 168;
export const NODE_H = 28;
const COL_W = NODE_W + 24;
const ROW_H = NODE_H + 10;
const GROUP_LABEL_H = 26;
const GROUP_GAP_Y = 34;
const PAD = 20;

export type GraphNode = { d: VaultDoc; x: number; y: number };
export type GraphEdge = { from: string; to: string };
export type GraphGroup = { id: string; x: number; y: number };
export type GraphLayout = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
  width: number;
  height: number;
};

export type LayoutOptions = { maxRows?: number; maxCols?: number };

/**
 * Lay notes out in workstream blocks without overlap.
 *
 * Each workstream is a block of columns at most `maxRows` tall; blocks are
 * packed left to right and wrap after `maxCols` columns. Every step is linear
 * in the number of notes and links, so a vault with thousands of notes stays
 * responsive and a crowded workstream grows sideways instead of spilling into
 * the next block.
 */
export function layoutGraph(
  docs: readonly VaultDoc[],
  linksFor: (doc: VaultDoc) => readonly VaultDoc[],
  { maxRows = 10, maxCols = 5 }: LayoutOptions = {},
): GraphLayout {
  const byGroup = new Map<string, VaultDoc[]>();
  for (const d of docs) {
    const list = byGroup.get(d.workstream);
    if (list) list.push(d);
    else byGroup.set(d.workstream, [d]);
  }

  const nodes: GraphNode[] = [];
  const groups: GraphGroup[] = [];
  let col = 0;
  let rowTop = PAD;
  let rowHeight = 0;
  let width = 0;
  for (const [id, members] of byGroup) {
    const cols = Math.ceil(members.length / maxRows);
    if (col > 0 && col + cols > maxCols) {
      rowTop += rowHeight + GROUP_GAP_Y;
      col = 0;
      rowHeight = 0;
    }
    const x0 = PAD + col * COL_W;
    groups.push({ id, x: x0, y: rowTop });
    members.forEach((d, i) => {
      nodes.push({
        d,
        x: x0 + Math.floor(i / maxRows) * COL_W,
        y: rowTop + GROUP_LABEL_H + (i % maxRows) * ROW_H,
      });
    });
    const rows = Math.min(members.length, maxRows);
    rowHeight = Math.max(rowHeight, GROUP_LABEL_H + rows * ROW_H);
    col += cols;
    width = Math.max(width, x0 + cols * COL_W);
  }

  const placed = new Set(nodes.map(n => n.d.id));
  const edges: GraphEdge[] = [];
  for (const n of nodes) {
    for (const to of linksFor(n.d)) {
      if (placed.has(to.id)) edges.push({ from: n.d.id, to: to.id });
    }
  }
  return {
    nodes,
    edges,
    groups,
    width: Math.max(width + PAD, 2 * PAD + COL_W),
    height: rowTop + rowHeight + PAD,
  };
}

/** The hovered or focused note plus its direct neighbours. */
export function neighbourhood(edges: readonly GraphEdge[], id: string | null): Set<string> {
  const out = new Set<string>();
  if (!id) return out;
  out.add(id);
  for (const e of edges) {
    if (e.from === id) out.add(e.to);
    else if (e.to === id) out.add(e.from);
  }
  return out;
}
