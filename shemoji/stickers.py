from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.types import FSInputFile, InputSticker, StickerSet


TILE_EMOJI = "🧩"
StickerProgressCallback = Callable[[str, int, int], Awaitable[None]]


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def make_sticker_set_name(user_id: int, bot_username: str) -> str:
    suffix = f"_by_{bot_username}"
    budget = 64 - len(suffix)
    if budget < 2:
        raise ValueError("Bot username is too long for sticker set names.")

    unique = f"e{_base36(user_id)}_{_base36(time.time_ns())}"
    prefix = unique[:budget].rstrip("_")
    return f"{prefix}{suffix}"


def make_title(grid_cols: int, grid_rows: int) -> str:
    return f"пак {grid_cols}х{grid_rows}"[:64]


def _input_sticker(file_id: str, sticker_format: str) -> InputSticker:
    return InputSticker(
        sticker=file_id,
        format=sticker_format,
        emoji_list=[TILE_EMOJI],
        keywords=["image", "tile", "emoji"],
    )


async def upload_sticker_files(
    bot: Bot,
    user_id: int,
    paths: list[Path],
    sticker_format: str,
    upload_concurrency: int,
    progress_callback: StickerProgressCallback | None = None,
) -> list[str]:
    if not paths:
        raise ValueError("Sticker set must contain at least one sticker.")

    queue: asyncio.Queue[tuple[int, Path]] = asyncio.Queue()
    for item in enumerate(paths):
        queue.put_nowait(item)

    uploaded_file_ids: list[str | None] = [None] * len(paths)
    progress_lock = asyncio.Lock()
    completed = 0

    async def worker() -> None:
        nonlocal completed
        while True:
            try:
                index, path = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            uploaded = await bot.upload_sticker_file(
                user_id=user_id,
                sticker=FSInputFile(path),
                sticker_format=sticker_format,
            )
            uploaded_file_ids[index] = uploaded.file_id
            if progress_callback:
                async with progress_lock:
                    completed += 1
                    await progress_callback("upload", completed, len(paths))

    worker_count = min(len(paths), max(1, upload_concurrency))
    tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    failed = next((task.exception() for task in done if task.exception() is not None), None)
    if failed is not None:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise failed
    await asyncio.gather(*pending)

    return [file_id for file_id in uploaded_file_ids if file_id is not None]


async def create_custom_emoji_set(
    bot: Bot,
    user_id: int,
    bot_username: str,
    paths: list[Path],
    sticker_format: str,
    title: str,
    needs_repainting: bool = False,
    upload_concurrency: int = 4,
    progress_callback: StickerProgressCallback | None = None,
) -> StickerSet:
    name = make_sticker_set_name(user_id, bot_username)

    uploaded_file_ids = await upload_sticker_files(
        bot=bot,
        user_id=user_id,
        paths=paths,
        sticker_format=sticker_format,
        upload_concurrency=upload_concurrency,
        progress_callback=progress_callback,
    )

    stickers = [_input_sticker(file_id, sticker_format) for file_id in uploaded_file_ids]
    initial, rest = stickers[:50], stickers[50:]

    created = False
    try:
        create_kwargs = {"needs_repainting": True} if needs_repainting else {}
        await bot.create_new_sticker_set(
            user_id=user_id,
            name=name,
            title=title,
            stickers=initial,
            sticker_type="custom_emoji",
            **create_kwargs,
        )
        created = True
        if progress_callback:
            await progress_callback("create", len(initial), len(stickers))

        for index, sticker in enumerate(rest, start=1):
            await bot.add_sticker_to_set(
                user_id=user_id,
                name=name,
                sticker=sticker,
            )
            if progress_callback:
                await progress_callback("create", len(initial) + index, len(stickers))
    except Exception:
        if created:
            try:
                await bot.delete_sticker_set(name=name)
            except Exception:
                pass
        raise

    sticker_set = await bot.get_sticker_set(name=name)
    custom_emoji_id = next(
        (sticker.custom_emoji_id for sticker in sticker_set.stickers if sticker.custom_emoji_id),
        None,
    )
    if custom_emoji_id:
        await bot.set_custom_emoji_sticker_set_thumbnail(
            name=name,
            custom_emoji_id=custom_emoji_id,
        )
    return sticker_set


def sticker_set_url(sticker_set_name: str) -> str:
    return f"https://t.me/addemoji/{sticker_set_name}"


def custom_emoji_grid_html(sticker_set: StickerSet, cols: int) -> str | None:
    body = custom_emoji_grid_body_html(sticker_set, cols)
    if body is None:
        return None
    return "<b>Пример:</b>\n\n" + body


def custom_emoji_grid_body_html(sticker_set: StickerSet, cols: int) -> str | None:
    ids = [sticker.custom_emoji_id for sticker in sticker_set.stickers]
    if not ids or any(custom_id is None for custom_id in ids):
        return None

    chunks: list[str] = []
    for index, custom_id in enumerate(ids):
        chunks.append(f'<tg-emoji emoji-id="{custom_id}">{TILE_EMOJI}</tg-emoji>')
        if (index + 1) % cols == 0:
            chunks.append("\n")
    return "".join(chunks).strip()
