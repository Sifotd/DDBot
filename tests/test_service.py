from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ddbot.config import Settings
from ddbot.db import Database
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
