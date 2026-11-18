// Risk ve Ton sekmesi: bölüm bazında duygu dağılımı (FR-09), öne çıkan cümleler ve
// risk faktörü değişiklikleri (FR-10) — kelime düzeyinde fark vurgusu ile.
import { useState } from "react";
import { LuMinus, LuPencil, LuPlus } from "react-icons/lu";
import { api } from "../../api";
import { ToneBar } from "../../charts/small";
import { Disclaimer, EmptyState, ErrorCard, FormBadge, Panel, Segmented, SkeletonBlock, cx } from "../../components/ui";
import { fmtDate, fmtNumber, fmtPercent, fmtTone } from "../../format";
import { sectionLabel, useI18n } from "../../i18n";
import { wordDiff } from "../../lib/diff";
import { useAsync } from "../../lib/useAsync";
import type { ChangeType, RiskChange, RiskChangesResponse, SentimentResponse, SentimentSection } from "../../types";

type Filter = "all" | ChangeType;

const sectionId = (s: SentimentSection) => `${s.filing.accession_no}:${s.item_code}`;

export function RiskTab({ ticker }: { ticker: string }) {
  const { state, reload } = useAsync(
    (signal) => Promise.all([api.sentiment(ticker, signal), api.riskChanges(ticker, signal)]),
    [ticker],
  );
  if (state.status === "loading") {
    return (
      <div className="grid-12">
        <Panel className="span-12">
          <SkeletonBlock lines={5} />
        </Panel>
        <Panel className="span-12">
          <SkeletonBlock lines={6} />
        </Panel>
      </div>
    );
  }
  if (state.status === "error") return <ErrorCard error={state.error} onRetry={reload} />;
  const [sentiment, risk] = state.data;
  return (
    <div className="grid-12">
      <SentimentPanel data={sentiment} />
      <ChangesPanel data={risk} />
      <div className="span-12">
        <Disclaimer text={risk.disclaimer || sentiment.disclaimer} />
      </div>
    </div>
  );
}

function latestOf(sections: SentimentSection[], code: string): SentimentSection | undefined {
  const list = sections.filter((s) => s.item_code === code || (code === "7" && s.item_code === "2"));
  return list[list.length - 1];
}

