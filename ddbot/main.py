from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from .config import get_settings
from .db import Database
from .handlers import router
from .middleware import AdminMiddleware
from .service import PublishingService


async def run() -> None:
    settings = get_settings()
    db = Database(settings.database_path)
    await db.initialize()
    # User-authored content is sent as plain text so angle brackets cannot break Telegram parsing.
    bot = Bot(settings.bot_token)
    service = PublishingService(bot, db, settings)
    dispatcher = Dispatcher(storage=MemoryStorage())
    router.message.middleware(AdminMiddleware(settings.admin_user_ids))
    router.callback_query.middleware(AdminMiddleware(settings.admin_user_ids))
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dispatcher.start_polling(
        bot,
        settings=settings,
        db=db,
        service=service,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
