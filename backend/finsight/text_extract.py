"""SEC rapor HTML'inden düz metin, bölüm (Item 1A / Item 7 / Item 2) ayıklama ve parçalama.

FR-07 (risk bölümü), FR-08 (MD&A duygu analizi) ve FR-10 (soru-cevap için parçalama) bu
modüle dayanır. Yalnızca stdlib + BeautifulSoup kullanılır; `lxml` varsa hızlı ayrıştırıcı
seçilir, yoksa `html.parser`'a düşülür.

Bölüm yapısı (bkz. `.claude/skills/sec-edgar/SKILL.md`):
- 10-K: Part I Item 1A "Risk Factors" → Item 1B (yoksa 1C / Item 2);
  Part II Item 7 "Management's Discussion..." → Item 7A (yoksa Item 8).
- 10-Q: Part I Item 2 "Management's Discussion..." → Item 3 (yoksa Item 4);
  Part II Item 1A "Risk Factors" → Item 2 / Item 5 / Item 6.

İçindekiler tablosu tuzağı: başlıklar önce içindekilerde geçer. Her başlık adayından bir sonraki
bitiş başlığına kadar olan metin alınır ve **en uzun** aday seçilir.
"""

from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import CData, Comment, Declaration, Doctype, ProcessingInstruction

try:  # bs4 >= 4.11
    from bs4 import XMLParsedAsHTMLWarning
except ImportError:  # pragma: no cover
    XMLParsedAsHTMLWarning = UserWarning

SECTION_TITLES: dict[str, str] = {
    "1A": "Risk Faktörleri",
    "7": "Yönetimin Değerlendirmesi (MD&A)",
    "2": "Yönetimin Değerlendirmesi (MD&A)",
}

# Bu kadar kelimeden kısa VE "not applicable"/"smaller reporting" içeren bölüm yok sayılır.
NOT_APPLICABLE_MAX_WORDS = 200
# Bu kadar kelimeden kısa bölüm (ör. yalnızca içindekiler satırı) gerçek bölüm sayılmaz.
MIN_SECTION_WORDS = 15
MAX_SENTENCE_CHARS = 1000
MIN_SENTENCE_WORDS = 3
HEADING_MAX_WORDS = 8  # bundan kısa paragraflar başlık sayılıp sonraki paragrafa katılır
HEADING_REST_MAX_CHARS = 150  # bölüm başlık satırının başlıktan sonra kalan en fazla uzunluğu

# --------------------------------------------------------------------------- HTML → metin

_REMOVE_TAGS = {"script", "style", "noscript", "template", "head", "title", "ix:header", "ix:hidden"}
_BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
    "article", "header", "footer", "blockquote", "pre", "hr", "center", "dl", "dt", "dd",
    "form", "address", "nav", "aside", "figure", "figcaption", "caption", "body", "html",
    "page", "thead", "tbody", "tfoot",
}
_SKIP_STRINGS = (Comment, Doctype, CData, ProcessingInstruction, Declaration)
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.I)
_PAGE_NOISE_LINE = re.compile(r"^(?:\d{1,3}|[-–—]\s*\d{1,3}\s*[-–—]|table of contents)$", re.I)
_SPACE_CHARS = {"\xa0": " ", " ": " ", " ": " ", " ": " ", "​": "",
                "﻿": "", "\r": "\n", "\t": " ", "\f": " ", "\v": " "}
_SPACE_TABLE = str.maketrans(_SPACE_CHARS)


def _parser_name() -> str:
    try:
        import lxml  # noqa: F401
    except ImportError:  # pragma: no cover - lxml kurulu olmayan ortam
        return "html.parser"
    return "lxml"


def _decode(html: str | bytes) -> str:
    if isinstance(html, bytes):
        try:
            return html.decode("utf-8-sig")
        except UnicodeDecodeError:
            return html.decode("latin-1")
    return html


def _is_hidden(tag: Tag) -> bool:
    if tag.attrs is None:
        return False
    if tag.has_attr("hidden"):
        return True
    style = tag.get("style")
    return bool(style and _DISPLAY_NONE.search(str(style)))


