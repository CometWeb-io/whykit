import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("an imported index cannot silently bypass the public export policy", () => {
  const scratch = mkdtempSync(join(tmpdir(), "whykit-public-build-"));
  try {
    const file = join(scratch, "unverified.json");
    writeFileSync(file, JSON.stringify({ exportMode: "public", docs: [{ sensitivity: "restricted" }] }));
    const outputs = ["vault", "bodies", "findings"].map(name => `src/generated/${name}.json`);
    const before = outputs.map(path => existsSync(path) ? readFileSync(path) : null);
    const result = spawnSync(process.execPath, ["scripts/build-vault-index.mjs", "--from", file], {
      env: { ...process.env, WHYKIT_EXPLORER_PRIVATE: "" }, encoding: "utf8",
    });
    assert.equal(result.status, 2);
    assert.match(result.stderr, /unverified index/);
    assert.deepEqual(outputs.map(path => existsSync(path) ? readFileSync(path) : null), before);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});

test("a malformed private opt-in fails before indexing", () => {
  const result = spawnSync(process.execPath, ["scripts/build-vault-index.mjs"], {
    env: { ...process.env, WHYKIT_EXPLORER_PRIVATE: "true" }, encoding: "utf8",
  });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /must be unset or 1/);
});
