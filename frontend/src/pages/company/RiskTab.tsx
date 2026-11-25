// Risk değişimi sekmesi (FR-10): iki son 10-K'nın risk faktörleri karşılaştırması —
// eklenen, değişen, çıkarılan paragraflar ve kelime düzeyinde fark vurgusu.
import { useState } from "react";
import { LuMinus, LuPencil, LuPlus } from "react-icons/lu";
import { api } from "../../api";
import { Disclaimer, EmptyState, ErrorCard, Panel, Segmented, SkeletonBlock, cx } from "../../components/ui";
import { fmtDate, fmtNumber, fmtPercent } from "../../format";
import { useI18n } from "../../i18n";
import { wordDiff } from "../../lib/diff";
import { useAsync } from "../../lib/useAsync";
import type { ChangeType, RiskChange, RiskChangesResponse } from "../../types";
import { RiskCounts } from "./OverviewTab";

type Filter = "all" | ChangeType;

export function RiskTab({ ticker }: { ticker: string }) {
  const { state, reload } = useAsync((signal) => api.riskChanges(ticker, signal), [ticker]);
  if (state.status === "loading") {
    return (
      <Panel>
        <SkeletonBlock lines={8} />
      </Panel>
    );
  }
  if (state.status === "error") return <ErrorCard error={state.error} onRetry={reload} />;
  return (
    <div className="grid-12">
      <ChangesPanel data={state.data} />
      <div className="span-12">
        <Disclaimer text={state.data.disclaimer} />
      </div>
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
      <RiskCounts counts={c} />
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
