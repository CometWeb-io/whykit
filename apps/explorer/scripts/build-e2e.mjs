// Build the two static sites the end-to-end suite runs against:
//   e2e/.build/northline  the worked example vault (rich data)
//   e2e/.build/empty      a fresh `whykit init --minimal` vault (empty states)
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
  execFileSync(cmd, args, { cwd: APP, stdio: "inherit", env: { ...process.env, ...env } });
}

function build(name, vaultDir) {
  run(process.execPath, ["scripts/build-vault-index.mjs"], { WHYKIT_VAULT_DIR: vaultDir });
  run(process.execPath, [resolve(APP, "node_modules/vite/bin/vite.js"), "build", "--outDir", join(OUT, name), "--emptyOutDir"]);
}

const scratch = mkdtempSync(join(tmpdir(), "whykit-explorer-e2e-"));
try {
  const fresh = join(scratch, "fresh");
  run(PYTHON, [resolve(REPO, "scripts/whykit.py"), "init", "--minimal", fresh]);
  build("empty", fresh);
  build("northline", resolve(REPO, "examples/northline"));
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
