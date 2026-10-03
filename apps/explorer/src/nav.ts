import { useLayoutEffect, useState } from "react";
import { parseHash, routeHref, withParams, type Params, type Route, type View } from "./lib/route.ts";

// Every piece of view state (filters, selection, search query) lives in the
// URL fragment, so a link or a reload restores exactly what was on screen.
// Page changes push a history entry; state changes within a page replace the
// current one, so Back leaves the page instead of undoing keystrokes.
const ROUTE_EVENT = "whykit:route";
let last: Route = parseHash(location.hash) ?? { view: "home" };

/** The route in the address bar, or the last one when the fragment is an in-page anchor. */
export function currentRoute(): Route {
  return parseHash(location.hash) ?? last;
}

export function navigate(route: Route, mode: "push" | "replace" = "push"): void {
  const href = routeHref(route);
  if (mode === "push") {
    location.hash = href;
    return;
  }
  history.replaceState(history.state, "", href);
  window.dispatchEvent(new Event(ROUTE_EVENT));
}

export function go(view: View, doc?: string, params?: Params): void {
  navigate({ view, ...(doc ? { doc } : {}), ...(params ? { params } : {}) });
}

/** Update view state in place; `null` removes a key. */
export function setParams(patch: Record<string, string | null | undefined>): void {
  navigate(withParams(currentRoute(), patch), "replace");
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(currentRoute);
  // Subscribe before the first paint so no navigation can slip past.
  useLayoutEffect(() => {
    // Anchors that are not routes keep the current page rather than resetting it.
    const on = () => {
      const next = parseHash(location.hash);
      if (!next) return;
      last = next;
      setRoute(prev => (routeHref(prev) === routeHref(next) ? prev : next));
    };
    window.addEventListener("hashchange", on);
    window.addEventListener(ROUTE_EVENT, on);
    return () => {
      window.removeEventListener("hashchange", on);
      window.removeEventListener(ROUTE_EVENT, on);
    };
  }, []);
  return route;
}
