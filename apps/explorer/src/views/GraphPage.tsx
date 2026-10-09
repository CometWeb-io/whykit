import { memo, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ChevronRight, X } from "lucide-react";
import { docs, linksFor, backlinksFor, resolveDoc } from "../lib/vault.ts";
import { hrefFor, type Params } from "../lib/route.ts";
import { layoutGraph, moveFocus, neighbourhood, typeahead, NODE_H, NODE_W, LINE_H, type GraphNode } from "../lib/graph.ts";
import { canvasMeasure, fitLabel, type FittedLabel } from "../lib/label.ts";
import { go, setParams } from "../nav.ts";
import { Empty, FacetSelect, StatusBadge, ClaimAssessment } from "../ui.tsx";

// Must match `.graph-node text` in styles.css: labels are measured in the font they are drawn in.
const LABEL_FONT = "11px ui-sans-serif, system-ui, sans-serif";
const LABEL_X = 9;
const LABEL_W = NODE_W - 2 * LABEL_X;

// Dimming everything else is one class on the <svg>, so a hover only re-renders
// the notes whose own state changes, not all of them.
type NodeState = "normal" | "near" | "current";

// The graph is one composite widget (a WAI-ARIA listbox) with a roving
// tabindex: exactly one note is in the Tab order, so a 5,000-note vault is one
// Tab stop rather than 5,000. Keys are handled once on the listbox.
const GraphNodeView = memo(function GraphNodeView({ node, label, links, state, selected, tabbable, onHover, onFocusNode, onSelect }: {
  node: GraphNode; label: FittedLabel; links: number; state: NodeState; selected: boolean; tabbable: boolean;
  onHover: (id: string | null) => void; onFocusNode: (id: string) => void; onSelect: (id: string) => void;
}) {
  const { d, x, y } = node;
  const top = (NODE_H - label.lines.length * LINE_H) / 2 + LINE_H - 3;
  const cls = ["graph-node", state === "normal" ? "" : state, selected ? "selected" : ""].filter(Boolean).join(" ");
  // Keyboard handling lives on the listbox (see onGraphKey); options only report focus.
  return <g transform={`translate(${x},${y})`} role="option" aria-selected={selected} tabIndex={tabbable ? 0 : -1} data-id={d.id}
    aria-label={`${d.title}${d.verificationStatus ? `, ${d.verificationStatus}` : ""}, ${links} link${links === 1 ? "" : "s"}`}
    onMouseEnter={() => onHover(d.id)} onMouseLeave={() => onHover(null)} onFocus={() => onFocusNode(d.id)} onBlur={() => onHover(null)}
    onClick={() => onSelect(d.id)} onDoubleClick={() => go("doc", d.id)} className={cls}>
    <title>{`${d.title}\n${d.id} · ${links} link${links === 1 ? "" : "s"}`}</title>
    <rect width={NODE_W} height={NODE_H} rx="6"/>
    <text x={LABEL_X} y={top}>{label.lines.map((line, i) => <tspan key={i} x={LABEL_X} dy={i ? LINE_H : 0}>{line}</tspan>)}</text>
  </g>;
});

const WORKSTREAM_LABELS: Record<string, string> = {
  "00-context": "Context", "01-strategy": "Strategy", "02-discoverability": "Discoverability", "03-website": "Website",
  "04-automation": "Automation", "05-operations": "Operations", "06-decisions": "Decisions", "07-research": "Research", reports: "Reports",
};

