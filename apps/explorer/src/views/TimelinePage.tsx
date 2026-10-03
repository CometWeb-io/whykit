import { useLayoutEffect, useMemo, useState } from "react";
import { vault, resolveDoc } from "../lib/vault.ts";
import { hrefFor, type Params } from "../lib/route.ts";
import { axisLabels, daysBetween, decisionTimeline, filterTimeline, isoDay, sortTimeline, startOfDay, timelineFacets, timelineScale, type TimelineEntry, type TimelineSort } from "../lib/timeline.ts";
import { setParams } from "../nav.ts";
import { Empty, FacetSelect, ShowMore, StatusBadge, useIncremental } from "../ui.tsx";

const STEP = 100;
const SORTS = [{ value: "oldest", label: "Oldest first" }] as const;

function period(e: TimelineEntry, today: number): string {
  if (e.start === null) return `No valid date (${e.row.date || "blank"})`;
  const from = isoDay(e.start);
  if (e.end !== null) return `${from} → ${isoDay(e.end)} · in force ${daysBetween(e.start, e.end)} days`;
  if (e.superseded) return `${from} → superseded, date unknown`;
  const days = daysBetween(e.start, today);
  return days >= 0 ? `${from} → today · ${days} days` : `${from} · in the future`;
}

/** Rendered width of an element, kept current as the layout changes. */
function useWidth(): [(el: HTMLElement | null) => void, number] {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0));
    observer.observe(el);
    return () => observer.disconnect();
  }, [el]);
  return [setEl, width];
}

function barClass(e: TimelineEntry): string {
  if (e.superseded) return "lifespan-bar superseded";
  if (/^(accepted|approved)$/.test(e.row.status)) return "lifespan-bar live";
  return "lifespan-bar pending";
}

export function TimelinePage({ params }: { params: Params }) {
  const status = params.status ?? "", owner = params.owner ?? "", tag = params.tag ?? "";
  const sort: TimelineSort = params.sort === "oldest" ? "oldest" : "newest";
  const today = startOfDay(new Date());
  const entries = useMemo(() => decisionTimeline(vault.decisions, resolveDoc), []);
  const facets = useMemo(() => timelineFacets(entries), [entries]);
  const scale = useMemo(() => timelineScale(entries, today), [entries, today]);
  const rows = useMemo(() => sortTimeline(filterTimeline(entries, { status, owner, tag }), sort), [entries, status, owner, tag, sort]);
  const [limit, more] = useIncremental(`${status}|${owner}|${tag}|${sort}`, STEP);
  const filtered = Boolean(status || owner || tag);
  const [axisRef, axisWidth] = useWidth();

  return <div className="page wide"><div className="eyebrow">Decision archaeology</div><h1>Decision timeline</h1>
    <p className="lede">When each decision was made and how long it stayed in force. A bar ends on the date of the decision that superseded it; open bars are still current.</p>
    {entries.length ? <>
      <div className="filters" role="group" aria-label="Filter decisions">
        <FacetSelect id="tl-status" label="Status" value={status} facets={facets.statuses} onChange={v => setParams({ status: v || null })}/>
        <FacetSelect id="tl-owner" label="Owner" value={owner} facets={facets.owners} onChange={v => setParams({ owner: v || null })}/>
        <FacetSelect id="tl-tag" label="Tag" value={tag} facets={facets.tags} onChange={v => setParams({ tag: v || null })}/>
        <FacetSelect id="tl-sort" label="Order" value={sort === "oldest" ? "oldest" : ""} facets={SORTS} allLabel="Newest first" onChange={v => setParams({ sort: v || null })}/>
        {filtered ? <button className="text-button clear" onClick={() => setParams({ status: null, owner: null, tag: null })}>Clear filters</button> : null}
      </div>
      <p className="muted small" role="status" aria-live="polite">{filtered ? `${rows.length} of ${entries.length} decisions match` : `${entries.length} decisions`}</p>
      {rows.length ? <>
        <div className="lifespans">
          {scale ? <div className="lifespan-axis" aria-hidden="true"><span/><div className="lifespan-track" ref={axisRef}>{axisLabels(scale.ticks.map(t => ({ ...t, at: scale.pos(t.at) })), axisWidth).map(t => <span key={t.at} className={t.label ? "tick" : "tick bare"} style={{ left: `${t.at}%` }}>{t.label}</span>)}<span className="today" style={{ left: `${scale.pos(today)}%` }} title={`Today, ${isoDay(today)}`}/></div></div> : null}
          <ol aria-label="Decisions over time">{rows.slice(0, limit).map(e => {
            const left = scale && e.start !== null ? scale.pos(e.start) : null;
            const right = scale ? scale.pos(e.end ?? (e.superseded ? e.start ?? today : Math.max(today, e.start ?? today))) : null;
            return <li key={e.row.id} className="lifespan">
              <div className="lifespan-text">
                <a href={hrefFor("doc", e.doc?.id ?? e.row.recordId)}><span className="mono decision-id">{e.row.id}</span> {e.row.title}</a>
                <span className="lifespan-meta"><StatusBadge status={e.row.status}/><span>{period(e, today)}</span></span>
                <span className="lifespan-meta muted">{e.row.owner || "No owner"}{e.successor ? <> · superseded by <a href={hrefFor("doc", e.successor.recordId)}>{e.successor.id}</a></> : null}{e.tags.length ? ` · ${e.tags.join(", ")}` : ""}</span>
              </div>
              <div className="lifespan-track" aria-hidden="true">
                {left !== null && right !== null ? <span className={barClass(e)} style={{ left: `${left}%`, width: `max(6px, ${Math.max(0, right - left)}%)` }}/> : null}
              </div>
            </li>;
          })}</ol>
        </div>
        <ShowMore shown={Math.min(limit, rows.length)} total={rows.length} step={STEP} onMore={more} noun="decisions"/>
      </> : <Empty>No decisions match these filters.</Empty>}
    </> : <Empty>The decision log is empty, so there is no history to draw yet.</Empty>}
  </div>;
}
