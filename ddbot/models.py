from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PostStatus(StrEnum):
    PUBLISHED = "published"
    MODIFIED = "modified"
    DELETED = "deleted"
    PARTIAL = "partial"


class DeliveryStatus(StrEnum):
    PUBLISHED = "published"
    MODIFIED = "modified"
    DELETED = "deleted"
    FAILED = "failed"


@dataclass(slots=True)
class Post:
    id: int
    admin_id: int
    text: str | None
    photo_file_id: str | None
    button_text: str | None
    button_url: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    photo_file_ids: list[str]


@dataclass(slots=True)
class Delivery:
    id: int
    post_id: int
    channel_key: str
    channel_username: str
    message_id: int | None
    text: str | None
    photo_file_id: str | None
    button_text: str | None
    button_url: str | None
    status: str
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    photo_file_ids: list[str]
    message_ids: list[int]
    orphaned_message_ids: list[int]


@dataclass(slots=True)
class ScheduledPush:
    id: int
    post_id: int
    channel_key: str
    interval_seconds: int
    next_run_at: datetime
    active: bool
    last_run_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class TopicLatestMessage:
    target_chat_id: int
    topic_id: int
    message_id: int
    sender_id: int | None
    sent_by_bot: bool
    content_type: str
    text: str | None
    photo_file_id: str | None
    last_pushed_message_id: int | None
    pushed_message_id: int | None
    last_error: str | None
    observed_at: datetime
    updated_at: datetime
