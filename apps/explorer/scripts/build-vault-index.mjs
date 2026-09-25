import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = resolve(HERE, "..");
const REPO = resolve(APP, "../..");
const VAULT = resolve(process.env.WHYKIT_VAULT_DIR || resolve(REPO, "examples/northline"));
const OUT = resolve(APP, "src/generated/vault.json");

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

const payload = JSON.parse(raw);
mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify(payload, null, 2) + "\n");
console.log(
  `Indexed ${payload.docs.length} docs, ${payload.evidence.length} evidence rows, ` +
  `${payload.decisions.length} decisions, ${payload.reviews.length} review events from ${VAULT}`,
);
