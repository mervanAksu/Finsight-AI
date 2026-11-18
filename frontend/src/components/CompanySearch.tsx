// Şirket arama kutusu (FR-01): /api/companies/search ile otomatik tamamlama.
// WAI-ARIA combobox kalıbı: ok tuşlarıyla gezinme, Enter ile seçim, Escape ile kapatma.
import { useEffect, useId, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";
import { LuLoaderCircle, LuSearch, LuX } from "react-icons/lu";
import { api } from "../api";
import { useI18n } from "../i18n";
import type { SearchResult } from "../types";
import { cx } from "./ui";

interface CompanySearchProps {
  onSelect: (result: SearchResult) => void;
  size?: "large" | "compact";
  placeholder?: string;
  label: string;
  autoFocus?: boolean;
  /** Seçimden sonra kutu temizlensin mi */
  clearOnSelect?: boolean;
  exclude?: string[];
}

type Phase = "idle" | "loading" | "done" | "error";

export function CompanySearch({
  onSelect,
  size = "compact",
  placeholder,
  label,
  autoFocus = false,
  clearOnSelect = true,
  exclude = [],
}: CompanySearchProps) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const excludeKey = exclude.join(",");

  // Yazma bittikten 200 ms sonra ara; önceki isteği iptal et.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setPhase("idle");
      return undefined;
    }
    const controller = new AbortController();
    setPhase("loading");
    const timer = window.setTimeout(() => {
      api.search(q, 8, controller.signal).then(
        (res) => {
          const skip = new Set(excludeKey ? excludeKey.split(",") : []);
          setResults(res.filter((r) => !skip.has(r.ticker)));
          setPhase("done");
          setActiveIndex(-1);
        },
        (err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          setResults([]);
          setPhase("error");
        },
      );
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, excludeKey]);

  // Dışarı tıklanınca kapat.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && e.target instanceof Node && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const choose = (r: SearchResult) => {
    onSelect(r);
    setOpen(false);
    setActiveIndex(-1);
    if (clearOnSelect) setQuery("");
    else setQuery(`${r.ticker} · ${r.name}`);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActiveIndex((i) => Math.min(results.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      const pick = results[activeIndex >= 0 ? activeIndex : 0];
      if (pick && open) {
        e.preventDefault();
        choose(pick);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const showList = open && query.trim().length > 0;
  const activeId = activeIndex >= 0 ? `${listId}-opt-${activeIndex}` : undefined;

  return (
    <div className={cx("search", `search-${size}`)} ref={wrapRef}>
      <label className="sr-only" htmlFor={`${listId}-input`}>
        {label}
      </label>
      <LuSearch className="search-icon" aria-hidden="true" />
      <input
        ref={inputRef}
        id={`${listId}-input`}
        className="search-input"
        type="search"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeId}
        autoComplete="off"
        spellCheck={false}
        placeholder={placeholder ?? t("header.searchPlaceholder")}
        value={query}
        autoFocus={autoFocus}
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {phase === "loading" && <LuLoaderCircle className="search-spinner" aria-hidden="true" />}
      {query && phase !== "loading" && (
        <button
          type="button"
          className="search-clear"
          aria-label={t("search.clear")}
          onClick={() => {
            setQuery("");
            inputRef.current?.focus();
          }}
        >
          <LuX aria-hidden="true" />
        </button>
      )}
      {showList && (
        <div className="search-pop">
          <ul id={listId} role="listbox" aria-label={t("search.results")} className="search-list">
            {results.map((r, i) => (
              <li
                key={r.ticker}
                id={`${listId}-opt-${i}`}
                role="option"
                aria-selected={i === activeIndex}
                className={cx("search-option", i === activeIndex && "is-active")}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => choose(r)}
              >
                <span className="opt-ticker">{r.ticker}</span>
                <span className="opt-name">{r.name}</span>
                <span className={cx("opt-status", r.ingested ? "is-ready" : "is-new")}>
                  {r.ingested ? t("search.analyzed") : t("search.notAnalyzed")}
                </span>
              </li>
            ))}
          </ul>
          {phase === "done" && results.length === 0 && <p className="search-msg">{t("search.noResults")}</p>}
          {phase === "loading" && results.length === 0 && <p className="search-msg">{t("search.searching")}</p>}
          {phase === "error" && <p className="search-msg is-error">{t("search.error")}</p>}
        </div>
      )}
    </div>
  );
}
