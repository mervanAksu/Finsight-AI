// Sayı ve tarih biçimlendirme. Varsayılan Intl.NumberFormat("tr-TR"); dil İngilizce
// seçildiğinde "en-US" kullanılır. Etkin dil App tarafından setFormatLang ile ayarlanır.
import type { Lang } from "./i18n";
import type { RatioUnit } from "./types";

let current: Lang = "tr";
const cache = new Map<string, Intl.NumberFormat>();

export function setFormatLang(lang: Lang): void {
  current = lang;
}

function locale(): string {
  return current === "en" ? "en-US" : "tr-TR";
}

function nf(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `${locale()}|${JSON.stringify(options)}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(locale(), options);
    cache.set(key, f);
  }
  return f;
}

const DASH = "—";

/** Kısaltılmış para: 281,7 mr $ · 1,2 mn $ (en: $281.7B). */
export function fmtMoneyShort(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const abs = Math.abs(value);
  const units: Array<[number, string, string]> = [
    [1e12, "tn", "T"],
    [1e9, "mr", "B"],
    [1e6, "mn", "M"],
    [1e3, "bin", "K"],
  ];
  for (const [size, trUnit, enUnit] of units) {
    if (abs >= size) {
      const scaled = value / size;
      const digits = Math.abs(scaled) >= 100 ? 0 : 1;
      const num = nf({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(scaled);
      return current === "en" ? `$${num}${enUnit}`.replace("$-", "-$") : `${num} ${trUnit} $`;
    }
  }
  const num = nf({ maximumFractionDigits: 0 }).format(value);
  return current === "en" ? `$${num}`.replace("$-", "-$") : `${num} $`;
}

/** Eksen etiketi için kısa para (para birimi işareti olmadan): 250 mr · 1,5 mn. */
export function fmtAxisMoney(value: number): string {
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  const units: Array<[number, string, string]> = [
    [1e12, "tn", "T"],
    [1e9, "mr", "B"],
    [1e6, "mn", "M"],
    [1e3, "bin", "K"],
  ];
  for (const [size, trUnit, enUnit] of units) {
    if (abs >= size) {
      const num = nf({ maximumFractionDigits: 1 }).format(value / size);
      return current === "en" ? `${num}${enUnit}` : `${num} ${trUnit}`;
    }
  }
  return nf({ maximumFractionDigits: 0 }).format(value);
}

/** Tam para: 281.724.000.000 $ (tooltip için). */
export function fmtMoneyFull(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return nf({ style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
}

/** Ham oran → yüzde: 0.361 → %36,1 */
export function fmtPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return nf({ style: "percent", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

/** İşaretli yüzde: +%14,9 / −%3,2 */
export function fmtSignedPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return nf({
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: "exceptZero",
  }).format(value);
}

/** Kat: 1,25x */
export function fmtTimes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${nf({ minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)}x`;
}

export function fmtRatio(value: number | null | undefined, unit: RatioUnit): string {
  return unit === "percent" ? fmtPercent(value) : fmtTimes(value);
}

/** Oran farkı: yüzde birimde puan (pp), kat biriminde fark. */
export function fmtRatioDelta(delta: number, unit: RatioUnit): string {
  if (unit === "percent") {
    const pts = nf({ minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(
      delta * 100,
    );
    return current === "en" ? `${pts} pp` : `${pts} puan`;
  }
  return nf({ minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(delta);
}

/** Net ton: +0,09 / −0,52 */
export function fmtTone(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return nf({ minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(value);
}

export function fmtNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return nf({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

export function fmtScore(value: number | null | undefined): string {
  return fmtNumber(value, 1);
}

// ---- Tarihler (ISO "YYYY-MM-DD"; saat dilimi kaymasını önlemek için UTC) ----
function parseDate(iso: string): Date | null {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = parseDate(iso);
  if (!d) return iso;
  return new Intl.DateTimeFormat(locale(), { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(
    d,
  );
}

export function fmtMonthYear(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = parseDate(iso);
  if (!d) return iso;
  return new Intl.DateTimeFormat(locale(), { month: "short", year: "numeric", timeZone: "UTC" }).format(d);
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = parseDate(iso);
  if (!d) return iso;
  return new Intl.DateTimeFormat(locale(), {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

/** Mali yıl etiketi: dönem sonu yılından "MY2025" / "FY2025". */
export function fmtFiscalYear(periodEnd: string): string {
  const y = periodEnd.slice(0, 4);
  return current === "en" ? `FY${y}` : `MY${y}`;
}

/** "0630" → "30 Haziran" */
export function fmtFiscalYearEnd(mmdd: string | null | undefined): string {
  if (!mmdd || mmdd.length !== 4) return DASH;
  const month = Number(mmdd.slice(0, 2));
  const day = Number(mmdd.slice(2));
  if (!month || !day) return mmdd;
  const d = new Date(Date.UTC(2001, month - 1, day));
  return new Intl.DateTimeFormat(locale(), { day: "numeric", month: "long", timeZone: "UTC" }).format(d);
}

export function fmtCik(cik: string): string {
  return cik;
}

/** Skor bandının görsel tonu (eşikler: financial-analysis §3). */
export type Tone = "positive" | "accent" | "warning" | "negative" | "muted";
export function scoreTone(total: number | null | undefined): Tone {
  if (total === null || total === undefined) return "muted";
  if (total >= 75) return "positive";
  if (total >= 60) return "accent";
  if (total >= 45) return "warning";
  return "negative";
}

// ---- Aşama 4: dönem tabanı (TTM / yıllık) ----
/** Değer anlık (bilanço) kalemi mi? TTM etiketi yalnızca süreli kalemlere uygulanır. */
export const INSTANT_METRICS = new Set([
  "total_assets",
  "current_assets",
  "current_liabilities",
  "total_liabilities",
  "stockholders_equity",
]);

/** Skor / değer dayanak satırı: "TTM · 30 Eyl 2026 · 10-Q" veya "Yıllık · 30 Haz 2026 · 10-K". */
export function fmtBasisLine(basis: string | null | undefined, period: string | null | undefined, form?: string | null): string {
  const parts: string[] = [];
  if (basis === "ttm") parts.push("TTM");
  else if (basis === "annual") parts.push(current === "en" ? "Annual" : "Yıllık");
  if (period) parts.push(fmtDate(period));
  if (form) parts.push(form);
  return parts.join(" · ");
}

/** Tablo sütun başlığı: yıllık "Haz 2026", TTM "TTM Eyl 2026". */
export function fmtPeriodHead(iso: string, basis?: string | null): string {
  return basis === "ttm" ? `TTM ${fmtMonthYear(iso)}` : fmtMonthYear(iso);
}

/** Saat: "09:30" */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = parseDate(iso);
  if (!d) return iso;
  return new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" }).format(d);
}

/** Skor katkısı (ağırlık × puan / kullanılan ağırlık): 21,3 */
export function fmtPoints(value: number | null | undefined): string {
  return fmtNumber(value, 1);
}
