from __future__ import annotations

import logging

from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeDefault

from .config import load_config
from .handlers import router
from .progress import JobLimiter
from .storage import SettingsStore
from .telegram_client import ResilientBot, TelegramRetryConfig


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    config.work_dir.mkdir(parents=True, exist_ok=True)

    store = SettingsStore(config.db_path, config.default_padding, config.default_long_side)
    bot = ResilientBot(
        token=config.bot_token,
        retry_config=TelegramRetryConfig(
            attempts=config.telegram_api_retries,
            retry_after_limit=config.telegram_retry_after_limit,
            base_delay=config.telegram_retry_base_delay,
            max_delay=config.telegram_retry_max_delay,
        ),
        sticker_concurrency=config.telegram_sticker_concurrency,
        upload_concurrency=config.telegram_upload_concurrency,
        default=DefaultBotProperties(parse_mode=None),
    )
    job_limiter = JobLimiter(config.media_job_concurrency, config.per_user_job_concurrency)

    dispatcher = Dispatcher(config=config, store=store, job_limiter=job_limiter)
    dispatcher.include_router(router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Что умеет бот"),
            BotCommand(command="settings", description="Настроить padding и размер"),
            BotCommand(command="view", description="Показать список моих паков"),
        ],
        scope=BotCommandScopeDefault(),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="emoji", description="Собрать emoji-пак из reply"),
        ],
        scope=BotCommandScopeAllGroupChats(),
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        store.close()
        await bot.session.close()
