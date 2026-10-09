import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";
import { vault, docsForEvidence } from "../lib/vault.ts";
import type { VaultDoc } from "../types.ts";
import { hrefFor, type Params } from "../lib/route.ts";
import { describeAge, evidenceFreshness, FRESHNESS_LABELS, FRESHNESS_ORDER, type Freshness, type FreshnessRow } from "../lib/freshness.ts";
import { startOfDay } from "../lib/timeline.ts";
import { setParams } from "../nav.ts";
import { Empty, FacetSelect, Metric, ShowMore, StatusBadge, ClaimAssessment, useIncremental } from "../ui.tsx";

const STEP = 100;

function AgeMeter({ row }: { row: FreshnessRow }) {
  const text = row.maxAge === null ? describeAge(row.ageDays) : `${describeAge(row.ageDays)} of ${row.maxAge}`;
  if (row.maxAge === null || row.ageDays === null || row.ageDays < 0) return <span className="mono small">{text}</span>;
  const share = row.maxAge === 0 ? 1 : row.ageDays / row.maxAge;
  return <span className="age"><span className="age-meter" aria-hidden="true"><span className={`age-fill ${row.state}`} style={{ width: `${Math.min(share, 1) * 100}%` }}/></span><span className="mono small">{text}</span></span>;
}

function Citers({ docs }: { docs: readonly { id: string; title: string }[] }) {
  if (!docs.length) return <span className="muted">—</span>;
  return <span title={docs.map(d => d.title).join(" · ")}>{docs.length}</span>;
}

const CITER_PREVIEW = 8;

function CiterList({ docs }: { docs: readonly VaultDoc[] }) {
  const item = (d: VaultDoc) => <li key={d.id}><a href={hrefFor("doc", d.id)}>{d.title}</a> <StatusBadge status={d.status}/></li>;
  return <>
    <ul>{docs.slice(0, CITER_PREVIEW).map(item)}</ul>
    {docs.length > CITER_PREVIEW ? <details><summary>{docs.length - CITER_PREVIEW} more</summary><ul>{docs.slice(CITER_PREVIEW).map(item)}</ul></details> : null}
  </>;
}

