from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import DeleteMessage, SendMessage

from ddbot.config import Settings
from ddbot.db import Database
from ddbot.models import DeliveryStatus
from ddbot.scheduler import PushScheduler
from ddbot.service import PublishingService, telegram_call


@pytest.mark.asyncio
async def test_publish_two_photos_in_order_and_persists_all_message_ids(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", database_path=tmp_path / "test.sqlite3",
        relay_traditional_to_topic=False,
    )
    sent = [
        SimpleNamespace(message_id=71, chat=SimpleNamespace(id=-100111)),
        SimpleNamespace(message_id=72, chat=SimpleNamespace(id=-100111)),
    ]
    bot = SimpleNamespace(send_media_group=AsyncMock(return_value=sent))
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    post_id, results = await service.publish(
        12,
        "caption",
        ["photo-1", "photo-2"],
        None,
        None,
        {"traditional": "@alicesmartpick"},
    )

    assert [result.ok for result in results] == [True]
    bot.send_media_group.assert_awaited_once()
    call = bot.send_media_group.await_args
    assert call.args[0] == "@alicesmartpick"
    assert [item.media for item in call.kwargs["media"]] == ["photo-1", "photo-2"]
    assert call.kwargs["media"][0].caption == "caption"
    assert call.kwargs["media"][1].caption is None
    post = await db.get_post(post_id)
    delivery = (await db.get_deliveries(post_id))[0]
    assert post and post.photo_file_ids == ["photo-1", "photo-2"]
    assert delivery.message_ids == [71, 72]


@pytest.mark.asyncio
async def test_bot_publish_immediately_relays_to_matching_topic(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", database_path=tmp_path / "test.sqlite3"
    )
    channel_message = SimpleNamespace(
        message_id=77,
        chat=SimpleNamespace(id=-100111),
        photo=[SimpleNamespace(file_id="photo-id")],
        caption="test",
    )
    topic_message = SimpleNamespace(message_id=88)
    bot = SimpleNamespace(
        id=123,
        send_photo=AsyncMock(return_value=channel_message),
        forward_message=AsyncMock(return_value=topic_message),
    )
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    _, results = await service.publish(
        12,
        "test",
        "photo-id",
        "__template__",
        None,
        {"korean": "@AliceSmartPicksKR"},
    )

    assert [result.ok for result in results] == [True, True]
    assert results[1].channel == "Topic 23669"
    bot.forward_message.assert_awaited_once_with(
        chat_id=-1003869352469,
        from_chat_id=-100111,
        message_id=77,
        message_thread_id=23669,
    )
    latest = await db.get_latest_topic_message(-1003869352469, 23669)
    assert latest and latest.message_id == 88 and latest.sent_by_bot


@pytest.mark.asyncio
async def test_traditional_publish_does_not_relay_to_topic_by_default(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", database_path=tmp_path / "test.sqlite3"
    )
    channel_message = SimpleNamespace(
        message_id=77, chat=SimpleNamespace(id=-100111), text="繁中内容", photo=None
    )
    topic_message = SimpleNamespace(message_id=88)
    bot = SimpleNamespace(
        id=123,
        send_message=AsyncMock(return_value=channel_message),
        forward_message=AsyncMock(return_value=topic_message),
    )
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    _, results = await service.publish(
        12,
        "繁中内容",
        None,
        "__template__",
        None,
        {"traditional": "@alicesmartpick"},
    )

    assert [result.ok for result in results] == [True]
    assert results[0].channel == "@alicesmartpick"
    bot.forward_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_traditional_recurring_topic_push_is_not_scheduled_by_default(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", database_path=tmp_path / "test.sqlite3"
    )
    service = PublishingService(SimpleNamespace(), db, settings)  # type: ignore[arg-type]
    scheduler = PushScheduler(db, service)
    post_id = await db.create_post(
        12,
        "内容",
        None,
        None,
        None,
        {"eai": "@AliceSmartPicksEN", "traditional": "@alicesmartpick"},
    )

    await scheduler.schedule(post_id, ["eai", "traditional"], 3600)

    schedules = await db.get_scheduled_pushes(active_only=True)
    assert [item.channel_key for item in schedules] == ["eai"]


@pytest.mark.asyncio
async def test_scheduled_push_goes_directly_to_matching_topic(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(
        12,
        "定时内容",
        None,
        "打开",
        "https://example.com",
        {"eai": "@AliceSmartPicksEN"},
    )
    await db.update_delivery(post_id, "eai", DeliveryStatus.PUBLISHED, message_id=10)
    delivery = (await db.get_deliveries(post_id))[0]
    topic_message = SimpleNamespace(message_id=99)
    bot = SimpleNamespace(id=123, send_message=AsyncMock(return_value=topic_message))
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", database_path=tmp_path / "test.sqlite3"
    )
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    result = await service.send_to_topic(delivery)

    assert result.message_id == 99
    bot.send_message.assert_awaited_once()
    call = bot.send_message.await_args
    assert call.args[:2] == (-1003869352469, "定时内容")
    assert call.kwargs["message_thread_id"] == 28604
    latest = await db.get_latest_topic_message(-1003869352469, 28604)
    assert latest and latest.message_id == 99 and latest.sent_by_bot


