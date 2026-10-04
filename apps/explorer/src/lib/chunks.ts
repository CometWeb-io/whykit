/**
 * The parts of the index that load after the first paint (note bodies, lint
 * findings) are separate JSON files in a normal build. A single-file build
 * (`npm run build:single`) cannot fetch files, because browsers refuse
 * `fetch()` from `file://`, so it embeds each one in the page instead, as an
 * inert `<script type="application/json">` data block holding the gzipped
 * JSON in base64. `readChunk` prefers such a block and fetches otherwise.
 */
export const CHUNK_ELEMENT_PREFIX = "whykit-chunk-";

type ElementLookup = { getElementById(id: string): { textContent: string | null } | null };
type Fetcher = (url: string) => Promise<Response>;

/** Inflate a gzip + base64 data block back to its text. */
export async function decodeChunk(encoded: string): Promise<string> {
  const binary = atob(encoded.trim());
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).text();
}

/** Read chunk `name`: from its embedded data block if the page has one, else from `url`. */
export async function readChunk<T>(
  name: string,
  url: string,
  doc: ElementLookup | undefined = globalThis.document,
  fetcher: Fetcher = u => fetch(u),
): Promise<T> {
  const embedded = doc?.getElementById(`${CHUNK_ELEMENT_PREFIX}${name}`);
  if (embedded) return JSON.parse(await decodeChunk(embedded.textContent ?? "")) as T;
  const res = await fetcher(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${res.url || url}`);
  return res.json() as Promise<T>;
}
