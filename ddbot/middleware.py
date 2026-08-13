from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject


class AdminMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: frozenset[int]) -> None:
        self.admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Channel posts have no administrator user identity; they are handled only
        # by the configured-channel relay handler.
        if isinstance(event, Message) and event.chat.type == ChatType.CHANNEL:
            return await handler(event, data)
        user = data.get("event_from_user")
        if user and user.id in self.admin_ids:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("您没有操作权限", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("您没有操作权限")
        return None
