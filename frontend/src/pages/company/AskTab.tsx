// Soru-Cevap sekmesi (FR-12, NFR-07): kaynak gösteren RAG cevapları.
// Cevaptaki [n] atıfları tıklanabilir rozettir; ilgili kaynak kartını vurgular.
import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import {
  LuCircleAlert,
  LuExternalLink,
  LuInfo,
  LuSearchX,
  LuSendHorizontal,
  LuShieldAlert,
  LuTrash2,
  LuTriangleAlert,
} from "react-icons/lu";
import { api } from "../../api";
import { Disclaimer, Panel, cx, errorMessage } from "../../components/ui";
import { fmtDate, fmtNumber, fmtPercent } from "../../format";
import { sectionLabel, useI18n } from "../../i18n";
import type { AskResponse, AskSource } from "../../types";

const MAX_LEN = 500;

type Turn =
  | { id: number; kind: "pending"; question: string }
  | { id: number; kind: "answer"; question: string; response: AskResponse }
  | { id: number; kind: "error"; question: string; message: string };

export function AskTab({ ticker, analyzed }: { ticker: string; analyzed: boolean }) {
  const { t } = useI18n();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const nextId = useRef(1);
  const endRef = useRef<HTMLDivElement | null>(null);
  const busy = turns.some((x) => x.kind === "pending");

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [turns]);

  const send = (question: string) => {
    const q = question.trim();
    if (!q || q.length > MAX_LEN || busy) return;
    const id = nextId.current++;
    setTurns((prev) => [...prev, { id, kind: "pending", question: q }]);
    setDraft("");
    api.ask(ticker, q).then(
      (response) =>
        setTurns((prev) => prev.map((x) => (x.id === id ? { id, kind: "answer", question: q, response } : x))),
      (err: unknown) =>
        setTurns((prev) =>
          prev.map((x) => (x.id === id ? { id, kind: "error", question: q, message: errorMessage(err, t) } : x)),
        ),
    );
  };

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    send(draft);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(draft);
    }
  };

  const examples = [t("ask.ex1"), t("ask.ex2"), t("ask.ex3")];
  const tooLong = draft.length > MAX_LEN;
  const lastDisclaimer = [...turns].reverse().find((x): x is Extract<Turn, { kind: "answer" }> => x.kind === "answer");

  return (
    <div className="grid-12">
      <Panel
        className="span-12 ask-panel"
        title={t("ask.title")}
        subtitle={t("ask.lead")}
        actions={
          turns.length > 0 ? (
            <button type="button" className="btn btn-ghost" onClick={() => setTurns([])} disabled={busy}>
              <LuTrash2 aria-hidden="true" />
              {t("ask.clear")}
            </button>
          ) : undefined
        }
      >
        {!analyzed && (
          <p className="msg-note is-warning ask-unanalyzed" role="note">
            <LuTriangleAlert aria-hidden="true" />
            <span>{t("ask.notAnalyzedNote")}</span>
          </p>
        )}
        <div className="chat" aria-live="polite">
          {turns.length === 0 && (
            <div className="chat-empty">
              <p className="sub-title">{t("ask.examples")}</p>
              <div className="chips">
                {examples.map((ex) => (
                  <button key={ex} type="button" className="chip-btn" onClick={() => send(ex)}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((turn) => (
            <div key={turn.id} className="turn">
              <div className="msg msg-user">
                <span className="msg-who">{t("ask.you")}</span>
                <p>{turn.question}</p>
              </div>
              {turn.kind === "pending" && (
                <div className="msg msg-bot is-pending" role="status">
                  <span className="msg-who">{t("ask.assistant")}</span>
                  <p className="thinking">
                    {t("ask.thinking")}
                    <span className="dots" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                  </p>
                </div>
              )}
              {turn.kind === "error" && (
                <div className="msg msg-bot is-error" role="alert">
                  <span className="msg-who">{t("ask.assistant")}</span>
                  <p>
                    <LuCircleAlert aria-hidden="true" /> {t("ask.error")}: {turn.message}
                  </p>
                </div>
              )}
              {turn.kind === "answer" && <Answer response={turn.response} turnId={turn.id} />}
            </div>
          ))}
          <div ref={endRef} />
        </div>

        <form className="ask-form" onSubmit={onSubmit}>
          <label className="sr-only" htmlFor="ask-input">
            {t("ask.inputLabel")}
          </label>
          <textarea
            id="ask-input"
            className="ask-input"
            rows={2}
            value={draft}
            placeholder={t("ask.placeholder")}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            aria-invalid={tooLong}
            aria-describedby="ask-hint"
          />
          <div className="ask-form-foot">
            <span id="ask-hint" className={cx("ask-hint", tooLong && "is-error")}>
              {tooLong ? t("ask.tooLong") : t("ask.hint")}
              <span className="ask-count">
                {fmtNumber(draft.length)}/{MAX_LEN}
              </span>
            </span>
            <button type="submit" className="btn btn-primary" disabled={busy || !draft.trim() || tooLong}>
              <LuSendHorizontal aria-hidden="true" />
              {t("ask.send")}
            </button>
          </div>
        </form>
      </Panel>
      <div className="span-12">
        <Disclaimer text={lastDisclaimer?.response.disclaimer} />
      </div>
    </div>
  );
}

function Answer({ response, turnId }: { response: AskResponse; turnId: number }) {
  const { t } = useI18n();
  const [highlight, setHighlight] = useState<number | null>(null);
  const sourceIds = new Set(response.sources.map((s) => s.n));
  const anchor = (n: number) => `src-${turnId}-${n}`;

  const focusSource = (n: number) => {
    setHighlight(n);
    const el = document.getElementById(anchor(n));
    if (el) {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      el.focus({ preventScroll: true });
    }
    window.setTimeout(() => setHighlight((h) => (h === n ? null : h)), 2200);
  };

  // "[1]" atıflarını rozete çevir; kaynağı olmayan numaralar düz metin kalır.
  const parts: ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(response.answer)) !== null) {
    const n = Number(m[1]);
    if (m.index > last) parts.push(response.answer.slice(last, m.index));
    if (sourceIds.has(n)) {
      parts.push(
        <button
          key={`c-${m.index}`}
          type="button"
          className="cite"
          onClick={() => focusSource(n)}
          aria-label={t("ask.citation", { n })}
        >
          {n}
        </button>,
      );
    } else {
      parts.push(m[0]);
    }
    last = m.index + m[0].length;
  }
  if (last < response.answer.length) parts.push(response.answer.slice(last));

  const notFound = response.mode === "not_found" || !response.found;

  return (
    <div className={cx("msg msg-bot", notFound && "is-notfound")}>
      <span className="msg-who">{t("ask.assistant")}</span>
      {response.advice_refused && (
        <p className="msg-note is-advice" role="note">
          <LuShieldAlert aria-hidden="true" />
          {t("ask.adviceNote")}
        </p>
      )}
      {response.warning && (
        <p className="msg-note is-warning" role="note">
          <LuTriangleAlert aria-hidden="true" />
          <span>
            <b>{t("ask.warningLabel")}:</b> {response.warning}
          </span>
        </p>
      )}
      {response.mode === "extractive" && (
        <p className="msg-note">
          <LuInfo aria-hidden="true" />
          {t("ask.extractiveNote")}
        </p>
      )}
      {notFound ? (
        <div className="notfound">
          <LuSearchX aria-hidden="true" />
          <div>
            <p className="notfound-title">{response.answer}</p>
            <p className="muted">{t("ask.notFoundNote")}</p>
          </div>
        </div>
      ) : (
        <p className={cx("answer", response.mode === "extractive" && "is-extractive")}>{parts}</p>
      )}
      {response.sources.length > 0 && (
        <div className="sources">
          <p className="sub-title">{t("ask.sources")}</p>
          <ol className="source-list">
            {response.sources.map((s) => (
              <SourceCard key={s.n} source={s} id={anchor(s.n)} active={highlight === s.n} />
            ))}
          </ol>
        </div>
      )}
      <p className="msg-meta">
        {response.model ?? response.mode} · {t("ask.latency", { sec: fmtNumber(response.latency_ms / 1000, 1) })}
      </p>
    </div>
  );
}

function SourceCard({ source: s, id, active }: { source: AskSource; id: string; active: boolean }) {
  const { t, lang } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const long = s.text.length > 340;
  return (
    <li id={id} tabIndex={-1} className={cx("source-card", active && "is-highlight")}>
      <div className="source-head">
        <span className="cite is-static" aria-hidden="true">
          {s.n}
        </span>
        <span className="source-title">
          {sectionLabel(lang, s.section, s.section_title)} <span className="muted">(Item {s.section})</span>
        </span>
        <span className="source-meta">
          {s.form} · {fmtDate(s.filing_date)}
        </span>
        <span className="source-sim">{t("ask.similarity", { pct: fmtPercent(s.similarity, 0) })}</span>
      </div>
      <blockquote className={cx("source-text", long && !expanded && "is-clamped")} lang="en">
        {s.text}
      </blockquote>
      <div className="source-foot">
        {long && (
          <button type="button" className="text-btn" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
            {expanded ? t("ask.showLess") : t("ask.showMore")}
          </button>
        )}
        <span className="source-acc">{s.accession_no}</span>
        <a className="text-link" href={s.url} target="_blank" rel="noreferrer">
          {t("ask.openEdgar")}
          <LuExternalLink aria-hidden="true" />
        </a>
      </div>
    </li>
  );
}
