from __future__ import annotations

import asyncio
from datetime import datetime
from random import choice
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from .config import AppConfig
from .constants import PACK_READY_LINK_PREVIEW, PARTY_POPPER_MESSAGE_EFFECT_ID
from .keyboards import pack_ready_keyboard, separate_preview_keyboard
from .media import (
    Grid,
    MediaError,
    make_static_tiles,
    make_tgs_tiles,
    make_video_tiles,
    parse_grid,
)
from .progress import (
    JobLimiter,
    ProgressEditor,
    StaticProgressEditor,
    finish_progress_with_error,
    thread_progress_callback,
    upload_progress_callback,
)
from .stickers import (
    create_custom_emoji_set,
    custom_emoji_grid_body_html,
    custom_emoji_grid_html,
    make_title,
    sticker_set_url,
)
from .storage import PackRecord, SettingsStore
from .views import pack_ready_html


logger = logging.getLogger(__name__)


def is_wrong_file_type_error(error: TelegramBadRequest) -> bool:
    return "wrong file type" in str(error).lower()


def tgs_auto_long_side_candidates(grid: Grid | None, long_side: int) -> list[int]:
    if grid is not None:
        return [long_side]
    return list(range(max(1, long_side), 1, -1))


def tgs_wrong_file_type_message(grid: Grid, auto_grid: bool) -> str:
    if auto_grid:
        return (
            "Telegram не принял .TGS-плитки даже на уменьшенной сетке. "
            "Попробуйте другой emoji или укажите сетку вручную, на 5x5."
        )
    return (
        f"Telegram не принял .TGS-плитки для сетки {grid.cols}x{grid.rows}. "
        "Попробуйте сетку поменьше, например 5x5."
    )


async def build_sticker_set_with_progress(
    bot: Bot,
    user_id: int,
    progress: ProgressEditor,
    batch,
    title: str,
    upload_concurrency: int,
    needs_repainting: bool = False,
) -> object:
    await progress.edit(
        f"Готово: {batch.grid.cols}x{batch.grid.rows}, {batch.grid.count} плиток.\n"
        "Готовлю загрузку emoji-пака...",
        force=True,
    )
    me = await bot.get_me()
    return await create_custom_emoji_set(
        bot=bot,
        user_id=user_id,
        bot_username=me.username,
        paths=batch.paths,
        sticker_format=batch.sticker_format,
        title=title,
        needs_repainting=needs_repainting,
        upload_concurrency=upload_concurrency,
        progress_callback=upload_progress_callback(progress, batch),
    )


async def send_pack_result(
    message: Message,
    store: SettingsStore,
    sticker_set,
    batch,
    padding: int,
    saxophone: list[str]
) -> None:
    url = sticker_set_url(sticker_set.name)
    ready_html = pack_ready_html(url, batch.grid.cols, batch.grid.rows, padding)
    effect_kwargs = (
        {"message_effect_id": PARTY_POPPER_MESSAGE_EFFECT_ID}
        if message.chat.type == "private"
        else {}
    )
    try:
        ready_message = await message.answer(
            ready_html,
            parse_mode=ParseMode.HTML,
            link_preview_options=PACK_READY_LINK_PREVIEW,
            reply_markup=pack_ready_keyboard(),
            **effect_kwargs,
        )
    except TelegramBadRequest:
        ready_message = await message.answer(
            pack_ready_html(url, batch.grid.cols, batch.grid.rows, padding, custom_emoji=False),
            parse_mode=ParseMode.HTML,
            link_preview_options=PACK_READY_LINK_PREVIEW,
            reply_markup=pack_ready_keyboard(),
        )
    row_id = store.save_pack_message(
        PackRecord(
            chat_id=ready_message.chat.id,
            message_id=ready_message.message_id,
            user_id=message.from_user.id,
            set_name=sticker_set.name,
            url=url,
            cols=batch.grid.cols,
            rows=batch.grid.rows,
            padding=padding,
            title=sticker_set.title,
        )
    )

    preview = custom_emoji_grid_html(sticker_set, batch.grid.cols)
    
    if preview:
        sax_lyric = ""
        if store.get_saxophone(message.from_user.id):
            sax_lyric = "\n\n"
            dt = datetime.now(ZoneInfo("Europe/Moscow"))
            if dt.hour == 0 and dt.minute == 0:
                sax_lyric += "и ты сегодня нас не жди домооооой а на часах ноль ноль"
            else:
                sax_lyric += choice(saxophone)
        try:
            await message.answer(
                preview + sax_lyric,
                parse_mode=ParseMode.HTML,
                reply_markup=separate_preview_keyboard(row_id),
            )
        except TelegramBadRequest:
            await message.answer(
                "Не смог отправить превью custom emoji от имени бота. "
                "Откройте пак по ссылке и соберите картинку вручную в посте." + sax_lyric
            )


async def send_pack_preview_reply(
    source_message: Message,
    sticker_set,
    batch,
) -> None:
    preview = custom_emoji_grid_body_html(sticker_set, batch.grid.cols)
    if not preview:
        raise MediaError("Не смог собрать превью emoji-пака.")
    await source_message.answer(
        preview,
        parse_mode=ParseMode.HTML,
        link_preview_options=PACK_READY_LINK_PREVIEW,
        reply_to_message_id=source_message.message_id,
    )


