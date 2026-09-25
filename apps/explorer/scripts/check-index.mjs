import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const file = resolve(HERE, "../src/generated/vault.json");
const raw = readFileSync(file, "utf8");
const data = JSON.parse(raw);

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

if (!Array.isArray(data.docs) || !data.docs.length) fail("no documents indexed");
unique(data.docs.map(d => d.id), "doc id");
unique(data.evidence.map(e => e.id), "evidence id");
unique(data.decisions.map(d => d.id), "decision id");

const docs = new Set(data.docs.map(d => d.id));
for (const d of data.decisions) {
  if (!d.recordId || !docs.has(d.recordId)) fail(`decision ${d.id} points at missing record ${d.recordId}`);
}
if (data.lint?.errors !== 0) fail(`vault index contains ${data.lint?.errors} lint errors`);
if (raw.includes("/mnt/data/") || raw.includes("\\Users\\") || raw.includes("/Users/")) {
  fail("generated index contains an absolute build-machine path");
}
if (Object.prototype.hasOwnProperty.call(data, "vaultRoot")) fail("vaultRoot must not be exposed to the browser bundle");

if (!process.exitCode) {
  console.log(`Index smoke OK: ${data.docs.length} docs, ${data.evidence.length} evidence rows, ${data.decisions.length} decisions`);
}
