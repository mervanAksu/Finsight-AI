from datetime import date

import pytest
from conftest import load

from finsight.edgar import (build_ticker_map, document_url, older_filing_files, pad_cik,
                            parse_filings, resolve_company)


@pytest.mark.parametrize("raw,expected", [
    (320193, "0000320193"), ("320193", "0000320193"), ("CIK0000320193", "0000320193"),
])
def test_pad_cik(raw, expected):
    assert pad_cik(raw) == expected


@pytest.mark.parametrize("bad", ["abc", "12345678901", ""])
def test_pad_cik_rejects_invalid(bad):
    with pytest.raises(ValueError):
        pad_cik(bad)


def test_resolve_by_ticker_and_cik():
    tmap = build_ticker_map(load("company_tickers_example.json"))
    assert resolve_company("aapl", tmap)["cik"] == "0000320193"
    assert resolve_company("320193", tmap)["ticker"] == "AAPL"
    with pytest.raises(KeyError):
        resolve_company("NOTREAL", tmap)


def test_parse_filings_keeps_only_target_forms_since_date():
    sub = load("submissions_example.json")
    rows = parse_filings(sub["filings"]["recent"], since=date(2023, 1, 1))
    assert [r["form"] for r in rows] == ["10-K", "8-K", "10-Q", "10-K/A"]
    amend = rows[-1]
    assert amend["is_amendment"] and amend["base_form"] == "10-K"
    assert rows[1]["items"] == "2.02,9.01"
    assert rows[1]["report_date"] == date(2026, 4, 28)


def test_older_files_only_when_needed():
    sub = load("submissions_example.json")
    assert older_filing_files(sub, date(2023, 1, 1)) == []
    assert older_filing_files(sub, date(2018, 1, 1)) == ["CIK0000000001-submissions-001.json"]


def test_document_url():
    assert document_url("0000320193", "0000320193-24-000123", "aapl-20240928.htm") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"
    )
