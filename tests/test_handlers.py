import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ddbot import handlers


class FakeState:
    def __init__(self, data: dict) -> None:
        self.data = data

    async def get_data(self) -> dict:
        return self.data.copy()

    async def update_data(self, **values) -> None:
        self.data.update(values)

    async def clear(self) -> None:
        self.data.clear()


@pytest.mark.asyncio
async def test_double_publish_click_only_publishes_once() -> None:
    state = FakeState(
        {
            "target_keys": ["eai"],
            "text": "content",
            "interval_seconds": None,
        }
    )

    async def publish(*args, **kwargs):
        await asyncio.sleep(0.02)
        return 1, []

    service = SimpleNamespace(publish=AsyncMock(side_effect=publish))
    scheduler = SimpleNamespace(schedule=AsyncMock())
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=987654),
        message=None,
        answer=AsyncMock(),
    )
    settings = SimpleNamespace(channels={"eai": "@channel"})

    await asyncio.gather(
        handlers.publish_draft(query, state, settings, service, scheduler),
        handlers.publish_draft(query, state, settings, service, scheduler),
    )

    service.publish.assert_awaited_once()
    scheduler.schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_album_waits_until_last_item_has_settled(monkeypatch) -> None:
    monkeypatch.setattr(handlers, "ALBUM_SETTLE_SECONDS", 0.01)

    def message(message_id: int):
        return SimpleNamespace(
            message_id=message_id,
            chat=SimpleNamespace(id=1),
            from_user=SimpleNamespace(id=2),
            media_group_id="album",
            photo=[SimpleNamespace(file_id=f"photo-{message_id}")],
            caption="caption" if message_id == 1 else None,
        )

    first = asyncio.create_task(handlers.collect_photos(message(1)))
    await asyncio.sleep(0.007)
    second = asyncio.create_task(handlers.collect_photos(message(2)))
    await asyncio.sleep(0.007)
    third = asyncio.create_task(handlers.collect_photos(message(3)))
    results = await asyncio.gather(first, second, third)

    assert results[0] == (["photo-1", "photo-2", "photo-3"], "caption")
    assert results[1:] == [None, None]


def test_content_length_uses_telegram_utf16_limits() -> None:
    assert handlers.content_length_error("a" * 4096, has_photo=False) is None
    assert handlers.content_length_error("a" * 4097, has_photo=False)
    assert handlers.content_length_error("😀" * 512, has_photo=True) is None
    assert handlers.content_length_error("😀" * 513, has_photo=True)
