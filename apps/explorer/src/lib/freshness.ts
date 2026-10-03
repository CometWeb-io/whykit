import type { EvidenceRow, VaultDoc, VaultPolicy } from "../types.ts";
import { daysBetween, parseDay } from "./timeline.ts";

/**
 * How an active evidence row stands against the vault's access-age policy:
 * - `stale`     last accessed longer ago than its type's window (lint: evidence.access_stale)
 * - `due`       inside the last fifth of its window: re-check soon
 * - `fresh`     accessed within its window
 * - `missing`   its type has a window but the row has no Accessed date (lint: evidence.access_missing)
 * - `future`    Accessed is after today (lint: evidence.access_future)
 * - `unchecked` no window is configured for its type, so age is informational only
 */
export type Freshness = "stale" | "due" | "fresh" | "missing" | "future" | "unchecked";

export const FRESHNESS_ORDER: readonly Freshness[] = ["stale", "missing", "future", "due", "fresh", "unchecked"];

export const FRESHNESS_LABELS: Record<Freshness, string> = {
  stale: "Stale", due: "Due soon", fresh: "Fresh", missing: "No access date", future: "Future date", unchecked: "No policy",
};

/** Share of the window after which a row counts as due soon. */
export const DUE_SHARE = 0.8;

const HISTORICAL = new Set(["superseded", "archived", "template"]);

/** A note still in force (not superseded, archived or a template). */
export function isLive(doc: VaultDoc): boolean {
  return !HISTORICAL.has(doc.status);
}

export type FreshnessRow = {
  e: EvidenceRow;
  state: Freshness;
  /** Days since `basis`; null when there is no usable date. */
  ageDays: number | null;
  /** Which column the age is measured from. Policy windows only ever use Accessed, like lint. */
  basis: "accessed" | "date" | null;
  maxAge: number | null;
  citers: VaultDoc[];
};

export type RetiredRow = {
  e: EvidenceRow;
  replacement: EvidenceRow | undefined;
  /** Notes still in force that cite the retired source: the follow-up list. */
  liveCiters: VaultDoc[];
  /** Superseded or archived notes; citing what was believed then is expected. */
  historicalCiters: VaultDoc[];
};

export function classify(e: EvidenceRow, policy: VaultPolicy | undefined, today: number): Omit<FreshnessRow, "e" | "citers"> {
  const maxAge = policy?.evidenceAccessAgeDays[e.type] ?? null;
  const accessed = parseDay(e.accessed);
  const fallback = accessed === null ? parseDay(e.date) : null;
  const basis = accessed !== null ? "accessed" : fallback !== null ? "date" : null;
  const from = accessed ?? fallback;
  const ageDays = from === null ? null : daysBetween(from, today);
  if (maxAge === null) return { state: "unchecked", ageDays, basis, maxAge };
  if (accessed === null) return { state: "missing", ageDays, basis, maxAge };
  const age = daysBetween(accessed, today);
  if (age < 0) return { state: "future", ageDays: age, basis, maxAge };
  if (age > maxAge) return { state: "stale", ageDays: age, basis, maxAge };
  if (age > maxAge * DUE_SHARE) return { state: "due", ageDays: age, basis, maxAge };
  return { state: "fresh", ageDays: age, basis, maxAge };
}

export function evidenceFreshness(
  evidence: readonly EvidenceRow[],
  policy: VaultPolicy | undefined,
  today: number,
  docsForEvidence: (id: string) => VaultDoc[],
): { active: FreshnessRow[]; retired: RetiredRow[] } {
  const byId = new Map(evidence.map(e => [e.id, e]));
  const active: FreshnessRow[] = [];
  const retired: RetiredRow[] = [];
  for (const e of evidence) {
    const citers = docsForEvidence(e.id);
    if (e.state === "retired") {
      retired.push({
        e,
        replacement: e.replacedBy ? byId.get(e.replacedBy) : undefined,
        liveCiters: citers.filter(isLive),
        historicalCiters: citers.filter(d => !isLive(d)),
      });
    } else {
      active.push({ e, citers, ...classify(e, policy, today) });
    }
  }
  const rank = new Map(FRESHNESS_ORDER.map((s, i) => [s, i]));
  active.sort((a, b) =>
    (rank.get(a.state)! - rank.get(b.state)!)
    || (b.citers.length - a.citers.length)
    || ((b.ageDays ?? -1) - (a.ageDays ?? -1))
    || a.e.id.localeCompare(b.e.id));
  retired.sort((a, b) => b.liveCiters.length - a.liveCiters.length || a.e.id.localeCompare(b.e.id));
  return { active, retired };
}

export function describeAge(days: number | null): string {
  if (days === null) return "no date";
  if (days < 0) return `${-days} day${days === -1 ? "" : "s"} ahead`;
  if (days === 0) return "today";
  return `${days} day${days === 1 ? "" : "s"}`;
}
