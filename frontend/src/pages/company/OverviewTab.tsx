// Özet sekmesi (FR-11, FR-14): skorun bileşenleri ve katkıları (açıklanabilir skor), temel
// kalemler (TTM / son bilanço, kaynaklı), ton ve risk özeti, son başvurular.
import { Fragment, useState } from "react";
import { LuArrowRight, LuChevronDown, LuExternalLink } from "react-icons/lu";
import { api } from "../../api";
import { ToneMeter } from "../../charts/small";
import {
  BasisTag,
  DeltaBadge,
  Disclaimer,
  ErrorCard,
  Figure,
  FormBadge,
  Panel,
  SkeletonBlock,
  SourceChip,
  cx,
} from "../../components/ui";
import {
  INSTANT_METRICS,
  fmtDate,
  fmtMoneyShort,
  fmtNumber,
  fmtPoints,
  fmtScore,
  scoreTone,
} from "../../format";
import { componentLabel, metricLabel, useI18n } from "../../i18n";
import type { Lang } from "../../i18n";
import { useAsync } from "../../lib/useAsync";
import { hrefFor } from "../../router";
import type { KeyMetric, Overview, ScoreComponent, ScoreResponse } from "../../types";

/** Yükselmesi olumsuz sayılan kalemler (YoY rozet rengi için). */
const LOWER_IS_BETTER = new Set(["total_liabilities"]);

export function OverviewTab({ ticker, overview }: { ticker: string; overview: Overview }) {
  const { t } = useI18n();
  const score = useAsync((signal) => api.score(ticker, signal), [ticker]);
  const cik = overview.company.cik;

  return (
    <div className="grid-12">
      <Panel className="span-7" title={t("overview.contribTitle")} subtitle={t("overview.contribLead")}>
        {score.state.status === "loading" && <SkeletonBlock lines={7} />}
        {score.state.status === "error" && <ErrorCard error={score.state.error} onRetry={score.reload} />}
        {score.state.status === "success" && <ScoreBreakdown data={score.state.data} analyzed={overview.analyzed} />}
      </Panel>

      <Panel className="span-5" title={t("overview.keyMetrics")} subtitle={t("overview.statementLead")}>
        <table className="data-table statement">
          <thead>
            <tr>
              <th scope="col">{t("overview.colItem")}</th>
              <th scope="col" className="num">
                {t("overview.colValue")}
              </th>
              <th scope="col" className="num">
                {t("overview.colChange")}
              </th>
            </tr>
          </thead>
          <tbody>
            {overview.key_metrics.map((m) => (
              <MetricRow key={m.metric} metric={m} />
            ))}
          </tbody>
        </table>
        <StatementSource metrics={overview.key_metrics} cik={cik} />
      </Panel>

      <Panel variant="margin" className="span-4" title={t("overview.toneTitle")}
        actions={
          overview.sentiment_summary ? (
            <a className="text-link" href={hrefFor({ name: "company", ticker, tab: "tone" })}>
              {t("overview.viewDetails")}
              <LuArrowRight aria-hidden="true" />
            </a>
          ) : undefined
        }
      >
        {overview.sentiment_summary ? (
          <>
            <div className="tone-pair">
              <ToneMeter label={t("overview.mdnaTone")} value={overview.sentiment_summary.mdna_net_tone} />
              <ToneMeter label={t("overview.riskTone")} value={overview.sentiment_summary.risk_net_tone} />
            </div>
            <p className="panel-foot">
              {overview.sentiment_summary.filing.form}, {fmtDate(overview.sentiment_summary.filing.filing_date)}.{" "}
              {t("overview.toneModel", { m: overview.sentiment_summary.model })}
            </p>
          </>
        ) : (
          <p className="muted-note">{t(overview.analyzed ? "overview.noToneAnalyzed" : "overview.noTone")}</p>
        )}
      </Panel>

      <Panel
        variant="margin"
        className="span-4"
        title={t("overview.riskTitle")}
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
          <>
            <RiskCounts counts={overview.risk_summary} />
            <p className="panel-foot">{t("overview.riskSince")}</p>
          </>
        ) : (
          <p className="muted-note">{t(overview.analyzed ? "overview.noRisk" : "overview.noRiskNotAnalyzed")}</p>
        )}
      </Panel>

      <Panel
        variant="margin"
        className="span-4 span-md-12"
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
            <li key={f.accession_no} className={cx("filing-row", overview.latest_filing?.accession_no === f.accession_no && "is-basis")}>
              <FormBadge form={f.form} />
              <span className="filing-main">
                <span className="filing-date">
                  {fmtDate(f.filing_date)}{" "}
                  {overview.latest_filing?.accession_no === f.accession_no && overview.score?.basis === "ttm" && <BasisTag basis="ttm" />}
                </span>
                <span className="filing-period">
                  {t("filings.period")} {fmtDate(f.report_date)}
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

      <div className="span-12">
        <p className="source-hint">{t("overview.sourceHint")}</p>
        <Disclaimer text={overview.disclaimer} />
      </div>
    </div>
  );
}

