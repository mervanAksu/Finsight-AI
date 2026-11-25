// Ana sayfa: kısa tanım, büyük arama kutusu (FR-01), analiz başlatma (FR-15) ve analiz edilen
// şirketlerin izleme tablosu (skor cetveli, son başvuru, metin analizi durumu).
import { useState } from "react";
import { LuGitCompareArrows, LuSearch } from "react-icons/lu";
import { api } from "../api";
import { ScoreMini } from "../charts/ScoreGauge";
import { CompanySearch } from "../components/CompanySearch";
import { IngestPanel } from "../components/IngestPanel";
import { ErrorCard, Skeleton, cx } from "../components/ui";
import { fmtDate } from "../format";
import { useI18n } from "../i18n";
import { useAsync } from "../lib/useAsync";
import { hrefFor, navigate } from "../router";
import type { CompanyListItem, SearchResult } from "../types";

export function HomePage() {
  const { t } = useI18n();
  const { state, reload } = useAsync((signal) => api.companies(signal), []);
  const [pending, setPending] = useState<SearchResult | null>(null);

  const onSelect = (r: SearchResult) => {
    if (r.ingested) {
      navigate({ name: "company", ticker: r.ticker, tab: "overview" });
    } else {
      setPending(r);
    }
  };

  const steps = [t("home.step1"), t("home.step2"), t("home.step3"), t("home.step4")];
  const isEmpty = state.status === "success" && state.data.length === 0;

  return (
    <div className="page home">
      <section className="intro" aria-labelledby="home-title">
        <div className="intro-main">
          <h1 id="home-title" className="intro-title">
            {t("home.title")}
          </h1>
          <p className="intro-lead">{t("home.lead")}</p>
          <div className="intro-search">
            <CompanySearch size="large" label={t("home.searchLabel")} onSelect={onSelect} clearOnSelect={false} autoFocus={false} />
            <p className="intro-hint">{t("home.searchHint")}</p>
          </div>
          {pending && (
            <IngestPanel
              key={pending.ticker}
              ticker={pending.ticker}
              name={pending.name}
              onDone={() => navigate({ name: "company", ticker: pending.ticker, tab: "overview" })}
            />
          )}
        </div>
        <aside className="intro-steps" aria-labelledby="steps-title">
          <h2 id="steps-title" className="steps-title">
            {t("home.stepsTitle")}
          </h2>
          <ol className="steps">
            {steps.map((s, i) => (
              <li key={s} className="step">
                <span className="step-n" aria-hidden="true">
                  {i + 1}
                </span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
        </aside>
      </section>

      <section className="coverage" aria-labelledby="companies-title">
        <div className="section-head">
          <div>
            <h2 id="companies-title" className="section-title">
              {t("home.analyzed")}
            </h2>
            {state.status === "success" && !isEmpty && <p className="section-sub">{t("home.analyzedCount", { n: state.data.length })}</p>}
          </div>
          {state.status === "success" && state.data.length >= 2 && (
            <a className="btn btn-secondary" href={hrefFor({ name: "compare", tickers: state.data.slice(0, 3).map((c) => c.ticker) })}>
              <LuGitCompareArrows aria-hidden="true" />
              {t("home.compareLink")}
            </a>
          )}
        </div>

        {state.status === "loading" && (
          <div className="panel panel-sheet" aria-busy="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="skeleton-row">
                <Skeleton width={60} />
                <Skeleton width="40%" />
                <Skeleton width={90} />
              </div>
            ))}
          </div>
        )}
        {state.status === "error" && <ErrorCard error={state.error} onRetry={reload} />}
        {isEmpty && <StartNote />}
        {state.status === "success" && state.data.length > 0 && (
          <div className="panel panel-sheet table-sheet">
            <table className="data-table coverage-table">
              <thead>
                <tr>
                  <th scope="col">{t("home.colCompany")}</th>
                  <th scope="col" className="hide-sm">
                    {t("home.colSector")}
                  </th>
                  <th scope="col">{t("home.colScore")}</th>
                  <th scope="col" className="hide-sm">
                    {t("home.colLastFiling")}
                  </th>
                  <th scope="col" className="hide-md">
                    {t("home.colText")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.data.map((c) => (
                  <CompanyRow key={c.ticker} company={c} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

/** Hiç işlenmiş şirket yokken: aramaya yönlendiren kısa not. */
function StartNote() {
  const { t } = useI18n();
  const focusSearch = () => {
    const input = document.querySelector<HTMLInputElement>(".intro-search input");
    if (input) {
      input.scrollIntoView({ block: "center", behavior: "smooth" });
      input.focus({ preventScroll: true });
    }
  };
  return (
    <div className="start-note">
      <div>
        <p className="start-title">{t("home.emptyTitle")}</p>
        <p className="muted">{t("home.startBody")}</p>
      </div>
      <button type="button" className="btn btn-primary" onClick={focusSearch}>
        <LuSearch aria-hidden="true" />
        {t("home.startCta")}
      </button>
    </div>
  );
}

function CompanyRow({ company }: { company: CompanyListItem }) {
  const { t } = useI18n();
  const href = hrefFor({ name: "company", ticker: company.ticker, tab: "overview" });
  return (
    <tr className="row-link">
      <th scope="row">
        <a className="company-link" href={href} aria-label={t("home.openCompany", { name: company.name })}>
          <span className="ticker">{company.ticker}</span>
          <span className="company-link-name">{company.name}</span>
        </a>
      </th>
      <td className="hide-sm muted">{company.sic_description ?? "—"}</td>
      <td>
        {company.score ? <ScoreMini total={company.score.total} band={company.score.band} /> : <span className="muted">—</span>}
      </td>
      <td className="hide-sm nowrap tnum">{fmtDate(company.last_filing_date)}</td>
      <td className="hide-md">
        <span className={cx("flag", company.analyzed ? "tone-positive" : "tone-muted")}>
          {company.analyzed ? t("home.textYes") : t("home.textNo")}
        </span>
      </td>
    </tr>
  );
}
