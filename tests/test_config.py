from pathlib import Path

import pytest

from tome.config import ConfigError, Settings


@pytest.fixture
def clean_env(monkeypatch):
    for name in (
        "TOME_HOME", "TOME_HOST", "TOME_PORT", "TOME_ALLOWED_HOSTS", "TOME_ALLOWED_ORIGINS",
        "TOME_NYTTIG_URL", "TOME_NYTTIG_USER", "TOME_NYTTIG_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_home_is_required(clean_env):
    with pytest.raises(ConfigError, match="TOME_HOME"):
        Settings.from_env()


def test_reads_environment(clean_env, tmp_path):
    clean_env.setenv("TOME_HOME", str(tmp_path))
    clean_env.setenv("TOME_HOST", "0.0.0.0")
    clean_env.setenv("TOME_PORT", "9000")
    clean_env.setenv("TOME_ALLOWED_HOSTS", "tome.example.org:*, other.local ,")
    clean_env.setenv("TOME_ALLOWED_ORIGINS", "https://tome.example.org")
    s = Settings.from_env()
    assert s.home == tmp_path
    assert (s.host, s.port) == ("0.0.0.0", 9000)
    assert s.allowed_hosts == ("tome.example.org:*", "other.local")
    assert s.allowed_origins == ("https://tome.example.org",)


def test_reads_nyttig_settings(clean_env, tmp_path):
    clean_env.setenv("TOME_HOME", str(tmp_path))
    clean_env.setenv("TOME_NYTTIG_URL", "https://news.example.org")
    clean_env.setenv("TOME_NYTTIG_USER", "tome")
    clean_env.setenv("TOME_NYTTIG_PASSWORD", "secret")
    s = Settings.from_env()
    assert (s.nyttig_url, s.nyttig_user, s.nyttig_password) == ("https://news.example.org", "tome", "secret")


def test_nyttig_settings_default_to_none(clean_env, tmp_path):
    s = Settings.from_env(home=tmp_path)
    assert (s.nyttig_url, s.nyttig_user, s.nyttig_password) == (None, None, None)


def test_defaults(clean_env, tmp_path):
    s = Settings.from_env(home=tmp_path)
    assert (s.host, s.port, s.allowed_hosts, s.allowed_origins) == ("127.0.0.1", 8765, (), ())


def test_overrides_win_and_none_is_ignored(clean_env, tmp_path):
    clean_env.setenv("TOME_HOME", str(tmp_path / "env"))
    clean_env.setenv("TOME_PORT", "9000")
    s = Settings.from_env(home=None, port=8801)
    assert s.home == tmp_path / "env"
    assert s.port == 8801


def test_bad_port(clean_env, tmp_path):
    clean_env.setenv("TOME_HOME", str(tmp_path))
    clean_env.setenv("TOME_PORT", "http")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_home_is_expanded(clean_env):
    assert Settings.from_env(home="~/bank").home == Path("~/bank").expanduser()