async def _build_media_batch(
    input_path: Path,
    output_dir: Path,
    media_kind: str,
    padding: int,
    grid: Grid | None,
    long_side: int,
    config: AppConfig,
    slice_callback,
):
    if media_kind == "image":
        return await asyncio.to_thread(
            make_static_tiles,
            input_path,
            output_dir,
            padding,
            grid,
            long_side,
            config.max_tiles,
            slice_callback,
        )
    return await asyncio.to_thread(
        make_video_tiles,
        input_path,
        output_dir,
        padding,
        grid,
        long_side,
        config.max_tiles,
        config.max_video_seconds,
        config.max_video_tile_bytes,
        slice_callback,
        config.media_tile_concurrency,
    )


async def _build_tgs_set(
    message: Message,
    user_id: int,
    progress: ProgressEditor,
    input_path: Path,
    temp_root: Path,
    padding: int,
    grid: Grid | None,
    long_side: int,
    config: AppConfig,
    slice_callback,
    needs_repainting: bool,
):
    batch = None
    sticker_set = None
    candidates = tgs_auto_long_side_candidates(grid, long_side)
    auto_grid = grid is None
    for attempt_index, candidate_long_side in enumerate(candidates):
        output_dir = temp_root / f"tiles_tgs_{attempt_index}_{candidate_long_side}"
        batch = await asyncio.to_thread(
            make_tgs_tiles,
            input_path,
            output_dir,
            padding,
            grid,
            candidate_long_side,
            config.max_tiles,
            config.max_video_seconds,
            config.max_video_tile_bytes,
            slice_callback,
        )
        try:
            sticker_set = await build_sticker_set_with_progress(
                message.bot,
                user_id,
                progress,
                batch,
                title=make_title(batch.grid.cols, batch.grid.rows),
                upload_concurrency=config.telegram_upload_concurrency,
                needs_repainting=needs_repainting,
            )
            break
        except TelegramBadRequest as error:
            if not is_wrong_file_type_error(error):
                raise
            if attempt_index >= len(candidates) - 1:
                raise MediaError(tgs_wrong_file_type_message(batch.grid, auto_grid)) from error
            next_long_side = candidates[attempt_index + 1]
            logger.warning(
                "Telegram rejected TGS grid %sx%s; retrying with long side %s",
                batch.grid.cols,
                batch.grid.rows,
                next_long_side,
            )
            await progress.edit(
                f"Telegram не принял TGS {batch.grid.cols}x{batch.grid.rows}.\n"
                f"Пробую {next_long_side} по длинной стороне...",
                force=True,
            )
    if batch is None or sticker_set is None:
        raise MediaError("Не удалось создать .TGS emoji-пак.")
    return batch, sticker_set


async def build_pack_from_file(
    message: Message,
    store: SettingsStore,
    config: AppConfig,
    job_limiter: JobLimiter,
    file_id: str,
    media_kind: str,
    suffix: str,
    saxophone: list[str],
    grid_text: str | None,
    needs_repainting: bool = False,
    owner_user_id: int | None = None,
    initial_progress_text: str = "Поставил задачу в очередь...",
    progress_reply_to_message_id: int | None = None,
    existing_progress_message: Message | None = None,
    progress_updates: bool = True,
    result_reply_to: Message | None = None,
    send_ready: bool = True,
) -> None:
    user_id = owner_user_id or message.from_user.id
    padding = store.get_padding(user_id)
    long_side = store.get_long_side(user_id)
    progress_message = existing_progress_message or await message.answer(
        initial_progress_text,
        reply_to_message_id=progress_reply_to_message_id,
    )
    progress = ProgressEditor(progress_message) if progress_updates else StaticProgressEditor(progress_message)

    temp_root: Path | None = None
    try:
        async with job_limiter.slot(user_id):
            temp_root = Path(tempfile.mkdtemp(prefix=f"emoji_{user_id}_", dir=config.work_dir))
            input_path = temp_root / f"input{suffix}"
            await progress.edit("Принял, скачиваю медиа...", force=True)
            telegram_file = await message.bot.get_file(file_id)
            await message.bot.download_file(telegram_file.file_path, input_path)

            grid = parse_grid(grid_text, config.max_tiles)
            loop = asyncio.get_running_loop()
            slice_callback = thread_progress_callback(progress, loop, "Нарезаю медиа на emoji-плитки...")

            if media_kind == "tgs":
                batch, sticker_set = await _build_tgs_set(
                    message,
                    user_id,
                    progress,
                    input_path,
                    temp_root,
                    padding,
                    grid,
                    long_side,
                    config,
                    slice_callback,
                    needs_repainting,
                )
            else:
                batch = await _build_media_batch(
                    input_path,
                    temp_root / "tiles",
                    media_kind,
                    padding,
                    grid,
                    long_side,
                    config,
                    slice_callback,
                )
                sticker_set = await build_sticker_set_with_progress(
                    message.bot,
                    user_id,
                    progress,
                    batch,
                    title=make_title(batch.grid.cols, batch.grid.rows),
                    upload_concurrency=config.telegram_upload_concurrency,
                    needs_repainting=needs_repainting,
                )

            try:
                await progress_message.delete()
            except TelegramBadRequest:
                pass

            if send_ready:
                await send_pack_result(message, store, sticker_set, batch, padding, saxophone)
            else:
                await send_pack_preview_reply(result_reply_to or message, sticker_set, batch)

    except MediaError as error:
        await finish_progress_with_error(progress_message, progress, str(error), progress_updates)
    except subprocess.CalledProcessError:
        logger.exception("ffmpeg failed")
        await finish_progress_with_error(
            progress_message,
            progress,
            "Не удалось обработать видео/GIF через ffmpeg.",
            progress_updates,
        )
    except Exception:
        logger.exception("media handling failed")
        await finish_progress_with_error(
            progress_message,
            progress,
            "Что-то пошло не так при создании emoji-пака.",
            progress_updates,
        )
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
