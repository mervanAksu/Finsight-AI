"""Türkçe sayı gösterimi testleri."""

import pytest

from finsight.formatting import fmt_decimal, fmt_percent, fmt_times, fmt_usd_short


@pytest.mark.parametrize("x, digits, expected", [
    (0.361, 1, "%36,1"),
    (-0.05, 1, "-%5,0"),
    (0.2, 0, "%20"),
    (0, 1, "%0,0"),
    (-0.0001, 1, "%0,0"),       # −0,0 üretilmez
    (12.5, 1, "%1.250,0"),      # binlik ayırıcı nokta
    (None, 1, "—"),
])
def test_fmt_percent(x, digits, expected):
    assert fmt_percent(x, digits) == expected


@pytest.mark.parametrize("x, digits, expected", [
    (1.25, 2, "1,25x"),
    (3.0, 1, "3,0x"),
    (-0.5, 2, "-0,50x"),
    (None, 2, "—"),
])
def test_fmt_times(x, digits, expected):
    assert fmt_times(x, digits) == expected


@pytest.mark.parametrize("x, expected", [
    (281_724_000_000, "281,7 mr $"),
    (3_400_000, "3,4 mn $"),
    (950, "950 $"),
    (12_500, "12.500 $"),
    (-3_400_000, "-3,4 mn $"),
    (-950, "-950 $"),
    (1_500_000_000_000, "1,5 trn $"),
    (999_999_999, "1,0 mr $"),  # yuvarlama üst birime taşır
    (0, "0 $"),
    (None, "—"),
])
def test_fmt_usd_short(x, expected):
    assert fmt_usd_short(x) == expected


def test_fmt_decimal():
    assert fmt_decimal(-0.52) == "-0,52"
    assert fmt_decimal(0.08) == "0,08"
    assert fmt_decimal(None) == "—"
