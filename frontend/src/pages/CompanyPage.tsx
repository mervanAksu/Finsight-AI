// Şirket sayfası: künye (masthead) + tazelik bandı + sekmeler
// (Özet, Finansallar, Oranlar, Metin tonu, Risk değişimi, Soru-Cevap, Başvurular).
// Sözleşme v1.1: işlenmemiş şirket 404 → "henüz işlenmedi" + veri çekme (FR-15);
// analyzed=false → yalnızca finansal veri var, künyede "Metin analizini çalıştır".
// Aşama 4: skorun dayanağı (TTM · dönem · form) ve /freshness → "Yeni rapor yayımlandı" bandı.
import { useEffect, useRef, useState } from "react";
import { LuCloudDownload, LuDownload, LuScanText } from "react-icons/lu";
import { api, ApiError } from "../api";
import { ScoreScale } from "../charts/ScoreGauge";
import { FreshnessBand, FreshnessStatus, freshnessOrNull } from "../components/Freshness";
import type { FreshnessState } from "../components/Freshness";
import { IngestPanel } from "../components/IngestPanel";
import { EmptyState, ErrorCard, Skeleton, Tip, cx } from "../components/ui";
import { fmtBasisLine, fmtDate, fmtDateTime, fmtFiscalYearEnd, fmtScore, scoreTone } from "../format";
import { bandLabel, useI18n } from "../i18n";
import type { MsgKey } from "../i18n";
import { useAsync } from "../lib/useAsync";
import { COMPANY_TABS, hrefFor } from "../router";
import type { CompanyTab } from "../router";
import type { Overview, ScoreSummary } from "../types";
import { AskTab } from "./company/AskTab";
import { FilingsTab } from "./company/FilingsTab";
import { FinancialsTab } from "./company/FinancialsTab";
import { OverviewTab } from "./company/OverviewTab";
import { RatiosTab } from "./company/RatiosTab";
import { RiskTab } from "./company/RiskTab";
import { ToneTab } from "./company/ToneTab";

const TAB_LABEL: Record<CompanyTab, MsgKey> = {
  overview: "tab.overview",
  financials: "tab.financials",
  ratios: "tab.ratios",
  tone: "tab.tone",
  risk: "tab.risk",
  ask: "tab.ask",
  filings: "tab.filings",
};

/** Backend api.TICKER_RE ile aynı: harf, rakam, nokta, tire; en fazla 10 karakter. */
const TICKER_RE = /^[A-Za-z0-9.-]{1,10}$/;

export function isValidTicker(ticker: string): boolean {
  return TICKER_RE.test(ticker);
}

/** Şirket kaydı var ama hiç finansal veri yok (ör. veri çekme yarıda kaldı). */
function hasFinancialData(o: Overview): boolean {
  return o.score !== null || o.key_metrics.length > 0;
}

export function CompanyPage({ ticker, tab }: { ticker: string; tab: CompanyTab }) {
  const { t } = useI18n();
  const valid = isValidTicker(ticker);
  const { state, reload } = useAsync(
    (signal) => (valid ? api.overview(ticker, signal) : Promise.reject(new ApiError(400, ""))),
    [ticker, valid],
  );

  if (state.status === "loading") {
    return (
      <div className="page company">
        <div className="masthead is-skeleton" aria-busy="true">
          <div className="mh-id">
            <Skeleton width={80} height={16} />
            <Skeleton width={320} height={34} />
            <Skeleton width={460} />
          </div>
          <div className="mh-score">
            <Skeleton width={180} height={44} />
            <Skeleton height={10} />
          </div>
        </div>
        <div className="grid-12">
          <div className="span-7 panel panel-sheet">
            <Skeleton height={220} />
          </div>
          <div className="span-5 panel panel-sheet">
            <Skeleton height={220} />
          </div>
        </div>
      </div>
    );
  }
  if (state.status === "error") {
    const status = state.error instanceof ApiError ? state.error.status : null;
    if (valid && status === 404) {
      // Geçerli biçimde ama veritabanında yok: SEC'ten çekmeyi öner (gerçek backend 404 döner).
      return (
        <div className="page company">
          <NotIngested ticker={ticker} onDone={reload} />
        </div>
      );
    }
    const notFound = !valid || status === 404 || status === 400;
    return (
      <div className="page company">
        <ErrorCard
          error={valid ? state.error : new Error(t("company.invalidTicker", { ticker }))}
          title={notFound ? t("state.companyNotFound") : undefined}
          onRetry={notFound ? undefined : reload}
        />
        {notFound && (
          <p className="back-link">
            <a href={hrefFor({ name: "home" })}>{t("state.backHome")}</a>
          </p>
        )}
      </div>
    );
  }
  return <CompanyLoaded ticker={ticker} tab={tab} overview={state.data} reload={reload} />;
}

