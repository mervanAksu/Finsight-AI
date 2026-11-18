// Ortak küçük bileşenler: kart, iskelet, hata/boş durumları, uyarı, kaynak çipi, ipucu.
import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { LuArrowDownRight, LuArrowUpRight, LuCircleAlert, LuExternalLink, LuInfo, LuMinus, LuRefreshCw } from "react-icons/lu";
import { ApiError, edgarFilingUrl } from "../api";
import { fmtDate, fmtSignedPercent } from "../format";
import { useI18n } from "../i18n";
import type { TFunc } from "../i18n";
import type { Source } from "../types";

export function cx(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ Panel
interface PanelProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}

export function Panel({ title, subtitle, actions, children, className, id }: PanelProps) {
  const headingId = useId();
  return (
    <section className={cx("panel", className)} id={id} aria-labelledby={title ? headingId : undefined}>
      {(title || actions) && (
        <header className="panel-head">
          <div className="panel-titles">
            {title && (
              <h2 className="panel-title" id={headingId}>
                {title}
              </h2>
            )}
            {subtitle && <p className="panel-sub">{subtitle}</p>}
          </div>
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

// ------------------------------------------------------------------ Skeleton
export function Skeleton({ width, height = 14, radius }: { width?: number | string; height?: number; radius?: number }) {
  return <span className="skeleton" style={{ width: width ?? "100%", height, borderRadius: radius }} aria-hidden="true" />;
}

export function SkeletonBlock({ lines = 3, chart = false }: { lines?: number; chart?: boolean }) {
  const { t } = useI18n();
  return (
    <div className="skeleton-block" role="status" aria-live="polite">
      <span className="sr-only">{t("state.loading")}</span>
      {chart && <Skeleton height={180} radius={8} />}
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={`${90 - i * 14}%`} />
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Hata
export function errorMessage(error: unknown, t: TFunc): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return t("state.errorNetwork");
    if (error.detail) return error.detail;
    return t("state.errorStatus", { status: error.status });
  }
  if (error instanceof Error && error.message) return error.message;
  return t("state.errorNetwork");
}

export function ErrorCard({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
  const { t } = useI18n();
  return (
    <div className="state-card state-error" role="alert">
      <LuCircleAlert className="state-icon" aria-hidden="true" />
      <div className="state-body">
        <p className="state-title">{title ?? t("state.errorTitle")}</p>
        <p className="state-text">{errorMessage(error, t)}</p>
      </div>
      {onRetry && (
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          <LuRefreshCw aria-hidden="true" />
          {t("state.retry")}
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
  icon,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="state-card state-empty">
      {icon && <span className="state-icon">{icon}</span>}
      <div className="state-body">
        <p className="state-title">{title}</p>
        {children && <div className="state-text">{children}</div>}
      </div>
      {action}
    </div>
  );
}

// ------------------------------------------------------------------ Uyarı (NFR-12)
export function Disclaimer({ text }: { text?: string | null }) {
  const { t, lang } = useI18n();
  // Sunucu metni Türkçedir; İngilizce arayüzde sabit çeviri gösterilir.
  const body = lang === "tr" && text ? text : t("disclaimer.full");
  return (
    <aside className="disclaimer" aria-label={t("disclaimer.short")}>
      <LuInfo aria-hidden="true" />
      <p>
        <strong>{t("disclaimer.short")}</strong> {body}
      </p>
    </aside>
  );
}

// ------------------------------------------------------------------ İpucu
// Balon `position: fixed` ile yerleşir; böylece kaydırılabilir tablo kapsayıcıları
// (overflow) tarafından kırpılmaz. Fare ve klavye odağıyla açılır.
type TipAlign = "center" | "start" | "end";
const TIP_W = 280;

interface TipPos {
  left: number;
  top: number;
  below: boolean;
  shift: string;
}

export function Tip({
  content,
  children,
  align = "center",
  className,
}: {
  content: ReactNode;
  children: ReactNode;
  align?: TipAlign;
  className?: string;
}) {
  const id = useId();
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<TipPos | null>(null);

  const show = () => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const below = r.top < 140;
    const top = below ? r.bottom + 8 : r.top - 8;
    const margin = 12;
    let left: number;
    let shift: string;
    if (align === "start") {
      left = Math.max(margin, Math.min(r.left, vw - TIP_W - margin));
      shift = "0";
    } else if (align === "end") {
      left = Math.min(vw - margin, Math.max(r.right, TIP_W + margin));
      shift = "-100%";
    } else {
      left = Math.max(TIP_W / 2 + margin, Math.min(r.left + r.width / 2, vw - TIP_W / 2 - margin));
      shift = "-50%";
    }
    setPos({ left, top, below, shift });
  };
  const hide = () => setPos(null);

  useEffect(() => {
    if (!pos) return undefined;
    window.addEventListener("scroll", hide, { passive: true, capture: true });
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, { capture: true });
      window.removeEventListener("resize", hide);
    };
  }, [pos]);

  return (
    <span className={cx("tip", className)} onMouseEnter={show} onMouseLeave={hide}>
      <span
        ref={triggerRef}
        className="tip-trigger"
        tabIndex={0}
        aria-describedby={id}
        onFocus={show}
        onBlur={hide}
        onKeyDown={(e) => {
          if (e.key === "Escape") hide();
        }}
      >
        {children}
      </span>
      <span
        role="tooltip"
        id={id}
        className={cx("tip-bubble", pos && "is-open", pos?.below && "is-below")}
        style={pos ? { left: pos.left, top: pos.top, transform: `translate(${pos.shift}, ${pos.below ? "0" : "-100%"})` } : undefined}
      >
        {content}
      </span>
    </span>
  );
}

export function InfoTip({ content, label, align }: { content: ReactNode; label: string; align?: TipAlign }) {
  return (
    <Tip content={content} align={align} className="info-tip">
      <LuInfo aria-label={label} role="img" />
    </Tip>
  );
}

// ------------------------------------------------------------------ Kaynak çipi
export function shortAccession(acc: string): string {
  const parts = acc.split("-");
  return parts.length === 3 ? `${parts[0]}-${parts[1]}-…` : acc;
}

export function SourceChip({ source, cik, align }: { source: Source | null; cik: string; align?: TipAlign }) {
  const { t } = useI18n();
  if (!source) return null;
  const url = source.url || edgarFilingUrl(cik, source.accession_no);
  return (
    <span className="source-chip-wrap">
      <Tip
        align={align}
        content={
          <span className="tip-rows">
            <span>
              <b>XBRL</b> {source.xbrl_tag}
            </span>
            {source.derived_from && source.derived_from.length > 0 && (
              <span>{t("fin.derived", { tags: source.derived_from.join(", ") })}</span>
            )}
            <span>
              <b>{t("filings.accession")}</b> {source.accession_no}
            </span>
            <span>
              <b>{t("filings.filed")}</b> {fmtDate(source.filed)}
            </span>
          </span>
        }
      >
        <span className="source-chip">
          {source.form} · {source.filed} · {shortAccession(source.accession_no)}
        </span>
      </Tip>
      <a className="icon-link" href={url} target="_blank" rel="noreferrer" aria-label={t("common.openEdgar", { form: source.form })}>
        <LuExternalLink aria-hidden="true" />
      </a>
    </span>
  );
}

// ------------------------------------------------------------------ YoY rozeti
export function DeltaBadge({
  value,
  higherIsBetter = true,
  label,
  formatted,
}: {
  value: number | null;
  higherIsBetter?: boolean;
  label?: string;
  formatted?: string;
}) {
  if (value === null || !Number.isFinite(value)) {
    return <span className="delta delta-muted">—</span>;
  }
  const up = value > 0;
  const flat = value === 0;
  const good = flat ? null : up === higherIsBetter;
  const Icon = flat ? LuMinus : up ? LuArrowUpRight : LuArrowDownRight;
  return (
    <span className={cx("delta", good === null ? "delta-muted" : good ? "delta-good" : "delta-bad")} title={label}>
      <Icon aria-hidden="true" />
      <span>{formatted ?? fmtSignedPercent(value)}</span>
    </span>
  );
}

// ------------------------------------------------------------------ Segment seçici
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ value: T; label: ReactNode; count?: number }>;
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={cx("seg-btn", o.value === value && "is-active")}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count !== undefined && <span className="seg-count">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Form rozeti
export function FormBadge({ form }: { form: string }) {
  const kind = form.startsWith("10-K") ? "k" : form.startsWith("10-Q") ? "q" : form.startsWith("8-K") ? "e" : "o";
  return <span className={cx("form-badge", `form-${kind}`)}>{form}</span>;
}
