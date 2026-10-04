import { test } from "node:test";
import assert from "node:assert/strict";
import { gzipSync } from "node:zlib";
import { CHUNK_ELEMENT_PREFIX, decodeChunk, readChunk } from "../src/lib/chunks.ts";
import { encodeChunk } from "../src/lib/singlefile.ts";

const sample = { "a/one": "Zażółć gęślą jaźń — </script> and   survive", "a/two": "x".repeat(10_000) };

test("an embedded chunk is gzip then base64, and decodes back to the same text", async () => {
  const text = JSON.stringify(sample);
  const encoded = encodeChunk(text);
  assert.match(encoded, /^[A-Za-z0-9+/]+=*$/, "base64 alphabet only, so it is inert inside an HTML element");
  assert.ok(encoded.length < text.length, "compressed");
  assert.equal(await decodeChunk(encoded), text);
  // Anything gzip produces decodes, not only what this module wrote.
  assert.equal(await decodeChunk(gzipSync(Buffer.from(text)).toString("base64")), text);
});

test("an embedded chunk wins over the network", async () => {
  const el = { textContent: `\n${encodeChunk(JSON.stringify(sample))}\n` };
  const doc = { getElementById: (id: string) => (id === `${CHUNK_ELEMENT_PREFIX}bodies` ? el : null) };
  const fetcher = () => Promise.reject(new Error("must not fetch"));
  assert.deepEqual(await readChunk("bodies", "./assets/bodies.json", doc, fetcher), sample);
});

test("without an embedded chunk the file is fetched, and an HTTP error is an error", async () => {
  const doc = { getElementById: () => null };
  const ok = (url: string) => Promise.resolve(new Response(JSON.stringify({ url }), { status: 200 }));
  assert.deepEqual(await readChunk("findings", "./assets/findings.json", doc, ok), { url: "./assets/findings.json" });
  const missing = () => Promise.resolve(new Response("nope", { status: 404 }));
  await assert.rejects(readChunk("findings", "./assets/findings.json", doc, missing), /404/);
});
