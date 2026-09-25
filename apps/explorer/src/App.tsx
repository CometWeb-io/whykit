import { useEffect, useMemo, useState, type ChangeEvent, type MouseEvent as ReactMouseEvent } from "react";
import {
  BookOpen, CheckCircle2, ChevronRight, FileText, GitBranch, HeartPulse,
  Menu, Network, Scale, Search, Shield, X, AlertTriangle, Copy, Terminal,
} from "lucide-react";
import { vault, docs, resolveDoc, linksFor, backlinksFor, canonicalDocs, searchDocs, evidenceFor, docsForEvidence } from "./lib/vault";
import { inlineText, parseMarkdown, slugify, type MdBlock, type MdInline } from "./lib/markdown";
import type { DocStatus, VaultDoc } from "./types";

type View = "home" | "decisions" | "evidence" | "reviews" | "graph" | "health" | "templates" | "adopt" | "doc";

const PRIMARY: { id: View; label: string; icon: typeof BookOpen }[] = [
  { id: "home", label: "Home", icon: BookOpen },
  { id: "decisions", label: "Decisions", icon: Scale },
  { id: "evidence", label: "Evidence", icon: Shield },
  { id: "reviews", label: "Reviews", icon: CheckCircle2 },
  { id: "graph", label: "Graph", icon: Network },
  { id: "health", label: "Health", icon: HeartPulse },
];

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

function parseHash(): { view: View; doc?: string } {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const doc = params.get("doc") || undefined;
  if (doc) return { view: "doc", doc };
  const raw = params.get("view") as View | null;
  const allowed: View[] = ["home", "decisions", "evidence", "reviews", "graph", "health", "templates", "adopt"];
  return { view: raw && allowed.includes(raw) ? raw : "home" };
}

function go(view: View, doc?: string) {
  location.hash = doc ? `doc=${encodeURIComponent(doc)}` : `view=${view}`;
}

function useRoute() {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    if (!location.hash) location.hash = "view=home";
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status.replace("_", " ")}</span>;
}

function Sensitivity({ value }: { value: string }) {
  return <span className={`sensitivity sensitivity-${value}`}>{value}</span>;
}

function Wordmark() {
  return <button className="wordmark" onClick={() => go("home")} aria-label="WhyKit home"><span className="mark">W</span><span>WhyKit</span></button>;
}

function Inline({ nodes }: { nodes: MdInline[] }) {
  return <>{nodes.map((n, i) => {
    if (n.t === "text") return <span key={i}>{n.v}</span>;
    if (n.t === "code") return <code key={i}>{n.v}</code>;
    if (n.t === "strong") return <strong key={i}><Inline nodes={n.v} /></strong>;
    if (n.t === "em") return <em key={i}><Inline nodes={n.v} /></em>;
    if (n.t === "wiki") {
      const d = resolveDoc(n.target);
      return d ? <a key={i} href={`#doc=${encodeURIComponent(d.id)}`}>{n.label === n.target ? d.title : n.label}</a>
        : <span key={i} className="broken-link">{n.label}</span>;
    }
    if (n.t === "link") {
      if (/^https?:/.test(n.href)) return <a key={i} href={n.href} target="_blank" rel="noreferrer">{n.label}</a>;
      const d = resolveDoc(n.href);
      return d ? <a key={i} href={`#doc=${encodeURIComponent(d.id)}`}>{n.label}</a> : <span key={i}>{n.label}</span>;
    }
    return null;
  })}</>;
}

function Block({ block }: { block: MdBlock }) {
  if (block.t === "h") {
    const Tag = `h${block.level}` as "h1" | "h2" | "h3" | "h4";
    return <Tag id={slugify(inlineText(block.children))}><Inline nodes={block.children} /></Tag>;
  }
  if (block.t === "p") return <p><Inline nodes={block.children} /></p>;
  if (block.t === "ul") return <ul>{block.items.map((x, i) => <li key={i}><Inline nodes={x} /></li>)}</ul>;
  if (block.t === "ol") return <ol>{block.items.map((x, i) => <li key={i}><Inline nodes={x} /></li>)}</ol>;
  if (block.t === "hr") return <hr />;
  if (block.t === "code") return <pre><code>{block.code}</code></pre>;
  if (block.t === "quote") return <blockquote>{block.children.map((x, i) => <p key={i}><Inline nodes={x} /></p>)}</blockquote>;
  if (block.t === "callout") return <aside className={`callout callout-${block.kind}`}>
    <span className="callout-kind">{block.kind}</span>
    {block.title ? <strong>{block.title}</strong> : null}
    {block.children.map((x, i) => <p key={i}><Inline nodes={x} /></p>)}
  </aside>;
  if (block.t === "table") return <div className="table-wrap"><table><thead><tr>{block.headers.map((x, i) => <th key={i}><Inline nodes={x} /></th>)}</tr></thead><tbody>{block.rows.map((row, ri) => <tr key={ri}>{row.map((x, ci) => <td key={ci}><Inline nodes={x} /></td>)}</tr>)}</tbody></table></div>;
  return null;
}

