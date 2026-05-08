from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from .config import AppConfig
from .constants import PADDING_EXAMPLE_GRID, VK_SMILIES_SET_NAME
from .keyboards import size_options
from .media import Grid, MediaError, make_static_tiles
from .stickers import TILE_EMOJI, create_custom_emoji_set
from .storage import PaddingExampleSet, SettingsStore, SizeExampleSet


logger = logging.getLogger(__name__)


def _grid_example_html(title: str, ids: list[str], cols: int) -> str:
    lines = [f"<b>{title}</b>"]
    for offset in range(0, len(ids), cols):
        row_ids = ids[offset : offset + cols]
        lines.append(
            "".join(
                f'<tg-emoji emoji-id="{custom_id}">{TILE_EMOJI}</tg-emoji>'
                for custom_id in row_ids
            )
        )
    return "\n".join(lines).strip()


def padding_example_messages_html(sticker_set, max_padding: int) -> list[str] | None:
    ids = [sticker.custom_emoji_id for sticker in sticker_set.stickers]
    expected = (max_padding + 1) * PADDING_EXAMPLE_GRID.count
    if len(ids) < expected or any(custom_id is None for custom_id in ids[:expected]):
        return None

    messages: list[str] = []
    offset = 0
    for padding in range(max_padding + 1):
        example_ids = ids[offset : offset + PADDING_EXAMPLE_GRID.count]
        messages.append(
            _grid_example_html(
                f"Пример паддинга: {padding}px",
                example_ids,
                PADDING_EXAMPLE_GRID.cols,
            )
        )
        offset += PADDING_EXAMPLE_GRID.count
    return messages


def padding_examples_html(sticker_set, max_padding: int) -> str | None:
    messages = padding_example_messages_html(sticker_set, max_padding)
    if messages is None:
        return None
    return "\n\n".join(messages)


def size_example_messages_html(sticker_set, options: list[int]) -> list[str] | None:
    ids = [sticker.custom_emoji_id for sticker in sticker_set.stickers]
    expected = sum(long_side * long_side for long_side in options)
    if len(ids) < expected or any(custom_id is None for custom_id in ids[:expected]):
        return None

    messages: list[str] = []
    offset = 0
    for long_side in options:
        count = long_side * long_side
        example_ids = ids[offset : offset + count]
        messages.append(_grid_example_html(f"Пример размера: {long_side}x{long_side}", example_ids, long_side))
        offset += count
    return messages


def size_examples_html(sticker_set, options: list[int]) -> str | None:
    messages = size_example_messages_html(sticker_set, options)
    if messages is None:
        return None
    return "\n\n".join(messages)


async def _first_static_vk_sticker(bot: Bot):
    source_set = await bot.get_sticker_set(name=VK_SMILIES_SET_NAME)
    if not source_set.stickers:
        raise MediaError("В VKsmilies не нашёл стикеров для примера.")

    first = source_set.stickers[0]
    if first.is_animated or first.is_video:
        raise MediaError("Первый стикер VKsmilies оказался не статичным, пример не собрать.")
    return first


async def ensure_padding_example_set(
    bot: Bot,
    store: SettingsStore,
    config: AppConfig,
    user_id: int,
    force: bool = False,
) -> object:
    existing = store.get_padding_example_set(user_id)
    if existing and not force:
        try:
            return await bot.get_sticker_set(name=existing.set_name)
        except TelegramBadRequest:
            store.clear_padding_example_set(user_id)

    me = await bot.get_me()
    first = await _first_static_vk_sticker(bot)
    temp_root = Path(tempfile.mkdtemp(prefix=f"padding_examples_{user_id}_", dir=config.work_dir))
    try:
        input_path = temp_root / "vk_first.webp"
        telegram_file = await bot.get_file(first.file_id)
        await bot.download_file(telegram_file.file_path, input_path)

        paths: list[Path] = []
        for padding in range(config.max_padding + 1):
            batch = await asyncio.to_thread(
                make_static_tiles,
                input_path,
                temp_root / f"padding_{padding}",
                padding,
                PADDING_EXAMPLE_GRID,
                1,
                config.max_tiles,
            )
            paths.extend(batch.paths)

        sticker_set = await create_custom_emoji_set(
            bot=bot,
            user_id=user_id,
            bot_username=me.username,
            paths=paths,
            sticker_format="static",
            title="примеры паддинга",
            upload_concurrency=config.telegram_upload_concurrency,
        )
        if existing:
            try:
                await bot.delete_sticker_set(name=existing.set_name)
            except TelegramBadRequest:
                pass
        store.save_padding_example_set(PaddingExampleSet(user_id=user_id, set_name=sticker_set.name))
        return sticker_set
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


