import pytest

from ddbot.config import Settings
from ddbot.ui import channel_choice, schedule_choice, template_keyboard, valid_button_url


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
        "https://t.me/AliceSmartPicksEN",
        "https://thealiceai.com/?code=N6WVL",
    ]
    assert [url for _, url in english] == expected_urls
    assert [url for _, url in korean] == [
        "https://t.me/thealiceai/28604",
        "https://t.me/AliceSmartPicksKR",
        "https://thealiceai.com/?code=N6WVL",
    ]
    assert len(template_keyboard(english).inline_keyboard) == 3


def test_traditional_chinese_channel_and_template(tmp_path) -> None:
    settings = Settings(
        bot_token="token",
        admin_user_ids="12",
        database_path=tmp_path / "db.sqlite3",
    )

    assert settings.channels["traditional"] == "@alicesmartpick"
    assert settings.topics["traditional"] == 28601
    traditional = settings.template_buttons("traditional")
    assert [text for text, _ in traditional] == [
        "💬 加入 Alice 聊天室",
        "🔮 查看預測頻道",
        "🤑 開始使用 Alice 獲利",
    ]
    assert traditional[1][1] == "https://t.me/alicesmartpick"
    assert traditional[2][1] == "https://thealiceai.com/?code=N6WVL"

    keyboard = channel_choice(settings.channels)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Alice（繁體中文）" in labels
    assert "全部 3 个频道" in labels
    assert "draft:target:traditional" in callbacks
    assert "draft:target:all" in callbacks


def test_schedule_choice_contains_only_fixed_intervals() -> None:
    labels = [button.text for row in schedule_choice().inline_keyboard for button in row]
    assert "仅发布一次" in labels
    assert "每 30 分钟" in labels
    assert "每 1 小时" in labels
    assert "每 6 小时" in labels
    assert "每 24 小时" in labels
    assert "自定义间隔" not in labels


def test_latest_push_uses_hourly_interval_and_enabled_topic_mapping(tmp_path) -> None:
    settings = Settings(
        bot_token="token", admin_user_ids="12", database_path=tmp_path / "db.sqlite3",
        topic_latest_push_enabled=True,
    )

    assert settings.topic_latest_push_interval_seconds == 3600
    assert settings.enabled_topics == {
        "eai": settings.topic_eai,
        "korean": settings.topic_korean,
        "russian": 348731,
    }


def test_russian_channel_topic_and_template():
    settings = Settings(
        bot_token="123:test", admin_user_ids="12",
        channel_alice_russian="@test_russian", topic_russian=12345,
        topic_latest_push_enabled=True,
    )
    assert settings.channels["russian"] == "@test_russian"
    assert settings.enabled_topics["russian"] == 12345
    buttons = settings.template_buttons("russian")
    assert len(buttons) == 3
    assert buttons[0][1] == "https://t.me/c/3869352469/12345"
    assert buttons[1][1] == "https://t.me/test_russian"
    callbacks = [
        button.callback_data
        for row in channel_choice(settings.channels).inline_keyboard for button in row
    ]
    assert "draft:target:russian" in callbacks


def test_topic_monitoring_remains_enabled_without_latest_repush():
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", topic_latest_push_enabled=False,
        topic_russian=12345,
    )
    assert settings.enabled_topics == {}
    assert settings.monitored_topics["russian"] == 12345
    assert "eai" in settings.monitored_topics


def test_latest_topic_repush_is_disabled_by_default():
    settings = Settings(bot_token="123:test", admin_user_ids="12")
    assert settings.topic_latest_push_enabled is False
    assert settings.enabled_topics == {}


def test_russian_channel_without_topic_only_publishes_to_channel():
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", channel_alice_russian="@test_russian",
        topic_russian=None,
    )
    assert "russian" in settings.channels
    assert not settings.should_relay_to_topic("russian")
