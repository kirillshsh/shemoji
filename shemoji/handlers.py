from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .builder import build_pack_from_file
from .config import AppConfig
from .constants import (
    DELETE_PACK_CALLBACK,
    GROUP_CHAT_TYPES,
    PACK_READY_LINK_PREVIEW,
    PADDING_EXAMPLES_CALLBACK,
    RENAME_PACK_CALLBACK,
    SEPARATE_PREVIEW_PREFIX,
    SETTINGS_BUTTON_TEXT,
    SETTINGS_EXAMPLES_CALLBACK,
    SIZE_EXAMPLES_CALLBACK,
    VIEW_DELETE_PACK_PREFIX,
)
from .examples import send_padding_examples_message, send_size_examples_message
from .keyboards import main_reply_keyboard, pack_view_keyboard, settings_keyboard, size_options
from .media import MediaError
from .progress import JobLimiter, StaticProgressEditor, finish_progress_with_error
from .sources import _custom_emoji_file, _message_file, group_grid_text
from .stickers import custom_emoji_grid_body_html
from .storage import LastViewMessage, PendingRename, SettingsStore
from .views import (
    START_TEXT,
    normalize_pack_title,
    refresh_last_view_message,
    send_pack_view,
    separate_preview_row_id,
    settings_text,
    sticker_set_owned_by_bot,
    view_delete_row_id,
    view_packs_html,
    view_row_id_from_start,
    visible_user_packs,
)


logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith(SEPARATE_PREVIEW_PREFIX))
async def separate_preview_callback(callback: CallbackQuery, store: SettingsStore) -> None:
    if callback.message is None:
        await callback.answer("Не вижу сообщение с примером.", show_alert=True)
        return

    row_id = separate_preview_row_id(callback.data)
    if row_id is None:
        await callback.answer("Не понял, какой пример отправить.", show_alert=True)
        return

    pack = store.get_pack_by_row_id(row_id)
    if pack is None:
        await callback.answer("Пак уже удалён или недоступен.", show_alert=True)
        return

    me = await callback.bot.get_me()
    if not sticker_set_owned_by_bot(pack.set_name, me.username):
        store.delete_pack_message(pack.chat_id, pack.message_id)
        await callback.answer("Пак уже удалён или недоступен.", show_alert=True)
        return

    try:
        sticker_set = await callback.bot.get_sticker_set(name=pack.set_name)
    except TelegramBadRequest:
        store.delete_pack_message(pack.chat_id, pack.message_id)
        await callback.answer("Пак уже удалён или недоступен.", show_alert=True)
        return

    preview = custom_emoji_grid_body_html(sticker_set, pack.cols)
    if not preview:
        await callback.answer("Не смог собрать пример.", show_alert=True)
        return

    await callback.message.answer(
        preview,
        parse_mode=ParseMode.HTML,
        link_preview_options=PACK_READY_LINK_PREVIEW,
        reply_markup=pack_view_keyboard(pack.url, row_id),
    )
    await callback.answer("Отправил отдельно")


@router.message(Command("start", "help"))
async def start(message: Message, store: SettingsStore) -> None:
    if message.chat.type != "private":
        return
    row_id = view_row_id_from_start(message.text)
    if row_id is not None:
        await send_pack_view(message, store, row_id)
        return
    await message.answer(START_TEXT, parse_mode=ParseMode.HTML, reply_markup=main_reply_keyboard())


@router.message(Command("view"))
async def view_packs(message: Message, store: SettingsStore) -> None:
    if message.chat.type != "private":
        return
    packs, bot_username = await visible_user_packs(message.bot, store, message.from_user.id)
    if not packs:
        store.clear_last_view_message(message.from_user.id)
        await message.answer(
            "Паков пока нет. Сначала отправь картинку, видео, GIF, стикер или premium emoji.",
            reply_markup=main_reply_keyboard(),
        )
        return

    view_message = await message.answer(
        view_packs_html(packs, bot_username),
        parse_mode=ParseMode.HTML,
        link_preview_options=PACK_READY_LINK_PREVIEW,
    )
    store.save_last_view_message(
        LastViewMessage(
            user_id=message.from_user.id,
            chat_id=view_message.chat.id,
            message_id=view_message.message_id,
        )
    )


