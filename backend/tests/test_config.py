"""config.py testleri: .env yükleme (python-dotenv isteğe bağlı) ve boş değer dayanıklılığı."""

import builtins
import os

import pytest

from finsight import config


def test_load_env_files_does_not_override_existing(monkeypatch, tmp_path):
    pytest.importorskip("dotenv")
    monkeypatch.delenv("FINSIGHT_DISABLE_DOTENV", raising=False)
    monkeypatch.setenv("FINSIGHT_TEST_KEEP", "kabuk")
    monkeypatch.delenv("FINSIGHT_TEST_NEW", raising=False)
    monkeypatch.delenv("FINSIGHT_TEST_FIRST", raising=False)
    first = tmp_path / "cwd.env"
    first.write_text("FINSIGHT_TEST_KEEP=dosya\nFINSIGHT_TEST_NEW=yeni\n"
                     "FINSIGHT_TEST_FIRST=calisma\n", encoding="utf-8")
    second = tmp_path / "root.env"
    second.write_text("FINSIGHT_TEST_FIRST=kok\n", encoding="utf-8")
    try:
        loaded = config.load_env_files([str(first), str(second), str(tmp_path / "yok.env")])
        assert loaded == [str(first), str(second)]
        assert os.environ["FINSIGHT_TEST_KEEP"] == "kabuk"  # mevcut değer ezilmez
        assert os.environ["FINSIGHT_TEST_NEW"] == "yeni"
        assert os.environ["FINSIGHT_TEST_FIRST"] == "calisma"  # ilk dosya önceliklidir
    finally:
        os.environ.pop("FINSIGHT_TEST_NEW", None)
        os.environ.pop("FINSIGHT_TEST_FIRST", None)


def test_load_env_files_can_be_disabled(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("FINSIGHT_TEST_DISABLED=1\n", encoding="utf-8")
    monkeypatch.setenv("FINSIGHT_DISABLE_DOTENV", "1")
    monkeypatch.delenv("FINSIGHT_TEST_DISABLED", raising=False)
    assert config.load_env_files([str(env)]) == []
    assert "FINSIGHT_TEST_DISABLED" not in os.environ


def test_load_env_files_without_python_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("FINSIGHT_TEST_NODOTENV=1\n", encoding="utf-8")
    monkeypatch.delenv("FINSIGHT_DISABLE_DOTENV", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dotenv":
            raise ImportError("dotenv yok")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert config.load_env_files([str(env)]) == []


def test_default_env_files_cwd_first_then_repo_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    files = config.default_env_files()
    assert files[0] == os.path.realpath(str(tmp_path / ".env"))
    assert files[-1].endswith(os.sep + ".env") and len(files) == 2


def test_empty_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("SEC_MAX_RPS", "")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SEC_CACHE_DIR", " ")
    assert config.get_max_rps() == 8.0
    assert config.get_database_url() == "sqlite:///finsight.db"
    assert config.get_cache_dir() == ".sec_cache"
    monkeypatch.setenv("SEC_MAX_RPS", "hızlı")
    with pytest.raises(config.ConfigError):
        config.get_max_rps()
    monkeypatch.setenv("SEC_MAX_RPS", "12")
    with pytest.raises(config.ConfigError):
        config.get_max_rps()


# --------------------------------------------------------------------------- Aşama 4 (SEC-16/5)


def test_sec_json_ttl_default_valid_and_lower_bound(monkeypatch):
    monkeypatch.delenv("FINSIGHT_SEC_JSON_TTL", raising=False)
    assert config.get_sec_json_ttl() == 3600
    monkeypatch.setenv("FINSIGHT_SEC_JSON_TTL", "")
    assert config.get_sec_json_ttl() == 3600
    monkeypatch.setenv("FINSIGHT_SEC_JSON_TTL", "300")
    assert config.get_sec_json_ttl() == 300
    for bad in ("0", "299", "-1", "abc", "nan", "inf"):
        monkeypatch.setenv("FINSIGHT_SEC_JSON_TTL", bad)
        with pytest.raises(config.ConfigError):
            config.get_sec_json_ttl()


def test_auto_refresh_minutes_off_or_at_least_30(monkeypatch):
    monkeypatch.delenv("FINSIGHT_AUTO_REFRESH_MINUTES", raising=False)
    assert config.get_auto_refresh_minutes() == 0
    monkeypatch.setenv("FINSIGHT_AUTO_REFRESH_MINUTES", "0")
    assert config.get_auto_refresh_minutes() == 0
    monkeypatch.setenv("FINSIGHT_AUTO_REFRESH_MINUTES", "60")
    assert config.get_auto_refresh_minutes() == 60
    for bad in ("1", "29", "-5", "x", "nan"):
        monkeypatch.setenv("FINSIGHT_AUTO_REFRESH_MINUTES", bad)
        with pytest.raises(config.ConfigError):
            config.get_auto_refresh_minutes()
