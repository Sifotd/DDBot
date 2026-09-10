from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, PhotoSize, User

from ddbot.config import Settings
from ddbot.db import Database
from ddbot.handlers import EnabledTopicMessageFilter, observe_latest_topic_message, relay_to_topic
from ddbot.scheduler import LatestTopicScheduler
from ddbot.service import PublishingService


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token="123:test",
        admin_user_ids="12",
        database_path=tmp_path / "test.sqlite3",
        topic_latest_push_enabled=True,
    )


@pytest.mark.asyncio
async def test_text_is_pushed_once_to_its_original_topic(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    await db.observe_topic_message(
        settings.target_group_id,
        settings.topic_eai,
        10,
        77,
        False,
        "text",
        "latest text",
        None,
    )
    bot = SimpleNamespace(
        id=123,
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=11)),
    )
    scheduler = LatestTopicScheduler(
        db, PublishingService(bot, db, settings)  # type: ignore[arg-type]
    )

    await scheduler.run_once()
    await scheduler.run_once()

    bot.send_message.assert_awaited_once_with(
        settings.target_group_id,
        "latest text",
        message_thread_id=settings.topic_eai,
    )
    latest = await db.get_latest_topic_message(
        settings.target_group_id, settings.topic_eai
    )
    assert latest and latest.message_id == 11
    assert latest.sent_by_bot is True
    assert latest.last_pushed_message_id == 10
    assert latest.pushed_message_id == 11


@pytest.mark.asyncio
async def test_latest_message_from_this_bot_is_not_pushed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    await db.observe_topic_message(
        settings.target_group_id,
        settings.topic_eai,
        20,
        123,
        True,
        "text",
        "bot output",
        None,
    )
    bot = SimpleNamespace(id=123, send_message=AsyncMock())
    scheduler = LatestTopicScheduler(
        db, PublishingService(bot, db, settings)  # type: ignore[arg-type]
    )

    await scheduler.run_once()

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_photo_and_caption_stay_in_matching_topic(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    await db.observe_topic_message(
        settings.target_group_id,
        settings.topic_korean,
        30,
        77,
        False,
        "photo",
        "photo caption",
        "largest-photo-id",
    )
    bot = SimpleNamespace(
        id=123,
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=31)),
    )
    scheduler = LatestTopicScheduler(
        db, PublishingService(bot, db, settings)  # type: ignore[arg-type]
    )

    await scheduler.run_once()

    bot.send_photo.assert_awaited_once_with(
        settings.target_group_id,
        "largest-photo-id",
        caption="photo caption",
        message_thread_id=settings.topic_korean,
    )