function CompanyLoaded({
  ticker,
  tab,
  overview,
  reload,
}: {
  ticker: string;
  tab: CompanyTab;
  overview: Overview;
  reload: () => void;
}) {
  const { t } = useI18n();
  const hasData = hasFinancialData(overview);
  const canRunText = hasData && !overview.analyzed;
  const [textRun, setTextRun] = useState(false);
  const trackRef = useRef<HTMLDivElement | null>(null);
  // Dar ekranda etkin sekme kaydırılabilir şeritte görünür kalsın.
  useEffect(() => {
    const track = trackRef.current;
    const active = track?.querySelector<HTMLElement>(".tab.is-active");
    if (track && active && track.scrollWidth > track.clientWidth) {
      track.scrollLeft = active.offsetLeft - (track.clientWidth - active.offsetWidth) / 2;
    }
  }, [tab]);
  const fresh = useAsync((signal) => (hasData ? freshnessOrNull(api.freshness(ticker, signal)) : Promise.resolve(null)), [
    ticker,
    hasData,
  ]);

  return (
    <div className="page company">
      <Masthead
        overview={overview}
        hasData={hasData}
        fresh={fresh.state}
        onRetryFresh={fresh.reload}
        onRunText={canRunText && !textRun ? () => setTextRun(true) : undefined}
      />
      <FreshnessBand
        state={fresh.state}
        ticker={ticker}
        name={overview.company.name}
        onUpdated={() => {
          reload();
          fresh.reload();
        }}
      />
      {canRunText && textRun && (
        <IngestPanel
          className="text-run"
          ticker={ticker}
          name={overview.company.name}
          heading={t("company.textRunHeading")}
          autoStart
          analyze
          onDone={() => {
            setTextRun(false);
            reload();
          }}
        />
      )}
      {hasData ? (
        <>
          <nav className="tabs" aria-label={t("company.tabsLabel")}>
            <div className="tabs-track" ref={trackRef}>
              {COMPANY_TABS.map((k) => (
                <a
                  key={k}
                  href={hrefFor({ name: "company", ticker, tab: k })}
                  className={cx("tab", k === tab && "is-active")}
                  aria-current={k === tab ? "page" : undefined}
                >
                  {t(TAB_LABEL[k])}
                </a>
              ))}
            </div>
          </nav>
          <div className="tab-panel" key={`${ticker}-${tab}`}>
            {tab === "overview" && <OverviewTab ticker={ticker} overview={overview} />}
            {tab === "financials" && <FinancialsTab ticker={ticker} cik={overview.company.cik} />}
            {tab === "ratios" && <RatiosTab ticker={ticker} />}
            {tab === "tone" && <ToneTab ticker={ticker} />}
            {tab === "risk" && <RiskTab ticker={ticker} />}
            {tab === "ask" && <AskTab ticker={ticker} analyzed={overview.analyzed} />}
            {tab === "filings" && <FilingsTab ticker={ticker} />}
          </div>
        </>
      ) : (
        <div className="not-analyzed">
          <EmptyState title={t("state.notAnalyzedTitle", { ticker })}>{t("state.notAnalyzedBody")}</EmptyState>
          <IngestPanel ticker={ticker} name={overview.company.name} onDone={reload} />
        </div>
      )}
    </div>
  );
}

/** İşlenmemiş şirket (404): kısa açıklama + SEC'ten veri çekme ve analiz (IngestPanel). */
function NotIngested({ ticker, onDone }: { ticker: string; onDone: () => void }) {
  const { t } = useI18n();
  // Adı göstermek için SEC ticker listesinde tam eşleşme aranır; bulunamazsa yalnızca ticker.
  const lookup = useAsync((signal) => api.search(ticker, 5, signal), [ticker]);
  const match =
    lookup.state.status === "success" ? lookup.state.data.find((r) => r.ticker.toUpperCase() === ticker) : undefined;

  return (
    <section className="not-ingested" aria-labelledby="not-ingested-title">
      <div className="not-ingested-head">
        <LuCloudDownload className="not-ingested-icon" aria-hidden="true" />
        <div>
          <p className="mh-kicker">
            <span className="ticker">{ticker}</span>
            {match && <span>{match.name}</span>}
          </p>
          <h1 id="not-ingested-title" className="not-ingested-title">
            {t("company.notIngestedTitle")}
          </h1>
          <p className="not-ingested-body">{t("company.notIngestedBody", { ticker })}</p>
        </div>
      </div>
      <IngestPanel ticker={ticker} name={match?.name ?? null} startLabel={t("company.fetchAndAnalyze")} onDone={onDone} />
      <p className="back-link">
        <a href={hrefFor({ name: "home" })}>{t("state.backHome")}</a>
      </p>
    </section>
  );
}

