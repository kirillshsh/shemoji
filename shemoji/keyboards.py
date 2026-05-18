from __future__ import annotations

import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .config import AppConfig
from .constants import (
    DELETE_PACK_CALLBACK,
    RENAME_PACK_CALLBACK,
    SEPARATE_PREVIEW_PREFIX,
    SETTINGS_BUTTON_TEXT,
    SETTINGS_EXAMPLES_CALLBACK,
    VIEW_DELETE_PACK_PREFIX,
)


def pack_ready_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сменить название", callback_data=RENAME_PACK_CALLBACK),
                InlineKeyboardButton(text="Удалить пак", callback_data=DELETE_PACK_CALLBACK),
            ]
        ]
    )


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=SETTINGS_BUTTON_TEXT)]],
        resize_keyboard=True,
        input_field_placeholder="Отправьте медиа или premium emoji",
    )


def pack_view_keyboard(url: str, row_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть пак", url=url)],
            [InlineKeyboardButton(text="Удалить пак", callback_data=f"{VIEW_DELETE_PACK_PREFIX}{row_id}")],
        ]
    )


def separate_preview_keyboard(row_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="В отдельном сообщении", callback_data=f"{SEPARATE_PREVIEW_PREFIX}{row_id}")]
        ]
    )


def max_auto_long_side(config: AppConfig) -> int:
    return max(1, math.isqrt(config.max_tiles))


def size_options(config: AppConfig) -> list[int]:
    start = 2 if max_auto_long_side(config) >= 2 else 1
    return list(range(start, max_auto_long_side(config) + 1))


def settings_keyboard(
    current_padding: int,
    current_long_side: int,
    config: AppConfig,
) -> InlineKeyboardMarkup:
    size_values = size_options(config)
    min_size, max_size = size_values[0], size_values[-1]
    padding_mark = " ⭐" if current_padding == config.default_padding else ""
    size_mark = " ⭐" if current_long_side == config.default_long_side else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➖", callback_data=f"padding:{max(0, current_padding - 1)}"),
                InlineKeyboardButton(text=f"Паддинг: {current_padding}px{padding_mark}", callback_data="noop"),
                InlineKeyboardButton(
                    text="➕",
                    callback_data=f"padding:{min(config.max_padding, current_padding + 1)}",
                ),
            ],
            [
                InlineKeyboardButton(text="➖", callback_data=f"size:{max(min_size, current_long_side - 1)}"),
                InlineKeyboardButton(
                    text=f"Размер: {current_long_side}x{current_long_side}{size_mark}",
                    callback_data="noop",
                ),
                InlineKeyboardButton(text="➕", callback_data=f"size:{min(max_size, current_long_side + 1)}"),
            ],
            [InlineKeyboardButton(text="🖼 Посмотреть примеры", callback_data=SETTINGS_EXAMPLES_CALLBACK)],
        ]
    )
