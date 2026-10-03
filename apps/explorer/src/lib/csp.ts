/**
 * The Content Security Policy of the static build. The production page has no
 * inline script or style element and loads nothing from another origin, so
 * everything is limited to the page's own origin. It ships as a <meta> tag
 * because a static host often cannot set headers; a host that can should send
 * it as a header with `frame-ancestors` added, which a <meta> tag cannot
 * carry (README.md, "Security headers", lists the header for common hosts).
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

/** The same policy as a response header, which can also forbid framing. */
export const CSP_HEADER = `${CSP}; frame-ancestors 'none'`;
