// Gruplu çubuk grafik (d3-scale + elle SVG). Dönem üzerine gelince / odaklanınca tüm
// serilerin değerini, YoY'u ve kaynağını gösteren ipucu açılır. `ttmIndex` verilirse o
// dönemin çubukları taralı çizilir (son on iki ay, Aşama 4): yıllık seriyle karışmaz.
import { max, min } from "d3-array";
import { scaleBand, scaleLinear } from "d3-scale";
import { useId, useState } from "react";
import type { KeyboardEvent } from "react";
import { fmtAxisMoney, fmtDate, fmtMoneyFull, fmtMoneyShort, fmtMonthYear, fmtSignedPercent } from "../format";
import { useI18n } from "../i18n";
import { useElementWidth } from "../lib/useElementWidth";
import type { Source } from "../types";

export interface BarPoint {
  value: number | null;
  yoy?: number | null;
  source?: Source | null;
}

export interface BarSeries {
  key: string;
  label: string;
  color: string; // CSS rengi, ör. "var(--chart-1)"
  points: Array<BarPoint | null>; // periods ile aynı sırada
}

interface BarChartProps {
  periods: string[];
  series: BarSeries[];
  height?: number;
  ariaLabel: string;
  showValueLabels?: boolean;
  /** Bu dönem TTM'dir: taralı çubuk ve "TTM" eksen etiketi. */
  ttmIndex?: number | null;
  /** Taralı dönemin eksen etiketi öneki (varsayılan "TTM "; bilanço için ""). */
  ttmPrefix?: string;
}

const MARGIN = { top: 22, right: 4, bottom: 30, left: 52 };

/** Yalnızca üst köşeleri yuvarlatılmış çubuk (negatifte alt köşeler). */
function barPath(x: number, y0: number, y1: number, w: number, r: number): string {
  const top = Math.min(y0, y1);
  const bottom = Math.max(y0, y1);
  const h = bottom - top;
  const rr = Math.min(r, w / 2, h);
  if (y1 <= y0) {
    // pozitif değer: yuvarlak üst
    return `M${x},${bottom}V${top + rr}Q${x},${top} ${x + rr},${top}H${x + w - rr}Q${x + w},${top} ${x + w},${top + rr}V${bottom}Z`;
  }
  return `M${x},${top}V${bottom - rr}Q${x},${bottom} ${x + rr},${bottom}H${x + w - rr}Q${x + w},${bottom} ${x + w},${bottom - rr}V${top}Z`;
}