export function GraphPage({ params }: { params: Params }) {
  const all = useMemo(() => docs.filter(d => d.status !== "template" && d.id !== "README" && !d.id.endsWith("/README")), []);
  const workstreams = useMemo(() => {
    const c = new Map<string, number>();
    for (const d of all) c.set(d.workstream, (c.get(d.workstream) ?? 0) + 1);
    return [...c].map(([value, count]) => ({ value, label: WORKSTREAM_LABELS[value] ?? value, count })).sort((a, b) => a.value.localeCompare(b.value));
  }, [all]);
  const ws = params.ws && workstreams.some(w => w.value === params.ws) ? params.ws : "";
  const layout = useMemo(() => {
    const shown = ws ? all.filter(d => d.workstream === ws) : all;
    // Ten rows suit a small vault; a crowded workstream (hundreds of notes)
    // would otherwise become one strip tens of thousands of pixels wide.
    const largest = Math.max(0, ...workstreams.filter(w => !ws || w.value === ws).map(w => w.count));
    return layoutGraph(shown, linksFor, { maxRows: Math.max(10, Math.ceil(Math.sqrt(largest * 2))) });
  }, [all, ws, workstreams]);
  const byId = useMemo(() => new Map(layout.nodes.map(n => [n.d.id, n])), [layout]);
  const indexOf = useMemo(() => new Map(layout.nodes.map((n, i) => [n.d.id, i])), [layout]);
  const linkCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of layout.edges) {
      counts.set(e.from, (counts.get(e.from) || 0) + 1);
      counts.set(e.to, (counts.get(e.to) || 0) + 1);
    }
    return counts;
  }, [layout]);
  const labels = useMemo(() => {
    const measure = canvasMeasure(LABEL_FONT);
    return new Map(layout.nodes.map(n => [n.d.id, fitLabel(n.d.title, LABEL_W, measure)]));
  }, [layout]);

  const selected = params.node && byId.has(params.node) ? params.node : null;
  const [hover, setHover] = useState<string | null>(null);
  const active = hover ?? selected;
  const near = useMemo(() => neighbourhood(layout.edges, active), [layout, active]);
  const onSelect = useCallback((id: string) => setParams({ node: id }), []);

  // Roving tabindex: the note that last had focus keeps the single Tab stop;
  // before that it is the selected note, else the first one.
  const [focusId, setFocusId] = useState<string | null>(null);
  const tabStop = focusId && byId.has(focusId) ? focusId : selected ?? layout.nodes[0]?.d.id ?? null;
  const onFocusNode = useCallback((id: string) => { setFocusId(id); setHover(id); }, []);
  const typed = useRef({ text: "", at: 0 });
  const focusIndex = (i: number) => {
    const id = layout.nodes[i]?.d.id;
    if (!id) return;
    setFocusId(id);
    wrap.current?.querySelector<SVGGElement>(`[data-id="${CSS.escape(id)}"]`)?.focus();
  };
  const onGraphKey = (e: ReactKeyboardEvent<SVGSVGElement>) => {
    const id = (e.target as Element).closest?.("[data-id]")?.getAttribute("data-id");
    const at = id ? indexOf.get(id) : undefined;
    if (!id || at === undefined || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "Enter") { e.preventDefault(); go("doc", id); return; }
    if (e.key === " ") { e.preventDefault(); onSelect(id); return; }
    const next = moveFocus(layout.nodes, at, e.key);
    if (next >= 0) { e.preventDefault(); focusIndex(next); return; }
    if (e.key.length === 1 && e.key.trim()) {
      // Type-ahead: characters typed within 700 ms build one prefix; the same
      // letter repeated cycles through the titles that start with it.
      e.preventDefault();
      const now = performance.now();
      const text = now - typed.current.at > 700 ? e.key : typed.current.text + e.key;
      typed.current = { text, at: now };
      const prefix = [...text].every(ch => ch.toLowerCase() === text[0]!.toLowerCase()) ? text[0]! : text;
      const hit = typeahead(layout.nodes, at, prefix);
      if (hit >= 0) focusIndex(hit);
    }
  };

  // The full edge layer never re-renders on hover; the active node's edges are
  // redrawn on top. Re-rendering every line on each hover cost ~120 ms at 5,000 notes.
  const cx = NODE_W / 2, cy = NODE_H / 2;
  const baseEdges = useMemo(() => <g className="edges" aria-hidden="true">{layout.edges.map((e, i) => {
    const a = byId.get(e.from), b = byId.get(e.to);
    return a && b ? <line key={i} x1={a.x + cx} y1={a.y + cy} x2={b.x + cx} y2={b.y + cy} className="edge"><title>{e.type === "claim" ? "Claim dependency" : "Document link"}</title></line> : null;
  })}</g>, [layout, byId, cx, cy]);
  const activeEdges = active ? layout.edges.filter(e => e.from === active || e.to === active) : [];

  // A deep link to a selected note scrolls it into view once.
  const wrap = useRef<HTMLDivElement>(null);
  const initial = useRef(selected);
  useEffect(() => {
    if (!initial.current) return;
    wrap.current?.querySelector(`[data-id="${CSS.escape(initial.current)}"]`)?.scrollIntoView({ block: "nearest", inline: "center" });
  }, []);

  const focusDoc = active ? byId.get(active)?.d : undefined;
  return <div className="page wide"><div className="eyebrow">Resolved wikilinks</div><h1>Knowledge graph</h1><p className="lede">Edges come from real Markdown links. Ambiguous aliases are lint errors rather than arbitrary graph connections. Select a note to inspect its links; press Enter or double-click to open it.</p>
    <p className="muted small" id="graph-keys">The graph is a single Tab stop. Arrow keys move between notes, Home and End jump to the first and last, typing a title moves to it, Space selects and Enter opens.</p>
    {all.length ? <>
      <div className="filters" role="group" aria-label="Graph options">
        <FacetSelect id="graph-ws" label="Workstream" value={ws} facets={workstreams} onChange={v => setParams({ ws: v || null, node: null })}/>
      </div>
      <div className="graph-inspector" aria-label="Selected note" role="region">
        {focusDoc ? <>
          <div className="inspector-head"><StatusBadge status={focusDoc.status}/><span className="mono small muted">{focusDoc.id}</span>{selected ? <button className="text-button" onClick={() => setParams({ node: null })}><X size={14} aria-hidden="true"/>Clear selection</button> : null}</div>
          <strong className="inspector-title">{focusDoc.title}</strong>
          <ClaimAssessment claims={focusDoc.claimId ? [focusDoc] : (focusDoc.claimIds ?? []).flatMap(cid => { const c = resolveDoc(cid); return c ? [c] : []; })}/>
          <span className="muted small">{focusDoc.owner || "No owner"} · links to {linksFor(focusDoc).length} · referenced by {backlinksFor(focusDoc).length}</span>
          <a className="text-button" href={hrefFor("doc", focusDoc.id)}>Open note <ChevronRight size={14} aria-hidden="true"/></a>
        </> : <span className="muted small">Hover, focus or select a note to see its details. The selection is kept in the page address, so it survives a reload and can be shared.</span>}
      </div>
      <div className="graph-wrap" ref={wrap}><svg className={active ? "graph-svg focusing" : "graph-svg"} viewBox={`0 0 ${layout.width} ${layout.height}`} style={{ width: layout.width, minWidth: 0, minHeight: 0 }} role="listbox" aria-label={`Knowledge graph: ${layout.nodes.length} notes, ${layout.edges.length} links`} aria-describedby="graph-keys" onKeyDown={onGraphKey}>
        <g aria-hidden="true">{layout.groups.map(g => <text key={g.id} x={g.x} y={g.y + 12} className="graph-group">{WORKSTREAM_LABELS[g.id] || g.id}</text>)}</g>
        {baseEdges}
        <g aria-hidden="true">{activeEdges.map((e, i) => { const a = byId.get(e.from), b = byId.get(e.to); return a && b ? <line key={i} x1={a.x + cx} y1={a.y + cy} x2={b.x + cx} y2={b.y + cy} className="edge active"/> : null; })}</g>
        {layout.nodes.map(n => {
          const id = n.d.id;
          const state: NodeState = !active ? "normal" : id === hover || (!hover && id === selected) ? "current" : near.has(id) ? "near" : "normal";
          return <GraphNodeView key={id} node={n} label={labels.get(id)!} links={linkCounts.get(id) || 0} state={state} selected={id === selected} tabbable={id === tabStop} onHover={setHover} onFocusNode={onFocusNode} onSelect={onSelect}/>;
        })}
      </svg></div><p className="muted small">{layout.nodes.length} notes · {layout.edges.length} resolved links</p>
    </> : <Empty>No notes to draw yet. The graph appears once working documents link to each other.</Empty>}
  </div>;
}
