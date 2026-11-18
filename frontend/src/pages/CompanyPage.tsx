// Şirket sayfası: başlık şeridi + sekmeler (Özet, Finansallar, Risk ve Ton, Soru-Cevap, Raporlar).
// Sözleşme v1.1: işlenmemiş şirket 404 → "henüz işlenmedi" + veri çekme (FR-15);
// analyzed=false → yalnızca finansal veri var, başlıkta "Metin analizini çalıştır".
import { useState } from "react";
import { LuCloudDownload, LuDownload, LuScanText } from "react-icons/lu";
import { api, ApiError } from "../api";
import { IngestPanel } from "../components/IngestPanel";
import { EmptyState, ErrorCard, Skeleton, cx } from "../components/ui";
import { fmtDateTime, fmtFiscalYearEnd, fmtScore, scoreTone } from "../format";
import { bandLabel, useI18n } from "../i18n";
import type { MsgKey } from "../i18n";
import { useAsync } from "../lib/useAsync";
import { COMPANY_TABS, hrefFor } from "../router";
import type { CompanyTab } from "../router";
import type { Overview } from "../types";
import { AskTab } from "./company/AskTab";
import { FilingsTab } from "./company/FilingsTab";
import { FinancialsTab } from "./company/FinancialsTab";
import { OverviewTab } from "./company/OverviewTab";
import { RiskTab } from "./company/RiskTab";

const TAB_LABEL: Record<CompanyTab, MsgKey> = {
  overview: "tab.overview",
  financials: "tab.financials",
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
  const [textRun, setTextRun] = useState(false);

  if (state.status === "loading") {
    return (
      <div className="page company">
        <div className="company-strip is-skeleton" aria-busy="true">
          <Skeleton width={260} height={28} />
          <Skeleton width={420} />
        </div>
        <div className="grid-12">
          <div className="span-4 panel">
            <Skeleton height={180} radius={90} />
          </div>
          <div className="span-8 panel">
            <Skeleton height={180} />
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

  const overview = state.data;
  const hasData = hasFinancialData(overview);
  const canRunText = hasData && !overview.analyzed;

  return (
    <div className="page company">
      <CompanyStrip
        overview={overview}
        hasData={hasData}
        onRunText={canRunText && !textRun ? () => setTextRun(true) : undefined}
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
          </nav>
          <div className="tab-panel" key={`${ticker}-${tab}`}>
            {tab === "overview" && <OverviewTab ticker={ticker} overview={overview} />}
            {tab === "financials" && <FinancialsTab ticker={ticker} cik={overview.company.cik} />}
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
        <span className="not-ingested-icon" aria-hidden="true">
          <LuCloudDownload />
        </span>
        <div>
          <p className="strip-ticker">
            <span className="ticker-tag is-large">{ticker}</span>
          </p>
          <h1 id="not-ingested-title" className="not-ingested-title">
            {t("company.notIngestedTitle")}
          </h1>
          <p className="not-ingested-body">{t("company.notIngestedBody", { ticker })}</p>
        </div>
      </div>
      <IngestPanel
        ticker={ticker}
        name={match?.name ?? null}
        startLabel={t("company.fetchAndAnalyze")}
        onDone={onDone}
      />
      <p className="back-link">
        <a href={hrefFor({ name: "home" })}>{t("state.backHome")}</a>
      </p>
    </section>
  );
}

function CompanyStrip({
  overview,
  hasData,
  onRunText,
}: {
  overview: Overview;
  hasData: boolean;
  onRunText?: () => void;
}) {
  const { t, lang } = useI18n();
  const c = overview.company;
  const total = overview.score?.total ?? null;
  const tone = scoreTone(total);
  const facts: Array<[string, string]> = [
    [t("company.cik"), c.cik],
    [t("company.sector"), c.sic_description ? `${c.sic_description}${c.sic ? ` (SIC ${c.sic})` : ""}` : "—"],
    [t("company.fiscalYearEnd"), fmtFiscalYearEnd(c.fiscal_year_end)],
  ];
  if (c.exchanges.length) facts.push([t("company.exchange"), c.exchanges.join(", ")]);

  return (
    <section className="company-strip" aria-labelledby="company-name">
      <div className="strip-main">
        <p className="strip-ticker">
          <span className="ticker-tag is-large">{c.ticker}</span>
          {hasData && !overview.analyzed && <span className="band-pill tone-muted">{t("home.textNotAnalyzed")}</span>}
          {overview.analysis_updated_at && (
            <span className="strip-updated">{t("company.updated", { date: fmtDateTime(overview.analysis_updated_at) })}</span>
          )}
        </p>
        <h1 id="company-name" className="strip-name">
          {c.name}
        </h1>
        <dl className="strip-facts">
          {facts.map(([k, v]) => (
            <div key={k} className="strip-fact">
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      {hasData && (
        <div className={cx("strip-side", onRunText && "has-action")}>
          {onRunText && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={onRunText}
              title={t("company.runTextHint")}
            >
              <LuScanText aria-hidden="true" />
              {t("company.runText")}
            </button>
          )}
          {overview.score && (
            <div className={`score-badge tone-${tone}`}>
              <span className="score-badge-label">{t("company.score")}</span>
              <span className="score-badge-value">{fmtScore(total)}</span>
              <span className="score-badge-band">{bandLabel(lang, overview.score.band)}</span>
            </div>
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
      )}
    </section>
  );
}
