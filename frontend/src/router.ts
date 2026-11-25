// Basit hash yönlendirici: #/ · #/company/EXMP/overview · #/compare?tickers=A,B
import { useSyncExternalStore } from "react";

export type CompanyTab = "overview" | "financials" | "ratios" | "tone" | "risk" | "ask" | "filings";
export const COMPANY_TABS: readonly CompanyTab[] = ["overview", "financials", "ratios", "tone", "risk", "ask", "filings"];

export type Route =
  | { name: "home" }
  | { name: "company"; ticker: string; tab: CompanyTab }
  | { name: "compare"; tickers: string[] }
  | { name: "notFound"; path: string };

function isTab(value: string): value is CompanyTab {
  return (COMPANY_TABS as readonly string[]).includes(value);
}

/** Bozuk yüzde kodlamasında (ör. "%E0", "%") URIError yerine null döner (SEC-11, CWE-248). */
function safeDecode(segment: string): string | null {
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "") || "/";
  const [pathPart, queryPart = ""] = raw.split("?", 2);
  const decoded = pathPart.split("/").filter(Boolean).map(safeDecode);
  // Çözülemeyen bir parça varsa sayfa çökmez; "bulunamadı" gösterilir.
  if (decoded.some((x) => x === null)) return { name: "notFound", path: raw };
  const segments = decoded as string[];
  let query: URLSearchParams;
  try {
    query = new URLSearchParams(queryPart);
  } catch {
    return { name: "notFound", path: raw };
  }

  if (segments.length === 0) return { name: "home" };
  if (segments[0] === "company" && segments[1]) {
    const tab = segments[2] ?? "overview";
    if (!isTab(tab) || segments.length > 3) return { name: "notFound", path: raw };
    return { name: "company", ticker: segments[1].toUpperCase(), tab };
  }
  if (segments[0] === "compare" && segments.length === 1) {
    const tickers = (query.get("tickers") ?? "")
      .split(",")
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    return { name: "compare", tickers: Array.from(new Set(tickers)).slice(0, 4) };
  }
  return { name: "notFound", path: raw };
}

export function hrefFor(route: Route): string {
  switch (route.name) {
    case "home":
      return "#/";
    case "company":
      return `#/company/${encodeURIComponent(route.ticker)}/${route.tab}`;
    case "compare":
      return route.tickers.length ? `#/compare?tickers=${route.tickers.map(encodeURIComponent).join(",")}` : "#/compare";
    case "notFound":
      return `#${route.path}`;
  }
}

export function navigate(route: Route, options: { replace?: boolean } = {}): void {
  const href = hrefFor(route);
  if (options.replace) {
    const url = `${window.location.pathname}${window.location.search}${href}`;
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = href;
  }
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

function getSnapshot(): string {
  return window.location.hash;
}

export function useHash(): string {
  return useSyncExternalStore(subscribe, getSnapshot, () => "");
}
