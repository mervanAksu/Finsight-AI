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
