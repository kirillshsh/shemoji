from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    bot_token: str
    work_dir: Path = Path(".work")
    db_path: Path = Path("bot.sqlite3")
    saxophone_path: Path = Path("saxophone.json")
    default_padding: int = 3
    max_padding: int = 5
    default_long_side: int = 3
    max_tiles: int = 50
    max_video_seconds: float = 3.0
    max_video_tile_bytes: int = 240_000
    media_job_concurrency: int = 6
    per_user_job_concurrency: int = 4
    media_tile_concurrency: int = 8
    telegram_api_retries: int = 5
    telegram_retry_after_limit: int = 90
    telegram_retry_base_delay: float = 0.5
    telegram_retry_max_delay: float = 8.0
    telegram_upload_concurrency: int = 12
    telegram_sticker_concurrency: int = 1


def load_config() -> AppConfig:
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is empty. Put it into .env or export it.")

    config = AppConfig(bot_token=token)
    root = Path.cwd()
    return replace(
        config,
        work_dir=root / config.work_dir,
        db_path=root / config.db_path,
        saxophone_path=root / config.saxophone_path,
    )
