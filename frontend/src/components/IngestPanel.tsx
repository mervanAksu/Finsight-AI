// Analiz başlatma ve ilerleme (FR-15): POST /ingest → /api/jobs/{id} yoklama.
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { LuCheck, LuCircleAlert, LuPlay, LuRefreshCw, LuTriangleAlert } from "react-icons/lu";
import { fmtNumber, fmtPercent } from "../format";
import { useI18n } from "../i18n";
import type { Job } from "../types";
import { ApiError, api } from "../api";
import { cx, errorHint, errorMessage, errorTitle } from "./ui";

const POLL_MS = 900;

type Phase =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "polling"; job: Job | null; jobId: number }
  | { kind: "done"; job: Job }
  | { kind: "error"; message: string; job: Job | null; title?: string; hint?: string | null; retryAt?: number | null };

export function IngestPanel({
  ticker,
  name,
  onDone,
  autoStart = false,
  analyze = true,
  heading,
  explain,
  startLabel,
  className,
}: {
  ticker: string;
  /** Şirket adı; bilinmiyorsa yalnızca ticker gösterilir. */
  name?: string | null;
  onDone: () => void;
  autoStart?: boolean;
  /** POST /ingest gövdesi: {"analyze": true} = finansal veri + metin analizi. */
  analyze?: boolean;
  /** Başlık satırı (varsayılan: ticker + ad). */
  heading?: ReactNode;
  /** Başlamadan önceki açıklama (varsayılan: ingest.explain). */
  explain?: ReactNode;
  /** Başlat düğmesi metni (varsayılan: "Analiz et"). */
  startLabel?: string;
  className?: string;
}) {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  // v1.5: 429 / 503 / 403 yanıtlarında sunucunun `detail` metni + ne yapılacağı + bekleme süresi.
  const failure = (err: unknown): Phase => ({
    kind: "error",
    message: errorMessage(err, t),
    job: null,
    title: errorTitle(err, t, t("ingest.failed")),
    hint: errorHint(err, t),
    retryAt: err instanceof ApiError && err.retryAfter !== null ? Date.now() + err.retryAfter * 1000 : null,
  });

  const [now, setNow] = useState(() => Date.now());
  const retryAt = phase.kind === "error" ? (phase.retryAt ?? null) : null;
  useEffect(() => {
    if (retryAt === null) return undefined;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      if (Date.now() >= retryAt) window.clearInterval(timer);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [retryAt]);
  const waitSec = retryAt !== null ? Math.max(0, Math.ceil((retryAt - now) / 1000)) : 0;

  const start = () => {
    setPhase({ kind: "starting" });
    api.ingest(ticker, analyze).then(
      (res) => setPhase({ kind: "polling", job: null, jobId: res.job_id }),
      (err: unknown) => setPhase(failure(err)),
    );
  };

  useEffect(() => {
    if (autoStart) start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const jobId = phase.kind === "polling" ? phase.jobId : null;
  useEffect(() => {
    if (jobId === null) return undefined;
    let cancelled = false;
    let timer = 0;
    const controller = new AbortController();
    const tick = () => {
      api.job(jobId, controller.signal).then(
        (job) => {
          if (cancelled) return;
          if (job.status === "done") {
            setPhase({ kind: "done", job });
            window.setTimeout(() => onDoneRef.current(), 700);
          } else if (job.status === "error") {
            setPhase({ kind: "error", message: job.error ?? t("ingest.failed"), job });
          } else {
            setPhase({ kind: "polling", job, jobId });
            timer = window.setTimeout(tick, POLL_MS);
          }
        },
        (err: unknown) => {
          if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
          setPhase(failure(err));
        },
      );
    };
    tick();
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [jobId, t]);

  const job = phase.kind === "polling" || phase.kind === "done" || phase.kind === "error" ? phase.job : null;
  const progress = phase.kind === "done" ? 1 : (job?.progress ?? 0);
  const busy = phase.kind === "starting" || phase.kind === "polling";
  const statusText =
    phase.kind === "done"
      ? t("ingest.done")
      : phase.kind === "error"
        ? (phase.title ?? t("ingest.failed"))
        : job?.status === "queued" || phase.kind === "starting"
          ? t("ingest.queued")
          : t("ingest.running");

  const warnings = job?.warnings ?? [];

  return (
    <div className={cx("ingest", phase.kind !== "idle" && "is-active", className)}>
      <div className="ingest-head">
        <div>
          <p className="ingest-name">
            {heading ?? (
              <>
                <span className="ticker">{ticker}</span> {name ?? ""}
              </>
            )}
          </p>
          {phase.kind === "idle" && <p className="ingest-explain">{explain ?? t("ingest.explain")}</p>}
        </div>
        {phase.kind === "idle" && (
          <button type="button" className="btn btn-primary" onClick={start}>
            <LuPlay aria-hidden="true" />
            {startLabel ?? t("ingest.start")}
          </button>
        )}
        {phase.kind === "error" && (
          <button type="button" className="btn btn-secondary" onClick={start} disabled={waitSec > 0}>
            <LuRefreshCw aria-hidden="true" />
            {waitSec > 0 ? t("ingest.retryIn", { sec: waitSec }) : t("ingest.retry")}
          </button>
        )}
      </div>

      {phase.kind !== "idle" && (
        <div className="ingest-progress" aria-live="polite">
          <div className="ingest-status">
            <span className={cx("ingest-state", `is-${phase.kind}`)}>
              {phase.kind === "done" && <LuCheck aria-hidden="true" />}
              {phase.kind === "error" && <LuCircleAlert aria-hidden="true" />}
              {busy && <span className="pulse-dot" aria-hidden="true" />}
              {statusText}
            </span>
            <span className="ingest-pct">{fmtPercent(progress, 0)}</span>
          </div>
          <div
            className="progress"
            role="progressbar"
            aria-label={t("ingest.progressLabel")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress * 100)}
          >
            <span
              className={cx("progress-fill", phase.kind === "error" && "is-error", busy && "is-busy")}
              style={{ width: `${Math.max(2, progress * 100)}%` }}
            />
          </div>
          <div className="ingest-meta">
            <span>{phase.kind === "error" ? phase.message : (job?.step ?? "…")}</span>
            {job && job.request_count > 0 && <span>{t("ingest.requests", { n: fmtNumber(job.request_count) })}</span>}
          </div>
          {phase.kind === "error" && phase.hint && <p className="state-hint ingest-hint">{phase.hint}</p>}
          {warnings.length > 0 && (
            <ul className="ingest-warnings" aria-label={t("ingest.warnings")}>
              {warnings.map((w) => (
                <li key={w}>
                  <LuTriangleAlert aria-hidden="true" />
                  <span>{w}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
