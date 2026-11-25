// Metin tonu sekmesi (FR-09): bölüm bazında duygu dağılımı ve öne çıkan cümleler.
import { useState } from "react";
import { api } from "../../api";
import { ToneBar } from "../../charts/small";
import { Disclaimer, ErrorCard, FormBadge, Panel, SkeletonBlock, cx } from "../../components/ui";
import { fmtDate, fmtNumber, fmtPercent, fmtTone } from "../../format";
import { sectionLabel, useI18n } from "../../i18n";
import { useAsync } from "../../lib/useAsync";
import type { SentimentResponse, SentimentSection } from "../../types";

const sectionId = (s: SentimentSection) => `${s.filing.accession_no}:${s.item_code}`;

export function ToneTab({ ticker }: { ticker: string }) {
  const { state, reload } = useAsync((signal) => api.sentiment(ticker, signal), [ticker]);
  if (state.status === "loading") {
    return (
      <div className="grid-12">
        <Panel className="span-7">
          <SkeletonBlock lines={6} />
        </Panel>
        <Panel className="span-5">
          <SkeletonBlock lines={6} />
        </Panel>
      </div>
    );
  }
  if (state.status === "error") return <ErrorCard error={state.error} onRetry={reload} />;
  return (
    <div className="grid-12">
      <SentimentPanel data={state.data} />
      <div className="span-12">
        <Disclaimer text={state.data.disclaimer} />
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

