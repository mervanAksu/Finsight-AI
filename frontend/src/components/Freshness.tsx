// Tazelik (Aşama 4 §4, FR-02/FR-15): SEC'te analizden sonra yeni 10-K/10-Q yayımlandı mı?
// - stale true  → şirket sayfasında "Yeni rapor yayımlandı" bandı + Güncelle (POST /ingest, artımlı)
// - stale false → başlıkta "SEC ile güncel"
// - stale null  → başlıkta "SEC kontrol edilemedi" + nedeni (error) + yeniden kontrol
// Eski sunucuda uç nokta yoksa (404) hiçbir şey gösterilmez.
import { useState } from "react";
import { LuCircleCheck, LuCircleAlert, LuFileClock, LuRefreshCw } from "react-icons/lu";
import { ApiError } from "../api";
import { fmtDate, fmtTime } from "../format";
import { useI18n } from "../i18n";
import type { AsyncState } from "../lib/useAsync";
import type { FilingBrief, Freshness } from "../types";
import { IngestPanel } from "./IngestPanel";
import { Tip, cx, errorMessage } from "./ui";

/** /freshness yanıtı; null = uç nokta yok (eski sunucu). */
export type FreshnessState = AsyncState<Freshness | null>;

function briefText(f: FilingBrief): string {
  return `${f.form} (${fmtDate(f.filing_date)})`;
}

/** Şirket başlığındaki kısa durum satırı. */
export function FreshnessStatus({ state, onRetry }: { state: FreshnessState; onRetry: () => void }) {
  const { t } = useI18n();
  if (state.status === "success" && state.data === null) return null;
  let tone = "muted";
  let icon = <LuFileClock aria-hidden="true" />;
  let text = t("fresh.checking");
  let detail: string | null = null;
  let retry = false;
  if (state.status === "error") {
    tone = "warning";
    icon = <LuCircleAlert aria-hidden="true" />;
    text = t("fresh.unknown");
    detail = errorMessage(state.error, t);
    retry = true;
  } else if (state.status === "success" && state.data) {
    const f = state.data;
    if (f.stale === null) {
      tone = "warning";
      icon = <LuCircleAlert aria-hidden="true" />;
      text = t("fresh.unknown");
      detail = f.error;
      retry = true;
    } else if (f.stale) {
      tone = "warning";
      icon = <LuFileClock aria-hidden="true" />;
      text = t("fresh.stale");
    } else {
      tone = "positive";
      icon = <LuCircleCheck aria-hidden="true" />;
      text = t("fresh.current");
    }
  }
  const f = state.status === "success" ? state.data : null;
  const tipBody = (
    <span className="src-tip">
      {detail && <span className="src-tip-note">{detail}</span>}
      {f && (
        <span className="src-tip-grid">
          {f.sec_latest && (
            <>
              <span>{t("fresh.secLatest")}</span>
              <span>{briefText(f.sec_latest)}</span>
            </>
          )}
          {f.analyzed_latest && (
            <>
              <span>{t("fresh.analyzedLatest")}</span>
              <span>{briefText(f.analyzed_latest)}</span>
            </>
          )}
          <span>{t("fresh.label")}</span>
          <span>{t("fresh.checkedAt", { time: `${fmtDate(f.checked_at)} ${fmtTime(f.checked_at)}` })}</span>
        </span>
      )}
    </span>
  );
  return (
    <span className={cx("fresh-status", `tone-text-${tone}`)} aria-live="polite">
      {state.status === "loading" ? (
        <span className="fresh-status-text">
          {icon}
          {text}
        </span>
      ) : (
        <Tip content={tipBody} align="start">
          <span className="fresh-status-text">
            {icon}
            {text}
          </span>
        </Tip>
      )}
      {retry && (
        <button type="button" className="text-btn" onClick={onRetry}>
          <LuRefreshCw aria-hidden="true" />
          {t("fresh.retry")}
        </button>
      )}
    </span>
  );
}

/** stale = true iken şirket başlığının altındaki bant; Güncelle artımlı analizi başlatır. */
export function FreshnessBand({
  state,
  ticker,
  name,
  onUpdated,
}: {
  state: FreshnessState;
  ticker: string;
  name: string;
  onUpdated: () => void;
}) {
  const { t } = useI18n();
  const [running, setRunning] = useState(false);
  if (state.status !== "success" || !state.data || state.data.stale !== true) return null;
  const f = state.data;
  // 8-K'lar stale yapmaz; bantta yalnızca 10-K/10-Q listelenir.
  const periodic = f.new_filings.filter((x) => /^10-[KQ]/.test(x.form));
  const list = (periodic.length ? periodic : f.sec_latest ? [f.sec_latest] : []).map(briefText).join(", ");
  return (
    <section className="fresh-band" aria-label={t("fresh.stale")}>
      <div className="fresh-band-row">
        <LuFileClock className="fresh-band-icon" aria-hidden="true" />
        <div className="fresh-band-text">
          <p className="fresh-band-title">{t(periodic.length > 1 ? "fresh.newFilings" : "fresh.newFiling", { list })}</p>
          {f.analyzed_latest && (
            <p className="fresh-band-sub">
              {t("fresh.analyzedThrough", { form: f.analyzed_latest.form, date: fmtDate(f.analyzed_latest.filing_date) })}{" "}
              {t("fresh.updateHint")}
            </p>
          )}
        </div>
        {!running && (
          <button type="button" className="btn btn-primary" onClick={() => setRunning(true)}>
            <LuRefreshCw aria-hidden="true" />
            {t("fresh.update")}
          </button>
        )}
      </div>
      {running && (
        <IngestPanel
          className="fresh-ingest"
          ticker={ticker}
          name={name}
          heading={t("fresh.updating")}
          autoStart
          analyze
          onDone={() => {
            setRunning(false);
            onUpdated();
          }}
        />
      )}
    </section>
  );
}

/** 404 (uç nokta yok) → null; diğer hatalar fırlatılır. */
export function freshnessOrNull(p: Promise<Freshness>): Promise<Freshness | null> {
  return p.catch((err: unknown) => {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) return null;
    throw err;
  });
}
