from pathlib import Path

import pytest

from ddbot.db import Database
from ddbot.models import DeliveryStatus, PostStatus


@pytest.mark.asyncio
async def test_post_and_independent_delivery_results(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(
        123,
        "正文",
        "photo",
        "打开",
        "https://example.com",
        {"eai": "@aliceeaichannel", "korean": "@alicekoreanbet"},
    )

    await db.update_delivery(post_id, "eai", DeliveryStatus.PUBLISHED, message_id=10)
    await db.update_delivery(post_id, "korean", DeliveryStatus.FAILED, error="权限不足")

    post = await db.get_post(post_id)
    deliveries = await db.get_deliveries(post_id)
    assert post and post.status == PostStatus.PARTIAL
    assert deliveries[0].message_id == 10
    assert deliveries[1].message_id is None
    assert deliveries[1].last_error == "权限不足"


@pytest.mark.asyncio
async def test_update_content_and_deleted_status(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(123, "旧", None, None, None, {"eai": "@aliceeaichannel"})
    await db.update_delivery(post_id, "eai", DeliveryStatus.PUBLISHED, message_id=42)
    await db.update_post_content(post_id, text="新")
    await db.update_delivery(post_id, "eai", DeliveryStatus.DELETED)

    post = await db.get_post(post_id)
    assert post and post.text == "新" and post.status == PostStatus.DELETED


@pytest.mark.asyncio
async def test_each_channel_keeps_independent_content_snapshot(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(
        123,
        "原文",
        None,
        "按钮",
        "https://example.com",
        {"eai": "@aliceeaichannel", "korean": "@alicekoreanbet"},
    )
    deliveries = await db.get_deliveries(post_id)
    await db.update_delivery_content(
        deliveries[0].id, text="仅频道一修改", button_text=None, button_url=None
    )

    updated = await db.get_deliveries(post_id)
    assert updated[0].text == "仅频道一修改"
    assert updated[0].button_text is None
    assert updated[1].text == "原文"
    assert updated[1].button_text == "按钮"


@pytest.mark.asyncio
async def test_initialize_does_not_restore_intentionally_removed_button(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(
        123, "正文", None, "按钮", "https://example.com", {"eai": "@aliceeaichannel"}
    )
    delivery = (await db.get_deliveries(post_id))[0]
    await db.update_delivery_content(delivery.id, button_text=None, button_url=None)

    await db.initialize()

    reloaded = (await db.get_deliveries(post_id))[0]
    assert reloaded.button_text is None
    assert reloaded.button_url is None


@pytest.mark.asyncio
async def test_one_deleted_channel_marks_post_partial(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    post_id = await db.create_post(
        123,
        "正文",
        None,
        None,
        None,
        {"eai": "@aliceeaichannel", "korean": "@alicekoreanbet"},
    )
    await db.update_delivery(post_id, "eai", DeliveryStatus.PUBLISHED, message_id=1)
    await db.update_delivery(post_id, "korean", DeliveryStatus.PUBLISHED, message_id=2)
    await db.update_delivery(post_id, "eai", DeliveryStatus.DELETED)

    post = await db.get_post(post_id)
    assert post and post.status == PostStatus.PARTIAL


@pytest.mark.asyncio
async def test_relay_claim_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    await db.initialize()
    first = await db.claim_relay(-1001, 10, "eai", -1002, 28604)
    duplicate = await db.claim_relay(-1001, 10, "eai", -1002, 28604)
    assert first is True
    assert duplicate is False
