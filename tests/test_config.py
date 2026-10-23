"""Environment parsing in app.config.

These exist because the GitHub Actions pipeline died at import time with
`ValueError: could not convert string to float: ''`. Actions substitutes an empty
string for any repository variable that is not configured, so `os.environ.get(name,
default)` returns "" instead of the default. Every setting must therefore treat a
blank value as "not configured".
"""
from app.config import _bool, _env, _float, _int


def test_env_blank_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "")
    assert _env("SOME_SETTING", "fallback") == "fallback"


def test_env_whitespace_only_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "   ")
    assert _env("SOME_SETTING", "fallback") == "fallback"


def test_env_value_is_stripped_but_kept(monkeypatch):
    monkeypatch.setenv("SOME_SETTING", "  deepl  ")
    assert _env("SOME_SETTING", "google_free") == "deepl"


def test_missing_variable_uses_default():
    assert _env("DEFINITELY_NOT_EXPORTED_BY_ANYONE", "default") == "default"


def test_numeric_helpers_survive_blank_values(monkeypatch):
    # The exact failure that broke the scheduled run.
    monkeypatch.setenv("REQUEST_DELAY", "")
    monkeypatch.setenv("MAX_AGE_HOURS", "")
    assert _float("REQUEST_DELAY", 2.0) == 2.0
    assert _int("MAX_AGE_HOURS", 120) == 120
    assert _float("REQUEST_DELAY", 2.0) + 1 == 3.0


def test_bool_helpers_survive_blank_values(monkeypatch):
    monkeypatch.setenv("SCRAPE_KCNA", "")
    assert _bool("SCRAPE_KCNA", True) is True       # blank must not switch a source off
    assert _bool("SCRAPE_VOK", False) is False
    monkeypatch.setenv("SCRAPE_VOK", "1")
    assert _bool("SCRAPE_VOK", False) is True
    monkeypatch.setenv("SCRAPE_VOK", "off")
    assert _bool("SCRAPE_VOK", True) is False


def test_module_defaults_are_reachable_when_everything_is_blank():
    """Import config the way a CI job with no configured variables sees it."""
    import importlib
    import os
    import subprocess
    import sys

    blank = dict(os.environ)
    for key in ("DATA_DIR", "REQUEST_DELAY", "MAX_AGE_HOURS", "MAX_ARTICLES_PER_RUN",
                "SCRAPE_KCNA", "SCRAPE_RODONG", "SCRAPE_VOK", "SCRAPE_MAGAZINES",
                "TRANSLATE_PROVIDER", "TRANSLATE_TARGET", "TRANSLATE_API_KEY"):
        blank[key] = ""
    out = subprocess.run(
        [sys.executable, "-c",
         "import app.config as c; "
         "print(c.REQUEST_DELAY, c.MAX_AGE_HOURS, c.SCRAPE_KCNA, c.TRANSLATE_PROVIDER)"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=blank, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "2.0 120 True google_free"
