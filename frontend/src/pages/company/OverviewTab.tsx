// Özet sekmesi: skor göstergesi + bileşenler, 6 temel kalem, ton ve risk özeti, son başvurular.
import { LuArrowRight, LuExternalLink } from "react-icons/lu";
import { api } from "../../api";
import { BANDS, ScoreGauge } from "../../charts/ScoreGauge";
import { ToneMeter } from "../../charts/small";
import {
  DeltaBadge,
  Disclaimer,
  ErrorCard,
  FormBadge,
  Panel,
  SkeletonBlock,
  SourceChip,
  Tip,
  cx,
} from "../../components/ui";
import { fmtDate, fmtMoneyFull, fmtMoneyShort, fmtNumber, fmtScore, scoreTone } from "../../format";
import { bandLabel, componentLabel, metricLabel, useI18n } from "../../i18n";
import type { Lang } from "../../i18n";
import { useAsync } from "../../lib/useAsync";
import { hrefFor } from "../../router";
import type { KeyMetric, Overview, ScoreComponent } from "../../types";

/** Yükselmesi olumsuz sayılan kalemler (YoY rozet rengi için). */
const LOWER_IS_BETTER = new Set(["total_liabilities"]);

export function OverviewTab({ ticker, overview }: { ticker: string; overview: Overview }) {
  const { t, lang } = useI18n();
  const score = useAsync((signal) => api.score(ticker, signal), [ticker]);
  const cik = overview.company.cik;

  return (
    <div className="grid-12">
      <Panel className="span-4 score-panel" title={t("overview.scoreTitle")}>
        <ScoreGauge total={overview.score?.total ?? null} band={overview.score?.band ?? null} size={330} />
        <dl className="score-meta">
          <div>
            <dt className="sr-only">{t("common.period")}</dt>
            <dd>{t("overview.asOf", { date: fmtDate(overview.score?.as_of_period) })}</dd>
          </div>
          {score.state.status === "success" && (
            <div>
              <dt className="sr-only">v</dt>
              <dd>{t("overview.weights", { v: score.state.data.weights_version })}</dd>
            </div>
          )}
        </dl>
        <ul className="band-legend" aria-label={t("overview.bandsLabel")}>
          {[...BANDS].reverse().map((b) => (
            <li key={b.name} className={cx(`tone-${b.tone}`, overview.score?.band === b.name && "is-current")}>
              <span className="band-swatch" aria-hidden="true" />
              <span className="band-name">{bandLabel(lang, b.name)}</span>
              <span className="band-range">
                {b.to === 100 ? `≥ ${b.from}` : b.from === 0 ? `< ${b.to}` : `${b.from}–${b.to - 1}`}
              </span>
            </li>
          ))}
        </ul>
        <p className="score-note">{t("overview.scoreNote")}</p>
      </Panel>

      <Panel className="span-8" title={t("overview.keyMetrics")}>
        <div className="metric-grid">
          {overview.key_metrics.map((m) => (
            <MetricCell key={m.metric} metric={m} cik={cik} />
          ))}
        </div>
      </Panel>

      <Panel className="span-7" title={t("overview.components")} subtitle={t("overview.componentsLead")}>
        {score.state.status === "loading" && <SkeletonBlock lines={6} />}
        {score.state.status === "error" && <ErrorCard error={score.state.error} onRetry={score.reload} />}
        {score.state.status === "success" && (
          <>
            {score.state.data.total === null && (
              <p className="muted-note">
                {t(overview.analyzed === false ? "overview.noScoreNotAnalyzed" : "overview.noScore")}
              </p>
            )}
            <ol className="components">
              {score.state.data.components.map((c) => (
                <ComponentRow key={c.key} component={c} />
              ))}
            </ol>
            {score.state.data.missing_components.length > 0 && (
              <p className="muted-note">
                {t("overview.missingComponents", {
                  list: missingLabels(score.state.data.missing_components, score.state.data.components, lang),
                })}
              </p>
            )}
          </>
        )}
      </Panel>

      <div className="span-5 stack">
        <Panel title={t("overview.toneTitle")}>
          {overview.sentiment_summary ? (
            <>
              <div className="tone-pair">
                <ToneMeter label={t("overview.mdnaTone")} value={overview.sentiment_summary.mdna_net_tone} />
                <ToneMeter label={t("overview.riskTone")} value={overview.sentiment_summary.risk_net_tone} />
              </div>
              <p className="panel-foot">
                {overview.sentiment_summary.filing.form} · {fmtDate(overview.sentiment_summary.filing.filing_date)} ·{" "}
                {t("overview.toneModel", { m: overview.sentiment_summary.model })}
              </p>
            </>
          ) : (
            <p className="muted-note">{t("overview.noTone")}</p>
          )}
        </Panel>
        <Panel
          title={t("overview.riskTitle")}
          subtitle={overview.risk_summary ? t("overview.riskSince") : undefined}
          actions={
            overview.risk_summary ? (
              <a className="text-link" href={hrefFor({ name: "company", ticker, tab: "risk" })}>
                {t("overview.viewDetails")}
                <LuArrowRight aria-hidden="true" />
              </a>
            ) : undefined
          }
        >
          {overview.risk_summary ? (
            <dl className="risk-counts">
              <div className="rc rc-added">
                <dt>{t("overview.added")}</dt>
                <dd>{fmtNumber(overview.risk_summary.added)}</dd>
              </div>
              <div className="rc rc-modified">
                <dt>{t("overview.modified")}</dt>
                <dd>{fmtNumber(overview.risk_summary.modified)}</dd>
              </div>
              <div className="rc rc-removed">
                <dt>{t("overview.removed")}</dt>
                <dd>{fmtNumber(overview.risk_summary.removed)}</dd>
              </div>
              <div className="rc rc-unchanged">
                <dt>{t("overview.unchanged")}</dt>
                <dd>{fmtNumber(overview.risk_summary.unchanged)}</dd>
              </div>
            </dl>
          ) : (
            <p className="muted-note">{t("overview.noRisk")}</p>
          )}
        </Panel>
        <Panel
          title={t("overview.latestFilings")}
          actions={
            <a className="text-link" href={hrefFor({ name: "company", ticker, tab: "filings" })}>
              {t("overview.allFilings")}
              <LuArrowRight aria-hidden="true" />
            </a>
          }
        >
          <ul className="filing-list">
            {overview.latest_filings.map((f) => (
              <li key={f.accession_no} className="filing-row">
                <FormBadge form={f.form} />
                <span className="filing-main">
                  <span className="filing-date">{fmtDate(f.filing_date)}</span>
                  <span className="filing-period">
                    {t("filings.period")}: {fmtDate(f.report_date)} · {f.accession_no}
                  </span>
                </span>
                <a
                  className="icon-link"
                  href={f.url}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={t("common.openEdgar", { form: f.form })}
                >
                  <LuExternalLink aria-hidden="true" />
                </a>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="span-12">
        <Disclaimer text={overview.disclaimer} />
      </div>
    </div>
  );
}

function missingLabels(keys: string[], components: ScoreComponent[], lang: Lang): string {
  return keys
    .map((k) => {
      const c = components.find((x) => x.key === k);
      return c ? componentLabel(lang, c.key, c.label) : k;
    })
    .join(", ");
}

function MetricCell({ metric, cik }: { metric: KeyMetric; cik: string }) {
  const { t, lang } = useI18n();
  return (
    <div className="metric">
      <p className="metric-label">{metricLabel(lang, metric.metric, metric.label)}</p>
      <p className="metric-value">
        <Tip content={fmtMoneyFull(metric.value)}>{fmtMoneyShort(metric.value)}</Tip>
      </p>
      <p className="metric-delta">
        <DeltaBadge value={metric.yoy} higherIsBetter={!LOWER_IS_BETTER.has(metric.metric)} label={t("common.yoy")} />
        <span className="metric-period">{fmtDate(metric.period_end)}</span>
      </p>
      <SourceChip source={metric.source} cik={cik} />
    </div>
  );
}

function ComponentRow({ component: c }: { component: ScoreComponent }) {
  const { t, lang } = useI18n();
  const tone = scoreTone(c.score);
  return (
    <li className={cx("comp", !c.available && "is-unavailable")}>
      <div className="comp-head">
        <span className="comp-label">{componentLabel(lang, c.key, c.label)}</span>
        <span className="comp-weight">{t("overview.weight", { w: c.weight })}</span>
        <span className={cx("comp-score", c.available && `tone-text-${tone}`)}>
          {c.available ? fmtScore(c.score) : t("overview.notComputed")}
        </span>
      </div>
      <div className="comp-track" aria-hidden="true">
        {c.available && c.score !== null && <span className={`comp-fill tone-bg-${tone}`} style={{ width: `${c.score}%` }} />}
      </div>
      <p className="comp-expl">{c.explanation}</p>
    </li>
  );
}
