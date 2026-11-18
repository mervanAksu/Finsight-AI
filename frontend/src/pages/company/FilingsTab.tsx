// Raporlar sekmesi: başvuru tablosu, form filtresi, 8-K kalem rozetleri, EDGAR bağlantısı (FR-02).
import { useState } from "react";
import { LuCheck, LuExternalLink } from "react-icons/lu";
import { api } from "../../api";
import { Disclaimer, ErrorCard, FormBadge, Panel, Segmented, SkeletonBlock } from "../../components/ui";
import { fmtDate } from "../../format";
import { useI18n } from "../../i18n";
import { useAsync } from "../../lib/useAsync";

type FormFilter = "" | "10-K" | "10-Q" | "8-K";

export function FilingsTab({ ticker }: { ticker: string }) {
  const { t } = useI18n();
  const [form, setForm] = useState<FormFilter>("");
  const { state, reload } = useAsync((signal) => api.filings(ticker, form || undefined, 50, signal), [ticker, form]);

  const options: Array<{ value: FormFilter; label: string }> = [
    { value: "", label: t("filings.all") },
    { value: "10-K", label: "10-K" },
    { value: "10-Q", label: "10-Q" },
    { value: "8-K", label: "8-K" },
  ];

  return (
    <div className="grid-12">
      <Panel
        className="span-12"
        title={t("filings.title")}
        subtitle={state.status === "success" ? t("filings.count", { n: state.data.filings.length }) : undefined}
        actions={<Segmented options={options} value={form} onChange={setForm} label={t("filings.filterLabel")} />}
      >
        {state.status === "loading" && <SkeletonBlock lines={6} />}
        {state.status === "error" && <ErrorCard error={state.error} onRetry={reload} />}
        {state.status === "success" && state.data.filings.length === 0 && <p className="muted-note">{t("filings.empty")}</p>}
        {state.status === "success" && state.data.filings.length > 0 && (
          <div className="table-wrap">
            <table className="data-table filings-table">
              <thead>
                <tr>
                  <th scope="col">{t("filings.form")}</th>
                  <th scope="col">{t("filings.filed")}</th>
                  <th scope="col">{t("filings.period")}</th>
                  <th scope="col">{t("filings.items")}</th>
                  <th scope="col">{t("filings.accession")}</th>
                  <th scope="col" className="center">
                    {t("filings.analyzed")}
                  </th>
                  <th scope="col" className="num">
                    <span className="sr-only">{t("filings.link")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.data.filings.map((f) => (
                  <tr key={f.accession_no}>
                    <td>
                      <FormBadge form={f.form} />
                    </td>
                    <td className="nowrap">{fmtDate(f.filing_date)}</td>
                    <td className="nowrap">{fmtDate(f.report_date)}</td>
                    <td>
                      {f.items.length > 0 ? (
                        <span className="item-badges">
                          {f.items.map((code, i) => (
                            <span key={code} className="item-badge" title={f.item_labels[i] ?? code}>
                              <b>{code}</b> {f.item_labels[i] ?? ""}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="mono-num nowrap">{f.accession_no}</td>
                    <td className="center">
                      {f.analyzed ? (
                        <span className="ok-mark">
                          <LuCheck aria-hidden="true" />
                          {t("filings.processed")}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="num">
                      <a
                        className="icon-link"
                        href={f.url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={t("common.openEdgar", { form: f.form })}
                      >
                        <LuExternalLink aria-hidden="true" />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <div className="span-12">
        <Disclaimer text={state.status === "success" ? state.data.disclaimer : undefined} />
      </div>
    </div>
  );
}
