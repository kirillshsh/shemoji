from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from .constants import PACK_READY_LINK_PREVIEW, SEPARATE_PREVIEW_PREFIX, VIEW_DELETE_PACK_PREFIX, VIEW_PACK_PREFIX
from .keyboards import pack_view_keyboard
from .media import MediaError
from .stickers import custom_emoji_grid_body_html
from .storage import PackRecord, SettingsStore


logger = logging.getLogger(__name__)

START_TEXT = """<tg-emoji emoji-id="5145373277329295567">😜</tg-emoji> <b>ПРИВЕТ</b>

Этот бот позволяет вставлять медиа прямо в текст постов.

Отправьте картинку, видео, GIF, кружок, стикер или премиум-эмодзи — бот нарежет их на премиум-эмодзи. Из них можно собрать изображение в любом посте, как пазл.

• <b>Анимации:</b> видео, GIF, кружки, видео-стикеры, .TGS-стикеры и анимированные премиум-эмодзи станут анимированными эмодзи (бот сам уберёт звук, подгонит формат и оставит первые 3 секунды).
• <b>Формат:</b> любое разрешение и соотношение сторон.
• <b>Сетка:</b> просто укажите её в подписи к файлу (например, <code>5x5</code> или <code>6x4</code>).

<tg-emoji emoji-id="6021582331251268218">⚙️</tg-emoji> <b>/settings</b> — настроить размер и отступы (padding)."""


def pack_ready_html(url: str, cols: int, rows: int, padding: int, custom_emoji: bool = True) -> str:
    emoji = '<tg-emoji emoji-id="5370870691140737817">🥳</tg-emoji>' if custom_emoji else "🥳"
    return f'{emoji} <a href="{url}"><b>Пак готов!</b></a>\n\nСетка: {cols}x{rows}\nПаддинг: {padding}px'


def normalize_pack_title(text: str | None) -> str:
    title = " ".join((text or "").split())
    if not title:
        raise MediaError("Название не должно быть пустым.")
    if len(title) > 64:
        raise MediaError("Название должно быть не длиннее 64 символов.")
    return title


def pack_view_deep_link(bot_username: str, row_id: int) -> str:
    return f"https://t.me/{bot_username}?start={VIEW_PACK_PREFIX}{row_id}"


def view_packs_html(packs: list[tuple[int, PackRecord]], bot_username: str) -> str:
    lines = [
        '<tg-emoji emoji-id="5470039656349310887">👅</tg-emoji> <b>Список паков:</b>',
        "",
    ]
    for index, (row_id, pack) in enumerate(packs, start=1):
        title = html.escape(pack.title)
        url = pack_view_deep_link(bot_username, row_id)
        lines.append(f'{index}) <a href="{url}">{title}</a>')
    lines.extend(["", "Нажми на пак, чтоб посмотреть его содержание"])
    return "\n".join(lines)


def settings_text(
    current_padding: int,
    current_long_side: int,
    default_padding: int,
    default_long_side: int,
) -> str:
    return f"""<tg-emoji emoji-id="6021582331251268218">⚙️</tg-emoji><b>Настройки</b>

• <b>Паддинг:</b> {current_padding}px (по умолчанию — {default_padding}px)
• <b>Размер:</b> {current_long_side} по длинной стороне (по умолчанию — {default_long_side})

<tg-emoji emoji-id="5472146462362048818">💡</tg-emoji><b>Как это работает:</b>
• <b>Паддинг:</b> Telegram по-разному рендерит эмодзи на ПК и телефонах — картинка может искажаться или разбиваться полосами. Паддинг добавляет невидимые отступы сверху и снизу, компенсируя эту разницу. Подбирается экспериментально.
• <b>Размер:</b> Задает автоматическую сетку, если вы не указали её в подписи к файлу. Чем больше значение, тем больше эмодзи будет по длинной стороне и тем детальнее выйдет картинка."""


def sticker_set_owned_by_bot(set_name: str, bot_username: str | None) -> bool:
    return bool(bot_username) and set_name.lower().endswith(f"_by_{bot_username.lower()}")


def _prefixed_int(text: str | None, prefix: str) -> int | None:
    if not text or not text.startswith(prefix):
        return None
    raw_id = text[len(prefix) :]
    return int(raw_id) if raw_id.isdigit() else None


def view_row_id_from_start(text: str | None) -> int | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    return _prefixed_int(parts[1], VIEW_PACK_PREFIX)


def view_delete_row_id(data: str | None) -> int | None:
    return _prefixed_int(data, VIEW_DELETE_PACK_PREFIX)


def separate_preview_row_id(data: str | None) -> int | None:
    return _prefixed_int(data, SEPARATE_PREVIEW_PREFIX)


async def visible_user_packs(bot: Bot, store: SettingsStore, user_id: int) -> tuple[list[tuple[int, PackRecord]], str]:
    me = await bot.get_me()
    bot_username = me.username or ""
    packs: list[tuple[int, PackRecord]] = []
    for row_id, pack in store.list_user_packs(user_id):
        if not sticker_set_owned_by_bot(pack.set_name, bot_username):
            store.delete_pack_message(pack.chat_id, pack.message_id)
            continue
        try:
            await bot.get_sticker_set(name=pack.set_name)
        except TelegramBadRequest:
            store.delete_pack_message(pack.chat_id, pack.message_id)
            continue
        packs.append((row_id, pack))
    return packs, bot_username


async def send_pack_view(message: Message, store: SettingsStore, row_id: int) -> None:
    pack = store.get_pack_by_row_id(row_id)
    if pack is None:
        await message.answer("Не нашёл этот пак в списке.")
        return
    if pack.user_id != message.from_user.id:
        await message.answer("Этот пак не твой.")
        return

    me = await message.bot.get_me()
    if not sticker_set_owned_by_bot(pack.set_name, me.username):
        store.delete_pack_message(pack.chat_id, pack.message_id)
        await message.answer("Пак уже удалён или недоступен.")
        return

    try:
        sticker_set = await message.bot.get_sticker_set(name=pack.set_name)
    except TelegramBadRequest:
        store.delete_pack_message(pack.chat_id, pack.message_id)
        await message.answer("Пак уже удалён или недоступен.")
        return

    preview = custom_emoji_grid_body_html(sticker_set, pack.cols)
    if not preview:
        await message.answer("Не смог собрать превью этого пака.")
        return

    await message.answer(
        preview,
        parse_mode=ParseMode.HTML,
        link_preview_options=PACK_READY_LINK_PREVIEW,
        reply_markup=pack_view_keyboard(pack.url, row_id),
    )


async def refresh_last_view_message(bot: Bot, store: SettingsStore, user_id: int) -> None:
    last_view = store.get_last_view_message(user_id)
    if last_view is None:
        return

    packs, bot_username = await visible_user_packs(bot, store, user_id)
    try:
        if not packs:
            await bot.edit_message_text(
                chat_id=last_view.chat_id,
                message_id=last_view.message_id,
                text="Паков пока нет. Сначала отправь картинку, видео, GIF, стикер или premium emoji.",
                link_preview_options=PACK_READY_LINK_PREVIEW,
            )
            return

        await bot.edit_message_text(
            chat_id=last_view.chat_id,
            message_id=last_view.message_id,
            text=view_packs_html(packs, bot_username),
            parse_mode=ParseMode.HTML,
            link_preview_options=PACK_READY_LINK_PREVIEW,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            logger.debug("last /view message edit failed: %s", error)