@pytest.mark.asyncio
async def test_one_topic_failure_does_not_stop_other_topics(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    for topic_id, message_id in ((settings.topic_eai, 40), (settings.topic_korean, 50)):
        await db.observe_topic_message(
            settings.target_group_id,
            topic_id,
            message_id,
            77,
            False,
            "text",
            f"topic {topic_id}",
            None,
        )

    async def send_message(chat_id: int, text: str, *, message_thread_id: int):
        if message_thread_id == settings.topic_eai:
            raise RuntimeError("first topic is broken")
        return SimpleNamespace(message_id=51)

    bot = SimpleNamespace(id=123, send_message=AsyncMock(side_effect=send_message))
    scheduler = LatestTopicScheduler(
        db, PublishingService(bot, db, settings)  # type: ignore[arg-type]
    )

    await scheduler.run_once()

    assert bot.send_message.await_count == 2
    korean = await db.get_latest_topic_message(
        settings.target_group_id, settings.topic_korean
    )
    assert korean and korean.message_id == 51 and korean.sent_by_bot


@pytest.mark.asyncio
async def test_unsupported_latest_type_is_skipped_and_logged_in_db(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    await db.observe_topic_message(
        settings.target_group_id,
        settings.topic_eai,
        60,
        77,
        False,
        "video",
        None,
        None,
    )
    bot = SimpleNamespace(id=123, send_message=AsyncMock(), send_photo=AsyncMock())
    scheduler = LatestTopicScheduler(
        db, PublishingService(bot, db, settings)  # type: ignore[arg-type]
    )

    await scheduler.run_once()

    latest = await db.get_latest_topic_message(
        settings.target_group_id, settings.topic_eai
    )
    assert latest and latest.last_error == "unsupported message type: video"
    bot.send_message.assert_not_awaited()
    bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_topic_listener_records_photo_and_identifies_this_bot(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    bot = SimpleNamespace(id=123)
    message = Message(
        message_id=70,
        date=datetime.now(UTC),
        chat=Chat(id=settings.target_group_id, type="supergroup"),
        message_thread_id=settings.topic_eai,
        from_user=User(id=123, is_bot=True, first_name="DDBot"),
        photo=[
            PhotoSize(file_id="small", file_unique_id="s", width=10, height=10),
            PhotoSize(file_id="large", file_unique_id="l", width=100, height=100),
        ],
        caption="caption",
    )

    await observe_latest_topic_message(
        message,
        settings,
        db,
        SimpleNamespace(bot=bot),  # type: ignore[arg-type]
    )

    latest = await db.get_latest_topic_message(
        settings.target_group_id, settings.topic_eai
    )
    assert latest and latest.photo_file_id == "large"
    assert latest.text == "caption"
    assert latest.sent_by_bot is True


@pytest.mark.asyncio
async def test_topic_bot_command_is_never_automatically_reposted(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    bot = SimpleNamespace(id=123, send_message=AsyncMock())
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]
    message = Message(
        message_id=75,
        date=datetime.now(UTC),
        chat=Chat(id=settings.target_group_id, type="supergroup"),
        message_thread_id=settings.topic_eai,
        from_user=User(id=77, is_bot=False, first_name="User"),
        text="/weekly_rank",
    )

    await observe_latest_topic_message(message, settings, db, service)
    await LatestTopicScheduler(db, service).run_once()

    bot.send_message.assert_not_awaited()
    latest = await db.get_latest_topic_message(
        settings.target_group_id, settings.topic_eai
    )
    assert latest and latest.content_type == "bot_command"
    assert latest.last_error == "unsupported message type: bot_command"


@pytest.mark.asyncio
async def test_channel_relay_output_is_recorded_as_bot_topic_message(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    bot = SimpleNamespace(
        id=123,
        forward_message=AsyncMock(return_value=SimpleNamespace(message_id=81)),
    )
    source = SimpleNamespace(
        chat=SimpleNamespace(id=-100200, username="AliceSmartPicksEN"),
        message_id=80,
        bot=bot,
        text="channel text",
        photo=None,
    )
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    await relay_to_topic(
        source,  # type: ignore[arg-type]
        settings,
        db,
        service,
        replace=False,
    )

    latest = await db.get_latest_topic_message(
        settings.target_group_id, settings.topic_eai
    )
    assert latest and latest.message_id == 81 and latest.sent_by_bot


@pytest.mark.asyncio
async def test_russian_channel_post_is_forwarded_to_russian_topic(tmp_path: Path) -> None:
    settings = Settings(
        bot_token="123:test",
        admin_user_ids="12",
        database_path=tmp_path / "test.sqlite3",
        channel_alice_russian="@AliceSmartPicksRU",
        topic_russian=348731,
    )
    db = Database(settings.database_path)
    await db.initialize()
    bot = SimpleNamespace(
        id=123,
        forward_message=AsyncMock(return_value=SimpleNamespace(message_id=91)),
    )
    source = SimpleNamespace(
        chat=SimpleNamespace(id=-100300, username="AliceSmartPicksRU"),
        message_id=90,
        bot=bot,
        text="Прогноз на сегодня",
        photo=None,
    )
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    await relay_to_topic(
        source,  # type: ignore[arg-type]
        settings,
        db,
        service,
        replace=False,
    )

    bot.forward_message.assert_awaited_once_with(
        chat_id=settings.target_group_id,
        from_chat_id=-100300,
        message_id=90,
        message_thread_id=348731,
    )
    relay = await db.get_relay(-100300, 90)
    assert relay and relay["channel_key"] == "russian"
    assert relay["topic_id"] == 348731
    assert relay["status"] == "forwarded"


@pytest.mark.asyncio
async def test_edited_channel_album_stays_grouped_and_replaces_old_copies(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    sources = [
        SimpleNamespace(
            chat=SimpleNamespace(id=-100200), message_id=80, text="edited", photo=None
        ),
        SimpleNamespace(
            chat=SimpleNamespace(id=-100200), message_id=81, text=None, photo=None
        ),
    ]
    for source, old_id in zip(sources, [180, 181], strict=True):
        await db.claim_relay(
            source.chat.id,
            source.message_id,
            "eai",
            settings.target_group_id,
            settings.topic_eai,
        )
        await db.finish_relay(
            source.chat.id, source.message_id, "forwarded", old_id
        )
    forwarded = [SimpleNamespace(message_id=280), SimpleNamespace(message_id=281)]
    bot = SimpleNamespace(
        id=123,
        forward_messages=AsyncMock(return_value=forwarded),
        delete_message=AsyncMock(return_value=True),
    )
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    result = await service.replace_relayed_messages(sources, "eai")  # type: ignore[arg-type]

    assert result.ok
    bot.forward_messages.assert_awaited_once_with(
        chat_id=settings.target_group_id,
        from_chat_id=-100200,
        message_ids=[80, 81],
        message_thread_id=settings.topic_eai,
    )
    assert [call.args[1] for call in bot.delete_message.await_args_list] == [180, 181]
    assert (await db.get_relay(-100200, 80))["forwarded_message_id"] == 280
    assert (await db.get_relay(-100200, 81))["forwarded_message_id"] == 281


@pytest.mark.asyncio
async def test_edited_album_item_updates_forwarded_copy_in_place(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    await db.claim_relay(-100200, 80, "eai", settings.target_group_id, settings.topic_eai)
    await db.finish_relay(-100200, 80, "forwarded", 180)
    source = SimpleNamespace(
        chat=SimpleNamespace(id=-100200),
        message_id=80,
        text=None,
        photo=[SimpleNamespace(file_id="edited-photo")],
        caption="edited caption",
    )
    bot = SimpleNamespace(
        id=123,
        edit_message_media=AsyncMock(return_value=True),
        forward_message=AsyncMock(),
    )
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    result = await service.edit_relayed_message(source, "eai")  # type: ignore[arg-type]

    assert result.ok
    bot.edit_message_media.assert_awaited_once()
    call = bot.edit_message_media.await_args
    assert call.kwargs["chat_id"] == settings.target_group_id
    assert call.kwargs["message_id"] == 180
    assert call.kwargs["media"].media == "edited-photo"
    bot.forward_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_russian_topic_observation_and_push_survive_restart(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.initialize()
    bot = SimpleNamespace(
        id=123, send_message=AsyncMock(return_value=SimpleNamespace(message_id=101))
    )
    service = PublishingService(bot, db, settings)
    message = Message(
        message_id=100, date=datetime.now(UTC),
        chat=Chat(id=-1003869352469, type="supergroup"),
        message_thread_id=348731,
        from_user=User(id=77, is_bot=False, first_name="User"),
        text="Привет!",
    )
    assert await EnabledTopicMessageFilter()(message, settings)
    await observe_latest_topic_message(message, settings, db, service)
    await LatestTopicScheduler(db, service).run_once()
    bot.send_message.assert_awaited_once_with(
        -1003869352469, "Привет!", message_thread_id=348731
    )
    restarted_db = Database(settings.database_path)
    await restarted_db.initialize()
    await LatestTopicScheduler(
        restarted_db, PublishingService(bot, restarted_db, settings)
    ).run_once()
    bot.send_message.assert_awaited_once()
