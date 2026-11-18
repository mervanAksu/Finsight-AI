"""Türkçe sayı gösterimi (ondalık ayırıcı virgül, binlik ayırıcı nokta).

Saf yardımcılar; skor açıklamaları ve gösterim alanları (display) için kullanılır.
Eksik değer (None) her zaman "—" olarak gösterilir, hiçbir zaman 0 olarak değil.
"""

MISSING = "—"


def _num(x: float, digits: int) -> str:
    """Mutlak değeri Türkçe biçimde yazar: 1234.5 → "1.234,5"."""
    s = f"{abs(x):,.{digits}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _sign(x: float, digits: int) -> str:
    """Yuvarlanmış değer sıfır değilse ve x negatifse eksi işareti (−0,0 üretilmez)."""
    return "-" if x < 0 and round(abs(x), digits) != 0 else ""


def fmt_percent(x: float | None, digits: int = 1) -> str:
    """Ham oranı yüzde olarak yazar: 0.361 → "%36,1", -0.05 → "-%5,0"."""
    if x is None:
        return MISSING
    pct = x * 100
    return f"{_sign(pct, digits)}%{_num(pct, digits)}"


def fmt_times(x: float | None, digits: int = 2) -> str:
    """Kat olarak yazar: 1.25 → "1,25x"."""
    if x is None:
        return MISSING
    return f"{_sign(x, digits)}{_num(x, digits)}x"


def fmt_decimal(x: float | None, digits: int = 2) -> str:
    """Düz ondalık sayı: -0.52 → "-0,52" (ör. net ton)."""
    if x is None:
        return MISSING
    return f"{_sign(x, digits)}{_num(x, digits)}"


_USD_UNITS = [(1e12, "trn"), (1e9, "mr"), (1e6, "mn")]


def fmt_usd_short(x: float | None) -> str:
    """Kısaltılmış USD: 281_724_000_000 → "281,7 mr $", 3_400_000 → "3,4 mn $", 950 → "950 $".

    1 milyonun altı tam sayı ve binlik ayırıcıyla yazılır (12_500 → "12.500 $").
    """
    if x is None:
        return MISSING
    a = abs(x)
    for i, (scale, name) in enumerate(_USD_UNITS):
        if a >= scale:
            scaled = round(a / scale, 1)
            # Yuvarlama bir üst birime taşırsa (999,95 mn → 1.000,0 mn) üst birimi kullan.
            if scaled >= 1000 and i > 0:
                scale, name = _USD_UNITS[i - 1]
                scaled = round(a / scale, 1)
            sign = "-" if x < 0 else ""
            return f"{sign}{_num(scaled, 1)} {name} $"
    sign = "-" if x < 0 and round(a) != 0 else ""
    return f"{sign}{_num(a, 0)} $"
