// Şirket sayfası: başlık şeridi + sekmeler (Özet, Finansallar, Risk ve Ton, Soru-Cevap, Raporlar).
import { LuDownload } from "react-icons/lu";
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

/** Şirket var ama analiz yok: skor ve kalemler boş (api-contract: alanlar null / boş liste). */
function isAnalyzed(o: Overview): boolean {
  return o.score !== null || o.key_metrics.length > 0;
}

export function CompanyPage({ ticker, tab }: { ticker: string; tab: CompanyTab }) {
  const { t } = useI18n();
  const { state, reload } = useAsync((signal) => api.overview(ticker, signal), [ticker]);

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
    const notFound = state.error instanceof ApiError && state.error.status === 404;
    return (
      <div className="page company">
        <ErrorCard
          error={state.error}
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
  const analyzed = isAnalyzed(overview);

  return (
    <div className="page company">
      <CompanyStrip overview={overview} analyzed={analyzed} />
      {analyzed ? (
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
            {tab === "ask" && <AskTab ticker={ticker} />}
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

function CompanyStrip({ overview, analyzed }: { overview: Overview; analyzed: boolean }) {
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
      {analyzed && (
        <div className="strip-side">
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
