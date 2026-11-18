// Küçük grafikler: oran sparkline'ı, ton çubukları ve karşılaştırma çubukları.
import { extent } from "d3-array";
import { scaleLinear, scalePoint } from "d3-scale";
import { line } from "d3-shape";
import { fmtPercent, fmtTone } from "../format";
import { useI18n } from "../i18n";
import { cx } from "../components/ui";

// ------------------------------------------------------------------ Sparkline
export function Sparkline({
  values,
  width = 92,
  height = 28,
  label,
  improving,
}: {
  values: Array<number | null>;
  width?: number;
  height?: number;
  label: string;
  improving: boolean | null;
}) {
  const pts = values.map((v, i) => ({ i, v }));
  const defined = pts.filter((p): p is { i: number; v: number } => p.v !== null);
  if (defined.length < 2) {
    return <span className="spark-empty" aria-label={label}>—</span>;
  }
  const [lo, hi] = extent(defined, (p) => p.v) as [number, number];
  const pad = 4;
  const x = scalePoint<number>()
    .domain(pts.map((p) => p.i))
    .range([pad, width - pad]);
  const y = scaleLinear()
    .domain(lo === hi ? [lo - 1, hi + 1] : [lo, hi])
    .range([height - pad, pad]);
  const gen = line<{ i: number; v: number | null }>()
    .defined((p) => p.v !== null)
    .x((p) => x(p.i) ?? 0)
    .y((p) => y(p.v ?? 0));
  const last = defined[defined.length - 1];
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <path d={gen(pts) ?? ""} className="spark-line" />
      {defined.map((p) => (
        <circle
          key={p.i}
          cx={x(p.i) ?? 0}
          cy={y(p.v)}
          r={p === last ? 2.75 : 1.6}
          className={cx("spark-dot", p === last && (improving === null ? "is-flat" : improving ? "is-good" : "is-bad"))}
        />
      ))}
    </svg>
  );
}

// ------------------------------------------------------------------ Ton çubuğu (yığılı %100)
export function ToneBar({
  positive,
  neutral,
  negative,
  compact = false,
}: {
  positive: number;
  neutral: number;
  negative: number;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const total = positive + neutral + negative || 1;
  const parts = [
    { key: "pos", n: positive, label: t("risk.positive") },
    { key: "neu", n: neutral, label: t("risk.neutral") },
    { key: "neg", n: negative, label: t("risk.negative") },
  ];
  return (
    <div
      className={cx("tonebar", compact && "is-compact")}
      role="img"
      aria-label={parts.map((p) => `${p.label} ${fmtPercent(p.n / total, 0)}`).join(", ")}
    >
      {parts.map((p) => {
        const share = p.n / total;
        return (
          <span key={p.key} className={`tonebar-seg seg-${p.key}`} style={{ flexGrow: share, flexBasis: 0 }}>
            {!compact && share >= 0.09 && <span className="tonebar-text">{fmtPercent(share, 0)}</span>}
          </span>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Net ton göstergesi (−1 … +1)
export function ToneMeter({ value, label }: { value: number | null; label: string }) {
  const scale = scaleLinear().domain([-1, 1]).range([0, 100]).clamp(true);
  const pos = value === null ? 50 : scale(value);
  const left = Math.min(50, pos);
  const width = Math.abs(pos - 50);
  const tone = value === null ? "muted" : value > 0.05 ? "positive" : value < -0.05 ? "negative" : "muted";
  return (
    <div className="tonemeter">
      <div className="tonemeter-head">
        <span className="tonemeter-label">{label}</span>
        <span className={cx("tonemeter-value", `tone-text-${tone}`)}>{fmtTone(value)}</span>
      </div>
      <div className="tonemeter-track" role="img" aria-label={`${label}: ${fmtTone(value)}`}>
        <span className="tonemeter-mid" />
        {value !== null && (
          <>
            <span className={cx("tonemeter-fill", `tone-bg-${tone}`)} style={{ left: `${left}%`, width: `${width}%` }} />
            <span className={cx("tonemeter-dot", `tone-bg-${tone}`)} style={{ left: `${pos}%` }} />
          </>
        )}
      </div>
      <div className="tonemeter-scale" aria-hidden="true">
        <span>−1</span>
        <span>0</span>
        <span>+1</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Yatay karşılaştırma çubukları
export interface HBarRow {
  key: string;
  label: string;
  value: number | null;
  display: string;
  best: boolean;
}

export function HBars({ rows, ariaLabel }: { rows: HBarRow[]; ariaLabel: string }) {
  const vals = rows.map((r) => r.value).filter((v): v is number => v !== null);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const x = scaleLinear()
    .domain([lo, hi === lo ? lo + 1 : hi])
    .range([0, 100]);
  const zero = x(0);
  return (
    <div className="hbars" role="list" aria-label={ariaLabel}>
      {rows.map((r) => {
        const pos = r.value === null ? zero : x(r.value);
        return (
          <div key={r.key} className={cx("hbar-row", r.best && "is-best")} role="listitem">
            <span className="hbar-label">{r.label}</span>
            <span className="hbar-track">
              {lo < 0 && <span className="hbar-zero" style={{ left: `${zero}%` }} />}
              {r.value !== null && (
                <span
                  className="hbar-fill"
                  style={{ left: `${Math.min(zero, pos)}%`, width: `${Math.max(0.6, Math.abs(pos - zero))}%` }}
                />
              )}
            </span>
            <span className="hbar-value">{r.display}</span>
          </div>
        );
      })}
    </div>
  );
}
