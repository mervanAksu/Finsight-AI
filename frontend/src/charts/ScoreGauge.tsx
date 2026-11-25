// Skor ölçeği: 0–100 doğrusal cetvel. Bant eşikleri (45 / 60 / 75; financial-analysis §3)
// cetvelin altında ince bölütler olarak, puan dikey bir işaretle gösterilir. Yarım daire
// gösterge yerine cetvel seçildi: aynı genişlikte daha kesin okunur ve tabloda küçültülebilir.
import { fmtScore, scoreTone } from "../format";
import type { Tone } from "../format";
import { bandLabel, useI18n } from "../i18n";
import { cx } from "../components/ui";
import { useElementWidth } from "../lib/useElementWidth";

export const BANDS: Array<{ from: number; to: number; tone: Tone; name: string }> = [
  { from: 0, to: 45, tone: "negative", name: "Zayıf" },
  { from: 45, to: 60, tone: "warning", name: "İzlenmeli" },
  { from: 60, to: 75, tone: "accent", name: "Dengeli" },
  { from: 75, to: 100, tone: "positive", name: "Güçlü" },
];

const clamp = (v: number) => Math.max(0, Math.min(100, v));

/** Büyük cetvel (şirket başlığı). */
export function ScoreScale({ total, band }: { total: number | null; band: string | null }) {
  const { t, lang } = useI18n();
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const h = 46;
  const padX = 8;
  const inner = Math.max(0, width - padX * 2);
  const x = (v: number) => padX + (clamp(v) / 100) * inner;
  const trackY = 18;
  const label = `${fmtScore(total)} / 100 · ${bandLabel(lang, band)}. ${t("score.scaleLabel")}`;

  return (
    <div className="score-scale" ref={ref} style={{ height: h }}>
      {width > 0 && (
        <svg width={width} height={h} role="img" aria-label={label}>
          {BANDS.map((b) => (
            <rect
              key={b.name}
              className={cx("scale-band", `tone-${b.tone}`, band === b.name && "is-current")}
              x={x(b.from) + (b.from === 0 ? 0 : 1)}
              y={trackY}
              width={Math.max(0, x(b.to) - x(b.from) - (b.from === 0 ? 1 : 2))}
              height={band === b.name ? 6 : 4}
            />
          ))}
          {[0, 45, 60, 75, 100].map((v) => (
            <g key={v}>
              <line className="scale-tick" x1={x(v)} x2={x(v)} y1={trackY + 7} y2={trackY + 11} />
              <text
                className="scale-label"
                x={x(v)}
                y={trackY + 24}
                textAnchor={v === 0 ? "start" : v === 100 ? "end" : "middle"}
              >
                {v}
              </text>
            </g>
          ))}
          {total !== null && (
            <g className="scale-marker" transform={`translate(${x(total)},0)`}>
              <path d="M-5,4 L5,4 L0,11 Z" />
              <line x1={0} x2={0} y1={11} y2={trackY + 8} />
            </g>
          )}
        </svg>
      )}
    </div>
  );
}

/** Küçük cetvel + sayı (tablolar, karşılaştırma). */
export function ScoreMini({ total, band, width = 72 }: { total: number | null; band?: string | null; width?: number }) {
  const { lang } = useI18n();
  const tone = scoreTone(total);
  const x = (v: number) => 1 + (clamp(v) / 100) * (width - 2);
  return (
    <span className="score-mini">
      <span className={cx("score-mini-num", `tone-text-${tone}`)}>{total === null ? "—" : fmtScore(total)}</span>
      <svg width={width} height={12} aria-hidden="true" className="score-mini-svg">
        {BANDS.map((b) => (
          <rect key={b.name} className={`scale-band tone-${b.tone}`} x={x(b.from)} y={5} width={Math.max(0, x(b.to) - x(b.from) - 1)} height={2} />
        ))}
        {total !== null && <rect className="scale-mini-mark" x={x(total) - 1} y={0} width={2.5} height={12} />}
      </svg>
      {band !== undefined && <span className={cx("score-mini-band", `tone-text-${tone}`)}>{bandLabel(lang, band ?? null)}</span>}
    </span>
  );
}
