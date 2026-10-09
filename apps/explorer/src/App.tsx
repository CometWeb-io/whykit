import {
  useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type ChangeEvent, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent,
} from "react";
import {
  BookOpen, CheckCircle2, ChevronRight, FileText, GitBranch, HeartPulse, History, Hourglass,
  Menu, Network, Scale, Search, Shield, X, AlertTriangle, Copy, Terminal,
} from "lucide-react";
import { vault, docs, resolveDoc, linksFor, backlinksFor, canonicalDocs, searchDocs, evidenceFor, docsForEvidence, decisionChain, bodyOf, openCues, useBodies, findings as lintFindings, useFindings } from "./lib/vault.ts";
import { createSlugger, inlineText, parseMarkdown, type MdBlock, type MdInline } from "./lib/markdown.ts";
import { hrefFor, type Params, type Route, type View } from "./lib/route.ts";
import { describeDue, reviewQueue } from "./lib/reviews.ts";
import { currentRoute, go, setParams, useRoute } from "./nav.ts";
import { countExposed, Empty, ExposureNotice, Metric, Sensitivity, ShowMore, StatusBadge, useIncremental } from "./ui.tsx";
import { ClaimAssessment } from "./ui.tsx";
import { GraphPage } from "./views/GraphPage.tsx";
import { TimelinePage } from "./views/TimelinePage.tsx";
import { FreshnessPage } from "./views/FreshnessPage.tsx";

const PRIMARY: { id: View; label: string; icon: typeof BookOpen }[] = [
  { id: "home", label: "Home", icon: BookOpen },
  { id: "decisions", label: "Decisions", icon: Scale },
  { id: "timeline", label: "Timeline", icon: History },
  { id: "evidence", label: "Evidence", icon: Shield },
  { id: "freshness", label: "Freshness", icon: Hourglass },
  { id: "reviews", label: "Reviews", icon: CheckCircle2 },
  { id: "graph", label: "Graph", icon: Network },
  { id: "health", label: "Health", icon: HeartPulse },
];

const VIEW_TITLES: Record<View, string> = {
  home: "Home", decisions: "Decisions", timeline: "Decision timeline", evidence: "Evidence", freshness: "Evidence freshness", reviews: "Reviews",
  graph: "Knowledge graph", health: "Health", templates: "Templates", adopt: "Adopt WhyKit", doc: "Document",
};

const WORKSTREAM_LABELS: Record<string, string> = {
  "00-context": "Context",
  "01-strategy": "Strategy",
  "02-discoverability": "Discoverability",
  "03-website": "Website",
  "04-automation": "Automation",
  "05-operations": "Operations",
  "06-decisions": "Decisions",
  "07-research": "Research",
  reports: "Reports",
};