def html_to_text(html: str | bytes) -> str:
    """EDGAR HTML/iXBRL belgesini okunabilir düz metne çevirir.

    - script/style/head ve gizli inline XBRL blokları (`ix:header`) ile `display:none`
      öğeleri atılır; görünür `ix:nonFraction` gibi değerler korunur.
    - Blok öğeler boş satırla ayrılır (paragraf sınırı), tablo satırları tek satır sonu,
      tablo hücreleri boşlukla ayrılır.
    - `&nbsp;` ve fazla boşluklar normalize edilir; yalnızca sayfa numarası veya
      "Table of Contents" içeren satırlar atılır.
    """
    with warnings.catch_warnings():
        # iXBRL belgeleri XHTML'dir; HTML ayrıştırıcıyla okumak bilinçli bir tercihtir.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(_decode(html), _parser_name())

    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        if tag.name.lower() in _REMOVE_TAGS or _is_hidden(tag):
            tag.decompose()

    # Derin iç içe (kapanmamış <font>) belgelerde özyineleme sınırına takılmamak için
    # açık yığınla dolaşılır. Öğeler: ("s", metin) çıktı işareti, ("n", düğüm, hücre_içi).
    out: list[str] = []
    stack: list[tuple] = [("n", soup, False)]
    while stack:
        item = stack.pop()
        if item[0] == "s":
            out.append(item[1])
            continue
        _, node, in_cell = item
        if isinstance(node, NavigableString):
            if not isinstance(node, _SKIP_STRINGS):
                out.append(str(node))
            continue
        if not isinstance(node, Tag):
            continue
        name = (node.name or "").lower()
        if name == "br":
            out.append(" " if in_cell else "\n")
            continue
        child_in_cell = in_cell
        if name in ("td", "th"):
            before, after, child_in_cell = "", " ", True
        elif name == "tr":
            before = after = " " if in_cell else "\n"
        elif name in _BLOCK_TAGS:
            before = after = " " if in_cell else "\n\n"
        else:
            before = after = ""
        out.append(before)
        stack.append(("s", after))
        for child in reversed(list(node.children)):
            stack.append(("n", child, child_in_cell))

    raw = "".join(out).translate(_SPACE_TABLE)
    lines: list[str] = []
    for line in raw.split("\n"):
        line = re.sub(r" {2,}", " ", line).strip()
        if line and _PAGE_NOISE_LINE.match(line):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------- bölüm ayıklama

_PART_PREFIX = r"(?:part[ \t]+i{1,3}[ \t]*[.,:\-–—]?[ \t]*)?"
_TITLE_SEP = r"[\s.:\-–—]*"
_RISK_TITLE = r"risk\s+factors"
_MDNA_TITLE = r"(?:management['’`]?s?\s+discussion|md\s*&\s*a)"


def _start_re(code: str, title: str) -> re.Pattern:
    # Başlık satırının kalanı ("...and Analysis of Financial Condition...") kısa ise başlığa
    # dahil edilir; uzunsa (başlık ile gövde aynı satırdaysa) gövdeden yemek için alınmaz.
    return re.compile(
        rf"^[ \t]*{_PART_PREFIX}item[ \t]*{re.escape(code)}(?![0-9a-z]){_TITLE_SEP}{title}"
        rf"(?:[^\n]{{0,{HEADING_REST_MAX_CHARS}}}(?=\n|\Z))?",
        re.I | re.M,
    )


def _end_re(codes: list[str], part_ii: bool = False) -> re.Pattern:
    alts = "|".join(re.escape(c) for c in codes)
    pattern = rf"^[ \t]*{_PART_PREFIX}item[ \t]*(?:{alts})(?![0-9a-z])"
    if part_ii:
        pattern = rf"(?:{pattern}|^[ \t]*part[ \t]+ii(?![0-9a-z]))"
    return re.compile(pattern, re.I | re.M)


_ANY_ITEM = re.compile(r"^[ \t]*item[ \t]*\d{1,2}[a-c]?(?![0-9a-z])", re.I | re.M)
_PART_II = re.compile(r"^[ \t]*part[ \t]+ii(?![0-9a-z])", re.I | re.M)
_NOT_APPLICABLE = re.compile(r"not\s+applicable|smaller\s+reporting", re.I)

