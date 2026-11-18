// Uygulama kabuğu: dil ve tema durumu, yönlendirme, başlık ve alt bilgi.
import { useEffect, useMemo, useState } from "react";
import { Footer, Header } from "./components/Layout";
import type { Theme } from "./components/Layout";
import { setFormatLang } from "./format";
import { I18nContext, translate } from "./i18n";
import type { I18nValue, Lang } from "./i18n";
import { readStorage, writeStorage } from "./lib/storage";
import { ComparePage } from "./pages/ComparePage";
import { CompanyPage } from "./pages/CompanyPage";
import { HomePage } from "./pages/HomePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { parseHash, useHash } from "./router";
import type { Route } from "./router";

const THEME_KEY = "finsight.theme";
const LANG_KEY = "finsight.lang";

function initialTheme(): Theme {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "dark" || attr === "light") return attr;
  const stored = readStorage(THEME_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function initialLang(): Lang {
  return readStorage(LANG_KEY) === "en" ? "en" : "tr";
}

function routeKey(route: Route): string {
  switch (route.name) {
    case "company":
      return `company:${route.ticker}`;
    case "compare":
      return "compare";
    default:
      return route.name;
  }
}

export function App() {
  const hash = useHash();
  const route = parseHash(hash);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [lang, setLangState] = useState<Lang>(initialLang);

  // Biçimlendirme dili, alt bileşenler render edilmeden önce ayarlanır.
  setFormatLang(lang);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    writeStorage(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute("lang", lang);
    writeStorage(LANG_KEY, lang);
  }, [lang]);

  // Sayfa (sekme değil) değişince başa kaydır.
  const pageKey = routeKey(route);
  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [pageKey]);

  useEffect(() => {
    const titles: Record<Route["name"], string> = {
      home: "FinSight AI",
      company: route.name === "company" ? `${route.ticker} · FinSight AI` : "FinSight AI",
      compare: `${translate(lang, "compare.title")} · FinSight AI`,
      notFound: `${translate(lang, "state.notFoundTitle")} · FinSight AI`,
    };
    document.title = titles[route.name];
  }, [route, lang]);

  const i18n = useMemo<I18nValue>(
    () => ({ lang, setLang: setLangState, t: (key, vars) => translate(lang, key, vars) }),
    [lang],
  );

  return (
    <I18nContext.Provider value={i18n}>
      <a className="skip-link" href="#main" onClick={(e) => {
        e.preventDefault();
        document.getElementById("main")?.focus();
      }}>
        {i18n.t("nav.skip")}
      </a>
      <Header route={route} theme={theme} onToggleTheme={() => setTheme((th) => (th === "dark" ? "light" : "dark"))} />
      <main id="main" className="main" tabIndex={-1}>
        {route.name === "home" && <HomePage />}
        {route.name === "company" && <CompanyPage key={route.ticker} ticker={route.ticker} tab={route.tab} />}
        {route.name === "compare" && <ComparePage tickers={route.tickers} />}
        {route.name === "notFound" && <NotFoundPage />}
      </main>
      <Footer />
    </I18nContext.Provider>
  );
}