function SentimentPanel({ data }: { data: SentimentResponse }) {
  const { t, lang } = useI18n();
  const sections = data.sections;
  const mdna = latestOf(sections, "7");
  const riskSec = latestOf(sections, "1A");
  const [selected, setSelected] = useState<string>(() => (mdna ? sectionId(mdna) : sections[0] ? sectionId(sections[0]) : ""));
  const current = sections.find((s) => sectionId(s) === selected) ?? sections[0];

  if (sections.length === 0) {
    return (
      <Panel className="span-12" title={t("risk.toneTitle")}>
        <p className="muted-note">{t("risk.noSentiment")}</p>
      </Panel>
    );
  }

  const title =
    mdna && riskSec
      ? t("risk.toneHeadline", { mdna: fmtTone(mdna.net_tone), risk: fmtTone(riskSec.net_tone) })
      : t("risk.toneTitle");

  // En yeni başvuru üstte olacak biçimde grupla.
  const groups = new Map<string, SentimentSection[]>();
  for (const s of sections) {
    const list = groups.get(s.filing.accession_no) ?? [];
    list.push(s);
    groups.set(s.filing.accession_no, list);
  }
  const ordered = Array.from(groups.values()).reverse();

  return (
    <>
      <Panel className="span-7" title={title} subtitle={t("risk.toneSub", { m: data.model })}>
        <div className="tone-legend" aria-hidden="true">
          <span>
            <i className="swatch seg-pos" />
            {t("risk.positive")}
          </span>
          <span>
            <i className="swatch seg-neu" />
            {t("risk.neutral")}
          </span>
          <span>
            <i className="swatch seg-neg" />
            {t("risk.negative")}
          </span>
        </div>
        <div className="tone-groups">
          {ordered.map((group) => (
            <div key={group[0].filing.accession_no} className="tone-group">
              <p className="tone-group-head">
                <FormBadge form={group[0].filing.form} />
                <span>{fmtDate(group[0].filing.filing_date)}</span>
                <span className="muted">
                  {t("filings.period")}: {fmtDate(group[0].filing.report_date)}
                </span>
              </p>
              {group.map((s) => {
                const id = sectionId(s);
                const net = s.net_tone ?? 0; // cümlesiz bölümde null
                const tone = net > 0.05 ? "positive" : net < -0.05 ? "negative" : "muted";
                return (
                  <button
                    key={id}
                    type="button"
                    className={cx("tone-row", id === sectionId(current) && "is-selected")}
                    aria-pressed={id === sectionId(current)}
                    onClick={() => setSelected(id)}
                  >
                    <span className="tone-row-title">
                      {sectionLabel(lang, s.item_code, s.title)}
                      <span className="muted"> · {t("risk.sentences", { n: fmtNumber(s.sentence_count) })}</span>
                    </span>
                    <ToneBar positive={s.positive} neutral={s.neutral} negative={s.negative} />
                    <span className={cx("tone-row-net", `tone-text-${tone}`)}>
                      <span className="sr-only">{t("risk.netTone")} </span>
                      {fmtTone(s.net_tone)}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        className="span-5"
        title={t("risk.sentencesTitle")}
        subtitle={`${current.filing.form} ${fmtDate(current.filing.filing_date)} · ${sectionLabel(lang, current.item_code, current.title)}`}
      >
        <SentenceList title={t("risk.mostNegative")} tone="negative" items={current.top_negative} />
        <SentenceList title={t("risk.mostPositive")} tone="positive" items={current.top_positive} />
      </Panel>
    </>
  );
}

function SentenceList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "positive" | "negative";
  items: Array<{ sentence: string; score: number }>;
}) {
  const { t } = useI18n();
  return (
    <div className={cx("sentences", `is-${tone}`)}>
      <h3 className="sub-title">{title}</h3>
      {items.length === 0 ? (
        <p className="muted-note">—</p>
      ) : (
        <ul>
          {items.map((it) => (
            <li key={it.sentence}>
              <blockquote lang="en">{it.sentence}</blockquote>
              <span className="sentence-score">{t("risk.prob", { v: fmtPercent(it.score, 0) })}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ChangesPanel({ data }: { data: RiskChangesResponse }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState<Filter>("all");

  if (!data.old_filing || !data.new_filing) {
    return (
      <Panel className="span-12" title={t("risk.changesTitle")}>
        <EmptyState title={t("risk.noChanges")}>{t("risk.noChangesBody")}</EmptyState>
      </Panel>
    );
  }
  const c = data.counts;
  const options: Array<{ value: Filter; label: string; count?: number }> = [
    { value: "all", label: t("risk.all"), count: c.added + c.modified + c.removed },
    { value: "added", label: t("overview.added"), count: c.added },
    { value: "modified", label: t("overview.modified"), count: c.modified },
    { value: "removed", label: t("overview.removed"), count: c.removed },
  ];
  const list = data.changes.filter((ch) => filter === "all" || ch.change_type === filter);

  return (
    <Panel
      className="span-12"
      title={t("risk.changesTitle")}
      subtitle={t("risk.changesBetween", {
        old: fmtDate(data.old_filing.filing_date),
        new: fmtDate(data.new_filing.filing_date),
        n: fmtNumber(c.unchanged),
      })}
      actions={<Segmented options={options} value={filter} onChange={setFilter} label={t("risk.filterLabel")} />}
    >
      {list.length === 0 ? (
        <p className="muted-note">{t("risk.noneOfType")}</p>
      ) : (
        <ol className="changes">
          {list.map((ch, i) => (
            <ChangeItem key={`${ch.change_type}-${i}`} change={ch} />
          ))}
        </ol>
      )}
      {list.some((ch) => ch.change_type === "modified") && <p className="panel-foot">{t("risk.diffLegend")}</p>}
    </Panel>
  );
}

function ChangeItem({ change }: { change: RiskChange }) {
  const { t } = useI18n();
  const typeLabel =
    change.change_type === "added" ? t("overview.added") : change.change_type === "removed" ? t("overview.removed") : t("overview.modified");
  const Icon = change.change_type === "added" ? LuPlus : change.change_type === "removed" ? LuMinus : LuPencil;

  return (
    <li className={cx("change", `is-${change.change_type}`)}>
      <div className="change-head">
        <span className={cx("change-type", `ct-${change.change_type}`)}>
          <Icon aria-hidden="true" />
          {typeLabel}
        </span>
        {change.similarity !== null && (
          <span className="change-sim">
            {t("risk.similarity", { pct: fmtPercent(change.similarity, 0) })}
            <span className="sim-bar" aria-hidden="true">
              <span style={{ width: `${change.similarity * 100}%` }} />
            </span>
          </span>
        )}
      </div>
      {change.change_type === "modified" && change.old_text && change.new_text ? (
        <DiffView oldText={change.old_text} newText={change.new_text} />
      ) : (
        <p className="change-text" lang="en">
          {change.new_text ?? change.old_text}
        </p>
      )}
    </li>
  );
}

/** Vurguyu yalnızca kelimelere uygular; sondaki boşluk vurgunun dışında kalır. */
function Mark({ text, kind }: { text: string; kind: "add" | "del" }) {
  const m = /^([\s\S]*?)(\s*)$/.exec(text);
  const body = m ? m[1] : text;
  const tail = m ? m[2] : "";
  return (
    <>
      {kind === "del" ? <del className="w-del">{body}</del> : <ins className="w-add">{body}</ins>}
      {tail}
    </>
  );
}

function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const { t } = useI18n();
  const ops = wordDiff(oldText, newText);
  return (
    <div className="diff">
      <div className="diff-col">
        <p className="diff-label">{t("risk.before")}</p>
        <p className="change-text" lang="en">
          {ops
            ? ops
                .filter((o) => o.type !== "add")
                .map((o, i) =>
                  o.type === "del" ? <Mark key={i} text={o.text} kind="del" /> : <span key={i}>{o.oldText ?? o.text}</span>,
                )
            : oldText}
        </p>
      </div>
      <div className="diff-col">
        <p className="diff-label">{t("risk.after")}</p>
        <p className="change-text" lang="en">
          {ops
            ? ops
                .filter((o) => o.type !== "del")
                .map((o, i) => (o.type === "add" ? <Mark key={i} text={o.text} kind="add" /> : <span key={i}>{o.text}</span>))
            : newText}
        </p>
      </div>
    </div>
  );
}
