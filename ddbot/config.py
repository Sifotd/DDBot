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
    channel_alice_eai: str = "@aliceeaichannel"
    channel_alice_korean: str = "@alicekoreanbet"
    channel_alice_traditional: str = "@alicesmartpick"
    flow_timeout_minutes: int = Field(default=30, ge=5, le=1440)
    target_group_id: int = -1003869352469
    topic_eai: int = 28604
    topic_korean: int = 23669
    topic_traditional: int = Field(default=28601, ge=1)

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
        "channel_alice_eai", "channel_alice_korean", "channel_alice_traditional"
    )
    @classmethod
    def validate_channel(cls, value: str) -> str:
        if not value.startswith("@"):
            raise ValueError("频道名必须以 @ 开头")
        return value

    @property
    def channels(self) -> dict[str, str]:
        channels = {"eai": self.channel_alice_eai, "korean": self.channel_alice_korean}
        channels["traditional"] = self.channel_alice_traditional
        return channels

    @property
    def topics(self) -> dict[str, int]:
        topics = {"eai": self.topic_eai, "korean": self.topic_korean}
        topics["traditional"] = self.topic_traditional
        return topics

    def template_buttons(self, channel_key: str) -> list[tuple[str, str]]:
        if channel_key == "eai":
            return [
                ("💬 Join Alice Chat", "https://t.me/thealiceai/28604"),
                ("🔮 Predict Channel", "https://t.me/aliceeaichannel"),
                ("🤑 Start Winning with Alice", "https://thealiceai.com/saba"),
            ]
        if channel_key == "korean":
            return [
                ("💬 Alice 채팅방 참여", "https://t.me/thealiceai/28604"),
                ("🔮 예측 채널 보기", "https://t.me/alicekoreanbet"),
                ("🤑 Alice에서 수익 시작", "https://thealiceai.com/saba"),
            ]
        if channel_key == "traditional":
            channel = self.channel_alice_traditional.removeprefix("@")
            return [
                ("💬 加入 Alice 聊天室", "https://t.me/thealiceai/28604"),
                ("🔮 查看預測頻道", f"https://t.me/{channel}"),
                ("🤑 開始使用 Alice 獲利", "https://thealiceai.com/saba"),
            ]
        raise ValueError(f"未知或未配置的频道：{channel_key}")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
