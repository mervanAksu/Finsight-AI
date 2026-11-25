// FinSight AI API istemcisi. Uç noktalar ve şekiller: docs/api-contract.md
import type {
  AskResponse,
  CompanyListItem,
  CompareResponse,
  FactsResponse,
  FilingsResponse,
  Freshness,
  Health,
  IngestResponse,
  Job,
  Overview,
  RatiosResponse,
  RiskChangesResponse,
  ScoreResponse,
  SearchPage,
  SearchReason,
  SearchResult,
  SentimentResponse,
} from "./types";

/** Derleme anında build.mjs tarafından gömülür ("" = aynı sunucu). */
export const API_BASE: string = (typeof __API_BASE__ === "string" ? __API_BASE__ : "").replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** v1.5: 429 / 503 yanıtlarındaki Retry-After (saniye); yoksa null. */
  readonly retryAfter: number | null;

  constructor(status: number, detail: string, retryAfter: number | null = null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

/** Retry-After başlığı: saniye veya HTTP tarihi. */
function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const sec = Number(value);
  if (Number.isFinite(sec) && sec >= 0) return Math.ceil(sec);
  const at = Date.parse(value);
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - Date.now()) / 1000));
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
  return (await requestWithHeaders<T>(path, init)).body;
}

async function requestWithHeaders<T>(path: string, init?: RequestInit): Promise<{ body: T; headers: Headers }> {
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
    throw new ApiError(res.status, detail, parseRetryAfter(res.headers.get("Retry-After")));
  }
  return { body: (await res.json()) as T, headers: res.headers };
}

const SEARCH_REASONS: readonly SearchReason[] = ["sec_user_agent_missing", "sec_unavailable"];

const enc = encodeURIComponent;

export const api = {
  health: (signal?: AbortSignal) => request<Health>("/api/health", { signal }),

  companies: (signal?: AbortSignal) => request<CompanyListItem[]>("/api/companies", { signal }),

  search: (q: string, limit = 10, signal?: AbortSignal) =>
    request<SearchResult[]>(`/api/companies/search?q=${enc(q)}&limit=${limit}`, { signal }),

  /** Arama + kapsam: sonuçlar SEC listesinden mi, yalnızca yerel veritabanından mı (FR-01). */
  searchWithScope: async (q: string, limit = 10, signal?: AbortSignal): Promise<SearchPage> => {
    const { body, headers } = await requestWithHeaders<SearchResult[]>(
      `/api/companies/search?q=${enc(q)}&limit=${limit}`,
      { signal },
    );
    const scopeRaw = headers.get("X-FinSight-Search-Scope");
    const scope = scopeRaw === "sec" || scopeRaw === "local" ? scopeRaw : null;
    const reasonRaw = headers.get("X-FinSight-Search-Reason");
    const reason = SEARCH_REASONS.find((r) => r === reasonRaw) ?? (scope === "local" ? "sec_unavailable" : null);
    return { results: body, scope, reason };
  },

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

  /** v1.4: SEC'te analizden sonra yeni 10-K/10-Q yayımlanmış mı (Aşama 4 §4). */
  freshness: (ticker: string, signal?: AbortSignal) =>
    request<Freshness>(`/api/companies/${enc(ticker)}/freshness`, { signal }),

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