export function FreshnessPage({ params }: { params: Params }) {
  const state = (FRESHNESS_ORDER as readonly string[]).includes(params.state ?? "") ? params.state as Freshness : "";
  const type = params.type ?? "";
  const onlyCited = params.retired === "cited";
  const today = startOfDay(new Date());
  const policy = vault.policy;
  const windows = Object.entries(policy?.evidenceAccessAgeDays ?? {});
  const { active, retired } = useMemo(() => evidenceFreshness(vault.evidence, policy, today, docsForEvidence), [policy, today]);
  const counts = useMemo(() => {
    const c = new Map<Freshness, number>();
    for (const r of active) c.set(r.state, (c.get(r.state) ?? 0) + 1);
    return c;
  }, [active]);
  const types = useMemo(() => {
    const c = new Map<string, number>();
    for (const r of active) if (r.e.type) c.set(r.e.type, (c.get(r.e.type) ?? 0) + 1);
    return [...c].map(([value, count]) => ({ value, label: value, count })).sort((a, b) => a.value.localeCompare(b.value));
  }, [active]);
  const rows = useMemo(() => active.filter(r => (!state || r.state === state) && (!type || r.e.type === type)), [active, state, type]);
  const retiredRows = onlyCited ? retired.filter(r => r.liveCiters.length) : retired;
  const stillCited = retired.filter(r => r.liveCiters.length).length;
  const [limit, more] = useIncremental(`${state}|${type}`, STEP);
  const stateFacets = FRESHNESS_ORDER.map(s => ({ value: s, label: FRESHNESS_LABELS[s], count: counts.get(s) ?? 0 }));

  return <div className="page wide"><div className="eyebrow">Provenance over time</div><h1>Evidence freshness</h1>
    <ClaimAssessment claims={vault.docs.filter(d => Boolean(d.claimId))}/>
    <p className="lede">How long ago each active source was last checked, measured against the access-age windows in <code>whykit.toml</code>, and which notes still rely on retired sources.</p>
    {vault.evidence.length ? <>
      <div className="metrics">
        <Metric label="Stale" value={counts.get("stale") ?? 0} detail={windows.length ? "past their window" : "no windows set"}/>
        <Metric label="Due soon" value={counts.get("due") ?? 0} detail="last fifth of window"/>
        <Metric label="Fresh" value={counts.get("fresh") ?? 0}/>
        <Metric label="Retired, still cited" value={stillCited} detail={`of ${retired.length} retired`}/>
      </div>

      <section aria-labelledby="fresh-policy"><h2 id="fresh-policy">Policy</h2>
        {windows.length ? <div className="policy-list">{windows.map(([t, days]) => <div key={t}><span>{t}</span><strong className="mono">{days} days</strong></div>)}</div>
          : <div className="notice"><AlertTriangle size={16} aria-hidden="true"/><span>No access-age windows are configured, so the ages below are informational and nothing can go stale. Add an <code>[evidence_access_age_days]</code> table to <code>whykit.toml</code> to set one per evidence type.</span></div>}
      </section>

      <section aria-labelledby="fresh-active"><h2 id="fresh-active">Active evidence</h2>
        <div className="filters" role="group" aria-label="Filter active evidence">
          <FacetSelect id="fr-state" label="Freshness" value={state} facets={stateFacets} onChange={v => setParams({ state: v || null })}/>
          <FacetSelect id="fr-type" label="Type" value={type} facets={types} onChange={v => setParams({ type: v || null })}/>
          {state || type ? <button className="text-button clear" onClick={() => setParams({ state: null, type: null })}>Clear filters</button> : null}
        </div>
        <p className="muted small" role="status" aria-live="polite">{state || type ? `${rows.length} of ${active.length} active rows match` : `${active.length} active rows`}</p>
        {rows.length ? <>
          <div className="table-wrap freshness-table" tabIndex={0} role="region" aria-label="Evidence freshness"><table><thead><tr><th scope="col">ID</th><th scope="col">Freshness</th><th scope="col">Source</th><th scope="col">Type</th><th scope="col">Accessed</th><th scope="col">Age / window (days)</th><th scope="col">Cited by</th></tr></thead>
            <tbody>{rows.slice(0, limit).map(r => <tr key={r.e.id}><td className="mono decision-id">{r.e.id}</td><td><StatusBadge status={r.state} label={FRESHNESS_LABELS[r.state]}/></td><td>{r.e.source}</td><td>{r.e.type || "—"}</td><td className="mono small">{r.e.accessed || (r.e.date ? `— (dated ${r.e.date})` : "—")}</td><td><AgeMeter row={r}/></td><td><Citers docs={r.citers}/></td></tr>)}</tbody></table></div>
          <ShowMore shown={Math.min(limit, rows.length)} total={rows.length} step={STEP} onMore={more} noun="rows"/>
        </> : <Empty>No active evidence matches these filters.</Empty>}
      </section>

      <section aria-labelledby="fresh-retired"><h2 id="fresh-retired">Retired evidence and who still cites it</h2>
        <p className="muted">Superseded and archived notes may keep citing what was believed at the time. Notes still in force that cite a retired source need a new source or a review.</p>
        {retired.length ? <>
          <label className="check"><input type="checkbox" checked={onlyCited} onChange={e => setParams({ retired: e.target.checked ? "cited" : null })}/> Only sources still cited by notes in force</label>
          {retiredRows.length ? <ul className="retired-list">{retiredRows.map(r => <li key={r.e.id} className="card">
            <div className="retired-head"><span className="mono decision-id">{r.e.id}</span><StatusBadge status="retired"/>{r.e.retiredOn ? <span className="muted small">retired {r.e.retiredOn}</span> : null}{r.replacement ? <span className="muted small">→ replaced by <span className="mono">{r.replacement.id}</span></span> : null}</div>
            <h3>{r.e.source}</h3>
            {r.e.why ? <p>{r.e.why}</p> : null}
            {r.liveCiters.length ? <div className="citers"><strong>Still cited by {r.liveCiters.length} note{r.liveCiters.length === 1 ? "" : "s"} in force</strong><CiterList docs={r.liveCiters}/></div>
              : <p className="muted small">No note in force cites it.</p>}
            {r.historicalCiters.length ? <p className="muted small">Also cited by {r.historicalCiters.length} superseded or archived note{r.historicalCiters.length === 1 ? "" : "s"}: {r.historicalCiters.map((d, i) => <span key={d.id}>{i ? ", " : ""}<a href={hrefFor("doc", d.id)}>{d.title}</a></span>)}</p> : null}
          </li>)}</ul> : <Empty tone="ok">No retired source is cited by a note in force.</Empty>}
        </> : <Empty>No evidence has been retired.</Empty>}
      </section>
    </> : <Empty>The evidence register is empty, so there is nothing to age yet.</Empty>}
  </div>;
}
