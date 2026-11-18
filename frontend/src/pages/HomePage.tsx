// Ana sayfa: açıklama, büyük arama kutusu, analiz başlatma ve işlenmiş şirketler ızgarası.
import { useState } from "react";
import { LuGitCompareArrows, LuInbox } from "react-icons/lu";
import { api } from "../api";
import { ScoreRing } from "../charts/ScoreGauge";
import { CompanySearch } from "../components/CompanySearch";
import { IngestPanel } from "../components/IngestPanel";
import { EmptyState, ErrorCard, Skeleton } from "../components/ui";
import { fmtDate, scoreTone } from "../format";
import { bandLabel, useI18n } from "../i18n";
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

  return (
    <div className="page home">
      <section className="hero" aria-labelledby="home-title">
        <div className="hero-main">
          <h1 id="home-title" className="hero-title">
            {t("home.title")}
          </h1>
          <p className="hero-lead">{t("home.lead")}</p>
          <div className="hero-search">
            <CompanySearch
              size="large"
              label={t("home.searchLabel")}
              onSelect={onSelect}
              clearOnSelect={false}
              autoFocus={false}
            />
            <p className="hero-hint">{t("home.searchHint")}</p>
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
        <aside className="hero-steps" aria-labelledby="steps-title">
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

      <section className="companies" aria-labelledby="companies-title">
        <div className="section-head">
          <div>
            <h2 id="companies-title" className="section-title">
              {t("home.analyzed")}
            </h2>
            {state.status === "success" && (
              <p className="section-sub">{t("home.analyzedCount", { n: state.data.length })}</p>
            )}
          </div>
          {state.status === "success" && state.data.length >= 2 && (
            <a
              className="btn btn-ghost"
              href={hrefFor({ name: "compare", tickers: state.data.slice(0, 3).map((c) => c.ticker) })}
            >
              <LuGitCompareArrows aria-hidden="true" />
              {t("home.compareLink")}
            </a>
          )}
        </div>

        {state.status === "loading" && (
          <div className="company-grid" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="company-card is-skeleton">
                <Skeleton width={56} height={56} radius={28} />
                <div className="company-card-body">
                  <Skeleton width="40%" height={16} />
                  <Skeleton width="80%" />
                  <Skeleton width="60%" />
                </div>
              </div>
            ))}
          </div>
        )}
        {state.status === "error" && <ErrorCard error={state.error} onRetry={reload} />}
        {state.status === "success" && state.data.length === 0 && (
          <EmptyState title={t("home.emptyTitle")} icon={<LuInbox aria-hidden="true" />}>
            {t("home.emptyBody")}
          </EmptyState>
        )}
        {state.status === "success" && state.data.length > 0 && (
          <ul className="company-grid">
            {state.data.map((c) => (
              <li key={c.ticker}>
                <CompanyCard company={c} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function CompanyCard({ company }: { company: CompanyListItem }) {
  const { t, lang } = useI18n();
  const total = company.score?.total ?? null;
  return (
    <a
      className="company-card"
      href={hrefFor({ name: "company", ticker: company.ticker, tab: "overview" })}
      aria-label={t("home.openCompany", { name: company.name })}
    >
      <ScoreRing total={total} />
      <div className="company-card-body">
        <p className="company-card-top">
          <span className="ticker-tag">{company.ticker}</span>
          {company.score && (
            <span className={`band-pill tone-${scoreTone(total)}`}>{bandLabel(lang, company.score.band)}</span>
          )}
        </p>
        <p className="company-card-name">{company.name}</p>
        <p className="company-card-meta">{company.sic_description ?? "—"}</p>
        <p className="company-card-meta">
          {t("home.lastFiling")}: {fmtDate(company.last_filing_date)}
        </p>
      </div>
    </a>
  );
}
