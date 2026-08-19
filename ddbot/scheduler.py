from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from aiogram.exceptions import TelegramAPIError

from .db import Database
from .service import PublishingService, api_error_text

logger = logging.getLogger(__name__)


class PushScheduler:
    """Persistent, restart-safe recurring pushes to configured Telegram topics."""

    def __init__(self, db: Database, service: PublishingService) -> None:
        self.db = db
        self.service = service
        self._wake = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._runner:
            self._runner = asyncio.create_task(self._run(), name="scheduled-topic-pushes")

    async def close(self) -> None:
        if not self._runner:
            return
        self._runner.cancel()
        with suppress(asyncio.CancelledError):
            await self._runner
        self._runner = None

    async def schedule(
        self, post_id: int, channel_keys: list[str], interval_seconds: int | None
    ) -> None:
        await self.db.replace_scheduled_pushes(post_id, channel_keys, interval_seconds)
        self._wake.set()

    async def stop(self, post_id: int) -> int:
        stopped = await self.db.stop_scheduled_pushes(post_id)
        self._wake.set()
        return stopped

    async def _run(self) -> None:
        while True:
            schedules = await self.db.get_scheduled_pushes(active_only=True)
            now = datetime.now(UTC)
            due = [item for item in schedules if item.next_run_at <= now]
            if due:
                for schedule in due:
                    await self._push(
                        schedule.id,
                        schedule.post_id,
                        schedule.channel_key,
                        schedule.next_run_at,
                    )
                continue

            self._wake.clear()
            timeout = None
            if schedules:
                earliest = min(item.next_run_at for item in schedules)
                timeout = max(0.05, earliest.timestamp() - now.timestamp())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def _push(
        self, schedule_id: int, post_id: int, channel_key: str, expected_run_at: datetime
    ) -> None:
        deliveries = await self.db.get_deliveries(post_id)
        delivery = next((item for item in deliveries if item.channel_key == channel_key), None)
        error: str | None = None
        if delivery is None:
            error = "找不到定时推送内容"
        else:
            try:
                await self.service.send_to_topic(delivery)
            except TelegramAPIError as exc:
                error = api_error_text(exc)
                logger.exception("Scheduled push %s failed", schedule_id)
            except Exception as exc:  # Keep one bad task from stopping all scheduled pushes.
                error = api_error_text(exc)
                logger.exception("Scheduled push %s failed unexpectedly", schedule_id)
        await self.db.complete_scheduled_run(schedule_id, expected_run_at, error)
