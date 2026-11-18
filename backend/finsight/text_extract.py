"""SEC rapor HTML'inden düz metin, bölüm (Item 1A / Item 7 / Item 2) ayıklama ve parçalama.

FR-07 (risk bölümü), FR-08 (MD&A duygu analizi) ve FR-10 (soru-cevap için parçalama) bu
modüle dayanır. Yalnızca stdlib + BeautifulSoup kullanılır; `lxml` varsa hızlı ayrıştırıcı
seçilir, yoksa `html.parser`'a düşülür.

Bölüm yapısı (bkz. `.claude/skills/sec-edgar/SKILL.md`):
- 10-K: Part I Item 1A "Risk Factors" → Item 1B (yoksa 1C / Item 2);
  Part II Item 7 "Management's Discussion..." → Item 7A (yoksa Item 8).
- 10-Q: Part I Item 2 "Management's Discussion..." → Item 3 (yoksa Item 4);
  Part II Item 1A "Risk Factors" → Item 2 / Item 5 / Item 6.

İçindekiler tablosu tuzağı: başlıklar önce içindekilerde geçer. Yalnızca "başlık benzeri"
satırlar aday sayılır (çapraz atıflar — "see Item 1A. Risk Factors, of this report" — elenir);
bitiş başlığına kadar 50 kelimeden kısa adaylar içindekiler satırıdır. Kalanların ilki seçilir;
hiç aday kalmazsa en uzun aday yedeğine dönülür.
"""

from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup
from bs4.element import (
    CData,
    Comment,
    Declaration,
    Doctype,
    NavigableString,
    ProcessingInstruction,
    Tag,
)

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
_APOS = "'’`´ʼ"
_RISK_TITLE = r"risk\s+factors"
# MD&A başlığının tamamı (varsa) başlığa dahildir; böylece satırın kalanı gövde cümlesi
# ise ayrıca tanınabilir.
_MDNA_TITLE = (
    rf"(?:management[{_APOS}]?s?\s+discussion"
    r"(?:\s+and\s+analysis(?:\s+of\s+(?:the\s+)?financial\s+condition"
    r"(?:\s+and\s+(?:the\s+)?results\s+of\s+operations)?)?)?|md\s*&\s*a)"
)
HEADING_MAX_CHARS = 150  # başlık satırının (gövde hariç) en fazla uzunluğu
TOC_MAX_WORDS = 50  # başlangıç ile bitiş arası bundan kısa aday içindekiler satırıdır


def _code_re(code: str) -> str:
    """'1A' → '1[ \t]*A' ("Item 1 A." yazımına da izin verir)."""
    m = re.fullmatch(r"(\d{1,2})([A-Za-z]?)", code)
    if not m or not m.group(2):
        return re.escape(code)
    return rf"{m.group(1)}[ \t]*{m.group(2)}"


def _start_re(code: str, title: str) -> re.Pattern:
    return re.compile(
        rf"^[ \t]*{_PART_PREFIX}item[ \t]*{_code_re(code)}(?![0-9a-z]){_TITLE_SEP}{title}",
        re.I | re.M,
    )


def _end_re(codes: list[str]) -> re.Pattern:
    alts = "|".join(_code_re(c) for c in codes)
    return re.compile(rf"^[ \t]*{_PART_PREFIX}item[ \t]*(?:{alts})(?![0-9a-z])", re.I | re.M)


_ANY_ITEM = re.compile(r"^[ \t]*item[ \t]*\d{1,2}(?:[ \t]*[a-c])?(?![0-9a-z])", re.I | re.M)
# Part II bitişi yalnızca satırın tamamı "PART II" (+ isteğe bağlı "— OTHER INFORMATION",
# sayfa numarası) ise kabul edilir; "Part II, Item 7 of this report ..." başlık değildir.
_PART_II = re.compile(
    r"^[ \t]*part[ \t]+ii[ \t]*(?:[.:\-–—][ \t]*)?(?:other[ \t]+information[ \t]*\.?[ \t]*)?"
    r"(?:\d{1,3}[ \t]*)?$",
    re.I | re.M,
)
_NOT_APPLICABLE = re.compile(r"not\s+applicable|smaller\s+reporting", re.I)
_REST_SEP = re.compile(r"^[ \t.:\-–—]*")
_PAGE_REF = re.compile(r"^(?:page[ \t]*)?\d{1,3}$", re.I)
_CONTINUATION = re.compile(r"[,;][ \t]*[a-z]|\.[ \t]+[a-z]")


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    ls = text.rfind("\n", 0, pos) + 1
    le = text.find("\n", pos)
    return ls, (len(text) if le == -1 else le)


def _continues_previous_line(text: str, line_start: int) -> bool:
    """Önceki satır (arada boş satır yoksa) virgülle veya küçük harfli kelimeyle bitiyorsa
    bu satır bir cümlenin devamıdır (ör. "see Part I,<br>Item 1A. Risk Factors, ...")."""
    if line_start == 0:
        return False
    prev_end = line_start - 1  # '\n'
    prev_start = text.rfind("\n", 0, prev_end) + 1
    prev = text[prev_start:prev_end].rstrip()
    if not prev:
        return False
    if prev.endswith(","):
        return True
    last = prev.split()[-1]
    return last.isalpha() and last.islower()


def _rest_breaks_heading(rest: str, line: str) -> bool:
    """Başlık belirtecinden sonra gelen metin bir cümle devamı mı?"""
    if not rest:
        return False
    if rest[0] in ",;":
        return True
    # Küçük harfle devam ("of this report", "contains", "discusses"); satırın tamamı küçük
    # harfse (ör. "item 1b. unresolved staff comments") bu kural uygulanmaz.
    if rest[0].islower() and any(c.isupper() for c in line):
        return True
    return False


