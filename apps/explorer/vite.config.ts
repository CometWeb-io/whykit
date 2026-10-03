import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { CSP } from "./src/lib/csp.ts";
import { buildSingleFile } from "./src/lib/singlefile.ts";

function contentSecurityPolicy(): Plugin {
  return {
    name: "whykit-explorer-csp",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      // Right after the charset, which must stay first, and before any script.
      handler: (html: string) => {
        const charset = '<meta charset="UTF-8" />';
        if (!html.includes(charset)) throw new Error("index.html lost its charset meta; the CSP has nowhere to go");
        return html.replace(charset, `${charset}\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`);
      },
    },
  };
}

const GENERATED = resolve(import.meta.dirname, "src/generated");
const SUMMARY_MODULE = "\0whykit-summary";
const NO_URL_MODULE = "\0whykit-no-url";

/** Largest single file `build:single` writes before it refuses (override with WHYKIT_SINGLE_MAX_MB). */
export const SINGLE_MAX_MB = 64;

/**
 * `vite build --mode single`: one HTML file that works from `file://`.
 *
 * Browsers neither run a module script loaded from `file://` nor let a page
 * there `fetch()` its own files, so this build inlines the script and the
 * stylesheet and embeds the summary, the note bodies and the lint findings as
 * compressed data blocks (see src/lib/singlefile.ts and src/lib/chunks.ts).
 * The summary stays out of the script so no vault text ends up inside
 * executable code; the script reads it back with a top-level await.
 */
function singleFile(): Plugin[] {
  // Resolution must run before Vite's own (JSON and `?url` handling); the
  // page can only be assembled after Vite has written it.
  return [{
    name: "whykit-explorer-single-file-data",
    apply: "build",
    enforce: "pre",
    resolveId(source, importer) {
      if (!importer?.replaceAll("\\", "/").endsWith("/src/lib/vault.ts")) return null;
      if (source === "../generated/vault.json") return SUMMARY_MODULE;
      // The chunks are embedded in the page, so their file URLs are unused;
      // left alone they would be inlined into the script as data: URLs.
      if (/^\.\.\/generated\/[^/]+\.json\?url$/.test(source)) return NO_URL_MODULE;
      return null;
    },
    load(id) {
      if (id === NO_URL_MODULE) return 'export default "";';
      if (id !== SUMMARY_MODULE) return null;
      return [
        `import { readChunk } from ${JSON.stringify(resolve(import.meta.dirname, "src/lib/chunks.ts"))};`,
        'export default await readChunk("summary", "", document, () => Promise.reject(new Error("the summary data block is missing")));',
      ].join("\n");
    },
  }, {
    name: "whykit-explorer-single-file",
    apply: "build",
    enforce: "post",
    generateBundle(_options, bundle) {
      const entries = Object.values(bundle).filter(item => item.type === "chunk");
      if (entries.length !== 1 || !entries[0]?.isEntry) throw new Error(`a single-file build needs exactly one script chunk, got ${entries.length}`);
      const script = entries[0];
      const styles = Object.values(bundle).filter(item => item.type === "asset" && item.fileName.endsWith(".css"));
      const page = bundle["index.html"];
      if (styles.length !== 1 || !styles[0] || styles[0].type !== "asset") throw new Error(`a single-file build needs exactly one stylesheet, got ${styles.length}`);
      if (!page || page.type !== "asset") throw new Error("the build wrote no index.html");
      const read = (name: string) => readFileSync(resolve(GENERATED, name), "utf8");
      const { html } = buildSingleFile({
        html: String(page.source),
        scriptFile: script.fileName,
        script: script.code,
        styleFile: styles[0].fileName,
        style: String(styles[0].source),
        chunks: { summary: read("vault.json"), bodies: read("bodies.json"), findings: read("findings.json") },
      });
      for (const name of Object.keys(bundle)) delete bundle[name];
      const limit = Number(process.env.WHYKIT_SINGLE_MAX_MB || SINGLE_MAX_MB) * 1024 * 1024;
      const size = Buffer.byteLength(html, "utf8");
      if (size > limit) {
        this.error(`the single file would be ${(size / 1048576).toFixed(1)} MB, over the ${(limit / 1048576).toFixed(0)} MB limit; serve the normal build instead, or raise WHYKIT_SINGLE_MAX_MB`);
      }
      this.emitFile({ type: "asset", fileName: "index.html", source: html });
    },
  }];
}

export default defineConfig(({ mode }) => mode === "single" ? {
  base: "./",
  plugins: [react(), singleFile()],
  build: {
    outDir: "dist-single",
    chunkSizeWarningLimit: 20_000,
    // Nothing may be left as a separate file.
    assetsInlineLimit: () => true,
    modulePreload: false,
  },
} : {
  // Relative asset URLs plus hash routing let the same build be served from a
  // domain root, a sub-path (GitHub Pages project sites) or opened locally.
  base: "./",
  plugins: [react(), contentSecurityPolicy()],
  build: {
    // The bundled summary index is a few megabytes on a large vault.
    chunkSizeWarningLimit: 20_000,
    // Never inline the note bodies as a data: URL, however small: the CSP's
    // `connect-src 'self'` would block fetching it.
    assetsInlineLimit: (file: string) => (file.endsWith(".json") ? false : undefined),
  },
  server: { host: "127.0.0.1", port: 5173 },
  preview: { host: "127.0.0.1", port: 4173 },
});
