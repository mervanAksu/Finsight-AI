// Finansallar sekmesi (FR-04, FR-08): akış kalemleri ve bilanço grafikleri, yıllık tablo +
// en son nokta (TTM / son çeyrek bilançosu, Aşama 4). Her hücrenin kaynağı ipucundadır (NFR-06).
import { useState } from "react";
import { api } from "../../api";
import { BarChart } from "../../charts/BarChart";
import type { BarSeries } from "../../charts/BarChart";
import { Disclaimer, EmptyState, ErrorCard, Figure, Panel, Segmented, SkeletonBlock } from "../../components/ui";
import { fmtMoneyShort, fmtMonthYear, fmtPercent, fmtPeriodHead } from "../../format";
import { metricLabel, useI18n } from "../../i18n";
import type { Lang, MsgKey, TFunc } from "../../i18n";
import { useAsync } from "../../lib/useAsync";
import type { FactMetric, FactValue, FactsResponse } from "../../types";

type SeriesKey = "revenue" | "net_income" | "operating_cash_flow" | "all";
const FLOW_METRICS = ["revenue", "net_income", "operating_cash_flow"] as const;
const FLOW_COLORS: Record<string, string> = {
  revenue: "var(--chart-1)",
  net_income: "var(--chart-3)",
  operating_cash_flow: "var(--chart-2)",
};
const BALANCE = [
  { key: "total_assets", color: "var(--chart-1)" },
  { key: "total_liabilities", color: "var(--chart-3)" },
  { key: "stockholders_equity", color: "var(--chart-4)" },
] as const;

const GROUPS: Array<{ title: MsgKey; keys: string[] }> = [
  { title: "fin.groupIncome", keys: ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income"] },
  { title: "fin.groupCash", keys: ["operating_cash_flow", "capex"] },
  {
    title: "fin.groupBalance",
    keys: ["total_assets", "current_assets", "current_liabilities", "total_liabilities", "stockholders_equity"],
  },
];

function pointsFor(metric: FactMetric | undefined, periods: string[], latestPeriod: string | null) {
  if (!metric) return null;
  const byPeriod = new Map(metric.values.map((v) => [v.period_end, v]));
  const pts = periods.map((p) => {
    const v = byPeriod.get(p);
    return v ? { value: v.value, yoy: v.yoy, source: v.source } : null;
  });
  if (latestPeriod) {
    const l = metric.latest;
    pts.push(l && l.period_end === latestPeriod ? { value: l.value, yoy: l.yoy, source: l.source } : null);
  }
  return pts;
}

/** Grafik başlığı bulguyu söyler: "Gelir 2 yılda %31,5 arttı" (yalnızca yıllık seri). */
function headlineFor(series: BarSeries, annualCount: number, periods: string[], t: TFunc): string {
  const vals = series.points.slice(0, annualCount).map((p) => p?.value ?? null);
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
  return t(change > 0 ? "fin.grewBy" : "fin.fellBy", { label: series.label, n: years, pct: fmtPercent(Math.abs(change)) });
}

export function FinancialsTab({ ticker }: { ticker: string; cik?: string }) {
  const { t, lang } = useI18n();
  const { state, reload } = useAsync((signal) => api.facts(ticker, undefined, signal), [ticker]);
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
          <SkeletonBlock lines={8} />
        </Panel>
      </div>
    );
  }
  if (state.status === "error") return <ErrorCard error={state.error} onRetry={reload} />;
  if (state.data.metrics.length === 0) return <EmptyState title={t("fin.noData")} />;
  return <FinancialsView facts={state.data} pick={pick} setPick={setPick} lang={lang} />;
}