/** `07-research` → `Research`; used when a workstream README has no title. */
function humanizeDir(dir: string): string {
  const words = dir.replace(/^\d+-/, "").replaceAll("-", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function Wordmark() {
  return <a className="wordmark" href={hrefFor("home")} aria-label="WhyKit home"><span className="mark" aria-hidden="true">W</span><span>WhyKit</span></a>;
}

function Inline({ nodes }: { nodes: MdInline[] }) {
  return <>{nodes.map((n, i) => {
    if (n.t === "text") return <span key={i}>{n.v}</span>;
    if (n.t === "code") return <code key={i}>{n.v}</code>;
    if (n.t === "strong") return <strong key={i}><Inline nodes={n.v} /></strong>;
    if (n.t === "em") return <em key={i}><Inline nodes={n.v} /></em>;
    if (n.t === "wiki") {
      const d = resolveDoc(n.target);
      return d ? <a key={i} href={hrefFor("doc", d.id)}>{n.label === n.target ? d.title : n.label}</a>
        : <span key={i} className="broken-link" title={`Unresolved link: ${n.target}`}>{n.label}</span>;
    }
    if (n.t === "link") {
      if (/^https?:/i.test(n.href)) return <a key={i} href={n.href} target="_blank" rel="noreferrer">{n.label}<span className="sr-only"> (opens in a new tab)</span></a>;
      const d = resolveDoc(n.href);
      return d ? <a key={i} href={hrefFor("doc", d.id)}>{n.label}</a> : <span key={i}>{n.label}</span>;
    }
    return null;
  })}</>;
}

function Block({ block, slug }: { block: MdBlock; slug: (text: string) => string }) {
  if (block.t === "h") {
    const Tag = `h${block.level}` as const;
    return <Tag id={slug(inlineText(block.children))}><Inline nodes={block.children} /></Tag>;
  }
  if (block.t === "p") return <p><Inline nodes={block.children} /></p>;
  if (block.t === "ul") return <ul>{block.items.map((x, i) => <li key={i}><Inline nodes={x} /></li>)}</ul>;
  if (block.t === "ol") return <ol>{block.items.map((x, i) => <li key={i}><Inline nodes={x} /></li>)}</ol>;
  if (block.t === "hr") return <hr />;
  if (block.t === "code") return <pre tabIndex={0}><code>{block.code}</code></pre>;
  if (block.t === "quote") return <blockquote>{block.children.map((x, i) => <p key={i}><Inline nodes={x} /></p>)}</blockquote>;
  if (block.t === "callout") return <aside className={`callout callout-${block.kind}`} aria-label={block.kind}>
    <span className="callout-kind" aria-hidden="true">{block.kind}</span>
    {block.title ? <strong>{block.title}</strong> : null}
    {block.children.map((x, i) => <p key={i}><Inline nodes={x} /></p>)}
  </aside>;
  if (block.t === "table") return <div className="table-wrap" tabIndex={0} role="region" aria-label="Table"><table><thead><tr>{block.headers.map((x, i) => <th key={i} scope="col"><Inline nodes={x} /></th>)}</tr></thead><tbody>{block.rows.map((row, ri) => <tr key={ri}>{row.map((x, ci) => <td key={ci}><Inline nodes={x} /></td>)}</tr>)}</tbody></table></div>;
  return null;
}

function MarkdownView({ source, skipH1 = true }: { source: string; skipH1?: boolean }) {
  const blocks = useMemo(() => {
    const parsed = parseMarkdown(source);
    return skipH1 && parsed[0]?.t === "h" && parsed[0].level === 1 ? parsed.slice(1) : parsed;
  }, [source, skipH1]);
  const slug = createSlugger();
  return <div className="markdown">{blocks.map((b, i) => <Block key={i} block={b} slug={slug} />)}</div>;
}

function DueList({ items, limit }: { items: ReturnType<typeof reviewQueue>; limit: number }) {
  return <div className="doc-list">{items.slice(0, limit).map(({ d, days }) => <button key={d.id} onClick={() => go("doc", d.id)}><div><strong>{d.title}</strong><span>{d.requiresReview ? "requires review" : describeDue(days)} · {d.owner || "no owner"}</span></div><span className={days < 0 ? "mono small overdue" : "mono small"}>{d.reviewBy}</span></button>)}</div>;
}

function HomePage() {
  const working = docs.filter(d => d.status !== "template");
  const canon = canonicalDocs();
  const latest = [...vault.decisions].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 4);
  const open = openCues();
  return <div className="page wide">
    <div className="eyebrow">Git-native evidence & decision ledger</div>
    <h1>{vault.vaultName}</h1>
    <p className="lede">The layer that remembers why. Every graph edge, decision and health signal below is derived from the Markdown vault — not a second application database.</p>
    {vault.vaultName.toLowerCase().includes("northline") ? <div className="notice"><AlertTriangle size={16} aria-hidden="true"/><span>This is a synthetic example vault. Northline, its people, metrics and sources are fictional.</span></div> : null}

    <div className="metrics">
      <Metric label="Working docs" value={working.length} />
      <Metric label="Canonical" value={canon.length} />
      <Metric label="Active evidence" value={vault.evidence.filter(e => e.state === "active").length} />
      <Metric label="Decisions" value={vault.decisions.length} />
    </div>

    <section aria-labelledby="home-decisions"><div className="section-head"><div><div className="eyebrow">Recent</div><h2 id="home-decisions">Decision memory</h2></div>{latest.length ? <button className="text-button" onClick={() => go("decisions")}>View all <ChevronRight size={15} aria-hidden="true"/></button> : null}</div>
      {latest.length ? <div className="cards two">{latest.map(d => <button className="card decision-card" key={d.id} onClick={() => go("doc", d.recordId)}><div><span className="mono decision-id">{d.id}</span><span className="muted small">{d.date}</span></div><h3>{d.title}</h3><div className="card-foot"><span>{d.owner}</span><StatusBadge status={d.status}/></div></button>)}</div>
        : <Empty>No decisions recorded yet. Add a row to the decision log when a material choice is made.</Empty>}
    </section>

    <section aria-labelledby="home-canon"><div className="section-head"><div><div className="eyebrow">Canonical layer</div><h2 id="home-canon">Current sources of truth</h2></div></div>
      {canon.length ? <div className="doc-list">{canon.slice(0, 8).map(d => <button key={d.id} onClick={() => go("doc", d.id)}><div><strong>{d.title}</strong><span>{d.summary || d.id}</span></div><div className="row-meta"><Sensitivity value={d.sensitivity}/><ChevronRight size={16} aria-hidden="true"/></div></button>)}</div>
        : <Empty>No approved source-of-truth documents yet. Set <code>source_of_truth: true</code> once a document is approved.</Empty>}
    </section>

    <section><div className="cards two"><div className="card"><div className="eyebrow">Integrity</div><h2>{vault.lint.errors === 0 ? "Mechanical checks pass" : `${vault.lint.errors} lint errors`}</h2><p>{vault.lint.files} files checked. {vault.lint.warnings} warnings. Truth still requires review; these checks only clear mechanical objections.</p><button className="text-button" onClick={() => go("health")}>Open Health <ChevronRight size={15} aria-hidden="true"/></button></div><div className="card"><div className="eyebrow">Unknowns</div><h2>{open} visible review cues</h2><p>Open-question headings and “Needs verification” markers remain visible rather than silently becoming facts.</p></div></div></section>
  </div>;
}

function DecisionsPage() {
  const rows = [...vault.decisions].reverse();
  return <div className="page"><div className="eyebrow">Append-only history</div><h1>Decisions</h1><p className="lede">Material choices keep their original rationale. Reversals create new records instead of rewriting the past.</p>
    {rows.length ? <p><a className="text-button inline" href={hrefFor("timeline")}>See how long each decision stayed in force <ChevronRight size={15} aria-hidden="true"/></a></p> : null}
    {rows.length ? <ol className="timeline">{rows.map(d => <li key={d.id}><button onClick={() => go("doc", d.recordId)}><div className="timeline-meta"><span className="mono decision-id">{d.id}</span><span>{d.date}</span><StatusBadge status={d.status}/></div><h2>{d.title}</h2><p>{d.owner}{d.supersedes ? ` · supersedes ${d.supersedes}` : ""}{d.supersededBy ? ` · superseded by ${d.supersededBy}` : ""}</p></button></li>)}</ol>
      : <Empty>The decision log is empty.</Empty>}
  </div>;
}

const EVIDENCE_STEP = 150;

function EvidencePage({ params }: { params: Params }) {
  const q = params.q ?? "";
  const needle = useDeferredValue(q.trim().toLowerCase());
  const haystacks = useMemo(() => vault.evidence.map(e => ({ e, text: `${e.id} ${e.source} ${e.sensitivity || "inherited"} ${e.type} ${e.claims} ${e.location} ${e.why || ""}`.toLowerCase() })), []);
  const rows = needle ? haystacks.filter(x => x.text.includes(needle)).map(x => x.e) : vault.evidence;
  const [limit, more] = useIncremental(needle, EVIDENCE_STEP);
  return <div className="page wide"><div className="eyebrow">Provenance</div><h1>Evidence</h1><p className="lede">Active and retired evidence keep stable IDs. Retirement preserves the source history instead of turning existing citations into dangling references.</p>
    {vault.evidence.length ? <>
      <label className="search-field"><Search size={16} aria-hidden="true"/><span className="sr-only">Filter evidence</span><input type="search" value={q} onChange={(e: ChangeEvent<HTMLInputElement>) => setParams({ q: e.target.value || null })} placeholder="Filter evidence…" /></label>
      <p className="muted small" role="status" aria-live="polite">{needle ? `${rows.length} of ${vault.evidence.length} rows match` : `${vault.evidence.length} rows`}</p>
      {rows.length ? <div className="table-wrap evidence-table" tabIndex={0} role="region" aria-label="Evidence register"><table><thead><tr><th scope="col">ID</th><th scope="col">State</th><th scope="col">Source</th><th scope="col">Sensitivity</th><th scope="col">Type / retired</th><th scope="col">Used by</th><th scope="col">Location / replacement</th><th scope="col">Claim / retirement reason</th></tr></thead><tbody>{rows.slice(0, limit).map(e => { const used = docsForEvidence(e.id); return <tr key={e.id}><td className="mono decision-id">{e.id}</td><td><StatusBadge status={e.state}/></td><td>{e.source}</td><td><Sensitivity value={e.sensitivity || "inherited"}/></td><td>{e.state === "active" ? (e.type || "—") : (e.retiredOn || "—")}</td><td><span title={used.map(d => d.title).join(" · ")}>{used.length}</span></td><td><span className="mono small">{e.state === "active" ? (e.location || "—") : (e.replacedBy ? `→ ${e.replacedBy}` : "—")}</span></td><td>{e.state === "active" ? e.claims : e.why}</td></tr>; })}</tbody></table></div>
        : null}
      {rows.length ? <ShowMore shown={Math.min(limit, rows.length)} total={rows.length} step={EVIDENCE_STEP} onMore={more} noun="rows"/>
        : <Empty>No evidence matches “{q.trim()}”.</Empty>}
    </> : <Empty>The evidence register is empty. Register a source before citing its <code>E-NNN</code> id.</Empty>}
  </div>;
}

function ReviewsPage() {
  const due = reviewQueue(docs, new Date(), 30);
  const events = [...vault.reviews].sort((a, b) => b.date.localeCompare(a.date));
  return <div className="page wide"><div className="eyebrow">Auditable re-checks</div><h1>Reviews</h1><p className="lede">The queue says what needs another look. The append-only event log records who reviewed it, what they concluded and when it should be reviewed again.</p>
    <div className="metrics three"><Metric label="Recorded events" value={events.length}/><Metric label="Due ≤30d" value={due.length}/><Metric label="Overdue" value={due.filter(x => x.days < 0).length}/></div>
    {due.length ? <section><h2>Due queue</h2><DueList items={due} limit={12}/></section> : null}
    <section><h2>Review history</h2>{events.length ? <div className="table-wrap" tabIndex={0} role="region" aria-label="Review history"><table><thead><tr><th scope="col">Date</th><th scope="col">Target</th><th scope="col">Reviewer</th><th scope="col">Outcome</th><th scope="col">Next review</th><th scope="col">Note</th></tr></thead><tbody>{events.map((r, i) => { const d = resolveDoc(r.targetId); return <tr key={`${r.date}-${r.targetId}-${i}`}><td className="mono small">{r.date}</td><td>{d ? <a className="table-link" href={hrefFor("doc", d.id)}>{d.title}</a> : r.target}</td><td>{r.reviewer}</td><td><StatusBadge status={r.outcome}/></td><td className="mono small">{r.nextReview || "—"}</td><td>{r.note || "—"}</td></tr>; })}</tbody></table></div> : <Empty>No review events recorded yet.</Empty>}</section>
  </div>;
}

const FINDINGS_STEP = 200;

/** Lint findings, from their own chunk: only this page reads them. */
function Findings() {
  const state = useFindings();
  const all = lintFindings();
  const [limit, more] = useIncremental("", FINDINGS_STEP);
  if (vault.lint.errors + vault.lint.warnings === 0) return <Empty tone="ok">No mechanical findings in this vault.</Empty>;
  if (!all) return state === "failed"
    ? <Empty>The findings could not be loaded. Reload the page to try again.</Empty>
    : <p className="muted findings-loading" role="status">Loading {vault.lint.errors + vault.lint.warnings} findings…</p>;
  return <><ul className="findings">{all.slice(0, limit).map((f, i) => {
    const d = resolveDoc(f.path.replace(/\.md$/, ""));
    const body = <><span>{f.level}</span><code>{f.code}</code><p>{f.message}</p><small>{f.path}{f.line ? `:${f.line}` : ""}</small></>;
    return <li key={i}>{d ? <a href={hrefFor("doc", d.id)} className={`finding ${f.level}`}>{body}</a> : <div className={`finding ${f.level}`}>{body}</div>}</li>;
  })}</ul><ShowMore shown={Math.min(limit, all.length)} total={all.length} step={FINDINGS_STEP} onMore={more} noun="findings"/></>;
}

function HealthPage() {
  const counts = docs.reduce<Record<string, number>>((a, d) => { a[d.status] = (a[d.status] || 0) + 1; return a; }, {});
  const reviews = reviewQueue(docs, new Date(), 30);
  const overdue = reviews.filter(x => x.days < 0);
  return <div className="page"><div className="eyebrow">Deterministic checks</div><h1>Health</h1><p className="lede">Shape, provenance and integrity — never meaning. A clean linter does not certify that a claim is true.</p>
    <div className="metrics three"><Metric label="Errors" value={vault.lint.errors}/><Metric label="Warnings" value={vault.lint.warnings}/><Metric label="Reviews ≤30d" value={reviews.length} detail={overdue.length ? `${overdue.length} overdue` : "none overdue"}/></div>
    {reviews.length ? <section><h2>Review queue</h2><DueList items={reviews} limit={10}/></section> : null}
    <section><h2>Status mix</h2><div className="status-grid">{Object.entries(counts).sort().map(([s, n]) => <div key={s}><StatusBadge status={s}/><strong>{n}</strong></div>)}</div></section>
    <section><h2>Findings</h2><Findings/></section>
    <section><h2>What Health deliberately cannot tell you</h2><div className="card"><ul><li>whether evidence is reliable or cherry-picked;</li><li>whether a hypothesis is commercially sensible;</li><li>whether an accepted decision was a good one;</li><li>whether sensitive data should have been imported at all.</li></ul></div></section>
  </div>;
}

function TemplatesPage() {
  const templates = docs.filter(d => d.status === "template" || d.id.startsWith("templates/"));
  return <div className="page"><div className="eyebrow">Reusable structures</div><h1>Templates</h1><p className="lede">Templates encode the questions a durable record must answer, without pretending the answers already exist.</p>
    {templates.length ? <div className="doc-list">{templates.map(d => <button key={d.id} onClick={() => go("doc", d.id)}><div><strong>{d.title}</strong><span>{d.summary || d.id}</span></div><ChevronRight size={16} aria-hidden="true"/></button>)}</div>
      : <Empty>This vault has no templates.</Empty>}
  </div>;
}

function AdoptPage() {
  const [copied, setCopied] = useState<"idle" | "ok" | "failed">("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const command = "uv run whykit init ../my-ledger";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied("ok");
    } catch {
      setCopied("failed");
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied("idle"), 1600);
  };
  return <div className="page"><div className="eyebrow">Portable by default</div><h1>Adopt WhyKit</h1><p className="lede">The core format is Markdown + Git. Explorer, Obsidian and agent adapters are optional.</p>
    <div className="notice"><Shield size={16} aria-hidden="true"/><span>The template can be public. Your real company vault should normally be private.</span></div>
    <section><h2>1. Create a clean vault</h2><div className="command"><Terminal size={16} aria-hidden="true"/><code>{command}</code><button onClick={() => void copy()}><Copy size={15} aria-hidden="true"/>{copied === "ok" ? "Copied" : copied === "failed" ? "Copy failed" : "Copy"}<span className="sr-only"> command</span></button></div><p className="sr-only" role="status" aria-live="polite">{copied === "ok" ? "Command copied to clipboard" : copied === "failed" ? "Clipboard unavailable; select the command to copy it" : ""}</p></section>
    <section><h2>2. Establish the foundations</h2><ol className="steps"><li>Replace <code>TODO</code> owners and fill <code>00-context/company.md</code>, <code>goals.md</code> and <code>terminology.md</code>.</li><li>Populate evidence before citing <code>E-NNN</code> identifiers.</li><li>Only set <code>source_of_truth: true</code> after a document is approved.</li><li>Run <code>uv run whykit lint --root ../my-ledger</code> before committing.</li></ol></section>
    <section><h2>3. Keep integrations downstream</h2><p>Agents, CRMs, task managers and code repositories can produce or consume handoffs. None of them becomes a second canonical copy of the reasoning ledger.</p></section>
    <section><h2>License</h2><div className="card"><strong>Apache License 2.0</strong><p>Reusable for commercial and private work, with an explicit patent grant. See the repository <code>LICENSE</code> and <code>NOTICE</code> files.</p></div></section>
  </div>;
}

