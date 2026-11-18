import json
from datetime import date

from conftest import UA, FakeFetcher, load

from finsight.edgar import build_ticker_map
from finsight.pipeline import collect
from finsight.rate_limiter import RateLimiter
from finsight.sec_client import SecClient


def test_collect_end_to_end_with_fake_sec(fake_clock):
    fetcher = FakeFetcher({
        "https://data.sec.gov/submissions/CIK0000000001.json":
            (200, json.dumps(load("submissions_example.json")).encode()),
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json":
            (200, json.dumps(load("companyfacts_example.json")).encode()),
    })
    client = SecClient(UA, fetcher=fetcher,
                       limiter=RateLimiter(8, clock=fake_clock, sleep=fake_clock.sleep))
    tmap = build_ticker_map(load("company_tickers_example.json"))

    data = collect(client, "EXMP", tmap, years=3, today=date(2026, 10, 10))

    assert data["company"]["name"] == "Example Corp"
    assert len(data["filings"]) == 4
    assert [s["value"] for s in data["facts"]["revenue"]] == [1000, 1100, 1210]
    assert client.request_count == 2  # eski başvuru dosyasına gerek yok
    assert all(h["User-Agent"] == UA for _, h in fetcher.calls)
