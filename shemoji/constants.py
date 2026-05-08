from __future__ import annotations

from aiogram.types import LinkPreviewOptions

from .media import Grid


PARTY_POPPER_MESSAGE_EFFECT_ID = "5046509860389126442"

RENAME_PACK_CALLBACK = "rename_pack"
DELETE_PACK_CALLBACK = "delete_pack"
PADDING_EXAMPLES_CALLBACK = "padding_examples"
SIZE_EXAMPLES_CALLBACK = "size_examples"
SETTINGS_EXAMPLES_CALLBACK = "settings_examples"
SETTINGS_BUTTON_TEXT = "⚙ Настройки"

VIEW_PACK_PREFIX = "view_"
VIEW_DELETE_PACK_PREFIX = "view_delete:"
SEPARATE_PREVIEW_PREFIX = "separate_preview:"

PACK_READY_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)
VK_SMILIES_SET_NAME = "VKsmilies"
PROGRESS_EDIT_INTERVAL = 1.1
PADDING_EXAMPLE_GRID = Grid(cols=5, rows=5)
GROUP_CHAT_TYPES = {"group", "supergroup"}
