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
    flow_timeout_minutes: int = Field(default=30, ge=5, le=1440)
    target_group_id: int = -1003869352469
    topic_eai: int = 28604
    topic_korean: int = 23669

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def parse_admins(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
            except ValueError as exc:
                raise ValueError("ADMIN_USER_IDS 必须是逗号分隔的 Telegram User ID") from exc
        return value

    @field_validator("channel_alice_eai", "channel_alice_korean")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        if not value.startswith("@"):
            raise ValueError("频道名必须以 @ 开头")
        return value

    @property
    def channels(self) -> dict[str, str]:
        return {"eai": self.channel_alice_eai, "korean": self.channel_alice_korean}

    @property
    def topics(self) -> dict[str, int]:
        return {"eai": self.topic_eai, "korean": self.topic_korean}

    def template_buttons(self, channel_key: str) -> list[tuple[str, str]]:
        if channel_key == "eai":
            return [
                ("💬 Join Alice Chat", "https://t.me/thealiceai/28604"),
                ("🔮 Predict Channel", "https://t.me/aliceeaichannel"),
                ("🤑 Start Winning with Alice", "https://thealiceai.com/saba"),
            ]
        return [
            ("💬 Alice 채팅방 참여", "https://t.me/thealiceai/28604"),
            ("🔮 예측 채널 보기", "https://t.me/alicekoreanbet"),
            ("🤑 Alice에서 수익 시작", "https://thealiceai.com/saba"),
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