async def ensure_size_example_set(
    bot: Bot,
    store: SettingsStore,
    config: AppConfig,
    user_id: int,
    force: bool = False,
) -> object:
    existing = store.get_size_example_set(user_id)
    if existing and not force:
        try:
            return await bot.get_sticker_set(name=existing.set_name)
        except TelegramBadRequest:
            store.clear_size_example_set(user_id)

    me = await bot.get_me()
    first = await _first_static_vk_sticker(bot)
    temp_root = Path(tempfile.mkdtemp(prefix=f"size_examples_{user_id}_", dir=config.work_dir))
    try:
        input_path = temp_root / "vk_first.webp"
        telegram_file = await bot.get_file(first.file_id)
        await bot.download_file(telegram_file.file_path, input_path)

        paths: list[Path] = []
        for long_side in size_options(config):
            batch = await asyncio.to_thread(
                make_static_tiles,
                input_path,
                temp_root / f"size_{long_side}",
                0,
                Grid(cols=long_side, rows=long_side),
                long_side,
                config.max_tiles,
            )
            paths.extend(batch.paths)

        sticker_set = await create_custom_emoji_set(
            bot=bot,
            user_id=user_id,
            bot_username=me.username,
            paths=paths,
            sticker_format="static",
            title="примеры размера",
            upload_concurrency=config.telegram_upload_concurrency,
        )
        if existing:
            try:
                await bot.delete_sticker_set(name=existing.set_name)
            except TelegramBadRequest:
                pass
        store.save_size_example_set(SizeExampleSet(user_id=user_id, set_name=sticker_set.name))
        return sticker_set
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


async def send_padding_examples_message(
    message: Message,
    bot: Bot,
    store: SettingsStore,
    config: AppConfig,
    user_id: int,
) -> None:
    progress = await message.answer("Готовлю примеры паддинга...")
    try:
        sticker_set = await ensure_padding_example_set(bot=bot, store=store, config=config, user_id=user_id)
        messages = padding_example_messages_html(sticker_set, config.max_padding)
        if not messages:
            sticker_set = await ensure_padding_example_set(
                bot=bot,
                store=store,
                config=config,
                user_id=user_id,
                force=True,
            )
            messages = padding_example_messages_html(sticker_set, config.max_padding)
        if not messages:
            raise MediaError("Не смог собрать custom emoji для примеров.")
        await progress.edit_text(messages[0], parse_mode=ParseMode.HTML)
        for html in messages[1:]:
            await message.answer(html, parse_mode=ParseMode.HTML)
    except MediaError as error:
        await progress.edit_text(str(error))
    except TelegramBadRequest:
        logger.exception("padding examples failed")
        await progress.edit_text("Не получилось отправить примеры паддинга.")


async def send_size_examples_message(
    message: Message,
    bot: Bot,
    store: SettingsStore,
    config: AppConfig,
    user_id: int,
) -> None:
    progress = await message.answer("Готовлю примеры размера...")
    try:
        options = size_options(config)
        sticker_set = await ensure_size_example_set(bot=bot, store=store, config=config, user_id=user_id)
        messages = size_example_messages_html(sticker_set, options)
        if not messages:
            sticker_set = await ensure_size_example_set(
                bot=bot,
                store=store,
                config=config,
                user_id=user_id,
                force=True,
            )
            messages = size_example_messages_html(sticker_set, options)
        if not messages:
            raise MediaError("Не смог собрать custom emoji для примеров.")
        await progress.edit_text(messages[0], parse_mode=ParseMode.HTML)
        for html in messages[1:]:
            await message.answer(html, parse_mode=ParseMode.HTML)
    except MediaError as error:
        await progress.edit_text(str(error))
    except TelegramBadRequest:
        logger.exception("size examples failed")
        await progress.edit_text("Не получилось отправить примеры размера.")
