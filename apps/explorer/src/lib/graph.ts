import type { VaultDoc } from "../types.ts";

export const NODE_W = 168;
/** Tall enough for a two-line label at LINE_H spacing. */
export const NODE_H = 38;
export const LINE_H = 13;
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

/**
 * The node a key moves keyboard focus to, or -1 when the key is not a
 * movement key. Up and Down follow the reading order (down a column, then on
 * to the top of the next); Left and Right go to the closest note in that
 * direction, preferring the same row; Home and End go to the first and last
 * note. At an edge the focus stays where it is.
 */
export function moveFocus(nodes: readonly GraphNode[], index: number, key: string): number {
  if (!nodes.length) return -1;
  const last = nodes.length - 1;
  const at = Math.min(Math.max(index, 0), last);
  switch (key) {
    case "ArrowDown": return Math.min(at + 1, last);
    case "ArrowUp": return Math.max(at - 1, 0);
    case "Home": return 0;
    case "End": return last;
    case "ArrowLeft":
    case "ArrowRight": {
      const here = nodes[at]!;
      const sign = key === "ArrowRight" ? 1 : -1;
      let best = at, bestScore = Infinity;
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i]!;
        const dx = (n.x - here.x) * sign;
        if (dx <= 0) continue;
        // A row change costs more than a column step, so Right stays on the row.
        const score = dx + 3 * Math.abs(n.y - here.y);
        if (score < bestScore) { bestScore = score; best = i; }
      }
      return best;
    }
    default: return -1;
  }
}

/**
 * Type-ahead: the first note after `index` whose title starts with `prefix`,
 * wrapping round. A longer prefix may match the current note itself, so typing
 * more of its title keeps focus where it is.
 */
export function typeahead(nodes: readonly GraphNode[], index: number, prefix: string): number {
  const needle = prefix.toLocaleLowerCase();
  if (!needle || !nodes.length) return -1;
  const start = needle.length === 1 ? index + 1 : index;
  for (let k = 0; k < nodes.length; k++) {
    const i = (((start + k) % nodes.length) + nodes.length) % nodes.length;
    if (nodes[i]!.d.title.toLocaleLowerCase().startsWith(needle)) return i;
  }
  return -1;
}