export function RiskCounts({ counts }: { counts: { added: number; modified: number; removed: number; unchanged: number } }) {
  const { t } = useI18n();
  const items: Array<[string, string, number]> = [
    ["added", t("overview.added"), counts.added],
    ["modified", t("overview.modified"), counts.modified],
    ["removed", t("overview.removed"), counts.removed],
    ["unchanged", t("overview.unchanged"), counts.unchanged],
  ];
  return (
    <dl className="risk-counts">
      {items.map(([k, label, n]) => (
        <div key={k} className={`rc rc-${k}`}>
          <dt>{label}</dt>
          <dd>{fmtNumber(n)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Tablo altında ortak kaynak: kalemlerin çoğu aynı başvurudan gelir. */
function StatementSource({ metrics, cik }: { metrics: KeyMetric[]; cik: string }) {
  const { t } = useI18n();
  const first = metrics.map((m) => currentPoint(m).source).find((s) => s) ?? null;
  if (!first) return null;
  return (
    <p className="panel-foot statement-source">
      <span>{t("common.source")}</span>
      <SourceChip source={first} cik={cik} align="start" />
    </p>
  );
}

/** v1.4: TTM noktası varsa o gösterilir; yoksa yıllık değer (eski sunucu veya TTM'siz kalem). */
export function currentPoint(m: KeyMetric) {
  if (m.ttm && m.ttm.value !== null) {
    return { value: m.ttm.value, period_end: m.ttm.period_end, yoy: m.ttm.yoy, source: m.ttm.source, basis: "ttm" as const };
  }
  return {
    value: m.value,
    period_end: m.period_end,
    yoy: m.yoy,
    source: m.source,
    // TTM şirketinde TTM'si olmayan kalem yıllık değere düşer (api-contract v1.4 ttm_reason).
    basis: m.ttm === undefined && m.ttm_reason === undefined ? m.basis : m.ttm_reason ? ("annual" as const) : m.basis,
  };
}

function MetricRow({ metric: m }: { metric: KeyMetric }) {
  const { t, lang } = useI18n();
  const instant = INSTANT_METRICS.has(m.metric);
  const cur = currentPoint(m);
  return (
    <tr>
      <th scope="row">
        <span className="stmt-label">{metricLabel(lang, m.metric, m.label)}</span>
        <span className="stmt-sub">
          <BasisTag basis={cur.basis} instant={instant && cur.basis === "ttm"} />
          {fmtDate(cur.period_end)}
        </span>
        {m.ttm_reason && <span className="stmt-note">{m.ttm_reason}</span>}
      </th>
      <td className="num stmt-value">
        {cur.value === null ? (
          <span className="muted" title={m.reason ?? undefined}>
            —
          </span>
        ) : (
          <Figure source={cur.source} value={cur.value} align="end">
            {fmtMoneyShort(cur.value)}
          </Figure>
        )}
      </td>
      <td className="num">
        <DeltaBadge value={cur.yoy} higherIsBetter={!LOWER_IS_BETTER.has(m.metric)} label={t("common.yoy")} />
      </td>
    </tr>
  );
}

function ScoreBreakdown({ data, analyzed }: { data: ScoreResponse; analyzed: boolean }) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState<string | null>(null);
  const comps = data.components;
  const wsum = comps.filter((c) => c.available && c.score !== null).reduce((a, c) => a + c.weight, 0);
  const contrib = (c: ScoreComponent) => (c.available && c.score !== null && wsum > 0 ? (c.weight * c.score) / wsum : null);

  if (data.total === null && comps.length === 0) {
    return <p className="muted-note">{t(!analyzed ? "overview.noScoreNotAnalyzed" : "overview.noScore")}</p>;
  }
  return (
    <>
      {data.total === null && <p className="muted-note">{t(!analyzed ? "overview.noScoreNotAnalyzed" : "overview.noScore")}</p>}
      <Anatomy components={comps} wsum={wsum} lang={lang} />
      <table className="data-table comp-table">
        <thead>
          <tr>
            <th scope="col">{t("overview.colComponent")}</th>
            <th scope="col" className="num">
              {t("overview.colWeight")}
            </th>
            <th scope="col" className="comp-score-col">
              {t("overview.colScore")}
            </th>
            <th scope="col" className="num">
              {t("overview.colContrib")}
            </th>
          </tr>
        </thead>
        <tbody>
          {comps.map((c) => {
            const isOpen = open === c.key;
            const detailId = `comp-detail-${c.key}`;
            const tone = scoreTone(c.score);
            const pts = contrib(c);
            return (
              <Fragment key={c.key}>
                <tr className={cx("comp-row", !c.available && "is-unavailable", isOpen && "is-open")}>
                  <th scope="row">
                    <button
                      type="button"
                      className="comp-toggle"
                      aria-expanded={isOpen}
                      aria-controls={detailId}
                      onClick={() => setOpen(isOpen ? null : c.key)}
                    >
                      <LuChevronDown aria-hidden="true" className="comp-chev" />
                      {componentLabel(lang, c.key, c.label)}
                    </button>
                  </th>
                  <td className="num muted">{t("overview.weight", { w: c.weight })}</td>
                  <td className="comp-score-col">
                    {c.available && c.score !== null ? (
                      <span className="comp-score">
                        <span className="comp-track" aria-hidden="true">
                          <span className={`comp-fill tone-bg-${tone}`} style={{ width: `${c.score}%` }} />
                        </span>
                        <span className={cx("comp-num", `tone-text-${tone}`)}>{fmtScore(c.score)}</span>
                      </span>
                    ) : (
                      <span className="muted">{t("overview.notComputed")}</span>
                    )}
                  </td>
                  <td className="num comp-contrib">{pts === null ? "—" : fmtPoints(pts)}</td>
                </tr>
                <tr id={detailId} className="comp-detail" hidden={!isOpen}>
                  <td colSpan={4}>
                    {c.indicators.length > 0 && (
                      <ul className="indicator-list">
                        {c.indicators.map((ind) => (
                          <li key={ind.key}>
                            <span>
                              {ind.label}
                              {ind.note && <span className="ind-note">{ind.note}</span>}
                              {ind.source && (
                                <span className="ind-note">
                                  {ind.source.form}, {fmtDate(ind.source.filing_date)}
                                </span>
                              )}
                            </span>
                            <span className="tnum">{ind.display}</span>
                            <span className="tnum muted">{ind.points === null ? "—" : `${fmtScore(ind.points)}`}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="comp-expl">{c.explanation}</p>
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="total-row">
            <th scope="row">{t("overview.total")}</th>
            <td className="num muted">{t("overview.weight", { w: wsum })}</td>
            <td />
            <td className="num total-num">{fmtScore(data.total)}</td>
          </tr>
        </tfoot>
      </table>
      {data.missing_components.length > 0 && (
        <p className="panel-foot">
          {t("overview.missingComponents", {
            list: data.missing_components
              .map((k) => {
                const c = comps.find((x) => x.key === k);
                return c ? componentLabel(lang, c.key, c.label) : k;
              })
              .join(", "),
          })}
        </p>
      )}
      {data.basis_notes && (Array.isArray(data.basis_notes) ? data.basis_notes.length > 0 : true) && (
        <p className="panel-foot">{Array.isArray(data.basis_notes) ? data.basis_notes.join(" ") : data.basis_notes}</p>
      )}
      <p className="panel-foot">
        {t("score.weights")} {data.weights_version}
        {data.method_version ? ` · ${t("score.method")} ${data.method_version}` : ""}
      </p>
    </>
  );
}

/** Skor anatomisi: her bileşen ağırlığı kadar genişlikte; dolu kısım kazanılan puan. */
function Anatomy({ components, wsum, lang }: { components: ScoreComponent[]; wsum: number; lang: Lang }) {
  const { t } = useI18n();
  const avail = components.filter((c) => c.available && c.score !== null);
  if (avail.length === 0 || wsum === 0) return null;
  return (
    <div className="anatomy" role="img" aria-label={t("overview.anatomyLabel")}>
      {avail.map((c) => (
        <span
          key={c.key}
          className="anatomy-seg"
          style={{ flexGrow: c.weight / wsum, flexBasis: 0 }}
          title={`${componentLabel(lang, c.key, c.label)}: ${fmtScore(c.score)} / 100`}
        >
          <span className="anatomy-fill" style={{ width: `${c.score ?? 0}%` }} />
        </span>
      ))}
    </div>
  );
}
