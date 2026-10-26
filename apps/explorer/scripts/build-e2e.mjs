// Build the two static sites the end-to-end suite runs against:
//   e2e/.build/northline  the worked example vault (rich data)
//   e2e/.build/empty      a fresh `whykit init --minimal` vault (empty states)
//   e2e/.build/synthetic  a 5,000-note synthetic vault (performance, policy)
//   e2e/.build/single     the example vault as one HTML file, opened from file://
// The synthetic vault comes from tests/synthetic_vault.py and deliberately
// carries lint errors, which `whykit explorer-index` refuses; its index is
// therefore built by calling the same Python function without the lint gate,
// then split into summary and bodies by the same script as every other build.
// The empty vault is built first so src/generated/vault.json is left holding
// the example index that `npm run dev` and `npm run build` expect.
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const APP = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = resolve(APP, "../..");
const PYTHON = process.env.PYTHON || "python3";
const OUT = resolve(APP, "e2e/.build");

function run(cmd, args, env = {}) {
  execFileSync(cmd, args, { cwd: APP, stdio: "inherit", env: { ...process.env, WHYKIT_EXPLORER_PRIVATE: "1", ...env } });
}

function build(name, vaultDir, env = {}) {
  run(process.execPath, ["scripts/build-vault-index.mjs"], { WHYKIT_VAULT_DIR: vaultDir, ...env });
  run(process.execPath, [resolve(APP, "node_modules/vite/bin/vite.js"), "build", "--outDir", join(OUT, name), "--emptyOutDir"]);
}

const SYNTHETIC_NOTES = Number(process.env.WHYKIT_SYNTHETIC_NOTES || 5000);

function buildSynthetic(name, vaultDir) {
  const code = [
    "import datetime as dt, json, sys",
    "from pathlib import Path",
    `sys.path[:0] = [${JSON.stringify(resolve(REPO, "src"))}, ${JSON.stringify(resolve(REPO, "tests"))}]`,
    "from synthetic_vault import AS_OF, generate",
    "from whykit.explorer_index import build_explorer_index",
    `root = generate(Path(${JSON.stringify(vaultDir)}), ${SYNTHETIC_NOTES}).resolve()`,
    "payload = build_explorer_index(root, today=dt.date.fromisoformat(AS_OF), private=True)",
    `Path(${JSON.stringify(join(vaultDir, "..", "synthetic-index.json"))}).write_text(json.dumps(payload), encoding='utf-8')`,
  ].join("\n");
  run(PYTHON, ["-c", code]);
  run(process.execPath, ["scripts/build-vault-index.mjs", "--from", join(vaultDir, "..", "synthetic-index.json")]);
  run(process.execPath, [resolve(APP, "node_modules/vite/bin/vite.js"), "build", "--outDir", join(OUT, name), "--emptyOutDir"]);
}

const scratch = mkdtempSync(join(tmpdir(), "whykit-explorer-e2e-"));
try {
  const publicVault = join(scratch, "public");
  const publicCode = `
import sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(resolve(REPO, "src"))})
from whykit.cli import main
from whykit.scaffold import create_evidence
root = Path(${JSON.stringify(publicVault)})
assert main(["init", "--minimal", str(root)]) == 0
register = root / "00-context/evidence-register.md"
register.write_text(register.read_text().replace("sensitivity: internal", "sensitivity: public"))
for label, source in (("restricted", "PRIVATE-ROW-CANARY"), ("public", "PUBLIC-ROW-CANARY")):
    create_evidence(root, source=source, location="https://example.com/fixture", kind="fixture", claims="Synthetic fixture", sensitivity=label)
for name, label, title, body in (
    ("public-safe", "public", "Public export fixture", "VISIBLE-PUBLIC-CANARY E-002"),
    ("private-source", "restricted", "PRIVATE-TITLE-CANARY", "PRIVATE-BODY-CANARY"),
    ("private-reference", "public", "DEPENDENT-TITLE-CANARY", "[[notes/private-source]]"),
    ("row-reference", "public", "ROW-DEPENDENT-CANARY", "E-001"),
):
    text = f"---\\ntitle: {title}\\ntype: reference\\nstatus: draft\\nowner: Ada Example\\ncreated: 2026-09-17\\nlast_updated: 2026-09-17\\nsource_of_truth: false\\nsensitivity: {label}\\nsource_ids: []\\n---\\n\\n# {title}\\n\\n{body}\\n"
    (root / "notes" / (name + ".md")).write_text(text, encoding="utf-8")
from whykit.lint import lint
errors = [item for item in lint(root)[1] if item.level == "error"]
assert not errors, errors
`;
  run(PYTHON, ["-c", publicCode]);
  let registerName = "evidence-register.md";
  for (const [site, name] of [["public", registerName], ["public-md", "evidence-register.MD"], ["public-name", "EVIDENCE-REGISTER.Md"]]) {
    run(PYTHON, ["-c", "from pathlib import Path; import sys; Path(sys.argv[1]).rename(sys.argv[2])",
      join(publicVault, "00-context", registerName), join(publicVault, "00-context", name)]);
    registerName = name;
    build(site, publicVault, { WHYKIT_EXPLORER_PRIVATE: "" });
    run(process.execPath, [resolve(APP, "node_modules/vite/bin/vite.js"), "build", "--mode", "single", "--outDir", join(OUT, site + "-single"), "--emptyOutDir"]);
  }
  const fresh = join(scratch, "fresh");
  run(PYTHON, [resolve(REPO, "scripts/whykit.py"), "init", "--minimal", fresh]);
  build("empty", fresh);
  buildSynthetic("synthetic", join(scratch, "synthetic"));
  build("northline", resolve(REPO, "examples/northline"));
  // Same index as the northline site, which is still in src/generated.
  run(process.execPath, [resolve(APP, "node_modules/vite/bin/vite.js"), "build", "--mode", "single", "--outDir", join(OUT, "single"), "--emptyOutDir"]);
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
