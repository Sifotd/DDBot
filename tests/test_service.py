from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ddbot.config import Settings
from ddbot.db import Database
from ddbot.models import DeliveryStatus
from ddbot.service import PublishingService


@pytest.mark.asyncio
async def test_bot_publish_immediately_relays_to_matching_topic(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    settings = Settings(
        bot_token="123:test", admin_user_ids="12", database_path=tmp_path / "test.sqlite3"
    )
    channel_message = SimpleNamespace(message_id=77, chat=SimpleNamespace(id=-100111))
    topic_message = SimpleNamespace(message_id=88)
    bot = SimpleNamespace(
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
        {"korean": "@alicekoreanbet"},
    )

    assert [result.ok for result in results] == [True, True]
    assert results[1].channel == "Topic 23669"
    bot.forward_message.assert_awaited_once_with(
        chat_id=-1003869352469,
        from_chat_id=-100111,
        message_id=77,
        message_thread_id=23669,
    )


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
        {"eai": "@aliceeaichannel"},
    )
    await db.update_delivery(post_id, "eai", DeliveryStatus.PUBLISHED, message_id=10)
    delivery = (await db.get_deliveries(post_id))[0]
    topic_message = SimpleNamespace(message_id=99)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=topic_message))
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