def _is_end_heading(text: str, m: re.Match) -> bool:
    """Bitiş başlığı ("Item 1B. Unresolved Staff Comments") başlık benzeri bir satır mı?"""
    ls, le = _line_bounds(text, m.start())
    line = text[ls:le].strip()
    if len(line) > HEADING_MAX_CHARS:
        return False
    rest = _REST_SEP.sub("", text[m.end():le]).rstrip()
    if _rest_breaks_heading(rest, line) or _CONTINUATION.search(rest):
        return False
    return not _continues_previous_line(text, ls)


def _start_heading(text: str, m: re.Match) -> int | None:
    """Başlangıç başlığı başlık benzeriyse gövdenin başladığı konumu, değilse None döner.

    Başlık "Risk Factors" / "Management's Discussion..." kelimelerinde biter. Satırın kalanı
    yalnızca sayfa numarası veya kısa bir ek ise başlığa dahildir; büyük harfle başlayan bir
    cümle ise (başlık ile gövde aynı satırda) gövdeye aittir.
    """
    ls, le = _line_bounds(text, m.end())
    head_start = _line_bounds(text, m.start())[0]
    line = text[ls:le]
    tail = text[m.end():le]
    rest = _REST_SEP.sub("", tail).rstrip()
    if _rest_breaks_heading(rest, line):
        return None
    if _continues_previous_line(text, head_start):
        return None
    if rest and not _PAGE_REF.match(rest):
        words = rest.split()
        if len(words) >= 4 or rest[-1] in ".!?":
            # Gövde cümlesi başlıkla aynı satırda: gövde bu cümleyle başlar.
            if m.end() - head_start > HEADING_MAX_CHARS:
                return None
            return m.end() + (len(tail) - len(tail.lstrip(" \t.:-–—")))
    if le - head_start > HEADING_MAX_CHARS:
        return None
    return le


def _find_end(text: str, pos: int, end_re: re.Pattern, part_ii: bool) -> int:
    ends = [m.start() for m in end_re.finditer(text, pos) if _is_end_heading(text, m)][:1]
    if part_ii:
        ends += [m.start() for m in _PART_II.finditer(text, pos)][:1]
    if not ends:
        ends = [m.start() for m in _ANY_ITEM.finditer(text, pos) if _is_end_heading(text, m)][:1]
    return min(ends) if ends else len(text)


# (bölüm kodu, başlangıç deseni, bitiş deseni, bitişte PART II, Part II içinde olmalı mı)
_SPECS: dict[str, list[tuple[str, re.Pattern, re.Pattern, bool, bool]]] = {
    "10-K": [
        ("1A", _start_re("1A", _RISK_TITLE), _end_re(["1B", "1C", "2"]), True, False),
        ("7", _start_re("7", _MDNA_TITLE), _end_re(["7A", "8"]), False, False),
    ],
    "10-Q": [
        ("2", _start_re("2", _MDNA_TITLE), _end_re(["3", "4"]), True, False),
        ("1A", _start_re("1A", _RISK_TITLE), _end_re(["2", "5", "6"]), False, True),
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


def _select_span(
    text: str, start_re: re.Pattern, end_re: re.Pattern, part_ii: bool, min_pos: int
) -> tuple[int, int] | None:
    """Başlık benzeri ve içindekiler satırı olmayan İLK adayı seçer.

    İçindekiler: başlangıç ile bitiş arası `TOC_MAX_WORDS` kelimeden kısa. Hiç uygun aday
    yoksa eski kurala (tüm adaylar arasından en uzun gövde) dönülür.
    """
    longest: tuple[int, int] | None = None
    for m in start_re.finditer(text):
        if m.start() < min_pos:
            continue
        body_start = _start_heading(text, m)
        heading_like = body_start is not None
        if body_start is None:
            body_start = m.end()
        span = _strip_span(text, body_start, _find_end(text, body_start, end_re, part_ii))
        if heading_like and len(text[span[0]:span[1]].split()) >= TOC_MAX_WORDS:
            return span
        if longest is None or (span[1] - span[0]) > (longest[1] - longest[0]):
            longest = span
    return longest


def extract_sections(text: str, form: str) -> dict[str, dict]:
    """Düz metinden risk faktörleri ve MD&A bölümlerini çıkarır.

    Dönüş: `{"1A": {"item_code", "title", "text", "char_start", "char_end"}, ...}`.
    `text == kaynak[char_start:char_end]`; başlık satırı ("Item 1A. Risk Factors") hariçtir.
    Bulunamayan veya "not applicable" geçilen bölüm sözlükte yer almaz.

    Başlangıç/bitiş başlıkları "başlık benzeri satır" olmalıdır (≤ 150 karakter, başlıktan
    sonra ", of this ..." / "contains ..." gibi cümle devamı yok). İçindekiler satırları
    elenir ve kalan adaylardan ilki seçilir.
    """
    base = _base_form(form)
    if base is None or not text:
        return {}
    first_part_ii = next((m.start() for m in _PART_II.finditer(text)), None)
    result: dict[str, dict] = {}
    for code, start_re, end_re, end_part_ii, needs_part_ii in _SPECS[base]:
        min_pos = first_part_ii if (needs_part_ii and first_part_ii is not None) else 0
        best = _select_span(text, start_re, end_re, end_part_ii, min_pos)
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