function DocPage({ id }: { id: string }) {
  const bodies = useBodies(true);
  const doc = resolveDoc(id);
  if (!doc) return <div className="page"><h1>Document not found</h1><p className="lede">No note called <code>{id}</code> exists in the generated vault index. It may have been renamed, or the index may be older than the vault — rerun <code>npm run index</code>.</p><a className="button" href={hrefFor("home")}>Back home</a></div>;
  const links = linksFor(doc), backs = backlinksFor(doc);
  const claims = doc.claimId ? [doc] : (doc.claimIds ?? []).map(resolveDoc).filter((d): d is NonNullable<typeof d> => Boolean(d?.claimId));
  const evidenceRows = doc.sourceIds.map(evidenceFor).filter((e): e is NonNullable<ReturnType<typeof evidenceFor>> => Boolean(e));
  const missing = doc.sourceIds.filter(sid => !evidenceFor(sid));
  return <div className="page doc-page"><nav aria-label="Breadcrumb"><a className="crumb" href={hrefFor("home")}>WhyKit <ChevronRight size={13} aria-hidden="true"/> <span>{doc.id}</span></a></nav>
    <div className="doc-meta"><StatusBadge status={doc.status}/><Sensitivity value={doc.sensitivity}/><span className="mono">{doc.type}</span>{doc.sourceOfTruth ? <span className="canonical">source of truth</span> : null}</div>
    <h1>{doc.title}</h1>
    <ClaimAssessment claims={claims}/><div className="doc-sub"><span>{doc.owner || "No owner"}</span><span>Updated {doc.lastUpdated || "—"}</span>{doc.reviewBy ? <span>Review by {doc.reviewBy}</span> : null}{doc.sourceIds.length ? <span>{doc.sourceIds.length} evidence ID{doc.sourceIds.length === 1 ? "" : "s"}</span> : null}</div>
    {doc.decisionId ? <DecisionLineage decisionId={doc.decisionId}/> : null}
    <DocBody body={bodyOf(doc)} state={bodies}/>
    {(evidenceRows.length || missing.length) ? <section><h2>Registered evidence</h2>
      {evidenceRows.length ? <div className="cards two">{evidenceRows.map(e => <div className="card" key={e.id}><div className="eyebrow">{e.id} · {e.state === "active" ? (e.type || "evidence") : "retired"}</div><h3>{e.source}</h3><Sensitivity value={e.sensitivity || "inherited"}/><p>{e.state === "active" ? e.claims : e.why}</p><small className="mono">{e.state === "active" ? e.location : (e.replacedBy ? `replacement: ${e.replacedBy}` : `retired ${e.retiredOn || ""}`)}</small></div>)}</div> : null}
      {missing.length ? <Empty>Not in the evidence register: <code>{missing.join(", ")}</code></Empty> : null}
    </section> : null}
    {(links.length || backs.length) ? <section className="relations"><h2>Relationships</h2><div className="cards two">{links.length ? <div className="card"><div className="eyebrow">Links to</div>{links.map(d => <a className="relation" key={d.id} href={hrefFor("doc", d.id)}>{d.title}<ChevronRight size={14} aria-hidden="true"/></a>)}</div> : null}{backs.length ? <div className="card"><div className="eyebrow">Referenced by</div>{backs.map(d => <a className="relation" key={d.id} href={hrefFor("doc", d.id)}>{d.title}<ChevronRight size={14} aria-hidden="true"/></a>)}</div> : null}</div></section> : null}
  </div>;
}

