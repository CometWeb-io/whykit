import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

/**
 * A Content Security Policy for the static build. The production page has no
 * inline script or style element and loads nothing from another origin, so
 * everything is limited to the page's own origin. The policy is a <meta> tag
 * because a static host often cannot set headers; one that can should send
 * the same policy as a header and add `frame-ancestors`, which a <meta> tag
 * cannot carry. The dev server injects inline scripts, so it is left alone.
 */
export const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

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

export default defineConfig({
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
