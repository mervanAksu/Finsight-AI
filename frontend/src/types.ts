// docs/api-contract.md (v1, v1.1 netleştirmeleriyle) ile birebir tipler. Alan adları sözleşmeden kopyalanmıştır;
// değiştirmeden önce sözleşme güncellenmelidir.

export type ISODate = string; // "2025-06-30"
export type ISODateTime = string; // "2026-11-18T13:00:00Z"

export interface Company {
  ticker: string;
  cik: string;
  name: string;
  sic: string | null;
  sic_description: string | null;
  fiscal_year_end: string | null; // "0630"
  exchanges: string[];
}

export interface FilingRef {
  accession_no: string;
  form: string;
  filing_date: ISODate;
  report_date: ISODate | null;
  url: string;
}

export interface Source {
  xbrl_tag: string;
  form: string;
  accession_no: string;
  filed: ISODate;
  derived_from: string[] | null;
  /** Başvuru klasörü (sözleşme v1.1): .../edgar/data/{cik}/{accession tiresiz}/ */
  url: string;
}

export interface ApiErrorBody {
  detail: string;
}

// GET /api/health
export interface LlmInfo {
  provider: string;
  model: string | null;
  enabled: boolean;
}
export interface Health {
  status: string;
  version: string;
  models: { sentiment: string; embedding: string; llm: LlmInfo };
}

// GET /api/companies
export interface ScoreBrief {
  total: number | null;
  band: string | null;
}
export interface CompanyListItem {
  ticker: string;
  cik: string;
  name: string;
  sic_description: string | null;
  analyzed: boolean;
  last_filing_date: ISODate | null;
  score: ScoreBrief | null;
}

// GET /api/companies/search
export interface SearchResult {
  ticker: string;
  cik: string;
  name: string;
  ingested: boolean;
}

// POST /api/companies/{ticker}/ingest
export interface IngestResponse {
  job_id: number;
  status: JobStatus;
}

// GET /api/jobs/{job_id}
export type JobStatus = "queued" | "running" | "done" | "error";
export interface Job {
  id: number;
  ticker: string;
  status: JobStatus;
  step: string | null;
  progress: number;
  error: string | null;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  request_count: number;
  /** İşi durdurmayan adım uyarıları (sözleşmeye ek alan). */
  warnings?: string[];
}

// GET /api/companies/{ticker}/overview
export type MetricKey =
  | "revenue"
  | "cost_of_revenue"
  | "gross_profit"
  | "operating_income"
  | "net_income"
  | "operating_cash_flow"
  | "capex"
  | "total_assets"
  | "current_assets"
  | "current_liabilities"
  | "total_liabilities"
  | "stockholders_equity";

export interface KeyMetric {
  metric: MetricKey | string;
  label: string;
  value: number | null;
  /** Kalemin hiç değeri yoksa null (backend views.key_metrics). */
  period_end: ISODate | null;
  yoy: number | null;
  source: Source | null;
  /** Değer yoksa neden (ör. "veri yok"). */
  reason?: string | null;
}
export interface ScoreSummary {
  total: number | null;
  band: string | null;
  as_of_period: ISODate | null;
}
export interface SentimentSummary {
  filing: FilingRef;
  mdna_net_tone: number | null;
  risk_net_tone: number | null;
  model: string;
}
export interface RiskCounts {
  added: number;
  removed: number;
  modified: number;
  unchanged: number;
}
export interface Overview {
  company: Company;
  /** Metin analizi yapılmış mı (sözleşme v1.1). false: yalnızca finansal veri çekilmiş. */
  analyzed: boolean;
  score: ScoreSummary | null;
  key_metrics: KeyMetric[];
  latest_filings: FilingRef[];
  sentiment_summary: SentimentSummary | null;
  risk_summary: RiskCounts | null;
  analysis_updated_at: ISODateTime | null;
  disclaimer: string;
}

// GET /api/companies/{ticker}/facts
export interface FactValue {
  period_start: ISODate | null;
  period_end: ISODate;
  value: number | null;
  yoy: number | null;
  source: Source | null;
}
export interface FactMetric {
  metric: MetricKey | string;
  label: string;
  kind: "duration" | "instant";
  values: FactValue[];
}
export interface FactsResponse {
  company: Company;
  metrics: FactMetric[];
  disclaimer: string;
}

