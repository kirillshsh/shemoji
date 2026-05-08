from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.methods import TelegramMethod


logger = logging.getLogger(__name__)
T = TypeVar("T")
STICKER_WRITE_METHODS = {
    "AddStickerToSet",
    "CreateNewStickerSet",
    "DeleteStickerSet",
    "ReplaceStickerInSet",
    "SetCustomEmojiStickerSetThumbnail",
    "SetStickerSetTitle",
}
UPLOAD_METHODS = {"UploadStickerFile"}


@dataclass(frozen=True)
class TelegramRetryConfig:
    attempts: int = 5
    retry_after_limit: int = 90
    base_delay: float = 0.5
    max_delay: float = 8.0


async def retry_telegram(
    action: Callable[[], Awaitable[T]],
    config: TelegramRetryConfig,
    label: str,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    jitter: Callable[[], float] = lambda: random.uniform(0.05, 0.35),
) -> T:
    attempts = max(1, config.attempts)
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except TelegramRetryAfter as error:
            delay = float(error.retry_after) + jitter()
            if attempt >= attempts or delay > config.retry_after_limit:
                logger.warning("%s hit flood limit for %.1fs; no retry left", label, delay)
                raise
            logger.warning("%s hit flood limit; retry %s/%s in %.1fs", label, attempt, attempts, delay)
            await sleep(delay)
        except (TelegramNetworkError, TelegramServerError) as error:
            if attempt >= attempts:
                logger.warning("%s failed after %s attempts: %s", label, attempts, error)
                raise
            delay = min(config.max_delay, config.base_delay * (2 ** (attempt - 1))) + jitter()
            logger.warning("%s transient Telegram error; retry %s/%s in %.1fs", label, attempt, attempts, delay)
            await sleep(delay)
        except TelegramBadRequest as error:
            if label != "UploadStickerFile" or "wrong file type" not in str(error).lower() or attempt >= attempts:
                raise
            delay = min(config.max_delay, config.base_delay * (2 ** (attempt - 1))) + jitter()
            logger.warning("%s got flaky file type validation; retry %s/%s in %.1fs", label, attempt, attempts, delay)
            await sleep(delay)
    raise RuntimeError("unreachable retry state")


def is_sticker_write_method(method: TelegramMethod[object]) -> bool:
    return type(method).__name__ in STICKER_WRITE_METHODS


def is_upload_method(method: TelegramMethod[object]) -> bool:
    return type(method).__name__ in UPLOAD_METHODS


class ResilientBot(Bot):
    def __init__(
        self,
        token: str,
        retry_config: TelegramRetryConfig,
        sticker_concurrency: int,
        upload_concurrency: int,
        default: DefaultBotProperties | None = None,
    ) -> None:
        super().__init__(token=token, default=default)
        self._retry_config = retry_config
        self._sticker_write_semaphore = asyncio.Semaphore(max(1, sticker_concurrency))
        self._upload_semaphore = asyncio.Semaphore(max(1, upload_concurrency))

    async def __call__(self, method: TelegramMethod[T], request_timeout: int | None = None) -> T:
        async def send() -> T:
            return await super(ResilientBot, self).__call__(method, request_timeout=request_timeout)

        label = type(method).__name__
        if is_upload_method(method):
            async with self._upload_semaphore:
                return await retry_telegram(send, self._retry_config, label)
        if is_sticker_write_method(method):
            async with self._sticker_write_semaphore:
                return await retry_telegram(send, self._retry_config, label)
        return await retry_telegram(send, self._retry_config, label)
