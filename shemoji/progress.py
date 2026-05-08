from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from .constants import PROGRESS_EDIT_INTERVAL


logger = logging.getLogger(__name__)


class JobLimiter:
    def __init__(self, media_concurrency: int, per_user_concurrency: int = 1) -> None:
        self.global_semaphore = asyncio.Semaphore(max(1, media_concurrency))
        self.per_user_concurrency = max(1, per_user_concurrency)
        self.user_semaphores: dict[int, asyncio.Semaphore] = {}
        self.lock = asyncio.Lock()

    async def _user_semaphore(self, user_id: int) -> asyncio.Semaphore:
        async with self.lock:
            semaphore = self.user_semaphores.get(user_id)
            if semaphore is None:
                semaphore = asyncio.Semaphore(self.per_user_concurrency)
                self.user_semaphores[user_id] = semaphore
            return semaphore

    @asynccontextmanager
    async def slot(self, user_id: int) -> AsyncIterator[None]:
        user_semaphore = await self._user_semaphore(user_id)
        async with user_semaphore:
            async with self.global_semaphore:
                yield


def progress_text(action: str, done: int | None = None, total: int | None = None) -> str:
    if done is None or total is None or total <= 0:
        return action
    percent = min(100, max(0, round(done * 100 / total)))
    return f"{action}\n{done}/{total} ({percent}%)"


class ProgressEditor:
    def __init__(self, message: Message, min_interval: float = PROGRESS_EDIT_INTERVAL) -> None:
        self.message = message
        self.min_interval = min_interval
        self.last_text = ""
        self.last_edit = 0.0
        self.lock = asyncio.Lock()

    async def edit(self, text: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (text == self.last_text or now - self.last_edit < self.min_interval):
            return

        async with self.lock:
            now = time.monotonic()
            if not force and (text == self.last_text or now - self.last_edit < self.min_interval):
                return
            try:
                await self.message.edit_text(text)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    logger.debug("progress edit failed: %s", error)
                return
            self.last_text = text
            self.last_edit = now


class StaticProgressEditor(ProgressEditor):
    async def edit(self, text: str, force: bool = False) -> None:
        return


def thread_progress_callback(
    progress: ProgressEditor,
    loop: asyncio.AbstractEventLoop,
    action: str,
):
    def report(done: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(
            progress.edit(progress_text(action, done, total), force=done >= total),
            loop,
        )

    return report


def upload_progress_callback(progress: ProgressEditor, batch):
    async def report(stage: str, done: int, total: int) -> None:
        if stage == "upload":
            action = (
                f"Готово: {batch.grid.cols}x{batch.grid.rows}, {batch.grid.count} плиток.\n"
                "Загружаю файлы в Telegram..."
            )
        else:
            action = (
                f"Готово: {batch.grid.cols}x{batch.grid.rows}, {batch.grid.count} плиток.\n"
                "Создаю emoji-пак..."
            )
        await progress.edit(progress_text(action, done, total), force=done >= total)

    return report


async def finish_progress_with_error(
    progress_message: Message,
    progress: ProgressEditor,
    text: str,
    progress_updates: bool,
) -> None:
    if progress_updates:
        await progress.edit(text, force=True)
        return
    try:
        await progress_message.edit_text(text)
    except TelegramBadRequest:
        pass