# (bölüm kodu, başlangıç deseni, bitiş deseni, Part II içinde olmalı mı)
_SPECS: dict[str, list[tuple[str, re.Pattern, re.Pattern, bool]]] = {
    "10-K": [
        ("1A", _start_re("1A", _RISK_TITLE), _end_re(["1B", "1C", "2"], part_ii=True), False),
        ("7", _start_re("7", _MDNA_TITLE), _end_re(["7A", "8"]), False),
    ],
    "10-Q": [
        ("2", _start_re("2", _MDNA_TITLE), _end_re(["3", "4"], part_ii=True), False),
        ("1A", _start_re("1A", _RISK_TITLE), _end_re(["2", "5", "6"]), True),
    ],
}


def _base_form(form: str) -> str | None:
    f = (form or "").strip().upper().replace(" ", "")
    if f.startswith("10-K"):
        return "10-K"
    if f.startswith("10-Q"):
        return "10-Q"
    return None


def _strip_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def extract_sections(text: str, form: str) -> dict[str, dict]:
    """Düz metinden risk faktörleri ve MD&A bölümlerini çıkarır.

    Dönüş: `{"1A": {"item_code", "title", "text", "char_start", "char_end"}, ...}`.
    `text == kaynak[char_start:char_end]`; başlık satırı ("Item 1A. Risk Factors") hariçtir.
    Bulunamayan veya "not applicable" geçilen bölüm sözlükte yer almaz.
    """
    base = _base_form(form)
    if base is None or not text:
        return {}
    part_ii_positions = [m.start() for m in _PART_II.finditer(text)]
    result: dict[str, dict] = {}
    for code, start_re, end_re, needs_part_ii in _SPECS[base]:
        best: tuple[int, int] | None = None
        for m in start_re.finditer(text):
            if needs_part_ii and part_ii_positions and m.start() < part_ii_positions[0]:
                continue
            body_start = m.end()
            end_match = end_re.search(text, body_start)
            if end_match is None:
                end_match = _ANY_ITEM.search(text, body_start)
            end = end_match.start() if end_match else len(text)
            span = _strip_span(text, body_start, end)
            if best is None or (span[1] - span[0]) > (best[1] - best[0]):
                best = span
        if best is None:
            continue
        body = text[best[0]:best[1]]
        n_words = len(body.split())
        if n_words < MIN_SECTION_WORDS:
            continue
        if n_words < NOT_APPLICABLE_MAX_WORDS and _NOT_APPLICABLE.search(body):
            continue
        result[code] = {
            "item_code": code,
            "title": SECTION_TITLES[code],
            "text": body,
            "char_start": best[0],
            "char_end": best[1],
        }
    return result


# --------------------------------------------------------------------------- cümle / paragraf

_DOT = ""  # kısaltmalardaki noktayı geçici olarak saklayan özel karakter
_ABBREV_CS = re.compile(
    r"\b(?:Inc|No|Nos|Mr|Mrs|Ms|Dr|Corp|Co|Ltd|Jr|Sr|St|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|"
    r"Sept|Oct|Nov|Dec|Fig)\.(?=\s)"
)
_ABBREV_CI = re.compile(r"\b(?:e\.g|i\.e|vs|approx|cf|viz)\.", re.I)
_ACRONYM = re.compile(r"\b(?:[A-Za-z]\.){2,}")  # U.S., U.K., N.A., L.P.
_INITIAL = re.compile(r"\b([A-Z])\.(?=\s+[A-Z])")  # "John D. Smith"
_SENT_END = re.compile(r"([.!?]+[\"'”’)\]]*)\s+(?=[\"“‘'(\[]?[A-Z0-9$•])")


def _protect(text: str) -> str:
    text = _ACRONYM.sub(lambda m: m.group(0).replace(".", _DOT), text)
    text = _ABBREV_CI.sub(lambda m: m.group(0).replace(".", _DOT), text)
    text = _ABBREV_CS.sub(lambda m: m.group(0).replace(".", _DOT), text)
    return _INITIAL.sub(lambda m: m.group(1) + _DOT, text)


