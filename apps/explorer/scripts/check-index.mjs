import { validateVaultIndex } from "../src/lib/model.ts";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const file = resolve(HERE, "../src/generated/vault.json");
const raw = readFileSync(file, "utf8");
const data = JSON.parse(raw);
validateVaultIndex(data);
const bodiesRaw = readFileSync(resolve(HERE, "../src/generated/bodies.json"), "utf8");
const bodies = JSON.parse(bodiesRaw);
const findingsRaw = readFileSync(resolve(HERE, "../src/generated/findings.json"), "utf8");
const findings = JSON.parse(findingsRaw);

function fail(message) {
  console.error(`index check failed: ${message}`);
  process.exitCode = 1;
}
function unique(items, label) {
  const seen = new Set();
  for (const x of items) {
    if (seen.has(x)) fail(`duplicate ${label}: ${x}`);
    seen.add(x);
  }
}

if (!Array.isArray(data.docs)) fail("no document array indexed");
unique(data.docs.map(d => d.id), "doc id");
unique(data.evidence.map(e => e.id), "evidence id");
unique(data.decisions.map(d => d.id), "decision id");

const docs = new Set(data.docs.map(d => d.id));
for (const d of data.decisions) {
  if (!d.recordId || !docs.has(d.recordId)) fail(`decision ${d.id} points at missing record ${d.recordId}`);
}
// The summary leaves bodies to the lazily loaded chunk, which must match it note for note.
for (const d of data.docs) {
  if (Object.prototype.hasOwnProperty.call(d, "body")) fail(`summary still carries the body of ${d.id}`);
  if (!Array.isArray(d.links)) fail(`summary has no resolved links for ${d.id}`);
  else for (const at of d.links) if (!Number.isInteger(at) || !data.docs[at]) fail(`${d.id} links to a note position that does not exist: ${at}`);
  if (typeof bodies[d.id] !== "string") fail(`no body for ${d.id} in bodies.json`);
}
if (Object.keys(bodies).length !== data.docs.length) fail("bodies.json does not match the summary note for note");
// Findings are a chunk of their own too; the summary keeps only the counts.
if (Object.prototype.hasOwnProperty.call(data.lint ?? {}, "findings")) fail("summary still carries the lint findings");
if (!Array.isArray(findings) || findings.length !== (data.lint?.errors ?? 0) + (data.lint?.warnings ?? 0)) {
  fail("findings.json does not match the summary's error and warning counts");
}
if (data.lint?.errors !== 0) fail(`vault index contains ${data.lint?.errors} lint errors`);
for (const text of [raw, bodiesRaw, findingsRaw]) {
  if (text.includes("/mnt/data/") || text.includes("\\Users\\") || text.includes("/Users/")) {
    fail("generated index contains an absolute build-machine path");
  }
}
if (Object.prototype.hasOwnProperty.call(data, "vaultRoot")) fail("vaultRoot must not be exposed to the browser bundle");

if (!process.exitCode) {
  console.log(`Index smoke OK: ${data.docs.length} docs, ${data.evidence.length} evidence rows, ${data.decisions.length} decisions`);
}
