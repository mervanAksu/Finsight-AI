// Skor göstergesi: yarım daire. Dış ince halka bant eşiklerini (45 / 60 / 75) gösterir,
// iç kalın yay toplam puanı. Bant renkleri financial-analysis §3 eşikleriyle aynıdır.
import { arc } from "d3-shape";
import { fmtScore, scoreTone } from "../format";
import type { Tone } from "../format";
import { bandLabel, useI18n } from "../i18n";

const BANDS: Array<{ from: number; to: number; tone: Tone; name: string }> = [
  { from: 0, to: 45, tone: "negative", name: "Zayıf" },
  { from: 45, to: 60, tone: "warning", name: "İzlenmeli" },
  { from: 60, to: 75, tone: "accent", name: "Dengeli" },
  { from: 75, to: 100, tone: "positive", name: "Güçlü" },
];

const toAngle = (v: number) => -Math.PI / 2 + (Math.max(0, Math.min(100, v)) / 100) * Math.PI;

interface ArcDatum {
  innerRadius: number;
  outerRadius: number;
  startAngle: number;
  endAngle: number;
}

export function ScoreGauge({ total, band, size = 300 }: { total: number | null; band: string | null; size?: number }) {
  const { lang } = useI18n();
  const w = size;
  const rOuter = w / 2 - 22;
  const h = rOuter + 30;
  const cxp = w / 2;
  const cyp = rOuter + 14;
  const ringOuter = rOuter;
  const ringInner = rOuter - 5;
  const valOuter = rOuter - 13;
  const valInner = rOuter - 31;
  const gen = arc<ArcDatum>().cornerRadius(2);
  const gap = 0.012;
  const tone = scoreTone(total);
  const valueAngle = total === null ? null : toAngle(total);
  const label = bandLabel(lang, band);

  const needle =
    valueAngle === null
      ? null
      : {
          x1: cxp + Math.sin(valueAngle) * (valInner - 4),
          y1: cyp - Math.cos(valueAngle) * (valInner - 4),
          x2: cxp + Math.sin(valueAngle) * (ringOuter + 3),
          y2: cyp - Math.cos(valueAngle) * (ringOuter + 3),
        };

  return (
    <figure className="gauge" style={{ maxWidth: w }}>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`${fmtScore(total)} / 100 · ${label}`}>
        <g transform={`translate(${cxp},${cyp})`}>
          {BANDS.map((b) => (
            <path
              key={b.name}
              className={`gauge-band tone-${b.tone}`}
              d={
                gen({
                  innerRadius: ringInner,
                  outerRadius: ringOuter,
                  startAngle: toAngle(b.from) + (b.from === 0 ? 0 : gap),
                  endAngle: toAngle(b.to) - (b.to === 100 ? 0 : gap),
                }) ?? ""
              }
            />
          ))}
          <path
            className="gauge-track"
            d={gen({ innerRadius: valInner, outerRadius: valOuter, startAngle: toAngle(0), endAngle: toAngle(100) }) ?? ""}
          />
          {valueAngle !== null && (
            <path
              className={`gauge-value tone-${tone}`}
              d={gen({ innerRadius: valInner, outerRadius: valOuter, startAngle: toAngle(0), endAngle: valueAngle }) ?? ""}
            />
          )}
        </g>
        {needle && <line className="gauge-needle" x1={needle.x1} y1={needle.y1} x2={needle.x2} y2={needle.y2} />}
        {[45, 60, 75].map((v) => {
          const a = toAngle(v);
          const r = ringOuter + 11;
          return (
            <text
              key={v}
              className="gauge-tick"
              x={cxp + Math.sin(a) * r}
              y={cyp - Math.cos(a) * r + 3}
              textAnchor={v < 50 ? "end" : v > 50 ? "start" : "middle"}
            >
              {v}
            </text>
          );
        })}
        <text className="gauge-end" x={cxp - (valInner + valOuter) / 2} y={cyp + 16} textAnchor="middle">
          0
        </text>
        <text className="gauge-end" x={cxp + (valInner + valOuter) / 2} y={cyp + 16} textAnchor="middle">
          100
        </text>
        <text className="gauge-total" x={cxp} y={cyp - 14} textAnchor="middle">
          {fmtScore(total)}
        </text>
        <text className={`gauge-band-label tone-text-${tone}`} x={cxp} y={cyp + 12} textAnchor="middle">
          {label}
        </text>
      </svg>
    </figure>
  );
}

/** Kartlarda kullanılan küçük halka. */
export function ScoreRing({ total, size = 56, stroke = 5 }: { total: number | null; size?: number; stroke?: number }) {
  const r = size / 2 - stroke / 2 - 1;
  const gen = arc<ArcDatum>().cornerRadius(stroke / 2);
  const tone = scoreTone(total);
  const end = total === null ? 0 : (Math.max(0, Math.min(100, total)) / 100) * Math.PI * 2;
  return (
    <svg className="score-ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <g transform={`translate(${size / 2},${size / 2})`}>
        <circle className="ring-track" r={r} fill="none" strokeWidth={stroke} />
        {total !== null && (
          <path
            className={`ring-value tone-${tone}`}
            d={gen({ innerRadius: r - stroke / 2, outerRadius: r + stroke / 2, startAngle: 0, endAngle: end }) ?? ""}
          />
        )}
        <text className="ring-text" textAnchor="middle" dy="0.35em">
          {total === null ? "—" : Math.round(total)}
        </text>
      </g>
    </svg>
  );
}
