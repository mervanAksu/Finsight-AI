// FinSight AI API istemcisi. Uç noktalar ve şekiller: docs/api-contract.md
import type {
  AskResponse,
  CompanyListItem,
  CompareResponse,
  FactsResponse,
  FilingsResponse,
  Health,
  IngestResponse,
  Job,
  Overview,
  RatiosResponse,
  RiskChangesResponse,
  ScoreResponse,
  SearchResult,
  SentimentResponse,
} from "./types";

/** Derleme anında build.mjs tarafından gömülür ("" = aynı sunucu). */
export const API_BASE: string = (typeof __API_BASE__ === "string" ? __API_BASE__ : "").replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function isDetailBody(value: unknown): value is { detail: unknown } {
  return typeof value === "object" && value !== null && "detail" in value;
}

/** FastAPI doğrulama hataları `detail` alanında dizi döndürebilir; okunur metne çevrilir. */
function detailToText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d: unknown) => (typeof d === "object" && d !== null && "msg" in d ? String((d as { msg: unknown }).msg) : null))
      .filter((s): s is string => s !== null);
    return parts.length ? parts.join("; ") : null;
  }
  return null;
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      ...init,
      headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "");
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body: unknown = await res.json();
      if (isDetailBody(body)) detail = detailToText(body.detail) ?? "";
    } catch {
      detail = "";
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  health: (signal?: AbortSignal) => request<Health>("/api/health", { signal }),

  companies: (signal?: AbortSignal) => request<CompanyListItem[]>("/api/companies", { signal }),

  search: (q: string, limit = 10, signal?: AbortSignal) =>
    request<SearchResult[]>(`/api/companies/search?q=${enc(q)}&limit=${limit}`, { signal }),

  ingest: (ticker: string, analyze = true) =>
    request<IngestResponse>(`/api/companies/${enc(ticker)}/ingest`, {
      method: "POST",
      body: JSON.stringify({ analyze }),
    }),

  job: (jobId: number, signal?: AbortSignal) => request<Job>(`/api/jobs/${jobId}`, { signal }),

  overview: (ticker: string, signal?: AbortSignal) =>
    request<Overview>(`/api/companies/${enc(ticker)}/overview`, { signal }),

  facts: (ticker: string, metric?: string, signal?: AbortSignal) =>
    request<FactsResponse>(`/api/companies/${enc(ticker)}/facts${metric ? `?metric=${enc(metric)}` : ""}`, { signal }),

  ratios: (ticker: string, signal?: AbortSignal) =>
    request<RatiosResponse>(`/api/companies/${enc(ticker)}/ratios`, { signal }),

  score: (ticker: string, signal?: AbortSignal) =>
    request<ScoreResponse>(`/api/companies/${enc(ticker)}/score`, { signal }),

  filings: (ticker: string, form?: string, limit = 50, signal?: AbortSignal) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (form) qs.set("form", form);
    return request<FilingsResponse>(`/api/companies/${enc(ticker)}/filings?${qs.toString()}`, { signal });
  },

  sentiment: (ticker: string, signal?: AbortSignal) =>
    request<SentimentResponse>(`/api/companies/${enc(ticker)}/sentiment`, { signal }),

  riskChanges: (ticker: string, signal?: AbortSignal) =>
    request<RiskChangesResponse>(`/api/companies/${enc(ticker)}/risk-changes`, { signal }),

  ask: (ticker: string, question: string, signal?: AbortSignal) =>
    request<AskResponse>(`/api/companies/${enc(ticker)}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
      signal,
    }),

  compare: (tickers: string[], signal?: AbortSignal) =>
    request<CompareResponse>(`/api/compare?tickers=${tickers.map(enc).join(",")}`, { signal }),

  exportCsvUrl: (ticker: string) => apiUrl(`/api/companies/${enc(ticker)}/export.csv`),
};

/** SEC EDGAR başvuru klasörü bağlantısı (Source nesnesinde url olmadığında). */
export function edgarFilingUrl(cik: string, accessionNo: string): string {
  const cikNum = String(Number.parseInt(cik, 10));
  return `https://www.sec.gov/Archives/edgar/data/${cikNum}/${accessionNo.replace(/-/g, "")}/`;
}
