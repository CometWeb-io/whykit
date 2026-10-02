import raw from "../generated/vault.json";
import type { VaultIndex } from "../types.ts";
import { createVaultModel } from "./model.ts";

export const model = createVaultModel(raw as VaultIndex);
export const { vault, docs, resolveDoc, linksFor, backlinksFor, canonicalDocs, searchDocs, evidenceFor, docsForEvidence } = model;
