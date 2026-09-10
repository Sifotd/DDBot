from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from aiogram.exceptions import TelegramAPIError

from .db import Database
from .service import PublishingService, api_error_text, is_bot_command_text

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
            if not self.service.settings.relay_traditional_to_topic:
                await self.db.stop_scheduled_pushes_for_channel("traditional")
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
        topic_keys = [
            key for key in channel_keys if self.service.settings.should_relay_to_topic(key)
        ]
        await self.db.replace_scheduled_pushes(post_id, topic_keys, interval_seconds)
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
            await self.db.deactivate_scheduled_push(schedule_id, error)
            return
        elif is_bot_command_text(delivery.text):
            error = "Bot 命令不允许自动定时推送"
            logger.warning(
                "Scheduled push skipped (bot command): schedule=%s channel=%s",
                schedule_id,
                channel_key,
            )
            await self.db.deactivate_scheduled_push(schedule_id, error)
            return
        else:
            try:
                if self.service.settings.should_relay_to_topic(channel_key):
                    topic_id = self.service.settings.topics[channel_key]
                    latest = await self.db.get_latest_topic_message(
                        self.service.settings.target_group_id, topic_id
                    )
                    if latest is not None and latest.sent_by_bot:
                        logger.info(
                            "Scheduled push skipped (latest is from this bot): "
                            "schedule=%s topic=%s message=%s",
                            schedule_id, topic_id, latest.message_id,
                        )
                    else:
                        await self.service.send_to_topic(delivery)
            except TelegramAPIError as exc:
                error = api_error_text(exc)
                logger.exception("Scheduled push %s failed", schedule_id)
            except Exception as exc:  # Keep one bad task from stopping all scheduled pushes.
                error = api_error_text(exc)
                logger.exception("Scheduled push %s failed unexpectedly", schedule_id)
        await self.db.complete_scheduled_run(schedule_id, expected_run_at, error)


class LatestTopicScheduler:
    """Hourly, independent re-pushes of the newest message in each enabled topic."""

    def __init__(self, db: Database, service: PublishingService) -> None:
        self.db = db
        self.service = service
        self._runner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._runner or not self.service.settings.topic_latest_push_enabled:
            return
        self._runner = asyncio.create_task(self._run(), name="latest-topic-pushes")
        logger.info(
            "Latest-topic scheduler started: interval=%ss topics=%s",
            self.service.settings.topic_latest_push_interval_seconds,
            self.service.settings.enabled_topics,
        )

    async def close(self) -> None:
        if not self._runner:
            return
        self._runner.cancel()
        with suppress(asyncio.CancelledError):
            await self._runner
        self._runner = None

    async def _run(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.service.settings.topic_latest_push_interval_seconds)

    async def run_once(self) -> None:
        for channel_key, topic_id in self.service.settings.enabled_topics.items():
            try:
                await self._push_topic(channel_key, topic_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A broken topic must never stop checks for the remaining topics.
                logger.exception(
                    "Latest-topic push failed unexpectedly: key=%s topic=%s",
                    channel_key,
                    topic_id,
                )

    async def _push_topic(self, channel_key: str, topic_id: int) -> None:
        chat_id = self.service.settings.target_group_id
        latest = await self.db.get_latest_topic_message(chat_id, topic_id)
        if latest is None:
            logger.info(
                "Latest-topic push skipped (no observed message): key=%s topic=%s",
                channel_key,
                topic_id,
            )
            return
        if latest.sent_by_bot:
            logger.info(
                "Latest-topic push skipped (latest is from this bot): key=%s topic=%s message=%s",
                channel_key,
                topic_id,
                latest.message_id,
            )
            return
        if latest.last_pushed_message_id == latest.message_id:
            logger.info(
                "Latest-topic push skipped (already pushed): key=%s topic=%s message=%s",
                channel_key,
                topic_id,
                latest.message_id,
            )
            return
        if latest.content_type not in {"text", "photo"}:
            error = f"unsupported message type: {latest.content_type}"
            await self.db.finish_latest_topic_push(
                chat_id, topic_id, latest.message_id, error=error
            )
            logger.warning(
                "Latest-topic push skipped (%s): key=%s topic=%s message=%s",
                error,
                channel_key,
                topic_id,
                latest.message_id,
            )
            return
        try:
            pushed = await self.service.push_latest_topic_message(latest)
        except Exception as exc:
            error = api_error_text(exc)
            await self.db.finish_latest_topic_push(
                chat_id, topic_id, latest.message_id, error=error
            )
            logger.exception(
                "Latest-topic push failed: key=%s topic=%s message=%s",
                channel_key,
                topic_id,
                latest.message_id,
            )
            return

        await self.db.finish_latest_topic_push(
            chat_id,
            topic_id,
            latest.message_id,
            pushed_message_id=pushed.message_id,
        )
        # Long polling normally does not echo a bot's own outgoing message. Persist it
        # explicitly so the next run sees the true latest message and cannot self-loop.
        await self.db.observe_topic_message(
            chat_id,
            topic_id,
            pushed.message_id,
            self.service.bot.id,
            True,
            latest.content_type,
            latest.text,
            latest.photo_file_id,
        )
        logger.info(
            "Latest-topic message pushed: key=%s topic=%s source=%s pushed=%s",
            channel_key,
            topic_id,
            latest.message_id,
            pushed.message_id,
        )
