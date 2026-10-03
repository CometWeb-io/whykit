export type View =
  | "home" | "decisions" | "timeline" | "evidence" | "freshness" | "reviews" | "graph" | "health"
  | "templates" | "adopt" | "doc";
export type Params = Record<string, string>;
/** `params` holds per-view state (filters, selection, search) and is omitted when empty. */
export type Route = { view: View; doc?: string; params?: Params };

export const PAGE_VIEWS: readonly View[] = [
  "home", "decisions", "timeline", "evidence", "freshness", "reviews", "graph", "health", "templates", "adopt",
];

// Routing lives in the URL fragment so the Explorer works from any static
// host. A fragment that is not a route (an in-page anchor such as `#content`
// or a heading slug) returns null so the caller can keep the current page
// instead of silently jumping home.
export function parseHash(hash: string): Route | null {
  const raw = hash.replace(/^#/, "");
  if (!raw) return { view: "home" };
  if (!raw.includes("=")) return null;
  const search = new URLSearchParams(raw);
  const params: Params = {};
  for (const [key, value] of search) {
    if (key !== "view" && key !== "doc") params[key] = value;
  }
  const withParams = (route: Route): Route => (Object.keys(params).length ? { ...route, params } : route);
  const doc = search.get("doc");
  if (doc) return withParams({ view: "doc", doc });
  const view = search.get("view") as View | null;
  return withParams({ view: view && PAGE_VIEWS.includes(view) ? view : "home" });
}

export function hrefFor(view: View, doc?: string, params?: Params): string {
  const search = new URLSearchParams(doc ? { doc } : { view });
  // Sorted keys give one canonical URL per state, so links compare equal.
  for (const key of Object.keys(params ?? {}).sort()) search.set(key, params![key]!);
  // URLSearchParams writes spaces as `+`; `%20` reads the same and is what
  // people expect to see in a shared link.
  return `#${search.toString().replaceAll("+", "%20")}`;
}

/** Route with `patch` applied; `null` or `undefined` removes a key, "" keeps it empty. */
export function withParams(route: Route, patch: Record<string, string | null | undefined>): Route {
  const params: Params = { ...route.params };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === undefined) delete params[key];
    else params[key] = value;
  }
  const next: Route = { view: route.view };
  if (route.doc !== undefined) next.doc = route.doc;
  if (Object.keys(params).length) next.params = params;
  return next;
}

export function routeHref(route: Route): string {
  return hrefFor(route.view, route.doc, route.params);
}
