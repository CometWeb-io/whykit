export type DocStatus = "template" | "draft" | "in_review" | "approved" | "superseded" | "archived";

export interface VaultDoc {
  id: string;
  title: string;
  aliases: string[];
  type: string;
  status: DocStatus;
  owner: string;
  created: string;
  lastUpdated: string;
  reviewBy: string;
  sourceOfTruth: boolean;
  sensitivity: string;
  sourceIds: string[];
  /** Every E-NNN the note mentions (front matter or body); absent in older indexes. */
  citations?: string[];
  tags: string[];
  workstream: string;
  decisionId: string | null;
  supersedes: string | null;
  summary: string;
  /**
   * The Markdown body. A full index from `whykit explorer-index` carries it;
   * the summary the Explorer bundles leaves it out and loads the bodies as a
   * separate chunk (see lib/split.ts).
   */
  body?: string;
  /**
   * Resolved outgoing links as positions in `docs`, precomputed when the body
   * is split off (positions rather than ids keep a large index about a
   * megabyte smaller).
   */
  links?: number[];
  /** Open-question headings and "Needs verification" markers in the body. */
  cues?: number;
}
export interface EvidenceRow {
  id: string;
  state: "active" | "retired";
  source: string;
  type: string;
  date: string;
  accessed: string;
  location: string;
  claims: string;
  retiredOn?: string;
  why?: string;
  replacedBy?: string | null;
}
export interface DecisionRow { id: string; title: string; date: string; owner: string; status: string; recordId: string; supersedes?: string | null; supersededBy?: string | null; }
export interface ReviewRow {
  date: string;
  target: string;
  targetId: string;
  reviewer: string;
  outcome: string;
  previousReview: string;
  nextReview: string;
  note: string;
}
export interface Finding { path: string; line?: number | null; level: "error" | "warning"; code: string; message: string; }
export interface VaultIndex {
  /** Machine contract version (see docs/automation.md); absent in indexes built by older releases. */
  contract_version?: number;
  generatedAt: string;
  vaultName: string;
  docs: VaultDoc[];
  evidence: EvidenceRow[];
  decisions: DecisionRow[];
  reviews: ReviewRow[];
  lint: { files: number; errors: number; warnings: number; findings: Finding[] };
  /** Freshness thresholds from whykit.toml; absent in indexes built by older releases. */
  policy?: VaultPolicy;
}
export interface VaultPolicy {
  evidenceAccessAgeDays: Record<string, number>;
  decisionReviewDays: number | null;
  statusDueDays: number | null;
}
