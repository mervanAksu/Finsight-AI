import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = pathlib.Path(__file__).parent / "fixtures"

UA = "FinSight-AI Test test@example.com"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeClock:
    """Gerçekten beklemeden zamanı ilerleten sahte saat."""

    def __init__(self):
        self.now = 0.0
        self.calls: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds: float):
        self.now += seconds


class FakeFetcher:
    """SEC yerine sabit yanıtlar döndürür; gelen başlıkları kaydeder."""

    def __init__(self, routes: dict[str, list[tuple[int, bytes]] | tuple[int, bytes]]):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        resp = self.routes.get(url, (404, b""))
        if isinstance(resp, list):
            return resp.pop(0) if len(resp) > 1 else resp[0]
        return resp


@pytest.fixture
def fake_clock():
    return FakeClock()