function FinancialsView({
  facts,
  pick,
  setPick,
  lang,
}: {
  facts: FactsResponse;
  pick: SeriesKey;
  setPick: (k: SeriesKey) => void;
  lang: Lang;
}) {
  const { t } = useI18n();
  const metricMap = new Map(facts.metrics.map((m) => [m.metric, m]));
  const periods = Array.from(new Set(facts.metrics.flatMap((m) => m.values.map((v) => v.period_end)))).sort();
  const lastAnnual = periods[periods.length - 1] ?? "";
  const latestPeriod =
    facts.metrics.map((m) => m.latest?.period_end).find((p): p is string => !!p && p > lastAnnual) ?? null;
  const chartPeriods = latestPeriod ? [...periods, latestPeriod] : periods;
  const ttmIndex = latestPeriod ? periods.length : null;

  const mk = (key: string, color: string): BarSeries | null => {
    const m = metricMap.get(key);
    const pts = pointsFor(m, periods, latestPeriod);
    return m && pts ? { key, label: metricLabel(lang, key, m.label), color, points: pts } : null;
  };
  const flowSeries = FLOW_METRICS.map((k) => mk(k, FLOW_COLORS[k])).filter((s): s is BarSeries => s !== null);
  const shown = pick === "all" ? flowSeries : flowSeries.filter((s) => s.key === pick);
  const flowTitle = pick === "all" || shown.length === 0 ? t("fin.allTitle") : headlineFor(shown[0], periods.length, periods, t);
  const balanceSeries = BALANCE.map((b) => mk(b.key, b.color)).filter((s): s is BarSeries => s !== null);

  const last = (key: string) => {
    const m = metricMap.get(key);
    return m?.latest?.value ?? m?.values[m.values.length - 1]?.value ?? null;
  };
  const tl = last("total_liabilities");
  const ta = last("total_assets");
  const la = tl !== null && ta ? tl / ta : null;

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
        <BarChart
          periods={chartPeriods}
          series={shown}
          ariaLabel={flowTitle}
          showValueLabels={shown.length === 1}
          height={280}
          ttmIndex={ttmIndex}
        />
      </Panel>

      <Panel className="span-5" title={la !== null ? t("fin.balanceTitle", { pct: fmtPercent(la) }) : t("fin.balanceSub")} subtitle={t("fin.balanceSub")}>
        <Legend series={balanceSeries} />
        <BarChart periods={chartPeriods} series={balanceSeries} ariaLabel={t("fin.balanceSub")} height={280} ttmIndex={ttmIndex} ttmPrefix="" />
      </Panel>

      <Panel className="span-12" title={t("fin.statementTitle")} subtitle={t("fin.statementLead")}>
        <div className="table-wrap">
          <table className="data-table facts-table">
            <thead>
              <tr>
                <th scope="col">{t("overview.colItem")}</th>
                {periods.map((p) => (
                  <th key={p} scope="col" className="num">
                    {fmtMonthYear(p)}
                  </th>
                ))}
                {latestPeriod && (
                  <th scope="col" className="num is-latest">
                    {fmtPeriodHead(latestPeriod, "ttm")}
                  </th>
                )}
              </tr>
            </thead>
            {GROUPS.map((g) => (
              <tbody key={g.title}>
                <tr className="group-row">
                  <th scope="rowgroup" colSpan={periods.length + (latestPeriod ? 2 : 1)}>
                    {t(g.title)}
                  </th>
                </tr>
                {g.keys
                  .map((k) => metricMap.get(k))
                  .filter((m): m is FactMetric => !!m)
                  .map((m) => (
                    <FactRow key={m.metric} metric={m} periods={periods} latestPeriod={latestPeriod} lang={lang} />
                  ))}
              </tbody>
            ))}
          </table>
        </div>
      </Panel>

      <div className="span-12">
        <Disclaimer text={facts.disclaimer} />
      </div>
    </div>
  );
}

function FactRow({ metric: m, periods, latestPeriod, lang }: { metric: FactMetric; periods: string[]; latestPeriod: string | null; lang: Lang }) {
  const { t } = useI18n();
  const byPeriod = new Map(m.values.map((v) => [v.period_end, v]));
  const cell = (v: FactValue | undefined | null, key: string, last: boolean, latest = false) => (
    <td key={key} className={latest ? "num is-latest" : "num"}>
      {v && v.value !== null ? (
        <Figure source={v.source} value={v.value} align={last ? "end" : "center"}>
          {fmtMoneyShort(v.value)}
        </Figure>
      ) : (
        <span className="muted" title={t("fin.missing")}>
          —
        </span>
      )}
    </td>
  );
  const isTotal = m.metric === "net_income" || m.metric === "stockholders_equity";
  return (
    <tr className={isTotal ? "is-total" : undefined}>
      <th scope="row">
        {metricLabel(lang, m.metric, m.label)}
        {latestPeriod && m.latest && m.kind === "duration" && <span className="sr-only"> ({t("basis.ttm")})</span>}
      </th>
      {periods.map((p, i) => cell(byPeriod.get(p), p, !latestPeriod && i === periods.length - 1))}
      {latestPeriod && cell(m.latest?.period_end === latestPeriod ? m.latest : null, "latest", true, true)}
    </tr>
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