export function BarChart({ periods, series, height = 260, ariaLabel, showValueLabels = false, ttmIndex = null, ttmPrefix = "TTM " }: BarChartProps) {
  const { t } = useI18n();
  const uid = useId().replace(/:/g, "");
  const periodLabel = (i: number) => (i === ttmIndex ? `${ttmPrefix}${fmtMonthYear(periods[i])}` : fmtMonthYear(periods[i]));
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);

  const values = series.flatMap((s) => s.points.map((p) => p?.value ?? null)).filter((v): v is number => v !== null);
  const lo = Math.min(0, min(values) ?? 0);
  const hi = Math.max(0, max(values) ?? 0);
  const innerW = Math.max(0, width - MARGIN.left - MARGIN.right);
  const innerH = height - MARGIN.top - MARGIN.bottom;

  const y = scaleLinear().domain([lo, hi === lo ? lo + 1 : hi]).nice(4).range([innerH, 0]);
  const x0 = scaleBand<string>().domain(periods).range([0, innerW]).paddingInner(0.3).paddingOuter(0.15);
  const groupW = Math.min(x0.bandwidth(), series.length * 56);
  const x1 = scaleBand<string>()
    .domain(series.map((s) => s.key))
    .range([0, groupW])
    .padding(series.length > 1 ? 0.14 : 0);
  const ticks = y.ticks(4);

  const onKey = (e: KeyboardEvent<SVGRectElement>, i: number) => {
    if (e.key === "ArrowRight") setActive(Math.min(periods.length - 1, i + 1));
    if (e.key === "ArrowLeft") setActive(Math.max(0, i - 1));
    if (e.key === "Escape") setActive(null);
  };

  // İpucu, etkin dönemin yanına yerleşir (sol yarıda sağa, sağ yarıda sola açılır).
  const bandLeft = active === null ? 0 : MARGIN.left + (x0(periods[active]) ?? 0);
  const openRight = bandLeft + x0.bandwidth() / 2 < width / 2;
  const tipStyle = openRight
    ? { left: bandLeft + x0.bandwidth() + 6, top: MARGIN.top }
    : { left: bandLeft - 6, top: MARGIN.top, transform: "translateX(-100%)" };

  return (
    <div className="chart" ref={ref} style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={ariaLabel}>
          <defs>
            {series.map((s) => (
              <pattern key={s.key} id={`${uid}-h-${s.key}`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="5" height="5" fill="var(--surface)" />
                <rect width="2.2" height="5" fill={s.color} />
              </pattern>
            ))}
          </defs>
          <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
            {ticks.map((tk) => (
              <g key={tk} transform={`translate(0,${y(tk)})`}>
                <line className={tk === 0 ? "axis-zero" : "grid"} x1={0} x2={innerW} />
                <text className="axis-label" x={-10} dy="0.32em" textAnchor="end">
                  {fmtAxisMoney(tk)}
                </text>
              </g>
            ))}
            {periods.map((p, i) => {
              const gx = (x0(p) ?? 0) + (x0.bandwidth() - groupW) / 2;
              return (
                <g key={p}>
                  {active === i && (
                    <rect className="band-hover" x={x0(p) ?? 0} y={0} width={x0.bandwidth()} height={innerH} />
                  )}
                  {series.map((s) => {
                    const pt = s.points[i];
                    if (!pt || pt.value === null) return null;
                    const bx = gx + (x1(s.key) ?? 0);
                    const bw = x1.bandwidth();
                    return (
                      <g key={s.key}>
                        <path
                          d={barPath(bx, y(0), y(pt.value), bw, 1)}
                          fill={i === ttmIndex ? `url(#${uid}-h-${s.key})` : s.color}
                          stroke={i === ttmIndex ? s.color : "none"}
                          strokeWidth={i === ttmIndex ? 1.25 : 0}
                          className="bar"
                        />
                        {showValueLabels && (
                          <text
                            className="bar-label"
                            x={bx + bw / 2}
                            y={pt.value >= 0 ? y(pt.value) - 6 : y(pt.value) + 14}
                            textAnchor="middle"
                          >
                            {fmtMoneyShort(pt.value)}
                          </text>
                        )}
                      </g>
                    );
                  })}
                  <text
                    className={i === ttmIndex ? "axis-label is-ttm" : "axis-label"}
                    x={(x0(p) ?? 0) + x0.bandwidth() / 2}
                    y={innerH + 20}
                    textAnchor="middle"
                  >
                    {periodLabel(i)}
                  </text>
                  <rect
                    className="hit"
                    x={x0(p) ?? 0}
                    y={0}
                    width={x0.bandwidth()}
                    height={innerH}
                    tabIndex={0}
                    role="button"
                    aria-label={`${periodLabel(i)}: ${series
                      .map((s) => `${s.label} ${fmtMoneyShort(s.points[i]?.value ?? null)}`)
                      .join(", ")}`}
                    onMouseEnter={() => setActive(i)}
                    onMouseLeave={() => setActive(null)}
                    onFocus={() => setActive(i)}
                    onBlur={() => setActive(null)}
                    onKeyDown={(e) => onKey(e, i)}
                  />
                </g>
              );
            })}
          </g>
        </svg>
      )}
      {active !== null && width > 0 && (
        <div
          className="chart-tip"
          style={tipStyle}
          role="presentation"
        >
          <p className="chart-tip-title">{periodLabel(active)}</p>
          {series.map((s) => {
            const pt = s.points[active];
            return (
              <div key={s.key} className="chart-tip-row">
                <span className="swatch" style={{ background: s.color }} aria-hidden="true" />
                <span className="chart-tip-label">{s.label}</span>
                <span className="chart-tip-value">
                  {fmtMoneyShort(pt?.value ?? null)}
                  {pt?.yoy !== undefined && pt?.yoy !== null && (
                    <span className={pt.yoy >= 0 ? "pos" : "neg"}> {fmtSignedPercent(pt.yoy)}</span>
                  )}
                </span>
              </div>
            );
          })}
          {(() => {
            const src = series.map((s) => s.points[active]?.source).find((sv) => sv);
            const full = series.length === 1 ? series[0].points[active]?.value ?? null : null;
            return (
              <>
                {full !== null && <p className="chart-tip-note">{fmtMoneyFull(full)}</p>}
                {src && (
                  <p className="chart-tip-note">
                    {t("common.source")}: {src.form}, {fmtDate(src.filed)}
                    <br />
                    <span className="tnum">{src.accession_no}</span>
                  </p>
                )}
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
