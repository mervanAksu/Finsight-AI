// Karşılaştırma ekranı (FR-13): 2–4 şirketin skorları ve son mali yıl oranları.
import { useState } from "react";
import { LuPlus, LuX } from "react-icons/lu";
import { api } from "../api";
import { ScoreRing } from "../charts/ScoreGauge";
import { HBars } from "../charts/small";
import { CompanySearch } from "../components/CompanySearch";
import { Disclaimer, EmptyState, ErrorCard, Panel, SkeletonBlock, cx } from "../components/ui";
import { fmtDate, fmtRatio, scoreTone } from "../format";
import { bandLabel, ratioLabel, useI18n } from "../i18n";
import { useAsync } from "../lib/useAsync";
import { hrefFor, navigate } from "../router";
import type { CompareCompany, CompareResponse, RatioMeta } from "../types";

const MAX = 4;

/** Oranın yönüne göre en iyi değere sahip şirketler (eşitlikte birden fazla). */
function bestTickers(meta: RatioMeta, companies: CompareCompany[]): Set<string> {
  const vals = companies
    .map((c) => ({ t: c.ticker, v: c.ratios[meta.key] ?? null }))
    .filter((x): x is { t: string; v: number } => x.v !== null);
  if (vals.length < 2) return new Set();
  const target = meta.higher_is_better ? Math.max(...vals.map((x) => x.v)) : Math.min(...vals.map((x) => x.v));
  return new Set(vals.filter((x) => x.v === target).map((x) => x.t));
}

