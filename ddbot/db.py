from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from .models import (
    Delivery,
    DeliveryStatus,
    Post,
    PostStatus,
    ScheduledPush,
    TopicLatestMessage,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    text TEXT,
    photo_file_id TEXT,
    photo_file_ids TEXT,
    button_text TEXT,
    button_url TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    channel_username TEXT NOT NULL,
    message_id INTEGER,
    message_ids TEXT,
    orphaned_message_ids TEXT,
    text TEXT,
    photo_file_id TEXT,
    photo_file_ids TEXT,
    button_text TEXT,
    button_url TEXT,
    status TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(post_id, channel_key)
);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deliveries_post ON deliveries(post_id);
CREATE TABLE IF NOT EXISTS relays (
    source_chat_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    channel_key TEXT NOT NULL,
    target_chat_id INTEGER NOT NULL,
    topic_id INTEGER NOT NULL,
    forwarded_message_id INTEGER,
    orphaned_message_ids TEXT,
    status TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source_chat_id, source_message_id)
);
CREATE TABLE IF NOT EXISTS scheduled_pushes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    next_run_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(post_id, channel_key)
);
CREATE INDEX IF NOT EXISTS idx_scheduled_pushes_due
ON scheduled_pushes(active, next_run_at);
CREATE TABLE IF NOT EXISTS topic_latest_messages (
    target_chat_id INTEGER NOT NULL,
    topic_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sender_id INTEGER,
    sent_by_bot INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    text TEXT,
    photo_file_id TEXT,
    last_pushed_message_id INTEGER,
    pushed_message_id INTEGER,
    last_error TEXT,
    observed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(target_chat_id, topic_id)
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            post_columns = {
                row[1] for row in await db.execute_fetchall("PRAGMA table_info(posts)")
            }
            if "photo_file_ids" not in post_columns:
                await db.execute("ALTER TABLE posts ADD COLUMN photo_file_ids TEXT")
            columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(deliveries)")}
            if "photo_file_ids" not in columns:
                await db.execute("ALTER TABLE deliveries ADD COLUMN photo_file_ids TEXT")
            if "message_ids" not in columns:
                await db.execute("ALTER TABLE deliveries ADD COLUMN message_ids TEXT")
            if "orphaned_message_ids" not in columns:
                await db.execute(
                    "ALTER TABLE deliveries ADD COLUMN orphaned_message_ids TEXT"
                )
            relay_columns = {
                row[1] for row in await db.execute_fetchall("PRAGMA table_info(relays)")
            }
            if "orphaned_message_ids" not in relay_columns:
                await db.execute(
                    "ALTER TABLE relays ADD COLUMN orphaned_message_ids TEXT"
                )
            added_snapshot_columns = False
            for name in ("text", "photo_file_id", "button_text", "button_url"):
                if name not in columns:
                    await db.execute(f"ALTER TABLE deliveries ADD COLUMN {name} TEXT")
                    added_snapshot_columns = True
            if added_snapshot_columns:
                await db.execute(
                    """UPDATE deliveries SET
                    text=(SELECT text FROM posts WHERE posts.id=deliveries.post_id),
                    photo_file_id=(SELECT photo_file_id FROM posts
                        WHERE posts.id=deliveries.post_id),
                    button_text=(SELECT button_text FROM posts
                        WHERE posts.id=deliveries.post_id),
                    button_url=(SELECT button_url FROM posts
                        WHERE posts.id=deliveries.post_id)"""
                )
            await db.commit()

    async def create_post(
        self,
        admin_id: int,
        text: str | None,
        photo_file_id: str | list[str] | None,
        button_text: str | None,
        button_url: str | None,
        targets: dict[str, str],
    ) -> int:
        now = now_iso()
        photo_ids = (
            photo_file_id
            if isinstance(photo_file_id, list)
            else ([photo_file_id] if photo_file_id else [])
        )
        first_photo = photo_ids[0] if photo_ids else None
        encoded_photos = json.dumps(photo_ids)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT INTO posts
                (admin_id,text,photo_file_id,photo_file_ids,button_text,button_url,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    admin_id,
                    text,
                    first_photo,
                    encoded_photos,
                    button_text,
                    button_url,
                    PostStatus.PARTIAL,
                    now,
                    now,
                ),
            )
            post_id = cursor.lastrowid
            assert post_id is not None
            await db.executemany(
                """INSERT INTO deliveries
                (post_id,channel_key,channel_username,text,photo_file_id,photo_file_ids,button_text,
                 button_url,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        post_id,
                        key,
                        username,
                        text,
                        first_photo,
                        encoded_photos,
                        button_text,
                        button_url,
                        DeliveryStatus.FAILED,
                        now,
                        now,
                    )
                    for key, username in targets.items()
                ],
            )
            await db.commit()
            return post_id

    async def update_delivery(
        self,
        post_id: int,
        channel_key: str,
        status: str,
        message_id: int | None = None,
        message_ids: list[int] | None = None,
        orphaned_message_ids: list[int] | None = None,
        error: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE deliveries SET status=?, message_id=COALESCE(?,message_id),
                message_ids=COALESCE(?,message_ids),
                orphaned_message_ids=COALESCE(?,orphaned_message_ids),
                last_error=?, updated_at=?
                WHERE post_id=? AND channel_key=?""",
                (
                    status,
                    message_id,
                    json.dumps(message_ids) if message_ids is not None else None,
                    (
                        json.dumps(orphaned_message_ids)
                        if orphaned_message_ids is not None else None
                    ),
                    error,
                    now_iso(),
                    post_id,
                    channel_key,
                ),
            )
            await db.commit()
        await self.refresh_post_status(post_id)

    async def update_post_content(self, post_id: int, **fields: Any) -> None:
        allowed = {"text", "photo_file_id", "photo_file_ids", "button_text", "button_url"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if isinstance(values.get("photo_file_ids"), list):
            values["photo_file_ids"] = json.dumps(values["photo_file_ids"])
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE posts SET {assignments}, updated_at=? WHERE id=?",  # noqa: S608
                (*values.values(), now_iso(), post_id),
            )
            await db.commit()

    async def update_delivery_content(self, delivery_id: int, **fields: Any) -> None:
        allowed = {"text", "photo_file_id", "photo_file_ids", "button_text", "button_url"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if isinstance(values.get("photo_file_ids"), list):
            values["photo_file_ids"] = json.dumps(values["photo_file_ids"])
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE deliveries SET {assignments}, updated_at=? WHERE id=?",  # noqa: S608
                (*values.values(), now_iso(), delivery_id),
            )
            await db.commit()

    async def refresh_post_status(self, post_id: int) -> None:
        deliveries = await self.get_deliveries(post_id)
        statuses = {item.status for item in deliveries}
        if not statuses:
            status = PostStatus.PARTIAL
        elif statuses == {DeliveryStatus.DELETED}:
            status = PostStatus.DELETED
        elif DeliveryStatus.FAILED in statuses:
            status = PostStatus.PARTIAL
        elif DeliveryStatus.MODIFIED in statuses:
            status = PostStatus.MODIFIED
        elif DeliveryStatus.DELETED in statuses:
            status = PostStatus.PARTIAL
        else:
            status = PostStatus.PUBLISHED
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE posts SET status=?, updated_at=? WHERE id=?",
                (status, now_iso(), post_id),
            )
            await db.commit()

    async def get_post(self, post_id: int) -> Post | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchall("SELECT * FROM posts WHERE id=?", (post_id,))
        return self._post(row[0]) if row else None

    async def get_deliveries(self, post_id: int) -> list[Delivery]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM deliveries WHERE post_id=? ORDER BY id", (post_id,)
            )
        return [self._delivery(row) for row in rows]

    async def recent_posts(self, limit: int = 10, offset: int = 0) -> list[Post]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM posts ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
            )
        return [self._post(row) for row in rows]

    async def replace_scheduled_pushes(
        self, post_id: int, channel_keys: list[str], interval_seconds: int | None
    ) -> list[ScheduledPush]:
        """Activate this post and stop older schedules targeting the same topics."""
        if not channel_keys:
            return []
        now = datetime.now(UTC)
        now_text = now.isoformat()
        placeholders = ",".join("?" for _ in channel_keys)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"""UPDATE scheduled_pushes SET active=0, updated_at=?
                WHERE active=1 AND channel_key IN ({placeholders})""",  # noqa: S608
                (now_text, *channel_keys),
            )
            if interval_seconds is None:
                await db.commit()
                return []
            next_run = (now + timedelta(seconds=interval_seconds)).isoformat()
            await db.executemany(
                """INSERT INTO scheduled_pushes
                (post_id,channel_key,interval_seconds,next_run_at,active,created_at,updated_at)
                VALUES (?,?,?,?,1,?,?)
                ON CONFLICT(post_id,channel_key) DO UPDATE SET
                interval_seconds=excluded.interval_seconds,
                next_run_at=excluded.next_run_at,
                active=1,
                last_run_at=NULL,
                last_error=NULL,
                updated_at=excluded.updated_at""",
                [
                    (post_id, key, interval_seconds, next_run, now_text, now_text)
                    for key in channel_keys
                ],
            )
            await db.commit()
        return await self.get_scheduled_pushes(post_id=post_id, active_only=True)

    async def stop_scheduled_pushes(self, post_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE scheduled_pushes SET active=0, updated_at=?
                WHERE post_id=? AND active=1""",
                (now_iso(), post_id),
            )
            await db.commit()
            return cursor.rowcount

    async def stop_scheduled_pushes_for_channel(self, channel_key: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE scheduled_pushes SET active=0, updated_at=?
                WHERE channel_key=? AND active=1""",
                (now_iso(), channel_key),
            )
            await db.commit()
            return cursor.rowcount

    async def deactivate_scheduled_push(self, schedule_id: int, error: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE scheduled_pushes SET active=0, last_error=?, updated_at=?
                WHERE id=? AND active=1""",
                (error, now_iso(), schedule_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_scheduled_pushes(
        self, post_id: int | None = None, active_only: bool = False
    ) -> list[ScheduledPush]:
        clauses: list[str] = []
        params: list[Any] = []
        if post_id is not None:
            clauses.append("post_id=?")
            params.append(post_id)
        if active_only:
            clauses.append("active=1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                f"SELECT * FROM scheduled_pushes{where} ORDER BY id",  # noqa: S608
                params,
            )
        return [self._scheduled_push(row) for row in rows]

    async def complete_scheduled_run(
        self, schedule_id: int, expected_run_at: datetime, error: str | None = None
    ) -> bool:
        """Advance a still-current schedule; false means it was stopped or replaced."""
        finished_at = datetime.now(UTC)
        async with aiosqlite.connect(self.path) as db:
            row = await db.execute_fetchall(
                "SELECT interval_seconds, active FROM scheduled_pushes WHERE id=?",
                (schedule_id,),
            )
            if not row or not row[0][1]:
                return False
            interval_seconds = row[0][0]
            next_run_at = expected_run_at + timedelta(seconds=interval_seconds)
            while next_run_at <= finished_at:
                next_run_at += timedelta(seconds=interval_seconds)
            cursor = await db.execute(
                """UPDATE scheduled_pushes SET next_run_at=?, last_run_at=?,
                last_error=?, updated_at=? WHERE id=? AND active=1 AND next_run_at=?""",
                (
                    next_run_at.isoformat(),
                    finished_at.isoformat(),
                    error,
                    finished_at.isoformat(),
                    schedule_id,
                    expected_run_at.isoformat(),
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def claim_relay(
        self,
        source_chat_id: int,
        source_message_id: int,
        channel_key: str,
        target_chat_id: int,
        topic_id: int,
    ) -> bool:
        now = now_iso()
        retry_before = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT INTO relays
                (source_chat_id,source_message_id,channel_key,target_chat_id,topic_id,
                 status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(source_chat_id,source_message_id) DO UPDATE SET
                channel_key=excluded.channel_key,
                target_chat_id=excluded.target_chat_id,
                topic_id=excluded.topic_id,
                status='pending',
                last_error=NULL,
                updated_at=excluded.updated_at
                WHERE relays.status='failed'
                   OR (relays.status='pending' AND relays.updated_at < ?)""",
                (
                    source_chat_id,
                    source_message_id,
                    channel_key,
                    target_chat_id,
                    topic_id,
                    "pending",
                    now,
                    now,
                    retry_before,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def finish_relay(
        self,
        source_chat_id: int,
        source_message_id: int,
        status: str,
        forwarded_message_id: int | None = None,
        error: str | None = None,
        orphaned_message_ids: list[int] | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE relays SET status=?, forwarded_message_id=?, last_error=?,
                orphaned_message_ids=COALESCE(?,orphaned_message_ids),
                updated_at=? WHERE source_chat_id=? AND source_message_id=?""",
                (
                    status,
                    forwarded_message_id,
                    error,
                    (
                        json.dumps(orphaned_message_ids)
                        if orphaned_message_ids is not None else None
                    ),
                    now_iso(),
                    source_chat_id,
                    source_message_id,
                ),
            )
            await db.commit()

    async def get_relay(self, source_chat_id: int, source_message_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """SELECT * FROM relays
                WHERE source_chat_id=? AND source_message_id=?""",
                (source_chat_id, source_message_id),
            )
        return dict(rows[0]) if rows else None

    async def observe_topic_message(
        self,
        target_chat_id: int,
        topic_id: int,
        message_id: int,
        sender_id: int | None,
        sent_by_bot: bool,
        content_type: str,
        text: str | None,
        photo_file_id: str | None,
    ) -> bool:
        """Store a topic's newest update without losing its push history."""
        now = now_iso()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT INTO topic_latest_messages
                (target_chat_id,topic_id,message_id,sender_id,sent_by_bot,content_type,
                 text,photo_file_id,observed_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(target_chat_id,topic_id) DO UPDATE SET
                message_id=excluded.message_id,
                sender_id=excluded.sender_id,
                sent_by_bot=excluded.sent_by_bot,
                content_type=excluded.content_type,
                text=excluded.text,
                photo_file_id=excluded.photo_file_id,
                last_error=NULL,
                observed_at=excluded.observed_at,
                updated_at=excluded.updated_at
                WHERE excluded.message_id > topic_latest_messages.message_id""",
                (
                    target_chat_id,
                    topic_id,
                    message_id,
                    sender_id,
                    int(sent_by_bot),
                    content_type,
                    text,
                    photo_file_id,
                    now,
                    now,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_latest_topic_message(
        self, target_chat_id: int, topic_id: int
    ) -> TopicLatestMessage | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """SELECT * FROM topic_latest_messages
                WHERE target_chat_id=? AND topic_id=?""",
                (target_chat_id, topic_id),
            )
        return self._topic_latest_message(rows[0]) if rows else None

    async def finish_latest_topic_push(
        self,
        target_chat_id: int,
        topic_id: int,
        source_message_id: int,
        pushed_message_id: int | None = None,
        error: str | None = None,
    ) -> bool:
        """Persist a push result only if the source is still this topic's latest message."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE topic_latest_messages SET
                last_pushed_message_id=CASE WHEN ? IS NULL THEN last_pushed_message_id ELSE ? END,
                pushed_message_id=CASE WHEN ? IS NULL THEN pushed_message_id ELSE ? END,
                last_error=?, updated_at=?
                WHERE target_chat_id=? AND topic_id=? AND message_id=?""",
                (
                    pushed_message_id,
                    source_message_id,
                    pushed_message_id,
                    pushed_message_id,
                    error,
                    now_iso(),
                    target_chat_id,
                    topic_id,
                    source_message_id,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _post(row: aiosqlite.Row) -> Post:
        photo_ids = Database._ids(row, "photo_file_ids", "photo_file_id")
        return Post(
            id=row["id"],
            admin_id=row["admin_id"],
            text=row["text"],
            photo_file_id=row["photo_file_id"],
            button_text=row["button_text"],
            button_url=row["button_url"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            photo_file_ids=photo_ids,
        )

    @staticmethod
    def _delivery(row: aiosqlite.Row) -> Delivery:
        photo_ids = Database._ids(row, "photo_file_ids", "photo_file_id")
        message_ids = Database._ids(row, "message_ids", "message_id")
        return Delivery(
            id=row["id"],
            post_id=row["post_id"],
            channel_key=row["channel_key"],
            channel_username=row["channel_username"],
            message_id=row["message_id"],
            text=row["text"],
            photo_file_id=row["photo_file_id"],
            button_text=row["button_text"],
            button_url=row["button_url"],
            status=row["status"],
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            photo_file_ids=photo_ids,
            message_ids=message_ids,
            orphaned_message_ids=Database._json_ids(row, "orphaned_message_ids"),
        )

    @staticmethod
    def _ids(row: aiosqlite.Row, plural: str, singular: str) -> list:
        raw = row[plural] if plural in row.keys() else None
        if raw:
            try:
                value = json.loads(raw)
                if isinstance(value, list):
                    return value
            except (TypeError, json.JSONDecodeError):
                pass
        value = row[singular]
        return [value] if value is not None else []

    @staticmethod
    def _json_ids(row: aiosqlite.Row | dict, column: str) -> list[int]:
        raw = row[column] if column in row.keys() else None
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return [int(item) for item in value] if isinstance(value, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    @staticmethod
    def _scheduled_push(row: aiosqlite.Row) -> ScheduledPush:
        return ScheduledPush(
            id=row["id"],
            post_id=row["post_id"],
            channel_key=row["channel_key"],
            interval_seconds=row["interval_seconds"],
            next_run_at=datetime.fromisoformat(row["next_run_at"]),
            active=bool(row["active"]),
            last_run_at=(
                datetime.fromisoformat(row["last_run_at"]) if row["last_run_at"] else None
            ),
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _topic_latest_message(row: aiosqlite.Row) -> TopicLatestMessage:
        return TopicLatestMessage(
            target_chat_id=row["target_chat_id"],
            topic_id=row["topic_id"],
            message_id=row["message_id"],
            sender_id=row["sender_id"],
            sent_by_bot=bool(row["sent_by_bot"]),
            content_type=row["content_type"],
            text=row["text"],
            photo_file_id=row["photo_file_id"],
            last_pushed_message_id=row["last_pushed_message_id"],
            pushed_message_id=row["pushed_message_id"],
            last_error=row["last_error"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