async def send_settings_message(message: Message, store: SettingsStore, config: AppConfig) -> None:
    padding = store.get_padding(message.from_user.id)
    long_side = store.get_long_side(message.from_user.id)
    await message.answer(
        settings_text(padding, long_side, config.default_padding, config.default_long_side),
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard(padding, long_side, config),
    )


@router.message(Command("settings"))
async def show_settings(message: Message, store: SettingsStore, config: AppConfig) -> None:
    if message.chat.type != "private":
        return
    await send_settings_message(message, store, config)


@router.message(F.text == SETTINGS_BUTTON_TEXT)
async def show_settings_button(message: Message, store: SettingsStore, config: AppConfig) -> None:
    if message.chat.type != "private":
        return
    await send_settings_message(message, store, config)


@router.callback_query(F.data.startswith("padding:"))
async def set_padding_callback(callback: CallbackQuery, store: SettingsStore, config: AppConfig) -> None:
    padding = int(callback.data.split(":", 1)[1])
    if padding < 0 or padding > config.max_padding:
        await callback.answer("Некорректное значение", show_alert=True)
        return

    store.set_padding(callback.from_user.id, padding)
    long_side = store.get_long_side(callback.from_user.id)
    await callback.message.edit_text(
        settings_text(padding, long_side, config.default_padding, config.default_long_side),
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard(padding, long_side, config),
    )
    await callback.answer(f"Паддинг: {padding}px")


@router.callback_query(F.data.startswith("size:"))
async def set_size_callback(callback: CallbackQuery, store: SettingsStore, config: AppConfig) -> None:
    long_side = int(callback.data.split(":", 1)[1])
    options = size_options(config)
    min_size = options[0]
    max_size = options[-1]
    if long_side < min_size or long_side > max_size:
        await callback.answer("Некорректное значение", show_alert=True)
        return

    store.set_long_side(callback.from_user.id, long_side)
    padding = store.get_padding(callback.from_user.id)
    await callback.message.edit_text(
        settings_text(padding, long_side, config.default_padding, config.default_long_side),
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard(padding, long_side, config),
    )
    await callback.answer(f"Размер: {long_side}")


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == SETTINGS_EXAMPLES_CALLBACK)
async def show_settings_examples(callback: CallbackQuery, store: SettingsStore, config: AppConfig) -> None:
    if callback.message is None:
        await callback.answer("Не вижу чат.", show_alert=True)
        return

    await callback.answer()
    await send_size_examples_message(callback.message, callback.bot, store, config, callback.from_user.id)
    await send_padding_examples_message(callback.message, callback.bot, store, config, callback.from_user.id)


@router.callback_query(F.data == PADDING_EXAMPLES_CALLBACK)
async def show_padding_examples(callback: CallbackQuery, store: SettingsStore, config: AppConfig) -> None:
    if callback.message is None:
        await callback.answer("Не вижу чат.", show_alert=True)
        return

    await callback.answer()
    await send_padding_examples_message(callback.message, callback.bot, store, config, callback.from_user.id)


@router.callback_query(F.data == SIZE_EXAMPLES_CALLBACK)
async def show_size_examples(callback: CallbackQuery, store: SettingsStore, config: AppConfig) -> None:
    if callback.message is None:
        await callback.answer("Не вижу чат.", show_alert=True)
        return

    await callback.answer()
    await send_size_examples_message(callback.message, callback.bot, store, config, callback.from_user.id)


@router.callback_query(F.data.startswith(VIEW_DELETE_PACK_PREFIX))
async def delete_view_pack_callback(callback: CallbackQuery, store: SettingsStore) -> None:
    if callback.message is None:
        await callback.answer("Не вижу сообщение с паком.", show_alert=True)
        return

    row_id = view_delete_row_id(callback.data)
    if row_id is None:
        await callback.answer("Не понял, какой пак удалить.", show_alert=True)
        return

    pack = store.get_pack_by_row_id(row_id)
    if pack is None:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        await refresh_last_view_message(callback.bot, store, callback.from_user.id)
        await callback.answer("Пак уже удалён")
        return
    if pack.user_id != callback.from_user.id:
        await callback.answer("Удалить может только создатель пака.", show_alert=True)
        return

    try:
        await callback.bot.delete_sticker_set(name=pack.set_name)
    except TelegramBadRequest:
        logger.exception("view sticker set delete failed")
        await callback.answer("Не получилось удалить пак.", show_alert=True)
        return

    pending = store.get_pending_rename(callback.from_user.id)
    if pending and pending.set_name == pack.set_name:
        store.clear_pending_rename(callback.from_user.id)
        try:
            await callback.bot.delete_message(chat_id=pending.chat_id, message_id=pending.prompt_message_id)
        except TelegramBadRequest:
            pass

    store.delete_pack_message(pack.chat_id, pack.message_id)
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await refresh_last_view_message(callback.bot, store, callback.from_user.id)
    await callback.answer("Пак удалён")


