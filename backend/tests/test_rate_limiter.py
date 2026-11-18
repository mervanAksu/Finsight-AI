import pytest

from finsight.rate_limiter import RateLimiter


def test_no_one_second_window_exceeds_limit(fake_clock):
    limiter = RateLimiter(8, clock=fake_clock, sleep=fake_clock.sleep)
    stamps = []
    for _ in range(100):
        limiter.acquire()
        stamps.append(fake_clock.now)
    # Herhangi bir 1 saniyelik pencerede en fazla 8 istek (NFR-02)
    for i, t in enumerate(stamps):
        in_window = [s for s in stamps[i:] if s < t + 1.0]
        assert len(in_window) <= 8
    assert stamps[-1] - stamps[0] >= 99 / 8


@pytest.mark.parametrize("rps", [0, 10, 25])
def test_rejects_rates_at_or_above_sec_limit(rps):
    with pytest.raises(ValueError):
        RateLimiter(rps)
