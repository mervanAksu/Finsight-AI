// Oranlar sekmesi (FR-06, FR-09): yıllık dönemler + TTM sütunu (Aşama 4), formül satırı,
// eğilim çizgisi ve son değişim. Eksik oran "—" ve nedeni ipucunda (eksik veri sıfır değildir).
import { api } from "../../api";
import { Sparkline } from "../../charts/small";
import { BasisTag, DeltaBadge, Disclaimer, EmptyState, ErrorCard, Panel, SkeletonBlock, Tip, cx } from "../../components/ui";
import { fmtDate, fmtMoneyShort, fmtPeriodHead, fmtRatio, fmtRatioDelta } from "../../format";
import { metricLabel, ratioLabel, useI18n } from "../../i18n";
import type { Lang } from "../../i18n";
import { useAsync } from "../../lib/useAsync";
import type { Ratio, RatiosResponse } from "../../types";

/** Dönemin tabanı: o dönemdeki herhangi bir oran değeri basis "ttm" taşıyorsa TTM. */
function periodBasis(data: RatiosResponse, period: string): "ttm" | "annual" {
  const info = data.period_info?.find((x) => x.period_end === period);
  if (info) return info.basis;
  return data.ratios.some((r) => r.values.some((v) => v.period_end === period && v.basis === "ttm")) ? "ttm" : "annual";
}

export function RatiosTab({ ticker }: { ticker: string }) {
  const { t, lang } = useI18n();
  const { state, reload } = useAsync((signal) => api.ratios(ticker, signal), [ticker]);
  if (state.status === "loading") {
    return (
      <Panel>
        <SkeletonBlock lines={10} />
      </Panel>
    );
  }
  if (state.status === "error") return <ErrorCard error={state.error} onRetry={reload} />;
  const data = state.data;
  if (data.ratios.length === 0) return <EmptyState title={t("fin.noData")} />;
  const bases = data.periods.map((p) => periodBasis(data, p));
  return (
    <div className="grid-12">
      <Panel className="span-12" title={t("fin.ratiosTitle")} subtitle={t("fin.ratiosLead")}>
        <div className="table-wrap">
          <table className="data-table ratios-table">
            <thead>
              <tr>
                <th scope="col">{t("fin.ratio")}</th>
                {data.periods.map((p, i) => (
                  <th key={p} scope="col" className={cx("num", bases[i] === "ttm" && "is-latest")}>
                    {bases[i] === "ttm" ? (
                      <>
                        <BasisTag basis="ttm" /> {fmtPeriodHead(p)}
                      </>
                    ) : (
                      fmtPeriodHead(p)
                    )}
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
              {data.ratios.map((r) => (
                <RatioRow key={r.key} ratio={r} periods={data.periods} bases={bases} lang={lang} />
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="span-12">
        <p className="source-hint">{t("overview.sourceHint")}</p>
        <Disclaimer text={data.disclaimer} />
      </div>
    </div>
  );
}

function RatioRow({ ratio: r, periods, bases, lang }: { ratio: Ratio; periods: string[]; bases: string[]; lang: Lang }) {
  const { t } = useI18n();
  const byPeriod = new Map(r.values.map((v) => [v.period_end, v]));
  const ordered = periods.map((p) => byPeriod.get(p) ?? null);
  const nums = ordered.map((v) => (v && v.status === "ok" ? v.value : null));
  const last = nums[nums.length - 1];
  const prev = nums[nums.length - 2];
  const rawDelta = last !== null && last !== undefined && prev !== null && prev !== undefined ? last - prev : null;
  // Gösterim hassasiyetinin altındaki farklar (0,00x / 0,0 puan) yatay kabul edilir.
  const epsilon = r.unit === "percent" ? 0.0005 : 0.005;
  const delta = rawDelta !== null && Math.abs(rawDelta) < epsilon ? 0 : rawDelta;
  const improving = delta === null || delta === 0 ? null : delta > 0 === r.higher_is_better;
  const label = ratioLabel(lang, r.key, r.label);

  return (
    <tr>
      <th scope="row" className="ratio-name">
        <span className="ratio-label">
          {label}
          <span className="dir-hint" title={r.higher_is_better ? t("fin.higherBetter") : t("fin.lowerBetter")}>
            {r.higher_is_better ? "↑" : "↓"}
            <span className="sr-only">{r.higher_is_better ? t("fin.higherBetter") : t("fin.lowerBetter")}</span>
          </span>
        </span>
        <span className="ratio-formula">{r.formula}</span>
      </th>
      {ordered.map((v, i) => {
        const align = i >= ordered.length - 1 ? "end" : "center";
        const latest = bases[i] === "ttm";
        if (!v || v.status === "missing" || v.value === null) {
          return (
            <td key={periods[i]} className={cx("num is-missing", latest && "is-latest")}>
              <Tip align={align} content={`${t("fin.missing")}: ${v?.reason ?? "—"}`}>
                —
              </Tip>
            </td>
          );
        }
        return (
          <td key={periods[i]} className={cx("num", latest && "is-latest")}>
            <Tip
              mark
              align={align}
              content={
                <span className="src-tip">
                  <span className="src-tip-value">
                    {label}: {fmtRatio(v.value, r.unit)}
                  </span>
                  <span className="src-tip-note">
                    {latest ? `${t("basis.ttmLong")}, ` : ""}
                    {fmtDate(v.period_end)}
                  </span>
                  <span className="src-tip-grid">
                    {v.inputs.map((inp) => (
                      <span key={inp.metric} className="contents">
                        <span>{metricLabel(lang, inp.metric, inp.metric)}</span>
                        <span className="tnum">{fmtMoneyShort(inp.value)}</span>
                      </span>
                    ))}
                    {v.inputs[0] && (
                      <>
                        <span>{t("filings.accession")}</span>
                        <span className="tnum">{v.inputs[0].accession_no}</span>
                      </>
                    )}
                  </span>
                  {v.note && (
                    <span className="src-tip-note">
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
        <DeltaBadge value={delta} higherIsBetter={r.higher_is_better} formatted={delta === null ? undefined : fmtRatioDelta(delta, r.unit)} />
      </td>
    </tr>
  );
}
