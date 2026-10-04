// Build-time only (Node): turns a normal Vite build into one HTML file that
// works when opened straight from disk. Browsers do not run module scripts
// loaded from `file://` and refuse `fetch()` there, so the script and the
// stylesheet are inlined and the lazily loaded chunks become data blocks
// (see chunks.ts). The Content Security Policy then names the inlined script
// and stylesheet by hash instead of allowing inline code in general.
import { createHash } from "node:crypto";
import { constants, gzipSync } from "node:zlib";
import { CHUNK_ELEMENT_PREFIX } from "./chunks.ts";

export interface SingleFileParts {
  /** The page Vite wrote, still loading `scriptFile` and `styleFile`. */
  html: string;
  scriptFile: string;
  script: string;
  styleFile: string;
  style: string;
  /** Chunk name → JSON text, embedded as `whykit-chunk-<name>`. */
  chunks: Readonly<Record<string, string>>;
}

/** gzip, then base64: the alphabet cannot end an HTML element early. */
export function encodeChunk(text: string): string {
  return gzipSync(Buffer.from(text, "utf8"), { level: constants.Z_BEST_COMPRESSION }).toString("base64");
}

function sha256(text: string): string {
  return `'sha256-${createHash("sha256").update(text, "utf8").digest("base64")}'`;
}

/** The policy of a single-file build: nothing but the two inlined elements. */
export function singleFileCsp(scriptHash: string, styleHash: string): string {
  return [
    "default-src 'none'",
    `script-src ${scriptHash}`,
    `style-src ${styleHash}`,
    "img-src data:",
    "connect-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Replace the one tag that loads `file`, or refuse. */
function replaceTag(html: string, tag: RegExp, file: string, replacement: string): string {
  const matches = html.match(new RegExp(tag.source, "g")) ?? [];
  if (matches.length !== 1) throw new Error(`the built page does not load ${file} exactly once (found ${matches.length})`);
  return html.replace(tag, () => replacement);
}

export function buildSingleFile(parts: SingleFileParts): { html: string; csp: string } {
  // `</script` inside the module would end the element. Every place it can
  // occur in valid JavaScript (string, template, regular expression, comment)
  // reads `<\/script` the same way.
  const script = parts.script.replace(/<\/(script)/gi, "<\\/$1");
  // `<!--` followed by `<script` would switch the HTML tokenizer into a state
  // where the closing tag no longer ends the element; no rewrite of
  // JavaScript fixes that in every syntactic position, so refuse instead.
  if (script.includes("<!--")) throw new Error("the script contains <!-- and cannot be inlined safely");
  if (/<\/style/i.test(parts.style)) throw new Error("the stylesheet contains </style and cannot be inlined");
  const file = (name: string) => `\\./${escapeRegExp(name)}`;
  let html = parts.html;
  if (!new RegExp(`src="${file(parts.scriptFile)}"`).test(html)) throw new Error(`the built page does not load ${parts.scriptFile}`);
  if (!new RegExp(`href="${file(parts.styleFile)}"`).test(html)) throw new Error(`the built page does not load ${parts.styleFile}`);
  // Placeholders first, so the check for leftover references below reads the
  // page itself and not the text of the inlined script or stylesheet.
  html = replaceTag(html, new RegExp(`<script\\b[^>]*\\ssrc="${file(parts.scriptFile)}"[^>]*>\\s*</script>`), parts.scriptFile, "<!--whykit:script-->");
  html = replaceTag(html, new RegExp(`<link\\b[^>]*\\shref="${file(parts.styleFile)}"[^>]*>`), parts.styleFile, "<!--whykit:style-->");
  const leftover = html.match(/<[^>]+\s(?:src|href)\s*=\s*"[^"]*"[^>]*>/i);
  if (leftover) throw new Error(`the single file still references another file: ${leftover[0]}`);
  const external = parts.style.match(/url\(\s*(?!["']?data:)[^)]*\)/i);
  if (external) throw new Error(`the stylesheet loads another file and cannot be inlined: ${external[0]}`);
  if (!html.includes("</body>")) throw new Error("the built page has no </body>");
  const blocks = Object.entries(parts.chunks)
    .map(([name, text]) => `    <script type="application/json" id="${CHUNK_ELEMENT_PREFIX}${name}">${encodeChunk(text)}</script>\n`)
    .join("");
  html = html
    .replace("</body>", () => `${blocks}  </body>`)
    .replace("<!--whykit:script-->", () => `<script type="module">${script}</script>`)
    .replace("<!--whykit:style-->", () => `<style>${parts.style}</style>`);
  const csp = singleFileCsp(sha256(script), sha256(parts.style));
  const charset = '<meta charset="UTF-8" />';
  if (!html.includes(charset)) throw new Error("the built page lost its charset meta; the policy has nowhere to go");
  html = html.replace(charset, () => `${charset}\n    <meta http-equiv="Content-Security-Policy" content="${csp}" />`);
  return { html, csp };
}
