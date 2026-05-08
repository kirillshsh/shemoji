from __future__ import annotations

from pathlib import Path

from aiogram.types import Message

from .media import MediaError


def _message_file(message: Message) -> tuple[str, str, str]:
    if message.sticker:
        if message.sticker.is_animated and not message.sticker.is_video:
            return message.sticker.file_id, "tgs", ".tgs"
        if message.sticker.is_video:
            return message.sticker.file_id, "video", ".webm"
        return message.sticker.file_id, "image", ".webp"

    if message.photo:
        return message.photo[-1].file_id, "image", ".jpg"

    if message.document:
        mime_type = message.document.mime_type or ""
        suffix = Path(message.document.file_name or "").suffix or ".bin"
        if suffix.lower() == ".tgs" or mime_type == "application/x-tgsticker":
            return message.document.file_id, "tgs", ".tgs"
        if mime_type.startswith("image/"):
            return message.document.file_id, "image", suffix
        if mime_type.startswith("video/"):
            return message.document.file_id, "video", suffix

    if message.video:
        return message.video.file_id, "video", ".mp4"

    if message.video_note:
        return message.video_note.file_id, "video", ".mp4"

    if message.animation:
        suffix = Path(message.animation.file_name or "").suffix or ".mp4"
        return message.animation.file_id, "video", suffix

    raise MediaError("Отправьте картинку, видео, GIF, кружок, стикер или премиум-эмодзи.")


def _message_size(message: Message) -> int | None:
    if message.sticker:
        return message.sticker.file_size
    if message.document:
        return message.document.file_size
    if message.video:
        return message.video.file_size
    if message.video_note:
        return message.video_note.file_size
    if message.animation:
        return message.animation.file_size
    return None


def _custom_emoji_id(message: Message) -> str | None:
    for entity in message.entities or []:
        entity_type = getattr(entity.type, "value", entity.type)
        if entity_type == "custom_emoji" and entity.custom_emoji_id:
            return entity.custom_emoji_id
    return None


def emoji_command_args(text: str | None) -> str | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    args = parts[1].strip()
    return args or None


def group_grid_text(command_message: Message, source_message: Message) -> str | None:
    return emoji_command_args(command_message.text) or source_message.caption


async def _custom_emoji_file(message: Message) -> tuple[str, str, str, bool] | None:
    custom_emoji_id = _custom_emoji_id(message)
    if custom_emoji_id is None:
        return None

    stickers = await message.bot.get_custom_emoji_stickers([custom_emoji_id])
    if not stickers:
        raise MediaError("Не нашёл этот premium emoji в Telegram.")

    sticker = stickers[0]
    needs_repainting = bool(getattr(sticker, "needs_repainting", False))
    if sticker.is_animated and not sticker.is_video:
        return sticker.file_id, "tgs", ".tgs", needs_repainting
    if sticker.is_video:
        return sticker.file_id, "video", ".webm", needs_repainting
    return sticker.file_id, "image", ".webp", needs_repainting
