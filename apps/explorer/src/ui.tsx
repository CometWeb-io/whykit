import { useState, type ChangeEvent, type ReactNode } from "react";
import { CheckCircle2, Inbox, ShieldAlert } from "lucide-react";
import type { Facet } from "./lib/timeline.ts";

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <span className={`status status-${status}`}>{label ?? status.replaceAll("_", " ")}</span>;
}

export function Sensitivity({ value }: { value: string }) {
  return <span className={`sensitivity sensitivity-${value}`}>{value}</span>;
}

export function Empty({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "ok" }) {
  return <div className={tone === "ok" ? "empty ok" : "empty"} role="status">{tone === "ok" ? <CheckCircle2 size={18} aria-hidden="true"/> : <Inbox size={18} aria-hidden="true"/>}<span>{children}</span></div>;
}

export function Metric({ label, value, detail }: { label: string; value: string | number; detail?: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong>{detail ? <small>{detail}</small> : null}</div>;
}

/**
 * Render long lists in pages. A 5,000-note vault produces thousands of lint
 * findings and hundreds of evidence rows; drawing all of them at once costs
 * more than anyone reads. The window resets whenever `key` (the filter) changes.
 */
export function useIncremental(key: string, step: number): [number, () => void] {
  const [state, setState] = useState({ key, limit: step });
  const limit = state.key === key ? state.limit : step;
  return [limit, () => setState({ key, limit: limit + step })];
}

export function ShowMore({ shown, total, step, onMore, noun }: { shown: number; total: number; step: number; onMore: () => void; noun: string }) {
  if (shown >= total) return null;
  return <div className="show-more">
    <span className="muted small">Showing {shown} of {total} {noun}</span>
    <button className="button" onClick={onMore}>Show {Math.min(step, total - shown)} more</button>
  </div>;
}

/** A labelled select bound to one URL parameter; "" means no filter. */
export function FacetSelect({ id, label, value, facets, onChange, allLabel = "All" }: {
  id: string; label: string; value: string; facets: readonly Facet[] | readonly { value: string; label: string; count?: number }[];
  onChange: (value: string) => void; allLabel?: string;
}) {
  return <div className="field">
    <label htmlFor={id}>{label}</label>
    <select id={id} value={value} onChange={(e: ChangeEvent<HTMLSelectElement>) => onChange(e.target.value)}>
      <option value="">{allLabel}</option>
      {facets.map(f => <option key={f.value} value={f.value}>{"label" in f ? f.label : f.value}{f.count !== undefined ? ` (${f.count})` : ""}</option>)}
      {value && !facets.some(f => f.value === value) ? <option value={value}>{value} (0)</option> : null}
    </select>
  </div>;
}

/** Sensitivity levels a static build must never expose without access control. */
export const EXPOSED_LEVELS = ["confidential", "restricted"] as const;

export function countExposed(docs: readonly { sensitivity: string }[]): Record<(typeof EXPOSED_LEVELS)[number], number> {
  const out = { confidential: 0, restricted: 0 };
  for (const d of docs) {
    const level = d.sensitivity.toLowerCase();
    if (level === "confidential" || level === "restricted") out[level]++;
  }
  return out;
}

/**
 * Shown on every page of a build that contains confidential or restricted
 * records. Explorer has no access control, so whoever can open the page can
 * read them; the warning cannot be dismissed for the same reason.
 */
export function ExposureNotice({ counts }: { counts: ReturnType<typeof countExposed> }) {
  const parts = EXPOSED_LEVELS.filter(level => counts[level] > 0).map(level => `${counts[level]} ${level}`);
  if (!parts.length) return null;
  const total = counts.confidential + counts.restricted;
  return <div className="exposure-wrap"><div className="exposure" role="note" aria-label="Sensitive content in this build">
    <ShieldAlert size={16} aria-hidden="true"/>
    <span><strong>This build includes {parts.join(" and ")} record{total === 1 ? "" : "s"}.</strong> Explorer has no access control: anyone who can open this page can read {total === 1 ? "it" : "them"}. Serve it only behind your own authentication.</span>
  </div></div>;
}
