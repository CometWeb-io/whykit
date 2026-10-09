import type { VaultDoc } from "../types.ts";

export type DueReview = { d: VaultDoc; days: number };

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function utcDay(value: Date): number {
  return Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate());
}

/** Approved documents whose `review_by` falls within `days` of `now`, most urgent first. */
export function reviewQueue(docs: readonly VaultDoc[], now: Date, days = 30): DueReview[] {
  const today = utcDay(now);
  return docs
    .filter(d => d.status === "approved" && ISO_DATE.test(d.reviewBy))
    .map(d => {
      const [year, month, day] = d.reviewBy.split("-").map(Number) as [number, number, number];
      return { d, days: Math.round((Date.UTC(year, month - 1, day) - today) / 86_400_000) };
    })
    .filter(x => Number.isFinite(x.days) && (x.days <= days || x.d.requiresReview))
    .sort((a, b) => a.days - b.days || a.d.title.localeCompare(b.d.title));
}

export function describeDue(days: number): string {
  if (days < 0) return `${Math.abs(days)} day${days === -1 ? "" : "s"} overdue`;
  if (days === 0) return "due today";
  return `due in ${days} day${days === 1 ? "" : "s"}`;
}