export function ComparePage({ tickers }: { tickers: string[] }) {
  const { t } = useI18n();
  const companies = useAsync((signal) => api.companies(signal), []);
  const key = tickers.join(",");
  const compare = useAsync<CompareResponse | null>(
    (signal) => (tickers.length >= 2 ? api.compare(tickers, signal) : Promise.resolve(null)),
    [key],
  );

  const setTickers = (next: string[]) => navigate({ name: "compare", tickers: next.slice(0, MAX) }, { replace: true });
  const add = (tk: string) => {
    if (!tickers.includes(tk) && tickers.length < MAX) setTickers([...tickers, tk]);
  };
  const remove = (tk: string) => setTickers(tickers.filter((x) => x !== tk));

  const available =
    companies.state.status === "success" ? companies.state.data.filter((c) => !tickers.includes(c.ticker)) : [];

  return (
    <div className="page compare">
      <header className="page-head">
        <h1 className="page-title">{t("compare.title")}</h1>
        <p className="page-lead">{t("compare.lead")}</p>
      </header>

      <section className="picker" aria-label={t("compare.selected")}>
        <ul className="picked">
          {tickers.map((tk) => (
            <li key={tk} className="picked-chip">
              <a href={hrefFor({ name: "company", ticker: tk, tab: "overview" })}>{tk}</a>
              <button type="button" onClick={() => remove(tk)} aria-label={t("compare.remove", { t: tk })}>
                <LuX aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
        {tickers.length < MAX ? (
          <div className="picker-add">
            <CompanySearch label={t("compare.add")} placeholder={t("compare.add")} onSelect={(r) => add(r.ticker)} exclude={tickers} />
            {available.length > 0 && (
              <div className="quick-add" role="group" aria-label={t("compare.quickAdd")}>
                <span className="muted">{t("compare.quickAdd")}:</span>
                {available.map((c) => (
                  <button key={c.ticker} type="button" className="chip-btn is-small" onClick={() => add(c.ticker)}>
                    <LuPlus aria-hidden="true" />
                    {c.ticker}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <p className="muted-note">{t("compare.max")}</p>
        )}
      </section>

      {tickers.length < 2 && (
        <EmptyState title={t("compare.needTwo")}>{t("compare.needTwoBody")}</EmptyState>
      )}
      {tickers.length >= 2 && compare.state.status === "loading" && (
        <Panel>
          <SkeletonBlock lines={6} chart />
        </Panel>
      )}
      {compare.state.status === "error" && <ErrorCard error={compare.state.error} onRetry={compare.reload} />}
      {compare.state.status === "success" && compare.state.data && <CompareView data={compare.state.data} />}
    </div>
  );
}

function CompareView({ data }: { data: CompareResponse }) {
  const { t, lang } = useI18n();
  const [ratioKey, setRatioKey] = useState<string>(
    data.ratio_meta.find((m) => m.key === "net_margin")?.key ?? data.ratio_meta[0]?.key ?? "",
  );
  const meta = data.ratio_meta.find((m) => m.key === ratioKey) ?? data.ratio_meta[0];
  const best = meta ? bestTickers(meta, data.companies) : new Set<string>();
  const cols = data.companies.length;

  return (
    <div className="grid-12">
      <section className={`span-12 score-cards cols-${cols}`} aria-label={t("compare.scores")}>
        {data.companies.map((c) => (
          <a key={c.ticker} className="score-card" href={hrefFor({ name: "company", ticker: c.ticker, tab: "overview" })}>
            <ScoreRing total={c.score?.total ?? null} size={64} stroke={6} />
            <div>
              <p className="company-card-top">
                <span className="ticker-tag">{c.ticker}</span>
                {c.score && (
                  <span className={`band-pill tone-${scoreTone(c.score.total)}`}>{bandLabel(lang, c.score.band)}</span>
                )}
              </p>
              <p className="company-card-name">{c.name}</p>
              <p className="company-card-meta">
                {t("common.period")}: {fmtDate(c.period_end)}
              </p>
            </div>
          </a>
        ))}
      </section>

      <Panel className="span-7" title={t("compare.ratios")}>
        <div className="table-wrap">
          <table className="data-table compare-table">
            <thead>
              <tr>
                <th scope="col">{t("fin.ratio")}</th>
                {data.companies.map((c) => (
                  <th key={c.ticker} scope="col" className="num">
                    {c.ticker}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.ratio_meta.map((m) => {
                const bestSet = bestTickers(m, data.companies);
                return (
                  <tr key={m.key} className={cx(m.key === ratioKey && "is-selected")}>
                    <th scope="row">
                      <button type="button" className="row-btn" onClick={() => setRatioKey(m.key)} aria-pressed={m.key === ratioKey}>
                        {ratioLabel(lang, m.key, m.label)}
                      </button>
                      <span className="dir-hint">{m.higher_is_better ? "↑" : "↓"}</span>
                    </th>
                    {data.companies.map((c) => {
                      const v = c.ratios[m.key] ?? null;
                      const isBest = bestSet.has(c.ticker);
                      return (
                        <td key={c.ticker} className={cx("num", isBest && "is-best")}>
                          {fmtRatio(v, m.unit)}
                          {isBest && <span className="sr-only"> ({t("compare.best")})</span>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="panel-foot">
          ↑ {t("fin.higherBetter")} · ↓ {t("fin.lowerBetter")} · <span className="best-key">{t("compare.best")}</span>
        </p>
      </Panel>

      {meta && (
        <Panel
          className="span-5"
          title={
            best.size > 0
              ? t("compare.chartTitle", { label: ratioLabel(lang, meta.key, meta.label), ticker: Array.from(best).join(", ") })
              : ratioLabel(lang, meta.key, meta.label)
          }
          actions={
            <label className="select-wrap">
              <span className="sr-only">{t("compare.chartPick")}</span>
              <select value={ratioKey} onChange={(e) => setRatioKey(e.target.value)}>
                {data.ratio_meta.map((m) => (
                  <option key={m.key} value={m.key}>
                    {ratioLabel(lang, m.key, m.label)}
                  </option>
                ))}
              </select>
            </label>
          }
        >
          <HBars
            ariaLabel={ratioLabel(lang, meta.key, meta.label)}
            rows={data.companies.map((c) => ({
              key: c.ticker,
              label: c.ticker,
              value: c.ratios[meta.key] ?? null,
              display: fmtRatio(c.ratios[meta.key] ?? null, meta.unit),
              best: best.has(c.ticker),
            }))}
          />
          <p className="panel-foot">{meta.higher_is_better ? t("fin.higherBetter") : t("fin.lowerBetter")}</p>
        </Panel>
      )}

      <div className="span-12">
        <Disclaimer text={data.disclaimer} />
      </div>
    </div>
  );
}