@router.callback_query(F.data == RENAME_PACK_CALLBACK)
async def rename_pack_callback(callback: CallbackQuery, store: SettingsStore) -> None:
    if callback.message is None:
        await callback.answer("Не вижу сообщение с паком.", show_alert=True)
        return

    pack = store.get_pack_message(callback.message.chat.id, callback.message.message_id)
    if pack is None:
        await callback.answer("Не нашёл этот пак в локальной базе.", show_alert=True)
        return
    if pack.user_id != callback.from_user.id:
        await callback.answer("Переименовать может только создатель пака.", show_alert=True)
        return

    previous = store.get_pending_rename(callback.from_user.id)
    if previous:
        try:
            await callback.bot.delete_message(chat_id=previous.chat_id, message_id=previous.prompt_message_id)
        except TelegramBadRequest:
            pass

    prompt = await callback.message.answer("Напишите новое название пака.")
    store.set_pending_rename(
        PendingRename(
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            pack_message_id=callback.message.message_id,
            prompt_message_id=prompt.message_id,
            set_name=pack.set_name,
        )
    )
    await callback.answer()


@router.callback_query(F.data == DELETE_PACK_CALLBACK)
async def delete_pack_callback(callback: CallbackQuery, store: SettingsStore) -> None:
    if callback.message is None:
        await callback.answer("Не вижу сообщение с паком.", show_alert=True)
        return

    pack = store.get_pack_message(callback.message.chat.id, callback.message.message_id)
    if pack is None:
        await callback.answer("Не нашёл этот пак в локальной базе.", show_alert=True)
        return
    if pack.user_id != callback.from_user.id:
        await callback.answer("Удалить может только создатель пака.", show_alert=True)
        return

    try:
        await callback.bot.delete_sticker_set(name=pack.set_name)
    except TelegramBadRequest:
        logger.exception("sticker set delete failed")
        await callback.answer("Не получилось удалить пак.", show_alert=True)
        return

    pending = store.get_pending_rename(callback.from_user.id)
    if pending and pending.set_name == pack.set_name:
        store.clear_pending_rename(callback.from_user.id)
        try:
            await callback.bot.delete_message(chat_id=pending.chat_id, message_id=pending.prompt_message_id)
        except TelegramBadRequest:
            pass

    store.delete_pack_message(pack.chat_id, pack.message_id)
    await refresh_last_view_message(callback.bot, store, callback.from_user.id)
    await callback.message.edit_text("Пак удалён.")
    await callback.answer("Пак удалён")


@router.message(Command("emoji"))
async def emoji_group_command(
    message: Message,
    store: SettingsStore,
    config: AppConfig,
    job_limiter: JobLimiter,
) -> None:
    if message.chat.type not in GROUP_CHAT_TYPES or message.from_user is None:
        return

    source = message.reply_to_message
    if source is None:
        await message.answer("Кинь /emoji в reply на медиа, стикер или premium emoji.", reply_to_message_id=message.message_id)
        return

    status = await message.answer("щас...", reply_to_message_id=message.message_id)
    owner_user_id = message.from_user.id

    try:
        file_id, media_kind, suffix = _message_file(source)
    except MediaError:
        await _build_group_pack_from_non_file(message, source, status, store, config, job_limiter, owner_user_id)
        return

    await build_pack_from_file(
        message,
        store,
        config,
        job_limiter,
        file_id,
        media_kind,
        suffix,
        group_grid_text(message, source),
        owner_user_id=owner_user_id,
        existing_progress_message=status,
        progress_updates=False,
        result_reply_to=source,
        send_ready=False,
    )