function MarkdownView({ source, skipH1 = true }: { source: string; skipH1?: boolean }) {
  const parsed = parseMarkdown(source);
  const blocks = skipH1 && parsed[0]?.t === "h" && parsed[0].level === 1 ? parsed.slice(1) : parsed;
  return <div className="markdown">{blocks.map((b, i) => <Block key={i} block={b} />)}</div>;
}

function Metric({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong>{detail ? <small>{detail}</small> : null}</div>;
}

function reviewQueue(days = 30) {
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return docs
    .filter(d => d.status === "approved" && /^\d{4}-\d{2}-\d{2}$/.test(d.reviewBy))
    .map(d => {
      const [year, month, day] = d.reviewBy.split("-").map(Number);
      const due = Date.UTC(year!, month! - 1, day!);
      return { d, days: Math.round((due - today) / 86_400_000) };
    })
    .filter(x => x.days <= days)
    .sort((a, b) => a.days - b.days || a.d.title.localeCompare(b.d.title));
}

function HomePage() {
  const working = docs.filter(d => d.status !== "template");
  const canon = canonicalDocs();
  const latest = [...vault.decisions].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 4);
  const open = docs.reduce((n, d) => n + (d.body.match(/Needs verification|## Open questions/gi)?.length || 0), 0);
  return <div className="page wide">
    <div className="eyebrow">Git-native evidence & decision ledger</div>
    <h1>{vault.vaultName}</h1>
    <p className="lede">The layer that remembers why. Every graph edge, decision and health signal below is derived from the Markdown vault — not a second application database.</p>
    {vault.vaultName.toLowerCase().includes("northline") ? <div className="notice"><AlertTriangle size={16}/><span>This is a synthetic example vault. Northline, its people, metrics and sources are fictional.</span></div> : null}

    <div className="metrics">
      <Metric label="Working docs" value={working.length} />
      <Metric label="Canonical" value={canon.length} />
      <Metric label="Active evidence" value={vault.evidence.filter(e=>e.state==="active").length} />
      <Metric label="Decisions" value={vault.decisions.length} />
    </div>

    <section><div className="section-head"><div><div className="eyebrow">Recent</div><h2>Decision memory</h2></div><button className="text-button" onClick={() => go("decisions")}>View all <ChevronRight size={15}/></button></div>
      <div className="cards two">{latest.map(d => <button className="card decision-card" key={d.id} onClick={() => go("doc", d.recordId)}><div><span className="mono decision-id">{d.id}</span><span className="muted small">{d.date}</span></div><h3>{d.title}</h3><div className="card-foot"><span>{d.owner}</span><StatusBadge status={d.status}/></div></button>)}</div>
    </section>

    <section><div className="section-head"><div><div className="eyebrow">Canonical layer</div><h2>Current sources of truth</h2></div></div>
      <div className="doc-list">{canon.slice(0, 8).map(d => <button key={d.id} onClick={() => go("doc", d.id)}><div><strong>{d.title}</strong><span>{d.summary || d.id}</span></div><div className="row-meta"><Sensitivity value={d.sensitivity}/><ChevronRight size={16}/></div></button>)}</div>
    </section>

    <section><div className="cards two"><div className="card"><div className="eyebrow">Integrity</div><h2>{vault.lint.errors === 0 ? "Mechanical checks pass" : `${vault.lint.errors} lint errors`}</h2><p>{vault.lint.files} files checked. {vault.lint.warnings} warnings. Truth still requires review; these checks only clear mechanical objections.</p><button className="text-button" onClick={() => go("health")}>Open Health <ChevronRight size={15}/></button></div><div className="card"><div className="eyebrow">Unknowns</div><h2>{open} visible review cues</h2><p>Open-question headings and “Needs verification” markers remain visible rather than silently becoming facts.</p></div></div></section>
  </div>;
}

function DecisionsPage() {
  return <div className="page"><div className="eyebrow">Append-only history</div><h1>Decisions</h1><p className="lede">Material choices keep their original rationale. Reversals create new records instead of rewriting the past.</p>
    <ol className="timeline">{[...vault.decisions].reverse().map(d => <li key={d.id}><button onClick={() => go("doc", d.recordId)}><div className="timeline-meta"><span className="mono decision-id">{d.id}</span><span>{d.date}</span><StatusBadge status={d.status}/></div><h2>{d.title}</h2><p>{d.owner}{d.supersedes ? ` · supersedes ${d.supersedes}` : ""}{d.supersededBy ? ` · superseded by ${d.supersededBy}` : ""}</p></button></li>)}</ol>
  </div>;
}

function EvidencePage() {
  const [q, setQ] = useState("");
  const rows = vault.evidence.filter(e => `${e.id} ${e.source} ${e.type} ${e.claims} ${e.location}`.toLowerCase().includes(q.toLowerCase()));
  return <div className="page wide"><div className="eyebrow">Provenance</div><h1>Evidence</h1><p className="lede">Active and retired evidence keep stable IDs. Retirement preserves the source history instead of turning existing citations into dangling references.</p>
    <label className="search-field"><Search size={16}/><input value={q} onChange={(e: ChangeEvent<HTMLInputElement>)=>setQ(e.target.value)} placeholder="Filter evidence…" /></label>
    <div className="table-wrap evidence-table"><table><thead><tr><th>ID</th><th>State</th><th>Source</th><th>Type / retired</th><th>Used by</th><th>Location / replacement</th><th>Claim / retirement reason</th></tr></thead><tbody>{rows.map(e=>{const used=docsForEvidence(e.id);return <tr key={e.id}><td className="mono decision-id">{e.id}</td><td><StatusBadge status={e.state}/></td><td>{e.source}</td><td>{e.state==="active"?(e.type||"—"):(e.retiredOn||"—")}</td><td><span title={used.map(d=>d.title).join(" · ")}>{used.length}</span></td><td><span className="mono small">{e.state==="active"?(e.location||"—"):(e.replacedBy?`→ ${e.replacedBy}`:"—")}</span></td><td>{e.state==="active"?e.claims:e.why}</td></tr>})}</tbody></table></div>
  </div>;
}

function ReviewsPage() {
  const due = reviewQueue(30);
  const events = [...vault.reviews].sort((a,b)=>b.date.localeCompare(a.date));
  return <div className="page wide"><div className="eyebrow">Auditable re-checks</div><h1>Reviews</h1><p className="lede">The queue says what needs another look. The append-only event log records who reviewed it, what they concluded and when it should be reviewed again.</p>
    <div className="metrics three"><Metric label="Recorded events" value={events.length}/><Metric label="Due ≤30d" value={due.length}/><Metric label="Overdue" value={due.filter(x=>x.days<0).length}/></div>
    {due.length?<section><h2>Due queue</h2><div className="doc-list">{due.slice(0,12).map(({d,days})=><button key={d.id} onClick={()=>go("doc",d.id)}><div><strong>{d.title}</strong><span>{days<0?`${Math.abs(days)} day(s) overdue`:days===0?"due today":`due in ${days} day(s)`} · {d.owner||"no owner"}</span></div><span className="mono small">{d.reviewBy}</span></button>)}</div></section>:null}
    <section><h2>Review history</h2>{events.length?<div className="table-wrap"><table><thead><tr><th>Date</th><th>Target</th><th>Reviewer</th><th>Outcome</th><th>Next review</th><th>Note</th></tr></thead><tbody>{events.map((r,i)=>{const d=resolveDoc(r.targetId);return <tr key={`${r.date}-${r.targetId}-${i}`}><td className="mono small">{r.date}</td><td>{d?<button className="table-link" onClick={()=>go("doc",d.id)}>{d.title}</button>:r.target}</td><td>{r.reviewer}</td><td><StatusBadge status={r.outcome}/></td><td className="mono small">{r.nextReview||"—"}</td><td>{r.note||"—"}</td></tr>})}</tbody></table></div>:<div className="empty"><CheckCircle2 size={18}/> No review events recorded yet.</div>}</section>
  </div>;
}

function GraphPage() {
  const graphDocs = docs.filter(d => d.status !== "template" && d.id !== "README" && !d.id.endsWith("/README"));
  const groups = [...new Set(graphDocs.map(d => d.workstream))];
  const nodes = graphDocs.map((d, i) => {
    const gi = Math.max(0, groups.indexOf(d.workstream));
    const inside = graphDocs.filter(x => x.workstream === d.workstream).indexOf(d);
    return { d, x: 80 + (gi % 4) * 260 + (inside % 3) * 62, y: 65 + Math.floor(gi / 4) * 230 + Math.floor(inside / 3) * 38 };
  });
  const byId = new Map(nodes.map(n => [n.d.id, n]));
  const edges = graphDocs.flatMap(d => linksFor(d).map(to => ({ from: d.id, to: to.id }))).filter(e => byId.has(e.to));
  const [hover, setHover] = useState<string | null>(null);
  const connected = useMemo(() => new Set<string>([hover, ...edges.flatMap(e => e.from === hover ? [e.to] : e.to === hover ? [e.from] : [])].filter((x): x is string => Boolean(x))), [hover, edges]);
  const maxX = Math.max(980, ...nodes.map(n=>n.x+120)); const maxY=Math.max(500,...nodes.map(n=>n.y+80));
  return <div className="page wide"><div className="eyebrow">Resolved wikilinks</div><h1>Knowledge graph</h1><p className="lede">Edges come from real Markdown links. Ambiguous aliases are lint errors rather than arbitrary graph connections.</p>
    <div className="graph-wrap"><svg viewBox={`0 0 ${maxX} ${maxY}`} role="img" aria-label="Knowledge graph">
      {edges.map((e,i)=>{const a=byId.get(e.from),b=byId.get(e.to); if(!a||!b)return null; const on=!hover||e.from===hover||e.to===hover; return <line key={i} x1={a.x+46} y1={a.y+13} x2={b.x+46} y2={b.y+13} className={on&&hover?"edge active":"edge"} opacity={hover && !on ? 0.15 : 0.65}/>})}
      {nodes.map(({d,x,y})=>{const on=!hover||connected.has(d.id); return <g key={d.id} transform={`translate(${x},${y})`} opacity={on?1:.2} onMouseEnter={()=>setHover(d.id)} onMouseLeave={()=>setHover(null)} onClick={()=>go("doc",d.id)} className="graph-node"><rect width="92" height="26" rx="6"/><text x="7" y="17">{d.title.length>18?d.title.slice(0,17)+"…":d.title}</text></g>})}
    </svg></div><p className="muted small">{nodes.length} notes · {edges.length} resolved links</p>
  </div>;
}

function HealthPage() {
  const counts = docs.reduce<Record<string,number>>((a,d)=>{a[d.status]=(a[d.status]||0)+1;return a},{});
  const reviews = reviewQueue(30);
  const overdue = reviews.filter(x => x.days < 0);
  return <div className="page"><div className="eyebrow">Deterministic checks</div><h1>Health</h1><p className="lede">Shape, provenance and integrity — never meaning. A clean linter does not certify that a claim is true.</p>
    <div className="metrics three"><Metric label="Errors" value={vault.lint.errors}/><Metric label="Warnings" value={vault.lint.warnings}/><Metric label="Reviews ≤30d" value={reviews.length} detail={overdue.length ? `${overdue.length} overdue` : "none overdue"}/></div>
    {reviews.length?<section><h2>Review queue</h2><div className="doc-list">{reviews.slice(0,10).map(({d,days})=><button key={d.id} onClick={()=>go("doc",d.id)}><div><strong>{d.title}</strong><span>{days<0?`${Math.abs(days)} day(s) overdue`:days===0?"due today":`due in ${days} day(s)`} · {d.owner||"no owner"}</span></div><span className="mono small">{d.reviewBy}</span></button>)}</div></section>:null}
    <section><h2>Status mix</h2><div className="status-grid">{Object.entries(counts).sort().map(([s,n])=><div key={s}><StatusBadge status={s}/><strong>{n}</strong></div>)}</div></section>
    <section><h2>Findings</h2>{vault.lint.findings.length===0?<div className="empty"><CheckCircle2 size={18}/> No mechanical findings in this vault.</div>:<div className="findings">{vault.lint.findings.map((f,i)=><button key={i} onClick={()=>{const d=resolveDoc(f.path.replace(/\.md$/,""));if(d)go("doc",d.id)}} className={`finding ${f.level}`}><span>{f.level}</span><code>{f.code}</code><p>{f.message}</p><small>{f.path}{f.line?`:${f.line}`:""}</small></button>)}</div>}</section>
    <section><h2>What Health deliberately cannot tell you</h2><div className="card"><ul><li>whether evidence is reliable or cherry-picked;</li><li>whether a hypothesis is commercially sensible;</li><li>whether an accepted decision was a good one;</li><li>whether sensitive data should have been imported at all.</li></ul></div></section>
  </div>;
}

function TemplatesPage() {
  const templates = docs.filter(d => d.status === "template" || d.id.startsWith("templates/"));
  return <div className="page"><div className="eyebrow">Reusable structures</div><h1>Templates</h1><p className="lede">Templates encode the questions a durable record must answer, without pretending the answers already exist.</p><div className="doc-list">{templates.map(d=><button key={d.id} onClick={()=>go("doc",d.id)}><div><strong>{d.title}</strong><span>{d.summary||d.id}</span></div><ChevronRight size={16}/></button>)}</div></div>;
}

function AdoptPage() {
  const [copied,setCopied]=useState(false); const command="python3 scripts/whykit.py init ../my-company-context";
  return <div className="page"><div className="eyebrow">Portable by default</div><h1>Adopt WhyKit</h1><p className="lede">The core format is Markdown + Git. Explorer, Obsidian and agent adapters are optional.</p>
    <div className="notice"><Shield size={16}/><span>The template can be public. Your real company vault should normally be private.</span></div>
    <section><h2>1. Create a clean vault</h2><div className="command"><Terminal size={16}/><code>{command}</code><button onClick={()=>{navigator.clipboard?.writeText(command);setCopied(true);setTimeout(()=>setCopied(false),1200)}} aria-label="Copy command"><Copy size={15}/>{copied?"Copied":"Copy"}</button></div></section>
    <section><h2>2. Establish the foundations</h2><ol className="steps"><li>Replace `TODO` owners and fill `00-context/company.md`, `goals.md` and `terminology.md`.</li><li>Populate evidence before citing `E-NNN` identifiers.</li><li>Only set `source_of_truth: true` after a document is approved.</li><li>Run `python3 scripts/whykit.py lint` before committing.</li></ol></section>
    <section><h2>3. Keep integrations downstream</h2><p>Agents, CRMs, task managers and code repositories can produce or consume handoffs. None of them becomes a second canonical copy of the reasoning ledger.</p></section>
    <section><h2>License</h2><div className="card"><strong>Apache License 2.0</strong><p>Reusable for commercial and private work, with an explicit patent grant. See the repository `LICENSE` and `NOTICE` files.</p></div></section>
  </div>;
}

function DocPage({ id }: { id: string }) {
  const doc = resolveDoc(id);
  if (!doc) return <div className="page"><h1>Document not found</h1><p className="lede">The requested note does not exist in the generated vault index.</p><button className="button" onClick={()=>go("home")}>Back home</button></div>;
  const links=linksFor(doc), backs=backlinksFor(doc);
  const evidenceRows=doc.sourceIds.map(evidenceFor).filter((e): e is NonNullable<ReturnType<typeof evidenceFor>> => Boolean(e));
  return <div className="page doc-page"><button className="crumb" onClick={()=>go("home")}>WhyKit <ChevronRight size={13}/> <span>{doc.id}</span></button>
    <div className="doc-meta"><StatusBadge status={doc.status}/><Sensitivity value={doc.sensitivity}/><span className="mono">{doc.type}</span>{doc.sourceOfTruth?<span className="canonical">source of truth</span>:null}</div>
    <h1>{doc.title}</h1><div className="doc-sub"><span>{doc.owner||"No owner"}</span><span>Updated {doc.lastUpdated||"—"}</span>{doc.sourceIds.length?<span>{doc.sourceIds.length} evidence ID{doc.sourceIds.length===1?"":"s"}</span>:null}</div>
    <MarkdownView source={doc.body}/>
    {evidenceRows.length?<section><h2>Registered evidence</h2><div className="cards two">{evidenceRows.map(e=><div className="card" key={e.id}><div className="eyebrow">{e.id} · {e.state==="active"?(e.type||"evidence"):"retired"}</div><h3>{e.source}</h3><p>{e.state==="active"?e.claims:e.why}</p><small className="mono">{e.state==="active"?e.location:(e.replacedBy?`replacement: ${e.replacedBy}`:`retired ${e.retiredOn||""}`)}</small></div>)}</div></section>:null}
    {(links.length||backs.length)?<section className="relations"><h2>Relationships</h2><div className="cards two">{links.length?<div className="card"><div className="eyebrow">Links to</div>{links.map(d=><button className="relation" key={d.id} onClick={()=>go("doc",d.id)}>{d.title}<ChevronRight size={14}/></button>)}</div>:null}{backs.length?<div className="card"><div className="eyebrow">Referenced by</div>{backs.map(d=><button className="relation" key={d.id} onClick={()=>go("doc",d.id)}>{d.title}<ChevronRight size={14}/></button>)}</div>:null}</div></section>:null}
  </div>;
}

function SearchOverlay({ onClose }: { onClose:()=>void }) {
  const [q,setQ]=useState(""); const results=searchDocs(q);
  useEffect(()=>{const on=(e:KeyboardEvent)=>{if(e.key==="Escape")onClose()};window.addEventListener("keydown",on);return()=>window.removeEventListener("keydown",on)},[onClose]);
  return <div className="overlay" role="dialog" aria-modal="true" aria-label="Search vault" onMouseDown={(e: ReactMouseEvent<HTMLDivElement>)=>{if(e.target===e.currentTarget)onClose()}}><div className="palette"><div className="palette-input"><Search size={18}/><input autoFocus value={q} onChange={(e: ChangeEvent<HTMLInputElement>)=>setQ(e.target.value)} placeholder="Search titles, claims, tags…"/><button onClick={onClose}><X size={17}/></button></div><div className="palette-results">{q&&!results.length?<p className="muted">No matching notes.</p>:results.map(d=><button key={d.id} onClick={()=>{go("doc",d.id);onClose()}}><div><strong>{d.title}</strong><small>{d.id}</small></div><StatusBadge status={d.status}/></button>)}</div></div></div>;
}

function App() {
  const route=useRoute(); const [menu,setMenu]=useState(false); const [search,setSearch]=useState(false);
  useEffect(()=>{const on=(e:KeyboardEvent)=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="k"){e.preventDefault();setSearch(v=>!v)}};window.addEventListener("keydown",on);return()=>window.removeEventListener("keydown",on)},[]);
  useEffect(()=>setMenu(false),[route.view,route.doc]);
  const streamDocs = Object.keys(WORKSTREAM_LABELS).map(id=>({id,label:WORKSTREAM_LABELS[id],doc:resolveDoc(`${id}/README`)})).filter(x=>x.doc);
  const current = route.view;
  return <div className="app"><a className="skip" href="#content">Skip to content</a><header><button className="icon-button mobile" onClick={()=>setMenu(true)} aria-label="Open navigation"><Menu size={18}/></button><Wordmark/><button className="search-trigger" onClick={()=>setSearch(true)}><Search size={15}/><span>Search vault</span><kbd>⌘K</kbd></button><div className="header-health"><span className={vault.lint.errors?"dot bad":"dot"}/>{vault.lint.errors?`${vault.lint.errors} errors`:"Vault clean"}</div></header>
    <aside className={menu?"sidebar open":"sidebar"}>{menu?<button className="close-nav" onClick={()=>setMenu(false)}><X size={18}/></button>:null}<nav><div className="nav-label">Vault</div>{PRIMARY.map(x=><button className={current===x.id?"active":""} key={x.id} onClick={()=>go(x.id)}><x.icon size={16}/>{x.label}</button>)}<div className="nav-label">Spine</div>{streamDocs.map((x,i)=><button key={x.id} className={route.doc===x.doc!.id?"active":""} onClick={()=>go("doc",x.doc!.id)}><span className="nav-index">{String(i).padStart(2,"0")}</span>{x.label}</button>)}<div className="nav-label">More</div><button className={current==="templates"?"active":""} onClick={()=>go("templates")}><FileText size={16}/>Templates</button><button className={current==="adopt"?"active":""} onClick={()=>go("adopt")}><GitBranch size={16}/>Adopt</button></nav><div className="sidebar-foot"><span>Generated from Markdown</span><small>{new Date(vault.generatedAt).toLocaleString()}</small></div></aside>
    {menu?<button className="scrim" onClick={()=>setMenu(false)} aria-label="Close navigation"/>:null}
    <main id="content">{route.view==="home"?<HomePage/>:route.view==="decisions"?<DecisionsPage/>:route.view==="evidence"?<EvidencePage/>:route.view==="reviews"?<ReviewsPage/>:route.view==="graph"?<GraphPage/>:route.view==="health"?<HealthPage/>:route.view==="templates"?<TemplatesPage/>:route.view==="adopt"?<AdoptPage/>:<DocPage id={route.doc||"Home"}/>}</main>
    {search?<SearchOverlay onClose={()=>setSearch(false)}/>:null}
  </div>;
}

export default App;
