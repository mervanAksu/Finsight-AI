// Üst başlık (logo, arama, karşılaştır, dil, tema) ve alt bilgi (uyarı + modeller).
import { LuGitCompareArrows, LuMoon, LuSun } from "react-icons/lu";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { Lang } from "../i18n";
import { useAsync } from "../lib/useAsync";
import { hrefFor, navigate } from "../router";
import type { Route } from "../router";
import { CompanySearch } from "./CompanySearch";
import { cx } from "./ui";

export type Theme = "light" | "dark";

/** Marka işareti: iki defter satırı ve muhasebedeki "toplam" çift çizgisi. */
export function Logo() {
  return (
    <svg className="logo-mark" width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <rect x="0.75" y="0.75" width="20.5" height="20.5" rx="2" className="logo-frame" />
      <path d="M5 7H17M5 11H13M5 15.2H17M5 17.6H17" className="logo-lines" />
    </svg>
  );
}

export function Header({
  route,
  theme,
  onToggleTheme,
}: {
  route: Route;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  const { t, lang, setLang } = useI18n();
  const langs: Lang[] = ["tr", "en"];
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="brand" href={hrefFor({ name: "home" })} aria-label={`${t("app.name")}, ${t("nav.home")}`}>
          <Logo />
          <span className="brand-name">
            FinSight<span className="brand-ai">AI</span>
          </span>
        </a>
        {route.name !== "home" && (
          <div className="topbar-search">
            <CompanySearch
              label={t("home.searchLabel")}
              onSelect={(r) => navigate({ name: "company", ticker: r.ticker, tab: "overview" })}
            />
          </div>
        )}
        <nav className="topbar-nav" aria-label={t("nav.main")}>
          <a
            className={cx("nav-link", route.name === "compare" && "is-active")}
            href={hrefFor({ name: "compare", tickers: [] })}
            aria-current={route.name === "compare" ? "page" : undefined}
          >
            <LuGitCompareArrows aria-hidden="true" />
            <span>{t("nav.compare")}</span>
          </a>
          <div className="lang-switch" role="group" aria-label={t("header.language")}>
            {langs.map((l) => (
              <button
                key={l}
                type="button"
                className={cx("lang-btn", lang === l && "is-active")}
                aria-pressed={lang === l}
                onClick={() => setLang(l)}
                lang={l}
              >
                {l.toUpperCase()}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={onToggleTheme}
            aria-label={theme === "dark" ? t("header.themeToLight") : t("header.themeToDark")}
            title={theme === "dark" ? t("header.themeToLight") : t("header.themeToDark")}
          >
            {theme === "dark" ? <LuSun aria-hidden="true" /> : <LuMoon aria-hidden="true" />}
          </button>
        </nav>
      </div>
    </header>
  );
}

export function Footer() {
  const { t } = useI18n();
  const { state } = useAsync((signal) => api.health(signal), []);
  return (
    <footer className="footer">
      <div className="footer-inner">
        <p className="footer-disclaimer">
          <strong>{t("disclaimer.short")}</strong> {t("disclaimer.full")}
        </p>
        <dl className="footer-meta">
          <div>
            <dt>{t("common.source")}</dt>
            <dd>SEC EDGAR</dd>
          </div>
          {state.status === "success" && (
            <div>
              <dt>{t("footer.models")}</dt>
              <dd>
                {t("footer.sentiment")} {state.data.models.sentiment}; {t("footer.embedding")}{" "}
                {state.data.models.embedding.split("/").pop()}; {t("footer.llm")}{" "}
                {state.data.models.llm.enabled ? state.data.models.llm.model : t("footer.llmOff")}
              </dd>
            </div>
          )}
          {state.status === "error" && (
            <div>
              <dt>API</dt>
              <dd className="footer-warn">{t("footer.apiDown")}</dd>
            </div>
          )}
          <div>
            <dt>{t("footer.project")}</dt>
            <dd>v{state.status === "success" ? state.data.version : "—"}</dd>
          </div>
        </dl>
      </div>
    </footer>
  );
}
