from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InputMediaPhoto, Message

from .config import Settings
from .db import Database
from .models import Delivery, DeliveryStatus, Post
from .ui import link_keyboard

T = TypeVar("T")


def api_error_text(exc: Exception) -> str:
    text = str(exc).replace("Telegram server says - ", "")
    return text[:300]


async def telegram_call(call: Callable[[], Awaitable[T]]) -> T:
    try:
        return await call()
    except TelegramRetryAfter as exc:
        await asyncio.sleep(min(exc.retry_after, 10))
        return await call()


@dataclass(slots=True)
class OperationResult:
    channel: str
    ok: bool
    detail: str


class PublishingService:
    def __init__(self, bot: Bot, db: Database, settings: Settings) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings

    async def publish(
        self,
        admin_id: int,
        text: str | None,
        photo_file_id: str | None,
        button_text: str | None,
        button_url: str | None,
        targets: dict[str, str],
    ) -> tuple[int, list[OperationResult]]:
        post_id = await self.db.create_post(
            admin_id, text, photo_file_id, button_text, button_url, targets
        )
        results: list[OperationResult] = []
        for key, channel in targets.items():
            try:
                message = await self._send(
                    channel, text, photo_file_id, button_text, button_url, key
                )
                await self.db.update_delivery(
                    post_id, key, DeliveryStatus.PUBLISHED, message.message_id
                )
                results.append(OperationResult(channel, True, "发布成功"))
                relay_result = await self.relay_published_message(message, key)
                results.append(relay_result)
            except TelegramAPIError as exc:
                detail = api_error_text(exc)
                await self.db.update_delivery(post_id, key, DeliveryStatus.FAILED, error=detail)
                results.append(OperationResult(channel, False, detail))
        return post_id, results

    async def relay_published_message(self, message: Message, channel_key: str) -> OperationResult:
        topic_id = self.settings.topics[channel_key]
        target = f"Topic {topic_id}"
        claimed = await self.db.claim_relay(
            message.chat.id,
            message.message_id,
            channel_key,
            self.settings.target_group_id,
            topic_id,
        )
        if not claimed:
            return OperationResult(target, True, "已转发（跳过重复）")
        try:
            forwarded = await telegram_call(
                lambda: self.bot.forward_message(
                    chat_id=self.settings.target_group_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=topic_id,
                )
            )
            await self.db.finish_relay(
                message.chat.id,
                message.message_id,
                "forwarded",
                forwarded.message_id,
            )
            return OperationResult(target, True, "转发成功")
        except TelegramAPIError as exc:
            detail = api_error_text(exc)
            await self.db.finish_relay(message.chat.id, message.message_id, "failed", error=detail)
            return OperationResult(target, False, f"转发失败：{detail}")

    async def edit(self, post: Post, deliveries: list[Delivery]) -> list[OperationResult]:
        results: list[OperationResult] = []
        for delivery in deliveries:
            if not delivery.message_id or delivery.status == DeliveryStatus.DELETED:
                continue
            try:
                await self._edit_one(delivery)
                await self.db.update_delivery(
                    post.id, delivery.channel_key, DeliveryStatus.MODIFIED
                )
                results.append(OperationResult(delivery.channel_username, True, "修改成功"))
            except TelegramAPIError as exc:
                detail = api_error_text(exc)
                await self.db.update_delivery(
                    post.id, delivery.channel_key, DeliveryStatus.FAILED, error=detail
                )
                results.append(OperationResult(delivery.channel_username, False, detail))
        return results

    async def replace_text_with_photo(
        self, post: Post, deliveries: list[Delivery]
    ) -> list[OperationResult]:
        """Telegram cannot change text into media, so send replacement before deleting old."""
        results: list[OperationResult] = []
        for delivery in deliveries:
            if not delivery.message_id or delivery.status == DeliveryStatus.DELETED:
                continue
            try:
                new_message = await self._send(
                    delivery.channel_username,
                    delivery.text,
                    delivery.photo_file_id,
                    delivery.button_text,
                    delivery.button_url,
                    delivery.channel_key,
                )
                delete_warning: str | None = None
                try:
                    channel = delivery.channel_username
                    message_id = delivery.message_id
                    await telegram_call(
                        lambda channel=channel, message_id=message_id: self.bot.delete_message(
                            channel,
                            message_id,  # type: ignore[arg-type]
                        )
                    )
                except TelegramAPIError as exc:
                    # The replacement exists, but the old post still needs manual cleanup.
                    delete_warning = f"新图片已发布，但旧消息删除失败：{api_error_text(exc)}"
                await self.db.update_delivery(
                    post.id,
                    delivery.channel_key,
                    DeliveryStatus.FAILED if delete_warning else DeliveryStatus.MODIFIED,
                    new_message.message_id,
                    delete_warning,
                )
                results.append(
                    OperationResult(
                        delivery.channel_username,
                        not delete_warning,
                        delete_warning or "更换图片成功",
                    )
                )
            except TelegramAPIError as exc:
                detail = api_error_text(exc)
                await self.db.update_delivery(
                    post.id, delivery.channel_key, DeliveryStatus.FAILED, error=detail
                )
                results.append(OperationResult(delivery.channel_username, False, detail))
        return results

    async def delete(self, post_id: int, deliveries: list[Delivery]) -> list[OperationResult]:
        results: list[OperationResult] = []
        for delivery in deliveries:
            if not delivery.message_id or delivery.status == DeliveryStatus.DELETED:
                continue
            try:
                channel = delivery.channel_username
                message_id = delivery.message_id
                await telegram_call(
                    lambda channel=channel, message_id=message_id: self.bot.delete_message(
                        channel,
                        message_id,  # type: ignore[arg-type]
                    )
                )
                await self.db.update_delivery(post_id, delivery.channel_key, DeliveryStatus.DELETED)
                results.append(OperationResult(delivery.channel_username, True, "删除成功"))
            except TelegramAPIError as exc:
                detail = api_error_text(exc)
                await self.db.update_delivery(
                    post_id, delivery.channel_key, DeliveryStatus.FAILED, error=detail
                )
                results.append(OperationResult(delivery.channel_username, False, detail))
        return results

    async def send_to_topic(self, delivery: Delivery) -> Message:
        """Send a fresh copy directly to the language topic for a recurring push."""
        topic_id = self.settings.topics[delivery.channel_key]
        markup = self._markup(
            delivery.button_text, delivery.button_url, delivery.channel_key
        )
        if delivery.photo_file_id:
            return await telegram_call(
                lambda: self.bot.send_photo(
                    self.settings.target_group_id,
                    delivery.photo_file_id,
                    caption=delivery.text,
                    reply_markup=markup,
                    message_thread_id=topic_id,
                )
            )
        return await telegram_call(
            lambda: self.bot.send_message(
                self.settings.target_group_id,
                delivery.text or "",
                reply_markup=markup,
                message_thread_id=topic_id,
            )
        )

    async def _send(
        self,
        channel: str,
        text: str | None,
        photo_file_id: str | None,
        button_text: str | None,
        button_url: str | None,
        channel_key: str | None = None,
    ) -> Message:
        markup = self._markup(button_text, button_url, channel_key)
        if photo_file_id:
            return await telegram_call(
                lambda: self.bot.send_photo(
                    channel, photo_file_id, caption=text, reply_markup=markup
                )
            )
        return await telegram_call(
            lambda: self.bot.send_message(channel, text or "", reply_markup=markup)
        )

    async def _edit_one(self, delivery: Delivery) -> Any:
        markup = self._markup(delivery.button_text, delivery.button_url, delivery.channel_key)
        if delivery.photo_file_id:
            media = InputMediaPhoto(media=delivery.photo_file_id, caption=delivery.text)
            return await telegram_call(
                lambda: self.bot.edit_message_media(
                    chat_id=delivery.channel_username,
                    message_id=delivery.message_id,
                    media=media,
                    reply_markup=markup,
                )
            )
        return await telegram_call(
            lambda: self.bot.edit_message_text(
                text=delivery.text or "",
                chat_id=delivery.channel_username,
                message_id=delivery.message_id,
                reply_markup=markup,
            )
        )

    def _markup(self, button_text: str | None, button_url: str | None, channel_key: str | None):
        if button_text == "__template__" and channel_key:
            from .ui import template_keyboard

            return template_keyboard(self.settings.template_buttons(channel_key))
        return link_keyboard(button_text, button_url)


def format_results(results: list[OperationResult]) -> str:
    if not results:
        return "没有可操作的频道消息。"
    return "\n".join(
        f"{'✅' if item.ok else '❌'} {item.channel} {item.detail}" for item in results
    )
