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
from .models import Delivery, DeliveryStatus, Post, TopicLatestMessage
from .ui import link_keyboard

T = TypeVar("T")


def is_bot_command_text(text: str | None) -> bool:
    """Return whether text could trigger a Telegram bot command."""
    return bool(text and text.startswith("/"))


def api_error_text(exc: Exception) -> str:
    text = str(exc).replace("Telegram server says - ", "")
    return text[:300]


async def telegram_call(call: Callable[[], Awaitable[T]]) -> T:
    for attempt in range(3):
        try:
            return await call()
        except TelegramRetryAfter as exc:
            if attempt == 2:
                raise
            await asyncio.sleep(max(0, exc.retry_after))
    raise RuntimeError("Telegram call exhausted retries")


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
        photo_file_id: str | list[str] | None,
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
                messages = await self._send(
                    channel, text, photo_file_id, button_text, button_url, key
                )
                message = messages[0]
                await self.db.update_delivery(
                    post_id,
                    key,
                    DeliveryStatus.PUBLISHED,
                    message.message_id,
                    [item.message_id for item in messages],
                )
                results.append(OperationResult(channel, True, "发布成功"))
                if self.settings.should_relay_to_topic(key):
                    relay_result = await self.relay_published_messages(messages, key)
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
        relay = await self.db.get_relay(message.chat.id, message.message_id)
        try:
            forwarded = await telegram_call(
                lambda: self.bot.forward_message(
                    chat_id=self.settings.target_group_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=topic_id,
                )
            )
            failed_ids, cleanup_errors = await self._delete_message_ids(
                self.settings.target_group_id,
                self.db._json_ids(relay, "orphaned_message_ids") if relay else [],
            )
            cleanup_warning = "; ".join(cleanup_errors) or None
            await self.db.finish_relay(
                message.chat.id,
                message.message_id,
                "forwarded",
                forwarded.message_id,
                cleanup_warning,
                failed_ids,
            )
            await self.observe_bot_topic_output(forwarded, topic_id, message)
            return OperationResult(
                target,
                not cleanup_errors,
                (
                    "转发成功"
                    if not cleanup_errors
                    else f"转发成功，遗留副本清理失败：{cleanup_warning}"
                ),
            )
        except TelegramAPIError as exc:
            detail = api_error_text(exc)
            await self.db.finish_relay(message.chat.id, message.message_id, "failed", error=detail)
            return OperationResult(target, False, f"转发失败：{detail}")

    async def relay_published_messages(
        self, messages: list[Message], channel_key: str
    ) -> OperationResult:
        """Forward an album as one grouped post; keep the single-message path unchanged."""
        if len(messages) == 1:
            return await self.relay_published_message(messages[0], channel_key)
        topic_id = self.settings.topics[channel_key]
        target = f"Topic {topic_id}"
        claimed: list[Message] = []
        for message in messages:
            if await self.db.claim_relay(
                message.chat.id,
                message.message_id,
                channel_key,
                self.settings.target_group_id,
                topic_id,
            ):
                claimed.append(message)
        if not claimed:
            return OperationResult(target, True, "已转发（跳过重复）")
        try:
            forwarded = await telegram_call(
                lambda: self.bot.forward_messages(
                    chat_id=self.settings.target_group_id,
                    from_chat_id=claimed[0].chat.id,
                    message_ids=[item.message_id for item in claimed],
                    message_thread_id=topic_id,
                )
            )
            if len(forwarded) != len(claimed):
                error = (
                    f"Telegram 仅转发了 {len(forwarded)}/{len(claimed)} 条相册消息，"
                    "已回滚本次转发"
                )
                failed_cleanup, _ = await self._delete_message_ids(
                    self.settings.target_group_id,
                    [message.message_id for message in forwarded],
                )
                for index, source in enumerate(claimed):
                    await self.db.finish_relay(
                        source.chat.id,
                        source.message_id,
                        "failed",
                        error=error,
                        orphaned_message_ids=failed_cleanup if index == 0 else [],
                    )
                return OperationResult(target, False, error)
            cleanup_warnings: list[str] = []
            for source, target_message in zip(claimed, forwarded, strict=True):
                relay = await self.db.get_relay(source.chat.id, source.message_id)
                failed_ids, cleanup_errors = await self._delete_message_ids(
                    self.settings.target_group_id,
                    self.db._json_ids(relay, "orphaned_message_ids") if relay else [],
                )
                await self.db.finish_relay(
                    source.chat.id,
                    source.message_id,
                    "forwarded",
                    target_message.message_id,
                    "; ".join(cleanup_errors) or None,
                    failed_ids,
                )
                cleanup_warnings.extend(cleanup_errors)
                await self.observe_bot_topic_output(target_message, topic_id, source)
            return OperationResult(
                target,
                not cleanup_warnings,
                (
                    "相册转发成功"
                    if not cleanup_warnings
                    else f"相册转发成功，遗留副本清理失败：{cleanup_warnings[0]}"
                ),
            )
        except TelegramAPIError as exc:
            detail = api_error_text(exc)
            for source in claimed:
                await self.db.finish_relay(
                    source.chat.id, source.message_id, "failed", error=detail
                )
            return OperationResult(target, False, f"相册转发失败：{detail}")

    async def replace_relayed_messages(
        self, messages: list[Message], channel_key: str
    ) -> OperationResult:
        """Forward an edited post again and retain IDs of old copies not yet deleted."""
        if not messages:
            return OperationResult(channel_key, False, "没有可转发的消息")
        topic_id = self.settings.topics[channel_key]
        target = f"Topic {topic_id}"
        previous = [
            await self.db.get_relay(message.chat.id, message.message_id)
            for message in messages
        ]
        for index, (message, old) in enumerate(zip(messages, previous, strict=True)):
            if old is None:
                await self.db.claim_relay(
                    message.chat.id,
                    message.message_id,
                    channel_key,
                    self.settings.target_group_id,
                    topic_id,
                )
                previous[index] = await self.db.get_relay(
                    message.chat.id, message.message_id
                )
        try:
            if len(messages) == 1:
                forwarded = [
                    await telegram_call(
                        lambda: self.bot.forward_message(
                            chat_id=self.settings.target_group_id,
                            from_chat_id=messages[0].chat.id,
                            message_id=messages[0].message_id,
                            message_thread_id=topic_id,
                        )
                    )
                ]
            else:
                forwarded = await telegram_call(
                    lambda: self.bot.forward_messages(
                        chat_id=self.settings.target_group_id,
                        from_chat_id=messages[0].chat.id,
                        message_ids=[message.message_id for message in messages],
                        message_thread_id=topic_id,
                    )
                )
            if len(forwarded) != len(messages):
                error = (
                    f"Telegram 仅转发了 {len(forwarded)}/{len(messages)} 条编辑相册，"
                    "已回滚本次转发"
                )
                failed_cleanup, _ = await self._delete_message_ids(
                    self.settings.target_group_id,
                    [message.message_id for message in forwarded],
                )
                for index, (source, old) in enumerate(
                    zip(messages, previous, strict=True)
                ):
                    if old:
                        retained_orphans = self.db._json_ids(
                            old, "orphaned_message_ids"
                        )
                        if index == 0:
                            retained_orphans.extend(failed_cleanup)
                        await self.db.finish_relay(
                            source.chat.id,
                            source.message_id,
                            old["status"],
                            old.get("forwarded_message_id"),
                            error,
                            list(dict.fromkeys(retained_orphans)),
                        )
                return OperationResult(target, False, error)
        except TelegramAPIError as exc:
            detail = api_error_text(exc)
            for source, old in zip(messages, previous, strict=True):
                if old:
                    await self.db.finish_relay(
                        source.chat.id,
                        source.message_id,
                        old["status"],
                        old.get("forwarded_message_id"),
                        detail,
                    )
            return OperationResult(target, False, f"更新转发失败：{detail}")

        warnings: list[str] = []
        for source, new_message, old in zip(messages, forwarded, previous, strict=True):
            old_ids: list[int] = []
            if old and old.get("forwarded_message_id"):
                old_ids.append(int(old["forwarded_message_id"]))
            if old:
                old_ids.extend(self.db._json_ids(old, "orphaned_message_ids"))
            failed_ids, errors = await self._delete_message_ids(
                self.settings.target_group_id, old_ids
            )
            warning = "; ".join(errors) or None
            if warning:
                warnings.append(warning)
            await self.db.finish_relay(
                source.chat.id,
                source.message_id,
                "forwarded",
                new_message.message_id,
                warning,
                failed_ids,
            )
            await self.observe_bot_topic_output(new_message, topic_id, source)
        return OperationResult(
            target,
            not warnings,
            "更新转发成功" if not warnings else f"新内容已转发，旧副本清理失败：{warnings[0]}",
        )

    async def edit_relayed_message(
        self, message: Message, channel_key: str
    ) -> OperationResult:
        """Edit a bot-owned forwarded copy in place so albums remain grouped."""
        relay = await self.db.get_relay(message.chat.id, message.message_id)
        if not relay or not relay.get("forwarded_message_id"):
            return await self.relay_published_message(message, channel_key)
        target_id = int(relay["forwarded_message_id"])
        topic_id = self.settings.topics[channel_key]
        try:
            if message.photo:
                await telegram_call(
                    lambda: self.bot.edit_message_media(
                        chat_id=self.settings.target_group_id,
                        message_id=target_id,
                        media=InputMediaPhoto(
                            media=message.photo[-1].file_id,
                            caption=message.caption,
                        ),
                    )
                )
            elif message.text is not None:
                await telegram_call(
                    lambda: self.bot.edit_message_text(
                        chat_id=self.settings.target_group_id,
                        message_id=target_id,
                        text=message.text,
                    )
                )
            else:
                return await self.replace_relayed_messages([message], channel_key)
            await self.db.finish_relay(
                message.chat.id,
                message.message_id,
                "forwarded",
                target_id,
                orphaned_message_ids=self.db._json_ids(
                    relay, "orphaned_message_ids"
                ),
            )
            return OperationResult(f"Topic {topic_id}", True, "原位更新成功")
        except TelegramAPIError:
            return await self.replace_relayed_messages([message], channel_key)

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
                new_messages = await self._send(
                    delivery.channel_username,
                    delivery.text,
                    delivery.photo_file_ids,
                    delivery.button_text,
                    delivery.button_url,
                    delivery.channel_key,
                )
                old_ids = list(
                    dict.fromkeys(delivery.message_ids + delivery.orphaned_message_ids)
                )
                failed_ids, delete_errors = await self._delete_message_ids(
                    delivery.channel_username, old_ids
                )
                delete_warning = (
                    f"新图片已发布，但旧消息删除失败：{'; '.join(delete_errors)}"
                    if delete_errors else None
                )
                await self.db.update_delivery(
                    post.id,
                    delivery.channel_key,
                    DeliveryStatus.FAILED if delete_warning else DeliveryStatus.MODIFIED,
                    new_messages[0].message_id,
                    [item.message_id for item in new_messages],
                    failed_ids,
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
                current_ids = list(dict.fromkeys(delivery.message_ids))
                orphan_ids = list(dict.fromkeys(delivery.orphaned_message_ids))
                failed_ids, errors = await self._delete_message_ids(
                    delivery.channel_username, current_ids + orphan_ids
                )
                if failed_ids:
                    current_failed = [item for item in current_ids if item in failed_ids]
                    orphan_failed = [item for item in orphan_ids if item in failed_ids]
                    detail = "; ".join(errors)
                    await self.db.update_delivery(
                        post_id,
                        delivery.channel_key,
                        DeliveryStatus.FAILED,
                        current_failed[0] if current_failed else None,
                        current_failed,
                        orphan_failed,
                        detail,
                    )
                    results.append(OperationResult(delivery.channel_username, False, detail))
                    continue
                await self.db.update_delivery(post_id, delivery.channel_key, DeliveryStatus.DELETED)
                results.append(OperationResult(delivery.channel_username, True, "删除成功"))
            except TelegramAPIError as exc:
                detail = api_error_text(exc)
                await self.db.update_delivery(
                    post_id, delivery.channel_key, DeliveryStatus.FAILED, error=detail
                )
                results.append(OperationResult(delivery.channel_username, False, detail))
        return results

    async def _delete_message_ids(
        self, chat_id: int | str, message_ids: list[int]
    ) -> tuple[list[int], list[str]]:
        failed: list[int] = []
        errors: list[str] = []
        for message_id in dict.fromkeys(message_ids):
            try:
                await telegram_call(
                    lambda message_id=message_id: self.bot.delete_message(chat_id, message_id)
                )
            except TelegramAPIError as exc:
                detail = api_error_text(exc)
                if "message to delete not found" in detail.lower():
                    continue
                failed.append(message_id)
                errors.append(f"消息 {message_id}: {detail}")
        return failed, errors

    async def send_to_topic(self, delivery: Delivery) -> Message:
        """Send a fresh copy directly to the language topic for a recurring push."""
        topic_id = self.settings.topics[delivery.channel_key]
        markup = self._markup(
            delivery.button_text, delivery.button_url, delivery.channel_key
        )
        if delivery.photo_file_ids:
            if len(delivery.photo_file_ids) > 1:
                sent = await telegram_call(
                    lambda: self.bot.send_media_group(
                        self.settings.target_group_id,
                        media=[
                            InputMediaPhoto(
                                media=photo_id,
                                caption=delivery.text if index == 0 else None,
                            )
                            for index, photo_id in enumerate(delivery.photo_file_ids)
                        ],
                        message_thread_id=topic_id,
                    )
                )
                for index, message in enumerate(sent):
                    await self._observe_bot_topic_content(
                        message,
                        topic_id,
                        "photo",
                        delivery.text if index == 0 else None,
                        delivery.photo_file_ids[index],
                    )
                return sent[0]
            messages: list[Message] = []
            for index, photo_id in enumerate(delivery.photo_file_ids):
                message = await telegram_call(
                    lambda photo_id=photo_id, index=index: self.bot.send_photo(
                        self.settings.target_group_id,
                        photo_id,
                        caption=delivery.text if index == 0 else None,
                        reply_markup=markup if index == 0 else None,
                        message_thread_id=topic_id,
                    )
                )
                messages.append(message)
                await self._observe_bot_topic_content(
                    message,
                    topic_id,
                    "photo",
                    delivery.text if index == 0 else None,
                    photo_id,
                )
            return messages[0]
        message = await telegram_call(
            lambda: self.bot.send_message(
                self.settings.target_group_id,
                delivery.text or "",
                reply_markup=markup,
                message_thread_id=topic_id,
            )
        )
        await self._observe_bot_topic_content(
            message, topic_id, "text", delivery.text or "", None
        )
        return message

    async def push_latest_topic_message(self, latest: TopicLatestMessage) -> Message:
        """Re-send one supported observed message to its original topic."""
        if latest.content_type == "photo" and latest.photo_file_id:
            return await telegram_call(
                lambda: self.bot.send_photo(
                    latest.target_chat_id,
                    latest.photo_file_id,
                    caption=latest.text,
                    message_thread_id=latest.topic_id,
                )
            )
        if latest.content_type == "text" and latest.text is not None:
            return await telegram_call(
                lambda: self.bot.send_message(
                    latest.target_chat_id,
                    latest.text,
                    message_thread_id=latest.topic_id,
                )
            )
        raise ValueError(f"Unsupported topic message type: {latest.content_type}")

    async def observe_bot_topic_output(
        self, target: Message, topic_id: int, source: Message
    ) -> None:
        """Record output from any bot-owned path that writes into a topic."""
        photo = getattr(source, "photo", None)
        if photo:
            await self._observe_bot_topic_content(
                target,
                topic_id,
                "photo",
                getattr(source, "caption", None),
                photo[-1].file_id,
            )
            return
        text = getattr(source, "text", None)
        content_type = (
            "text" if text is not None else str(getattr(source, "content_type", "unknown"))
        )
        await self._observe_bot_topic_content(
            target, topic_id, content_type, text, None
        )

    async def _observe_bot_topic_content(
        self,
        message: Message,
        topic_id: int,
        content_type: str,
        text: str | None,
        photo_file_id: str | None,
    ) -> None:
        await self.db.observe_topic_message(
            self.settings.target_group_id,
            topic_id,
            message.message_id,
            self.bot.id,
            True,
            content_type,
            text,
            photo_file_id,
        )

    async def _send(
        self,
        channel: str,
        text: str | None,
        photo_file_id: str | list[str] | None,
        button_text: str | None,
        button_url: str | None,
        channel_key: str | None = None,
    ) -> list[Message]:
        markup = self._markup(button_text, button_url, channel_key)
        photo_ids = (
            photo_file_id
            if isinstance(photo_file_id, list)
            else ([photo_file_id] if photo_file_id else [])
        )
        if photo_ids:
            if len(photo_ids) > 1:
                return await telegram_call(
                    lambda: self.bot.send_media_group(
                        channel,
                        media=[
                            InputMediaPhoto(
                                media=photo_id, caption=text if index == 0 else None
                            )
                            for index, photo_id in enumerate(photo_ids)
                        ],
                    )
                )
            messages: list[Message] = []
            for index, photo_id in enumerate(photo_ids):
                messages.append(
                    await telegram_call(
                        lambda photo_id=photo_id, index=index: self.bot.send_photo(
                            channel,
                            photo_id,
                            caption=text if index == 0 else None,
                            reply_markup=markup if index == 0 else None,
                        )
                    )
                )
            return messages
        return [
            await telegram_call(
                lambda: self.bot.send_message(channel, text or "", reply_markup=markup)
            )
        ]

    async def _edit_one(self, delivery: Delivery) -> Any:
        markup = self._markup(delivery.button_text, delivery.button_url, delivery.channel_key)
        if delivery.photo_file_id:
            media = InputMediaPhoto(media=delivery.photo_file_id, caption=delivery.text)
            return await telegram_call(
                lambda: self.bot.edit_message_media(
                    chat_id=delivery.channel_username,
                    message_id=delivery.message_id,
                    media=media,
                    reply_markup=markup if len(delivery.photo_file_ids) == 1 else None,
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