def _split_long(sentence: str, limit: int = MAX_SENTENCE_CHARS) -> list[str]:
    """`limit` karakteri aşan cümleyi önce '; ' noktalarından, sonra kelime sınırından keser."""
    if len(sentence) <= limit:
        return [sentence]
    pieces: list[str] = []
    for part in re.split(r"(?<=;)\s+", sentence):
        while len(part) > limit:
            # Parçaları eşit boya yakın kes; sonda tek kelimelik artık kalmasın.
            target = len(part) // -(-len(part) // limit)
            cut = part.rfind(" ", 0, target + 1)
            cut = cut if cut > 0 else target
            pieces.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            pieces.append(part)
    merged: list[str] = []
    for p in pieces:  # kısa '; ' parçalarını sınırı aşmadan birleştir
        if merged and len(merged[-1]) + 1 + len(p) <= limit:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def split_sentences(text: str) -> list[str]:
    """Regex tabanlı cümle bölücü.

    Kısaltmalarda (U.S., Inc., No., e.g., i.e., Mr., Dr., vs., approx.) bölmez; satır
    sonları da cümle sınırıdır. 3 kelimeden kısa parçalar atılır, 1000 karakteri aşan
    cümleler parçalara kesilir.
    """
    if not text:
        return []
    sentences: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        protected = _SENT_END.sub(lambda m: m.group(1) + "\n", _protect(line))
        for raw in protected.split("\n"):
            s = re.sub(r"\s+", " ", raw.replace(_DOT, ".")).strip()
            for piece in _split_long(s):
                if len(piece.split()) >= MIN_SENTENCE_WORDS:
                    sentences.append(piece)
    return sentences


def split_paragraphs(text: str) -> list[str]:
    """Metni paragraflara böler.

    Metinde boş satır varsa paragraf sınırı boş satırdır (blok içindeki satır sonları
    boşluğa çevrilir — sabit satır genişlikli eski metin başvuruları için); hiç boş satır
    yoksa her satır bir paragraftır. 8 kelimeden kısa başlık benzeri paragraflar sonraki
    paragrafın başına eklenir (sonda kalırsa öncekine).
    """
    if not text or not text.strip():
        return []
    if re.search(r"\n[ \t]*\n", text):
        blocks = re.split(r"\n[ \t]*\n", text)
    else:
        blocks = text.split("\n")
    paras = [re.sub(r"\s+", " ", b).strip() for b in blocks]
    paras = [p for p in paras if p]

    result: list[str] = []
    pending: list[str] = []
    for p in paras:
        if len(p.split()) < HEADING_MAX_WORDS:
            pending.append(p)
            continue
        result.append(" ".join([*pending, p]))
        pending = []
    if pending:
        tail = " ".join(pending)
        if result:
            result[-1] = result[-1] + " " + tail
        else:
            result.append(tail)
    return result


def _words(text: str) -> list[str]:
    return text.split()


def chunk_text(text: str, target_words: int = 350, overlap_words: int = 50) -> list[str]:
    """Metni ~`target_words` kelimelik, `overlap_words` kelime örtüşen parçalara böler.

    Paragraf sınırlarına saygılıdır: paragraflar bölünmeden biriktirilir; tek başına
    `target_words`'ü aşan paragraf cümlelerden (gerekirse kelimelerden) bölünür. Her yeni
    parça, öncekinin son `overlap_words` kelimesiyle başlar.
    """
    if target_words <= 0:
        raise ValueError("target_words pozitif olmalı")
    if not 0 <= overlap_words < target_words:
        raise ValueError("overlap_words 0 ile target_words arasında olmalı")

    units: list[str] = []
    for para in split_paragraphs(text):
        if len(_words(para)) <= target_words:
            units.append(para)
            continue
        buf: list[str] = []
        for sent in split_sentences(para) or [para]:
            sw = _words(sent)
            if len(sw) > target_words:
                if buf:
                    units.append(" ".join(buf))
                    buf = []
                for i in range(0, len(sw), target_words):
                    units.append(" ".join(sw[i:i + target_words]))
                continue
            if buf and len(_words(" ".join(buf))) + len(sw) > target_words:
                units.append(" ".join(buf))
                buf = []
            buf.append(sent)
        if buf:
            units.append(" ".join(buf))

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    overlap: str = ""
    for unit in units:
        n = len(_words(unit))
        if current and current_words + n > target_words:
            chunk = "\n\n".join(current)
            chunks.append(chunk)
            tail = _words(chunk)[-overlap_words:] if overlap_words else []
            overlap = " ".join(tail)
            current = [overlap] if overlap else []
            current_words = len(tail)
        current.append(unit)
        current_words += n
    if current and (not chunks or current_words > len(_words(overlap))):
        chunks.append("\n\n".join(current))
    return chunks
