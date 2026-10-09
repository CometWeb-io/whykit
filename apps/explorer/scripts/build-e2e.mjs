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

function build(name, vaultDir) {
  run(process.execPath, ["scripts/build-vault-index.mjs"], { WHYKIT_VAULT_DIR: vaultDir });
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
    "from whykit.scaffold import _with_evidence_sensitivity",
    "register = root / '00-context/evidence-register.md'",
    "text = _with_evidence_sensitivity(register.read_text(encoding='utf-8'), 'active', 'internal')",
    "text = '\\n'.join(line.rsplit('internal', 1)[0] + 'restricted |' if line.startswith('| E-001 |') else line for line in text.splitlines()) + '\\n'",
    "register.write_text(text, encoding='utf-8')",
    "payload = build_explorer_index(root, today=dt.date.fromisoformat(AS_OF), private=True)",
    `Path(${JSON.stringify(join(vaultDir, "..", "synthetic-index.json"))}).write_text(json.dumps(payload), encoding='utf-8')`,
  ].join("\n");
  run(PYTHON, ["-c", code]);
  run(process.execPath, ["scripts/build-vault-index.mjs", "--from", join(vaultDir, "..", "synthetic-index.json")]);
  run(process.execPath, [resolve(APP, "node_modules/vite/bin/vite.js"), "build", "--outDir", join(OUT, name), "--emptyOutDir"]);
}

const scratch = mkdtempSync(join(tmpdir(), "whykit-explorer-e2e-"));
try {
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