async def _build_group_pack_from_non_file(
    message: Message,
    source: Message,
    status: Message,
    store: SettingsStore,
    config: AppConfig,
    job_limiter: JobLimiter,
    owner_user_id: int,
) -> None:
    try:
        custom_emoji_file = await _custom_emoji_file(source)
    except MediaError as error:
        await finish_progress_with_error(status, StaticProgressEditor(status), str(error), progress_updates=False)
        return
    except TelegramBadRequest:
        logger.exception("group custom emoji lookup failed")
        await finish_progress_with_error(
            status,
            StaticProgressEditor(status),
            "Не получилось скачать этот premium emoji.",
            progress_updates=False,
        )
        return

    if custom_emoji_file is not None:
        file_id, media_kind, suffix, needs_repainting = custom_emoji_file
        await build_pack_from_file(
            message,
            store,
            config,
            job_limiter,
            file_id,
            media_kind,
            suffix,
            group_grid_text(message, source) or source.text,
            needs_repainting=needs_repainting,
            owner_user_id=owner_user_id,
            existing_progress_message=status,
            progress_updates=False,
            result_reply_to=source,
            send_ready=False,
        )
        return

    await finish_progress_with_error(
        status,
        StaticProgressEditor(status),
        "Ответь /emoji на картинку, видео, GIF, кружок, стикер или premium emoji.",
        progress_updates=False,
    )


@router.message((F.chat.type == "private") & (F.photo | F.document | F.video | F.video_note | F.animation | F.sticker))
async def handle_media(message: Message, store: SettingsStore, config: AppConfig, job_limiter: JobLimiter) -> None:
    try:
        file_id, media_kind, suffix = _message_file(message)
    except MediaError as error:
        await message.answer(str(error))
        return

    await build_pack_from_file(message, store, config, job_limiter, file_id, media_kind, suffix, message.caption)


@router.message((F.chat.type == "private") & F.text)
async def handle_text(message: Message, store: SettingsStore, config: AppConfig, job_limiter: JobLimiter) -> None:
    pending = store.get_pending_rename(message.from_user.id)
    if pending is not None:
        await _finish_pending_rename(message, store, pending)
        return

    if (message.text or "").startswith("/"):
        await message.answer("Эта команда больше не нужна. Откройте /settings и выберите padding или размер кнопками.")
        return

    try:
        custom_emoji_file = await _custom_emoji_file(message)
    except MediaError as error:
        await message.answer(str(error))
        return
    except TelegramBadRequest:
        logger.exception("custom emoji lookup failed")
        await message.answer("Не получилось скачать этот premium emoji.")
        return

    if custom_emoji_file is not None:
        file_id, media_kind, suffix, needs_repainting = custom_emoji_file
        await build_pack_from_file(
            message,
            store,
            config,
            job_limiter,
            file_id,
            media_kind,
            suffix,
            message.text,
            needs_repainting=needs_repainting,
        )
        return

    await message.answer(
        "Отправьте картинку, видео, GIF, кружок, стикер или premium emoji. Настройки: /settings",
        reply_markup=main_reply_keyboard(),
    )


async def _finish_pending_rename(message: Message, store: SettingsStore, pending: PendingRename) -> None:
    if message.chat.id != pending.chat_id:
        await message.answer("Название нужно отправить в том же чате, где нажали кнопку.")
        return

    try:
        title = normalize_pack_title(message.text)
        await message.bot.set_sticker_set_title(name=pending.set_name, title=title)
    except MediaError as error:
        await message.answer(str(error))
        return
    except TelegramBadRequest:
        logger.exception("sticker set title update failed")
        await message.answer("Не получилось сменить название пака.")
        return

    store.update_pack_title(pending.chat_id, pending.pack_message_id, title)
    store.clear_pending_rename(message.from_user.id)

    for chat_id, message_id in [
        (message.chat.id, message.message_id),
        (pending.chat_id, pending.prompt_message_id),
    ]:
        try:
            await message.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest:
            pass


@router.message(F.chat.type == "private")
async def fallback(message: Message) -> None:
    await message.answer(
        "Отправьте картинку, видео, GIF, кружок, стикер или premium emoji. Настройки: /settings",
        reply_markup=main_reply_keyboard(),
    )