/** Skorun dayanağı: "TTM · 30 Eyl 2026 · 10-Q"; ipucunda başvuru, veri tarihi, yöntem. */
export function ScoreBasis({ score, method }: { score: ScoreSummary; method?: string | null }) {
  const { t } = useI18n();
  if (!score.basis && !score.as_of_filing) {
    // Eski sunucu: yalnızca dönem
    return <span className="score-basis">{t("overview.asOf", { date: fmtDate(score.as_of_period) })}</span>;
  }
  const period = score.data_through ?? score.as_of_period;
  const line = fmtBasisLine(score.basis, period, score.as_of_filing?.form);
  return (
    <Tip
      mark
      align="end"
      className="score-basis"
      content={
        <span className="src-tip">
          <span className="src-tip-value">{t("score.basisLabel")}</span>
          <span className="src-tip-grid">
            <span>{t("common.period")}</span>
            <span>{score.basis === "ttm" ? t("basis.ttmLong") : t("basis.annualLong")}</span>
            <span>{t("score.dataThrough")}</span>
            <span>{fmtDate(period)}</span>
            {score.as_of_filing && (
              <>
                <span>{t("score.filing")}</span>
                <span>{score.as_of_filing.form}</span>
                <span>{t("filings.accession")}</span>
                <span className="tnum">{score.as_of_filing.accession_no}</span>
                <span>{t("score.filed")}</span>
                <span>{fmtDate(score.as_of_filing.filing_date)}</span>
              </>
            )}
            {method && (
              <>
                <span>{t("score.method")}</span>
                <span>{method}</span>
              </>
            )}
          </span>
          <span className="src-tip-note">{score.basis === "ttm" ? t("score.ttmExplain") : t("score.annualExplain")}</span>
        </span>
      }
    >
      {line}
    </Tip>
  );
}

function Masthead({
  overview,
  hasData,
  fresh,
  onRetryFresh,
  onRunText,
}: {
  overview: Overview;
  hasData: boolean;
  fresh: FreshnessState;
  onRetryFresh: () => void;
  onRunText?: () => void;
}) {
  const { t, lang } = useI18n();
  const c = overview.company;
  const score = overview.score;
  const total = score?.total ?? null;
  const tone = scoreTone(total);

  return (
    <section className="masthead" aria-labelledby="company-name">
      <div className="mh-id">
        <p className="mh-kicker">
          <span className="ticker">{c.ticker}</span>
          {c.exchanges.length > 0 && <span>{c.exchanges.join(", ")}</span>}
          {hasData && !overview.analyzed && <span className="flag tone-muted">{t("home.textNotAnalyzed")}</span>}
        </p>
        <h1 id="company-name" className="mh-name">
          {c.name}
        </h1>
        <dl className="mh-facts" aria-label={t("company.facts")}>
          <div>
            <dt>{t("company.cik")}</dt>
            <dd className="tnum">{c.cik}</dd>
          </div>
          <div>
            <dt>{t("company.sector")}</dt>
            <dd>
              {c.sic_description ?? "—"}
              {c.sic && <span className="muted"> · SIC {c.sic}</span>}
            </dd>
          </div>
          <div>
            <dt>{t("company.fiscalYearEnd")}</dt>
            <dd>{fmtFiscalYearEnd(c.fiscal_year_end)}</dd>
          </div>
          {overview.analysis_updated_at && (
            <div>
              <dt>{t("company.lastAnalysis")}</dt>
              <dd>{fmtDateTime(overview.analysis_updated_at)}</dd>
            </div>
          )}
          {hasData && (
            <div>
              <dt>{t("fresh.label")}</dt>
              <dd>
                <FreshnessStatus state={fresh} onRetry={onRetryFresh} />
              </dd>
            </div>
          )}
        </dl>
      </div>

      {hasData && (
        <div className="mh-side">
          {score && (
            <div className="mh-score">
              <p className="mh-score-label">{t("overview.scoreTitle")}</p>
              <p className="mh-score-line">
                <span className="mh-score-num">{fmtScore(total)}</span>
                <span className="mh-score-of">/ 100</span>
                <span className={cx("mh-band", `tone-text-${tone}`)}>{bandLabel(lang, score.band)}</span>
              </p>
              <ScoreScale total={total} band={score.band} />
              <p className="mh-basis">
                <ScoreBasis score={score} />
              </p>
            </div>
          )}
          <div className="mh-actions">
            {onRunText && (
              <button type="button" className="btn btn-primary" onClick={onRunText} title={t("company.runTextHint")}>
                <LuScanText aria-hidden="true" />
                {t("company.runText")}
              </button>
            )}
            <a
              className="btn btn-secondary"
              href={api.exportCsvUrl(c.ticker)}
              download={`${c.ticker}-finsight.csv`}
              aria-label={t("company.downloadCsvLabel", { ticker: c.ticker })}
            >
              <LuDownload aria-hidden="true" />
              {t("company.downloadCsv")}
            </a>
          </div>
        </div>
      )}
    </section>
  );
}
