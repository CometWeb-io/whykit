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
  tags: string[];
  workstream: string;
  decisionId: string | null;
  supersedes: string | null;
  summary: string;
  body: string;
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
}