function DocBody({ body, state }: { body: string | undefined; state: ReturnType<typeof useBodies> }) {
  if (body !== undefined) return <MarkdownView source={body}/>;
  return state === "failed"
    ? <Empty>The note text could not be loaded. Reload the page to try again.</Empty>
    : <p className="muted doc-loading" role="status">Loading the note text…</p>;
}

function DecisionLineage({ decisionId }: { decisionId: string }) {
  const chain = decisionChain(decisionId);
  if (chain.length < 2) return null;
  const latest = chain.at(-1);
  const isLatest = latest?.id === decisionId;
  return <section className="lineage" aria-labelledby="lineage-title">
    <div className="eyebrow">Append-only history</div>
    <h2 id="lineage-title">Supersession chain</h2>
    {!isLatest && latest ? <p className="notice"><AlertTriangle size={16} aria-hidden="true"/><span>{decisionId} is no longer current. The latest decision in this chain is <a href={hrefFor("doc", latest.recordId)}>{latest.id}: {latest.title}</a>.</span></p> : null}
    <ol className="chain">{chain.map((d, i) => {
      const here = d.id === decisionId;
      const body = <><span className="chain-head"><span className="mono decision-id">{d.id}</span><StatusBadge status={d.status}/></span><span className="chain-title">{d.title}</span><span className="muted small">{d.date}{i > 0 ? ` · supersedes ${chain[i - 1]?.id ?? ""}` : ""}</span></>;
      return <li key={d.id} className={here ? "chain-step current" : "chain-step"} aria-current={here ? "step" : undefined}>
        {here ? <div className="chain-card">{body}</div> : <a className="chain-card" href={hrefFor("doc", d.recordId)}>{body}</a>}
      </li>;
    })}</ol>
  </section>;
}