// GET /api/companies/{ticker}/ratios
export type RatioUnit = "percent" | "times";
export interface RatioInput {
  metric: string;
  value: number | null;
  accession_no: string;
}
export interface RatioValue {
  period_end: ISODate;
  value: number | null;
  status: "ok" | "missing";
  reason: string | null;
  note: string | null;
  inputs: RatioInput[];
}
export interface Ratio {
  key: string;
  label: string;
  formula: string;
  unit: RatioUnit;
  higher_is_better: boolean;
  values: RatioValue[];
}
export interface RatiosResponse {
  company: Company;
  periods: ISODate[];
  ratios: Ratio[];
  disclaimer: string;
}

// GET /api/companies/{ticker}/score
export interface ScoreIndicator {
  key: string;
  label: string;
  value: number | null;
  display: string;
  points: number | null;
  reason?: string | null;
  threshold?: string;
}
export interface ScoreComponent {
  key: string;
  label: string;
  weight: number;
  score: number | null;
  available: boolean;
  indicators: ScoreIndicator[];
  explanation: string;
}
export interface ScoreResponse {
  total: number | null;
  band: string | null;
  as_of_period: ISODate | null;
  weights_version: string;
  components: ScoreComponent[];
  missing_components: string[];
  explanation: string | null;
  disclaimer: string;
}

// GET /api/companies/{ticker}/filings
export interface Filing extends FilingRef {
  items: string[];
  item_labels: string[];
  analyzed: boolean;
}
export interface FilingsResponse {
  company: Company;
  filings: Filing[];
  disclaimer: string;
}

// GET /api/companies/{ticker}/sentiment
export interface ScoredSentence {
  sentence: string;
  score: number;
}
export interface SentimentSection {
  filing: FilingRef;
  item_code: string; // "1A" | "7" | "2"
  title: string;
  sentence_count: number;
  positive: number;
  negative: number;
  neutral: number;
  /** Bölümde cümle yoksa null (sentiment.summarize). */
  net_tone: number | null;
  top_negative: ScoredSentence[];
  top_positive: ScoredSentence[];
}
export interface SentimentResponse {
  model: string;
  sections: SentimentSection[];
  disclaimer: string;
}

// GET /api/companies/{ticker}/risk-changes
export type ChangeType = "added" | "removed" | "modified";
export interface RiskChange {
  change_type: ChangeType;
  old_text: string | null;
  new_text: string | null;
  similarity: number | null;
}
export interface RiskChangesResponse {
  old_filing: FilingRef | null;
  new_filing: FilingRef | null;
  counts: RiskCounts;
  changes: RiskChange[];
  disclaimer: string;
}

// POST /api/companies/{ticker}/ask
export type AskMode = "llm" | "extractive" | "not_found";
export interface AskSource {
  n: number;
  chunk_id: number;
  section: string;
  section_title: string;
  form: string;
  filing_date: ISODate;
  accession_no: string;
  url: string;
  text: string;
  similarity: number;
}
export interface AskResponse {
  question: string;
  answer: string;
  found: boolean;
  sources: AskSource[];
  mode: AskMode;
  model: string | null;
  latency_ms: number;
  disclaimer: string;
  /** Soru yatırım tavsiyesi niyeti taşıyorsa true; cevabın başında sabit ret cümlesi vardır (v1.1). */
  advice_refused: boolean;
  /** Ör. parçalar farklı bir gömme modeliyle oluşturulmuşsa yeniden analiz uyarısı. */
  warning?: string | null;
}

// GET /api/compare
export interface RatioMeta {
  key: string;
  label: string;
  unit: RatioUnit;
  higher_is_better: boolean;
}
export interface CompareCompany {
  ticker: string;
  name: string;
  period_end: ISODate | null;
  score: ScoreBrief | null;
  ratios: Record<string, number | null>;
}
export interface CompareResponse {
  ratio_meta: RatioMeta[];
  companies: CompareCompany[];
  disclaimer: string;
}
