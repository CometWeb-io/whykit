// Write the two files the Explorer bundles:
//   src/generated/vault.json   the summary, part of the main bundle
//   src/generated/bodies.json  every note body, a chunk loaded after first paint
//   src/generated/findings.json  the lint findings, a chunk Health loads
//
// By default the full index comes from `whykit explorer-index` for
// $WHYKIT_VAULT_DIR. `--from <file>` splits an index that was already written
// (the end-to-end build uses it for a synthetic vault).
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { splitIndex } from "../src/lib/split.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = resolve(HERE, "..");
const REPO = resolve(APP, "../..");
const VAULT = resolve(process.env.WHYKIT_VAULT_DIR || resolve(REPO, "examples/northline"));
const OUT = resolve(APP, "src/generated/vault.json");
const BODIES = resolve(APP, "src/generated/bodies.json");
const FINDINGS = resolve(APP, "src/generated/findings.json");
const SENSITIVE = new Set(["confidential", "restricted"]);

const fromAt = process.argv.indexOf("--from");
const FROM = fromAt > 0 ? process.argv[fromAt + 1] : undefined;
if (fromAt > 0 && !FROM) {
  console.error("--from needs a file");
  process.exit(2);
}

function write(payload, label) {
  const { summary, bodies, findings } = splitIndex(payload);
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, JSON.stringify(summary) + "\n");
  writeFileSync(BODIES, JSON.stringify(bodies) + "\n");
  writeFileSync(FINDINGS, JSON.stringify(findings) + "\n");
  console.log(
    `Indexed ${payload.docs.length} docs, ${payload.evidence.length} evidence rows, ` +
    `${payload.decisions.length} decisions, ${payload.reviews.length} review events from ${label}`,
  );
  const sensitive = payload.docs.filter(d => SENSITIVE.has(String(d.sensitivity).toLowerCase()));
  if (sensitive.length) {
    console.warn(
      `warning: this build includes ${sensitive.length} confidential or restricted note(s). ` +
      "Explorer has no access control; serve the build only behind your own authentication.",
    );
  }
}

if (FROM) {
  write(JSON.parse(readFileSync(resolve(FROM), "utf8")), FROM);
  process.exit(0);
}

if (!existsSync(resolve(VAULT, "Home.md"))) {
  console.error(`Not a WhyKit vault: ${VAULT}`);
  process.exit(2);
}

// Canonical WhyKit front matter / table parsing lives in Python. The Explorer
// must not maintain a second parser of the same contract.
let raw;
try {
  raw = execFileSync(
    process.env.PYTHON || "python3",
    [
      resolve(REPO, "scripts/whykit.py"),
      "explorer-index",
      "--root",
      VAULT,
      "--json",
    ],
    {
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    },
  );
} catch (err) {
  const out = err?.stdout ? String(err.stdout) : "";
  const errText = err?.stderr ? String(err.stderr) : String(err);
  if (out.trim()) {
    try {
      const failed = JSON.parse(out);
      if (failed?.lint?.errors > 0) {
        console.error(`Vault has ${failed.lint.errors} lint error(s); Explorer index not generated.`);
        for (const f of (failed.lint.findings || []).filter(item => item.level === "error")) {
          console.error(`- ${f.path}: ${f.message}`);
        }
        process.exit(1);
      }
    } catch {
      // fall through
    }
  }
  console.error(errText);
  process.exit(err?.status || 1);
}

write(JSON.parse(raw), VAULT);
