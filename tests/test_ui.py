import pytest

from ddbot.config import Settings
from ddbot.ui import template_keyboard, valid_button_url


@pytest.mark.parametrize(
    "url", ["https://example.com/a", "http://example.com", "tg://resolve?domain=x"]
)
def test_valid_urls(url: str) -> None:
    assert valid_button_url(url)


@pytest.mark.parametrize("url", ["example.com", "javascript:alert(1)", "", "ftp://example.com"])
def test_invalid_urls(url: str) -> None:
    assert not valid_button_url(url)


def test_settings_parses_admin_ids(tmp_path) -> None:
    settings = Settings(
        bot_token="token", admin_user_ids="12, 34", database_path=tmp_path / "db.sqlite3"
    )
    assert settings.admin_user_ids == frozenset({12, 34})


def test_settings_parses_admin_ids_from_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("ADMIN_USER_IDS", "12,34")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))

    settings = Settings()

    assert settings.admin_user_ids == frozenset({12, 34})


def test_language_templates_have_three_buttons(tmp_path) -> None:
    settings = Settings(
        bot_token="token", admin_user_ids="12", database_path=tmp_path / "db.sqlite3"
    )
    english = settings.template_buttons("eai")
    korean = settings.template_buttons("korean")
    assert len(english) == len(korean) == 3
    assert "Join Alice Chat" in english[0][0]
    assert "채팅방" in korean[0][0]
    expected_urls = [
        "https://t.me/thealiceai/28604",
        "https://t.me/aliceeaichannel",
        "https://thealiceai.com/saba",
    ]
    assert [url for _, url in english] == expected_urls
    assert [url for _, url in korean] == [
        "https://t.me/thealiceai/28604",
        "https://t.me/alicekoreanbet",
        "https://thealiceai.com/saba",
    ]
    assert len(template_keyboard(english).inline_keyboard) == 3