@pytest.mark.asyncio
async def test_scheduled_push_never_sends_bot_command(tmp_path: Path) -> None:
    settings = Settings(
        bot_token="123:test",
        admin_user_ids="12",
        database_path=tmp_path / "test.sqlite3",
    )
    db = Database(settings.database_path)
    await db.initialize()
    post_id = await db.create_post(
        12, "/weekly_rank", None, None, None, {"eai": settings.channel_alice_eai}
    )
    bot = SimpleNamespace(id=123, send_message=AsyncMock())
    scheduler = PushScheduler(
        db, PublishingService(bot, db, settings)  # type: ignore[arg-type]
    )
    await scheduler.schedule(post_id, ["eai"], 3600)
    schedule = (await db.get_scheduled_pushes(active_only=True))[0]

    await scheduler._push(schedule.id, post_id, "eai", schedule.next_run_at)

    bot.send_message.assert_not_awaited()
    updated = (await db.get_scheduled_pushes(post_id=post_id))[0]
    assert updated.active is False
    assert updated.last_error == "Bot 命令不允许自动定时推送"


@pytest.mark.asyncio
async def test_recurring_push_waits_for_user_message_even_when_latest_push_disabled(tmp_path):
    settings = Settings(
        bot_token="123:test", admin_user_ids="12",
        database_path=tmp_path / "db.sqlite3", topic_latest_push_enabled=False,
    )
    db = Database(settings.database_path)
    await db.initialize()
    post_id = await db.create_post(
        12, "scheduled", None, None, None, {"eai": settings.channel_alice_eai}
    )
    bot = SimpleNamespace(
        id=123, send_message=AsyncMock(return_value=SimpleNamespace(message_id=12))
    )
    scheduler = PushScheduler(db, PublishingService(bot, db, settings))
    await scheduler.schedule(post_id, ["eai"], 3600)
    schedule = (await db.get_scheduled_pushes(active_only=True))[0]
    await db.observe_topic_message(
        settings.target_group_id, settings.topic_eai, 10, 123, True, "text", "bot", None
    )
    await scheduler._push(schedule.id, post_id, "eai", schedule.next_run_at)
    bot.send_message.assert_not_awaited()
    updated = (await db.get_scheduled_pushes(active_only=True))[0]
    assert updated.next_run_at > schedule.next_run_at
    assert updated.last_error is None

    await db.observe_topic_message(
        settings.target_group_id, settings.topic_eai, 11, 77, False, "text", "user", None
    )
    await scheduler._push(updated.id, post_id, "eai", updated.next_run_at)
    bot.send_message.assert_awaited_once()
    updated = (await db.get_scheduled_pushes(active_only=True))[0]
    await scheduler._push(updated.id, post_id, "eai", updated.next_run_at)
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_album_delete_retries_only_messages_that_remain(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(
        1, "album", ["p1", "p2"], None, None, {"eai": "@channel"}
    )
    await db.update_delivery(
        post_id, "eai", DeliveryStatus.PUBLISHED, message_id=101, message_ids=[101, 102]
    )
    temporary = TelegramBadRequest(
        DeleteMessage(chat_id="@channel", message_id=102), "temporary failure"
    )
    bot = SimpleNamespace(delete_message=AsyncMock(side_effect=[True, temporary, True]))
    settings = Settings(bot_token="123:test", admin_user_ids="1", database_path=db.path)
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    await service.delete(post_id, await db.get_deliveries(post_id))
    partial = (await db.get_deliveries(post_id))[0]
    assert partial.status == DeliveryStatus.FAILED
    assert partial.message_ids == [102]

    await service.delete(post_id, [partial])
    finished = (await db.get_deliveries(post_id))[0]
    assert finished.status == DeliveryStatus.DELETED
    assert [call.args[1] for call in bot.delete_message.await_args_list] == [101, 102, 102]


@pytest.mark.asyncio
async def test_photo_replacement_retains_undeleted_old_message_ids(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(
        1, "new album", ["new1", "new2"], None, None, {"eai": "@channel"}
    )
    await db.update_delivery(
        post_id, "eai", DeliveryStatus.PUBLISHED, message_id=101, message_ids=[101, 102]
    )
    new_messages = [
        SimpleNamespace(message_id=201, chat=SimpleNamespace(id=-1001)),
        SimpleNamespace(message_id=202, chat=SimpleNamespace(id=-1001)),
    ]
    temporary = TelegramBadRequest(
        DeleteMessage(chat_id="@channel", message_id=102), "temporary failure"
    )
    bot = SimpleNamespace(
        send_media_group=AsyncMock(return_value=new_messages),
        delete_message=AsyncMock(side_effect=[True, temporary]),
    )
    settings = Settings(bot_token="123:test", admin_user_ids="1", database_path=db.path)
    service = PublishingService(bot, db, settings)  # type: ignore[arg-type]

    post = await db.get_post(post_id)
    assert post
    await service.replace_text_with_photo(post, await db.get_deliveries(post_id))

    delivery = (await db.get_deliveries(post_id))[0]
    assert delivery.message_ids == [201, 202]
    assert delivery.orphaned_message_ids == [102]
    assert delivery.status == DeliveryStatus.FAILED


@pytest.mark.asyncio
async def test_retry_after_waits_for_full_server_delay(monkeypatch) -> None:
    retry = TelegramRetryAfter(
        SendMessage(chat_id=1, text="x"), "flood control", retry_after=15
    )
    call = AsyncMock(side_effect=[retry, "ok"])
    sleep = AsyncMock()
    monkeypatch.setattr("ddbot.service.asyncio.sleep", sleep)

    assert await telegram_call(call) == "ok"
    sleep.assert_awaited_once_with(15)
