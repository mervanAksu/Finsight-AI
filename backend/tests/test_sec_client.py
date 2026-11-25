import json

import pytest
from conftest import UA, FakeFetcher

from finsight.config import ConfigError, get_user_agent
from finsight.rate_limiter import RateLimiter
from finsight.sec_client import FileCache, SecClient, SecError

SUB_URL = "https://data.sec.gov/submissions/CIK0000000001.json"


def make_client(fetcher, fake_clock, **kw):
    limiter = RateLimiter(8, clock=fake_clock, sleep=fake_clock.sleep)
    return SecClient(UA, limiter=limiter, fetcher=fetcher, sleep=fake_clock.sleep, **kw)


def test_every_request_carries_user_agent(fake_clock):
    fetcher = FakeFetcher({SUB_URL: (200, b'{"name": "Example Corp"}')})
    client = make_client(fetcher, fake_clock)
    assert client.submissions(1)["name"] == "Example Corp"
    url, headers = fetcher.calls[0]
    assert url == SUB_URL
    assert headers["User-Agent"] == UA


@pytest.mark.parametrize("ua", ["", "FinSight-AI no-email"])
def test_client_refuses_missing_or_anonymous_user_agent(ua):
    with pytest.raises(ValueError):
        SecClient(ua, fetcher=FakeFetcher({}))


def test_config_requires_user_agent(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ConfigError):
        get_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    assert get_user_agent() == UA


def test_retries_then_succeeds(fake_clock):
    fetcher = FakeFetcher({SUB_URL: [(503, b""), (429, b""), (200, b"{}")]})
    client = make_client(fetcher, fake_clock)
    assert client.get_json(SUB_URL) == {}
    assert client.request_count == 3


def test_gives_up_after_three_retries(fake_clock):
    fetcher = FakeFetcher({SUB_URL: (503, b"")})
    client = make_client(fetcher, fake_clock)
    with pytest.raises(SecError):
        client.get_json(SUB_URL)
    assert len(fetcher.calls) == 4  # 1 deneme + 3 yeniden deneme (NFR-03)


def test_404_is_not_retried(fake_clock):
    fetcher = FakeFetcher({})
    client = make_client(fetcher, fake_clock)
    with pytest.raises(SecError):
        client.get_json(SUB_URL)
    assert len(fetcher.calls) == 1


def test_cache_prevents_second_request(tmp_path, fake_clock):
    fetcher = FakeFetcher({SUB_URL: (200, json.dumps({"a": 1}).encode())})
    client = make_client(fetcher, fake_clock, cache=FileCache(str(tmp_path)))
    client.get_json(SUB_URL)
    client.get_json(SUB_URL)
    assert len(fetcher.calls) == 1


def test_file_cache_set_is_atomic(tmp_path, monkeypatch):
    """O5: yazım geçici dosya + os.replace ile; yarıda kalan yazım eski içeriği bozmaz."""
    import os

    cache = FileCache(str(tmp_path))
    cache.set(SUB_URL, b"eski")
    assert cache.get(SUB_URL) == b"eski"

    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        replaced.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", recording_replace)
    cache.set(SUB_URL, b"yeni")
    assert cache.get(SUB_URL) == b"yeni"
    assert len(replaced) == 1 and replaced[0][1] == cache._path(SUB_URL)
    assert os.path.dirname(replaced[0][0]) == str(tmp_path)  # aynı dosya sistemi

    def failing_replace(src, dst):
        raise OSError("disk dolu")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        cache.set(SUB_URL, b"yarim")
    assert cache.get(SUB_URL) == b"yeni"  # eski içerik sağlam
    assert sorted(os.listdir(tmp_path)) == [os.path.basename(cache._path(SUB_URL))]


# --------------------------------------------------------------------------- Aşama 4 (§5)

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/1/000000000126000010/doc.htm"


def test_cache_ttl_policy_by_url_class():
    from finsight.sec_client import cache_ttl_for

    assert cache_ttl_for(DOC_URL) is None  # yayımlanmış belge süresiz
    assert cache_ttl_for(SUB_URL) == 3600
    assert cache_ttl_for(FACTS_URL, json_ttl=900) == 900
    assert cache_ttl_for(TICKERS_URL) == 24 * 3600


def test_file_cache_expiry_per_url_class(tmp_path):
    import os

    now = [10_000_000.0]
    cache = FileCache(str(tmp_path), clock=lambda: now[0], json_ttl_seconds=3600)
    for url in (SUB_URL, DOC_URL, TICKERS_URL):
        cache.set(url, b"x")
        os.utime(cache._path(url), (now[0], now[0]))
    now[0] += 3601  # JSON süresi doldu; belge ve ticker listesi hâlâ geçerli
    assert cache.get(SUB_URL) is None
    assert cache.get(DOC_URL) == b"x" and cache.get(TICKERS_URL) == b"x"
    now[0] += 24 * 3600
    assert cache.get(TICKERS_URL) is None
    assert cache.get(DOC_URL) == b"x"  # süresiz


def test_legacy_ttl_argument_applies_to_all_urls(tmp_path):
    cache = FileCache(str(tmp_path), ttl_seconds=0, clock=lambda: 1e12)
    cache.set(DOC_URL, b"x")
    assert cache.get(DOC_URL) is None


def test_fresh_skips_cache_read_but_writes(tmp_path, fake_clock):
    fetcher = FakeFetcher({SUB_URL: [(200, b'{"v": 1}'), (200, b'{"v": 2}')]})
    client = make_client(fetcher, fake_clock, cache=FileCache(str(tmp_path)))
    assert client.submissions(1) == {"v": 1}
    assert client.submissions(1) == {"v": 1}  # önbellekten
    assert client.submissions(1, fresh=True) == {"v": 2}  # önbellek okunmadı
    assert len(fetcher.calls) == 2
    assert client.submissions(1) == {"v": 2}  # taze yanıt önbelleğe yazıldı


def test_sec_error_carries_status(fake_clock):
    client = make_client(FakeFetcher({SUB_URL: (403, b"")}), fake_clock)
    with pytest.raises(SecError) as exc:
        client.get_bytes(SUB_URL)
    assert exc.value.status == 403


def test_redirects_followed_only_within_sec_gov():
    """SEC-17: sec.gov dışına yönlendirme izlenmez (User-Agent'taki e-posta sızmasın)."""
    from finsight.sec_client import follow_sec_redirects, is_allowed_sec_url

    assert is_allowed_sec_url(SUB_URL) and is_allowed_sec_url(DOC_URL)
    for bad in ("http://www.sec.gov/x", "https://sec.gov.kotu.example/x",
                "https://kotu.example/sec.gov", "https://evilsec.gov/x"):
        assert not is_allowed_sec_url(bad), bad

    visited = []

    def get(url):
        visited.append(url)
        if url == SUB_URL:
            return 301, b"", "https://www.sec.gov/moved.json"
        return 200, b"ok", None

    assert follow_sec_redirects(get, SUB_URL) == (200, b"ok")
    assert visited == [SUB_URL, "https://www.sec.gov/moved.json"]

    visited.clear()
    with pytest.raises(SecError):
        follow_sec_redirects(lambda u: (visited.append(u) or (302, b"", "https://kotu.example/")),
                             SUB_URL)
    assert visited == [SUB_URL]  # kötü adrese istek gitmedi
    with pytest.raises(SecError):
        follow_sec_redirects(lambda u: (302, b"", u), SUB_URL)  # döngü
