export type View = "home" | "decisions" | "evidence" | "reviews" | "graph" | "health" | "templates" | "adopt" | "doc";
export type Route = { view: View; doc?: string };

export const PAGE_VIEWS: readonly View[] = ["home", "decisions", "evidence", "reviews", "graph", "health", "templates", "adopt"];

// Routing lives in the URL fragment so the Explorer works from any static
// host. A fragment that is not a route (an in-page anchor such as `#content`
// or a heading slug) returns null so the caller can keep the current page
// instead of silently jumping home.
export function parseHash(hash: string): Route | null {
  const raw = hash.replace(/^#/, "");
  if (!raw) return { view: "home" };
  if (!raw.includes("=")) return null;
  const params = new URLSearchParams(raw);
  const doc = params.get("doc");
  if (doc) return { view: "doc", doc };
  const view = params.get("view") as View | null;
  return { view: view && PAGE_VIEWS.includes(view) ? view : "home" };
}

export function hrefFor(view: View, doc?: string): string {
  return doc ? `#doc=${encodeURIComponent(doc)}` : `#view=${view}`;
}