function SearchOverlay({ initial, onClose }: { initial: string; onClose: () => void }) {
  // The query is mirrored into the address (`search=`), so a reload or a
  // shared link reopens the dialog with the same results.
  const [q, setQ] = useState(initial);
  const [active, setActive] = useState(0);
  const deferred = useDeferredValue(q);
  // Titles, ids, tags and summaries are searchable at once; note text joins
  // the results when its chunk arrives.
  const bodies = useBodies(true);
  // `bodies` is a real input: searchDocs reads the chunk once it is attached.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const results = useMemo(() => searchDocs(deferred), [deferred, bodies]);
  // The highlighted row resets when the query changes; clamp while the
  // deferred result list catches up so it never points past the end.
  const current = Math.min(active, Math.max(results.length - 1, 0));
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  // Focus before the first paint so keys typed right after the shortcut land in the box.
  useLayoutEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    input.current?.focus();
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; opener?.focus?.(); };
  }, []);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);
  const open = (id: string) => { onClose(); go("doc", id); };
  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
    if (e.key === "Tab") {
      // Keep focus inside the modal dialog.
      const focusable = [...e.currentTarget.querySelectorAll<HTMLElement>("input, button")];
      const first = focusable[0], last = focusable.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      return;
    }
    if (!results.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((current + 1) % results.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((current - 1 + results.length) % results.length); }
    else if (e.key === "Enter" && e.target === input.current) { e.preventDefault(); const hit = results[current]; if (hit) open(hit.id); }
  };
  const optionId = (i: number) => `search-option-${i}`;
  // The backdrop is a pointer-only convenience: Escape and the close button
  // are the keyboard paths out, and the dialog owns its own key handling.
  // Options are reached through aria-activedescendant on the combobox, so they
  // are intentionally not focusable themselves.
  /* eslint-disable jsx-a11y/no-static-element-interactions, jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus */
  return <div className="overlay" onMouseDown={(e: ReactMouseEvent<HTMLDivElement>) => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="palette" role="dialog" aria-modal="true" aria-label="Search vault" onKeyDown={onKeyDown}>
      <div className="palette-input"><Search size={18} aria-hidden="true"/><input ref={input} type="text" value={q} onChange={(e: ChangeEvent<HTMLInputElement>) => { setQ(e.target.value); setActive(0); setParams({ search: e.target.value }); }} placeholder="Search titles, claims, tags…" aria-label="Search vault" role="combobox" aria-expanded={results.length > 0} aria-controls="search-results" aria-autocomplete="list" aria-activedescendant={results.length ? optionId(current) : undefined}/><button onClick={onClose} aria-label="Close search"><X size={17} aria-hidden="true"/></button></div>
      <div className="palette-results" id="search-results" role="listbox" aria-label="Results" ref={list}>
        {results.map((d, i) => <div key={d.id} id={optionId(i)} data-index={i} role="option" aria-selected={i === current} className={i === current ? "option active" : "option"} onMouseMove={() => setActive(i)} onClick={() => open(d.id)}><div><strong>{d.title}</strong><small>{d.id}</small></div><StatusBadge status={d.status}/></div>)}
      </div>
      <p className="palette-status" role="status" aria-live="polite">{!deferred.trim() ? "Type to search. ↑↓ to move, Enter to open, Esc to close." : results.length ? `${results.length} result${results.length === 1 ? "" : "s"}` : "No matching notes."}{deferred.trim() && bodies !== "ready" ? (bodies === "failed" ? " Note text could not be loaded; titles, tags and summaries only." : " Still loading note text; titles, tags and summaries only so far.") : ""}</p>
    </div>
  </div>;
  /* eslint-enable jsx-a11y/no-static-element-interactions, jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus */
}

