// Finansallar sekmesi: zaman serisi grafiği, bilanço grafiği ve oranlar tablosu (FR-08…FR-10).
import { useState } from "react";
import { api } from "../../api";
import { BarChart } from "../../charts/BarChart";
import type { BarSeries } from "../../charts/BarChart";
import { Sparkline } from "../../charts/small";
import { DeltaBadge, Disclaimer, EmptyState, ErrorCard, InfoTip, Panel, Segmented, SkeletonBlock, Tip } from "../../components/ui";
import {
  fmtDate,
  fmtMoneyShort,
  fmtMonthYear,
  fmtPercent,
  fmtRatio,
  fmtRatioDelta,
} from "../../format";
import { metricLabel, ratioLabel, useI18n } from "../../i18n";
import type { Lang, TFunc } from "../../i18n";
import { useAsync } from "../../lib/useAsync";
import type { FactMetric, FactsResponse, Ratio, RatiosResponse } from "../../types";

type SeriesKey = "revenue" | "net_income" | "operating_cash_flow" | "all";
const FLOW_METRICS = ["revenue", "net_income", "operating_cash_flow"] as const;
const FLOW_COLORS: Record<string, string> = {
  revenue: "var(--chart-1)",
  net_income: "var(--chart-2)",
  operating_cash_flow: "var(--chart-5)",
};
const BALANCE = [
  { key: "total_assets", color: "var(--chart-1)" },
  { key: "total_liabilities", color: "var(--chart-4)" },
  { key: "stockholders_equity", color: "var(--chart-2)" },
] as const;

function toSeries(metric: FactMetric | undefined, periods: string[], color: string, lang: Lang): BarSeries | null {
  if (!metric) return null;
  const byPeriod = new Map(metric.values.map((v) => [v.period_end, v]));
  return {
    key: metric.metric,
    label: metricLabel(lang, metric.metric, metric.label),
    color,
    points: periods.map((p) => {
      const v = byPeriod.get(p);
      return v ? { value: v.value, yoy: v.yoy, source: v.source } : null;
    }),
  };
}

/** Grafik başlığı bulguyu söyler: "Gelir 2 yılda %31,5 arttı". */
function headlineFor(series: BarSeries, periods: string[], t: TFunc): string {
  const vals = series.points.map((p) => p?.value ?? null);
  const firstIdx = vals.findIndex((v) => v !== null);
  let lastIdx = -1;
  vals.forEach((v, i) => {
    if (v !== null) lastIdx = i;
  });
  if (firstIdx < 0 || lastIdx <= firstIdx) return series.label;
  const first = vals[firstIdx] as number;
  const last = vals[lastIdx] as number;
  const years = Math.max(1, Math.round((Date.parse(periods[lastIdx]) - Date.parse(periods[firstIdx])) / 3.15e10));
  if (first === 0) return series.label;
  const change = (last - first) / Math.abs(first);
  if (Math.abs(change) < 0.01) return t("fin.flat", { label: series.label, n: years });
  return t(change > 0 ? "fin.grewBy" : "fin.fellBy", {
    label: series.label,
    n: years,
    pct: fmtPercent(Math.abs(change)),
  });
}

export function FinancialsTab({ ticker, cik }: { ticker: string; cik: string }) {
  const { t, lang } = useI18n();
  const { state, reload } = useAsync(
    (signal) => Promise.all([api.facts(ticker, undefined, signal), api.ratios(ticker, signal)]),
    [ticker],
  );
  const [pick, setPick] = useState<SeriesKey>("revenue");

  if (state.status === "loading") {
    return (
      <div className="grid-12">
        <Panel className="span-7">
          <SkeletonBlock chart lines={1} />
        </Panel>
        <Panel className="span-5">
          <SkeletonBlock chart lines={1} />
        </Panel>
        <Panel className="span-12">
          <SkeletonBlock lines={6} />
        </Panel>
      </div>
    );
  }
  if (state.status === "error") return <ErrorCard error={state.error} onRetry={reload} />;

  const [facts, ratios] = state.data;
  if (facts.metrics.length === 0 && ratios.ratios.length === 0) {
    return <EmptyState title={t("fin.noData")} />;
  }
  return <FinancialsView facts={facts} ratios={ratios} pick={pick} setPick={setPick} cik={cik} lang={lang} />;
}

