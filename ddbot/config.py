from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_user_ids: Annotated[frozenset[int], NoDecode]
    database_path: Path = Path("data/ddbot.sqlite3")
    channel_alice_eai: str = "@AliceSmartPicksEN"
    channel_alice_korean: str = "@AliceSmartPicksKR"
    channel_alice_traditional: str = "@alicesmartpick"
    channel_alice_russian: str | None = None
    alice_start_url: str = "https://thealiceai.com/?code=N6WVL"
    flow_timeout_minutes: int = Field(default=30, ge=5, le=1440)
    target_group_id: int = -1003869352469
    topic_eai: int = 28604
    topic_korean: int = 23669
    topic_traditional: int = Field(default=28601, ge=1)
    topic_russian: int | None = Field(default=348731, ge=1)
    relay_traditional_to_topic: bool = False
    topic_latest_push_enabled: bool = False
    topic_latest_push_interval_seconds: int = Field(default=3600, ge=60)

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def parse_admins(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
            except ValueError as exc:
                raise ValueError("ADMIN_USER_IDS 必须是逗号分隔的 Telegram User ID") from exc
        return value

    @field_validator(
        "channel_alice_eai", "channel_alice_korean", "channel_alice_traditional",
        "channel_alice_russian",
    )
    @classmethod
    def validate_channel(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("@"):
            raise ValueError("频道名必须以 @ 开头")
        return value

    @property
    def channels(self) -> dict[str, str]:
        channels = {"eai": self.channel_alice_eai, "korean": self.channel_alice_korean}
        channels["traditional"] = self.channel_alice_traditional
        if self.channel_alice_russian:
            channels["russian"] = self.channel_alice_russian
        return channels

    @property
    def topics(self) -> dict[str, int]:
        topics = {"eai": self.topic_eai, "korean": self.topic_korean}
        topics["traditional"] = self.topic_traditional
        if self.topic_russian:
            topics["russian"] = self.topic_russian
        return topics

    def should_relay_to_topic(self, channel_key: str) -> bool:
        return channel_key in self.topics and (
            channel_key != "traditional" or self.relay_traditional_to_topic
        )

    @property
    def monitored_topics(self) -> dict[str, int]:
        return {
            key: topic_id for key, topic_id in self.topics.items()
            if self.should_relay_to_topic(key)
        }

    @property
    def enabled_topics(self) -> dict[str, int]:
        if not self.topic_latest_push_enabled:
            return {}
        return self.monitored_topics

    def template_buttons(self, channel_key: str) -> list[tuple[str, str]]:
        if channel_key == "russian" and self.channel_alice_russian:
            channel = self.channel_alice_russian.removeprefix("@")
            group = str(self.target_group_id).removeprefix("-100")
            chat_url = (
                f"https://t.me/c/{group}/{self.topic_russian}"
                if self.topic_russian else "https://t.me/thealiceai"
            )
            return [
                ("💬 Чат Alice", chat_url),
                ("🔮 Канал прогнозов", f"https://t.me/{channel}"),
                ("🤑 Начать выигрывать с Alice", self.alice_start_url),
            ]
        if channel_key == "eai":
            channel = self.channel_alice_eai.removeprefix("@")
            return [
                ("💬 Join Alice Chat", "https://t.me/thealiceai/28604"),
                ("🔮 Predict Channel", f"https://t.me/{channel}"),
                ("🤑 Start Winning with Alice", self.alice_start_url),
            ]
        if channel_key == "korean":
            channel = self.channel_alice_korean.removeprefix("@")
            return [
                ("💬 Alice 채팅방 참여", "https://t.me/thealiceai/28604"),
                ("🔮 예측 채널 보기", f"https://t.me/{channel}"),
                ("🤑 Alice에서 수익 시작", self.alice_start_url),
            ]
        if channel_key == "traditional":
            channel = self.channel_alice_traditional.removeprefix("@")
            return [
                ("💬 加入 Alice 聊天室", "https://t.me/thealiceai/28604"),
                ("🔮 查看預測頻道", f"https://t.me/{channel}"),
                ("🤑 開始使用 Alice 獲利", self.alice_start_url),
            ]
        raise ValueError(f"未知或未配置的频道：{channel_key}")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