const EXPOSED = countExposed([...docs, ...vault.evidence.map(e => ({ sensitivity: e.sensitivity || "internal" }))]);

function App() {
  const route = useRoute();
  // Fetch the note bodies once the first paint is done, so search and the
  // document view rarely have to wait for them.
  useBodies();
  const search = route.params?.search;
  const setSearch = (open: boolean) => setParams({ search: open ? (currentRoute().params?.search ?? "") : null });
  // The mobile menu belongs to the route it was opened on, so any navigation
  // closes it without an effect that sets state after render.
  const [menuRoute, setMenuRoute] = useState<Route | null>(null);
  const menu = menuRoute === route;
  const setMenu = (open: boolean) => setMenuRoute(open ? route : null);
  const main = useRef<HTMLElement>(null);
  const firstRender = useRef(true);
  // A layout effect: the shortcut works from the first painted frame, not after
  // the browser gets round to passive effects on a busy main thread.
  useLayoutEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setParams({ search: currentRoute().params?.search === undefined ? "" : null });
      }
      else if (e.key === "Escape") setMenuRoute(null);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  useEffect(() => {
    const doc = route.view === "doc" && route.doc ? resolveDoc(route.doc) : undefined;
    document.title = `${doc ? doc.title : VIEW_TITLES[route.view]} · WhyKit Explorer`;
    // A route change replaces the whole page: start at the top and move focus
    // to the new content so keyboard and screen-reader users are not left on a
    // control that no longer exists. Skip the initial load.
    if (firstRender.current) { firstRender.current = false; return; }
    window.scrollTo(0, 0);
    main.current?.focus({ preventScroll: true });
  }, [route.view, route.doc]);
  const streamDocs = useMemo(() => docs
    .filter(d => /^[^/]+\/README$/.test(d.id) && !d.id.startsWith("templates/"))
    .map(d => { const dir = d.id.split("/")[0]!; return { dir, label: WORKSTREAM_LABELS[dir] ?? (d.title === "README" ? humanizeDir(dir) : d.title), doc: d, index: dir.match(/^(\d+)-/)?.[1] }; }), []);
  const current = route.view;
  const params = route.params ?? {};
  const navButton = (active: boolean) => ({ className: active ? "active" : "", "aria-current": active ? "page" as const : undefined });
  return <div className="app"><a className="skip" href="#content" onClick={e => { e.preventDefault(); main.current?.focus(); }}>Skip to content</a><header><button className="icon-button mobile" onClick={() => setMenu(true)} aria-label="Open navigation" aria-expanded={menu} aria-controls="sidebar"><Menu size={18} aria-hidden="true"/></button><Wordmark/><button className="search-trigger" onClick={() => setSearch(true)} aria-label="Search vault" aria-haspopup="dialog" aria-keyshortcuts="Meta+K Control+K"><Search size={15} aria-hidden="true"/><span>Search vault</span><kbd aria-hidden="true">⌘K</kbd></button><div className="header-health"><span className={vault.lint.errors ? "dot bad" : "dot"} aria-hidden="true"/>{vault.lint.errors ? `${vault.lint.errors} errors` : "Vault clean"}</div></header>
    <aside id="sidebar" className={menu ? "sidebar open" : "sidebar"}>{menu ? <button className="close-nav" onClick={() => setMenu(false)} aria-label="Close navigation"><X size={18} aria-hidden="true"/></button> : null}<nav aria-label="Vault"><div className="nav-label">Vault</div>{PRIMARY.map(x => <button {...navButton(current === x.id)} key={x.id} onClick={() => go(x.id)}><x.icon size={16} aria-hidden="true"/>{x.label}</button>)}{streamDocs.length ? <><div className="nav-label">Spine</div>{streamDocs.map(x => <button key={x.dir} {...navButton(route.doc === x.doc.id)} onClick={() => go("doc", x.doc.id)}><span className="nav-index" aria-hidden="true">{x.index ?? ""}</span>{x.label}</button>)}</> : null}<div className="nav-label">More</div><button {...navButton(current === "templates")} onClick={() => go("templates")}><FileText size={16} aria-hidden="true"/>Templates</button><button {...navButton(current === "adopt")} onClick={() => go("adopt")}><GitBranch size={16} aria-hidden="true"/>Adopt</button></nav><div className="sidebar-foot"><span>Generated from Markdown</span><small><time dateTime={vault.generatedAt}>{new Date(vault.generatedAt).toLocaleString()}</time></small></div></aside>
    {menu ? <button className="scrim" onClick={() => setMenu(false)} aria-label="Close navigation" tabIndex={-1}/> : null}
    <main id="content" ref={main} tabIndex={-1}><ExposureNotice counts={EXPOSED}/>{route.view === "home" ? <HomePage/> : route.view === "decisions" ? <DecisionsPage/> : route.view === "timeline" ? <TimelinePage params={params}/> : route.view === "evidence" ? <EvidencePage params={params}/> : route.view === "freshness" ? <FreshnessPage params={params}/> : route.view === "reviews" ? <ReviewsPage/> : route.view === "graph" ? <GraphPage params={params}/> : route.view === "health" ? <HealthPage/> : route.view === "templates" ? <TemplatesPage/> : route.view === "adopt" ? <AdoptPage/> : <DocPage id={route.doc || "Home"}/>}</main>
    {search !== undefined ? <SearchOverlay initial={search} onClose={() => setSearch(false)}/> : null}
  </div>;
}

export default App;