function FinancialsView({
  facts,
  ratios,
  pick,
  setPick,
  lang,
}: {
  facts: FactsResponse;
  ratios: RatiosResponse;
  pick: SeriesKey;
  setPick: (k: SeriesKey) => void;
  cik: string;
  lang: Lang;
}) {
  const { t } = useI18n();
  const metricMap = new Map(facts.metrics.map((m) => [m.metric, m]));
  const periods =
    ratios.periods.length > 0
      ? ratios.periods
      : Array.from(new Set(facts.metrics.flatMap((m) => m.values.map((v) => v.period_end)))).sort();

  const flowSeries = FLOW_METRICS.map((k) => toSeries(metricMap.get(k), periods, FLOW_COLORS[k], lang)).filter(
    (s): s is BarSeries => s !== null,
  );
  const shown = pick === "all" ? flowSeries : flowSeries.filter((s) => s.key === pick);
  const flowTitle = pick === "all" || shown.length === 0 ? t("fin.allTitle") : headlineFor(shown[0], periods, t);

  const balanceSeries = BALANCE.map((b) => toSeries(metricMap.get(b.key), periods, b.color, lang)).filter(
    (s): s is BarSeries => s !== null,
  );
  const la = ratios.ratios.find((r) => r.key === "liabilities_to_assets");
  const laLast = la ? la.values[la.values.length - 1]?.value ?? null : null;

  const options: Array<{ value: SeriesKey; label: string }> = [
    ...flowSeries.map((s) => ({ value: s.key as SeriesKey, label: s.label })),
    { value: "all", label: t("fin.allSeries") },
  ];

  return (
    <div className="grid-12">
      <Panel
        className="span-7"
        title={flowTitle}
        subtitle={t("fin.seriesSub")}
        actions={<Segmented options={options} value={pick} onChange={setPick} label={t("fin.seriesPicker")} />}
      >
        {shown.length > 1 && <Legend series={shown} />}
        <BarChart periods={periods} series={shown} ariaLabel={flowTitle} showValueLabels={shown.length === 1} height={280} />
      </Panel>

      <Panel
        className="span-5"
        title={laLast !== null ? t("fin.balanceTitle", { pct: fmtPercent(laLast) }) : t("fin.balanceSub")}
        subtitle={t("fin.balanceSub")}
      >
        <Legend series={balanceSeries} />
        <BarChart periods={periods} series={balanceSeries} ariaLabel={t("fin.balanceSub")} height={280} />
      </Panel>

      <Panel className="span-12" title={t("fin.ratiosTitle")} subtitle={t("fin.ratiosLead")}>
        <RatiosTable ratios={ratios} lang={lang} />
      </Panel>

      <div className="span-12">
        <Disclaimer text={ratios.disclaimer} />
      </div>
    </div>
  );
}

function Legend({ series }: { series: BarSeries[] }) {
  return (
    <ul className="legend">
      {series.map((s) => (
        <li key={s.key}>
          <span className="swatch" style={{ background: s.color }} aria-hidden="true" />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function RatiosTable({ ratios, lang }: { ratios: RatiosResponse; lang: Lang }) {
  const { t } = useI18n();
  const periods = ratios.periods;
  return (
    <div className="table-wrap">
      <table className="data-table ratios-table">
        <thead>
          <tr>
            <th scope="col">{t("fin.ratio")}</th>
            {periods.map((p) => (
              <th key={p} scope="col" className="num">
                {fmtMonthYear(p)}
              </th>
            ))}
            <th scope="col" className="center">
              {t("fin.trend")}
            </th>
            <th scope="col" className="num">
              {t("fin.change")}
            </th>
          </tr>
        </thead>
        <tbody>
          {ratios.ratios.map((r) => (
            <RatioRow key={r.key} ratio={r} periods={periods} lang={lang} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RatioRow({ ratio: r, periods, lang }: { ratio: Ratio; periods: string[]; lang: Lang }) {
  const { t } = useI18n();
  const byPeriod = new Map(r.values.map((v) => [v.period_end, v]));
  const ordered = periods.map((p) => byPeriod.get(p) ?? null);
  const nums = ordered.map((v) => (v && v.status === "ok" ? v.value : null));
  const last = nums[nums.length - 1];
  const prev = nums[nums.length - 2];
  const delta = last !== null && last !== undefined && prev !== null && prev !== undefined ? last - prev : null;
  const improving = delta === null || delta === 0 ? null : delta > 0 === r.higher_is_better;
  const label = ratioLabel(lang, r.key, r.label);

  return (
    <tr>
      <th scope="row" className="ratio-name">
        <span>{label}</span>
        <InfoTip
          label={`${t("fin.formula")}: ${r.formula}`}
          align="start"
          content={
            <span className="tip-rows">
              <span>
                <b>{t("fin.formula")}</b> {r.formula}
              </span>
              <span>{r.higher_is_better ? t("fin.higherBetter") : t("fin.lowerBetter")}</span>
            </span>
          }
        />
      </th>
      {ordered.map((v, i) => {
        const align = i >= ordered.length - 1 ? "end" : "center";
        if (!v || v.status === "missing" || v.value === null) {
          return (
            <td key={periods[i]} className="num is-missing">
              <Tip align={align} content={`${t("fin.missing")}: ${v?.reason ?? "—"}`}>
                —
              </Tip>
            </td>
          );
        }
        return (
          <td key={periods[i]} className="num">
            <Tip
              align={align}
              content={
                <span className="tip-rows">
                  <span>
                    <b>{fmtDate(v.period_end)}</b>
                  </span>
                  {v.inputs.map((inp) => (
                    <span key={inp.metric}>
                      {metricLabel(lang, inp.metric, inp.metric)}: {fmtMoneyShort(inp.value)}
                    </span>
                  ))}
                  {v.inputs[0] && (
                    <span>
                      {t("common.source")}: {v.inputs[0].accession_no}
                    </span>
                  )}
                  {v.note && (
                    <span>
                      {t("fin.note")}: {v.note}
                    </span>
                  )}
                </span>
              }
            >
              {fmtRatio(v.value, r.unit)}
              {v.note && <sup className="note-mark">*</sup>}
            </Tip>
          </td>
        );
      })}
      <td className="center">
        <Sparkline values={nums} label={`${label}: ${nums.map((n) => fmtRatio(n, r.unit)).join(", ")}`} improving={improving} />
      </td>
      <td className="num">
        <DeltaBadge
          value={delta}
          higherIsBetter={r.higher_is_better}
          formatted={delta === null ? undefined : fmtRatioDelta(delta, r.unit)}
        />
      </td>
    </tr>
  );
}
