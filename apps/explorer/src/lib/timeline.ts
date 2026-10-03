import type { DecisionRow, VaultDoc } from "../types.ts";

const DAY = 86_400_000;

/** Milliseconds since the epoch for an ISO `YYYY-MM-DD` date, or null. */
export function parseDay(value: string | undefined | null): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value ?? "").trim());
  if (!m) return null;
  const t = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  // Reject dates that roll over (2026-02-30).
  return new Date(t).toISOString().slice(0, 10) === m[0] ? t : null;
}

export function isoDay(t: number): string {
  return new Date(t).toISOString().slice(0, 10);
}

/** Midnight UTC of `date`, so day counts do not depend on the time of day. */
export function startOfDay(date: Date): number {
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
}

export function daysBetween(from: number, to: number): number {
  return Math.round((to - from) / DAY);
}

export type TimelineEntry = {
  row: DecisionRow;
  doc: VaultDoc | undefined;
  tags: string[];
  /** Decision date; null when the log has no valid date. */
  start: number | null;
  /** When a successor replaced it (the successor's date); null while it is live or when unknown. */
  end: number | null;
  successor: DecisionRow | undefined;
  /** Superseded, by status or by a recorded successor. */
  superseded: boolean;
};

/**
 * One entry per decision-log row, with the period it was in force: from its
 * own date until the date of the decision that superseded it.
 */
export function decisionTimeline(
  decisions: readonly DecisionRow[],
  resolveDoc: (id: string) => VaultDoc | undefined,
): TimelineEntry[] {
  const byId = new Map(decisions.map(d => [d.id, d]));
  const successorOf = new Map<string, DecisionRow>();
  for (const d of decisions) {
    if (d.supersedes && !successorOf.has(d.supersedes)) successorOf.set(d.supersedes, d);
  }
  return decisions.map(row => {
    const successor = (row.supersededBy ? byId.get(row.supersededBy) : undefined) ?? successorOf.get(row.id);
    const doc = row.recordId ? resolveDoc(row.recordId) : undefined;
    const start = parseDay(row.date);
    const succeeded = successor ? parseDay(successor.date) : null;
    return {
      row,
      doc,
      tags: doc?.tags ?? [],
      start,
      end: succeeded !== null && start !== null && succeeded >= start ? succeeded : null,
      successor,
      superseded: Boolean(successor) || row.status === "superseded",
    };
  });
}

export type TimelineFilter = { status?: string; owner?: string; tag?: string };

export function filterTimeline(entries: readonly TimelineEntry[], f: TimelineFilter): TimelineEntry[] {
  return entries.filter(e =>
    (!f.status || e.row.status === f.status)
    && (!f.owner || e.row.owner === f.owner)
    && (!f.tag || e.tags.includes(f.tag)));
}

export type Facet = { value: string; count: number };

function facet(values: Iterable<string>): Facet[] {
  const counts = new Map<string, number>();
  for (const v of values) if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts].map(([value, count]) => ({ value, count })).sort((a, b) => a.value.localeCompare(b.value));
}

export function timelineFacets(entries: readonly TimelineEntry[]) {
  return {
    statuses: facet(entries.map(e => e.row.status)),
    owners: facet(entries.map(e => e.row.owner)),
    tags: facet(entries.flatMap(e => e.tags)),
  };
}

export type Tick = { at: number; label: string };
export type Scale = { min: number; max: number; ticks: Tick[]; pos: (t: number) => number };

/**
 * A time axis covering every dated entry up to `today`, with month, quarter
 * or year ticks depending on the span. `pos` maps a time to 0–100 (percent).
 */
export function timelineScale(entries: readonly TimelineEntry[], today: number): Scale | null {
  const times = entries.flatMap(e => [e.start, e.end]).filter((t): t is number => t !== null);
  if (!times.length) return null;
  const first = Math.min(...times);
  const last = Math.max(today, ...times);
  const a = new Date(first), b = new Date(last);
  const min = Date.UTC(a.getUTCFullYear(), a.getUTCMonth(), 1);
  const max = Date.UTC(b.getUTCFullYear(), b.getUTCMonth() + 1, 1);
  const months = (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + b.getUTCMonth() - a.getUTCMonth() + 1;
  const step = months <= 15 ? 1 : months <= 48 ? 3 : 12;
  const ticks: Tick[] = [];
  const cursor = new Date(min);
  // Align quarter and year ticks to calendar boundaries.
  cursor.setUTCMonth(Math.floor(cursor.getUTCMonth() / step) * step);
  if (cursor.getTime() < min) cursor.setUTCMonth(cursor.getUTCMonth() + step);
  while (cursor.getTime() <= max) {
    const y = cursor.getUTCFullYear(), m = cursor.getUTCMonth();
    const label = step === 12 ? `${y}` : step === 3 ? `Q${m / 3 + 1} ${y}` : `${y}-${String(m + 1).padStart(2, "0")}`;
    ticks.push({ at: cursor.getTime(), label });
    cursor.setUTCMonth(m + step);
  }
  const span = max - min || 1;
  return { min, max, ticks, pos: (t: number) => ((t - min) / span) * 100 };
}

export type TimelineSort = "newest" | "oldest";

export function sortTimeline(entries: readonly TimelineEntry[], sort: TimelineSort): TimelineEntry[] {
  const key = (e: TimelineEntry) => e.start ?? Number.NEGATIVE_INFINITY;
  const out = [...entries].sort((a, b) => key(a) - key(b) || a.row.id.localeCompare(b.row.id));
  return sort === "newest" ? out.reverse() : out;
}

/**
 * Keep axis labels from colliding: each label needs its own measured share of
 * the track (about 6.5 px per monospace character plus padding) and must start
 * early enough to fit before the right edge. Ticks that lose their label keep
 * their mark.
 */
export function axisLabels(ticks: { at: number; label: string }[], trackPx: number) {
  const share = (label: string) => ((label.length * 6.5 + 14) / Math.max(trackPx, 1)) * 100;
  let freeFrom = Number.NEGATIVE_INFINITY;
  return ticks.map(t => {
    const need = share(t.label);
    if (t.at < freeFrom || t.at + need > 100) return { ...t, label: "" };
    freeFrom = t.at + need;
    return t;
  });
}
