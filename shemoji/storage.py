from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PackRecord:
    chat_id: int
    message_id: int
    user_id: int
    set_name: str
    url: str
    cols: int
    rows: int
    padding: int
    title: str


@dataclass(frozen=True)
class PendingRename:
    user_id: int
    chat_id: int
    pack_message_id: int
    prompt_message_id: int
    set_name: str


@dataclass(frozen=True)
class PaddingExampleSet:
    user_id: int
    set_name: str


@dataclass(frozen=True)
class SizeExampleSet:
    user_id: int
    set_name: str


@dataclass(frozen=True)
class LastViewMessage:
    user_id: int
    chat_id: int
    message_id: int


class SettingsStore:
    def __init__(self, db_path: Path, default_padding: int, default_long_side: int) -> None:
        self.db_path = db_path
        self.default_padding = default_padding
        self.default_long_side = default_long_side
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                padding INTEGER NOT NULL,
                saxophone INTEGER NOT NULL
            )
            """
        )
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(user_settings)").fetchall()
        }
        if "long_side" not in columns:
            self._conn.execute("ALTER TABLE user_settings ADD COLUMN long_side INTEGER")
            self._conn.execute(
                "UPDATE user_settings SET long_side = ? WHERE long_side IS NULL",
                (self.default_long_side,),
            )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pack_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                set_name TEXT NOT NULL,
                url TEXT NOT NULL,
                cols INTEGER NOT NULL,
                rows INTEGER NOT NULL,
                padding INTEGER NOT NULL,
                title TEXT NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_renames (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                pack_message_id INTEGER NOT NULL,
                prompt_message_id INTEGER NOT NULL,
                set_name TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS padding_example_sets (
                user_id INTEGER PRIMARY KEY,
                set_name TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS size_example_sets (
                user_id INTEGER PRIMARY KEY,
                set_name TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS last_view_messages (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def get_padding(self, user_id: int) -> int:
        row = self._conn.execute(
            "SELECT padding FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return self.default_padding
        return int(row[0])

    def set_padding(self, user_id: int, padding: int) -> None:
        self._conn.execute(
            """
            INSERT INTO user_settings(user_id, padding, long_side, saxophone)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET padding = excluded.padding
            """,
            (user_id, padding, self.get_long_side(user_id), int(self.get_saxophone(user_id))),
        )
        self._conn.commit()

    def get_long_side(self, user_id: int) -> int:
        row = self._conn.execute(
            "SELECT long_side FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return self.default_long_side
        return int(row[0])

    def set_long_side(self, user_id: int, long_side: int) -> None:
        self._conn.execute(
            """
            INSERT INTO user_settings(user_id, padding, long_side, saxophone)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET long_side = excluded.long_side
            """,
            (user_id, self.get_padding(user_id), long_side, int(self.get_saxophone(user_id))),
        )
        self._conn.commit()

    def get_saxophone(self, user_id: int) -> bool:
        row = self._conn.execute(
            "SELECT saxophone FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return False
        return bool(row[0])

    def set_saxophone(self, user_id: int, saxophone: bool) -> None:
        self._conn.execute(
            """
            INSERT INTO user_settings(user_id, padding, long_side, saxophone)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET saxophone = excluded.saxophone
            """,
            (user_id, self.get_padding(user_id), self.get_long_side(user_id), int(saxophone)),
        )
        self._conn.commit()

    def save_pack_message(self, record: PackRecord) -> int:
        self._conn.execute(
            """
            INSERT INTO pack_messages(
                chat_id, message_id, user_id, set_name, url, cols, rows, padding, title
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                user_id = excluded.user_id,
                set_name = excluded.set_name,
                url = excluded.url,
                cols = excluded.cols,
                rows = excluded.rows,
                padding = excluded.padding,
                title = excluded.title
            """,
            (
                record.chat_id,
                record.message_id,
                record.user_id,
                record.set_name,
                record.url,
                record.cols,
                record.rows,
                record.padding,
                record.title,
            ),
        )
        self._conn.commit()
        row_id = self.get_pack_row_id(record.chat_id, record.message_id)
        if row_id is None:
            raise RuntimeError("Saved pack row was not found.")
        return row_id

    def get_pack_row_id(self, chat_id: int, message_id: int) -> int | None:
        row = self._conn.execute(
            """
            SELECT rowid
            FROM pack_messages
            WHERE chat_id = ? AND message_id = ?
            """,
            (chat_id, message_id),
        ).fetchone()
        return int(row[0]) if row else None

    def get_pack_message(self, chat_id: int, message_id: int) -> PackRecord | None:
        row = self._conn.execute(
            """
            SELECT chat_id, message_id, user_id, set_name, url, cols, rows, padding, title
            FROM pack_messages
            WHERE chat_id = ? AND message_id = ?
            """,
            (chat_id, message_id),
        ).fetchone()
        return PackRecord(*row) if row else None

    def get_pack_by_row_id(self, row_id: int) -> PackRecord | None:
        row = self._conn.execute(
            """
            SELECT chat_id, message_id, user_id, set_name, url, cols, rows, padding, title
            FROM pack_messages
            WHERE rowid = ?
            """,
            (row_id,),
        ).fetchone()
        return PackRecord(*row) if row else None

    def list_user_packs(self, user_id: int) -> list[tuple[int, PackRecord]]:
        rows = self._conn.execute(
            """
            SELECT rowid, chat_id, message_id, user_id, set_name, url, cols, rows, padding, title
            FROM pack_messages
            WHERE user_id = ?
            ORDER BY rowid ASC
            """,
            (user_id,),
        ).fetchall()
        return [(int(row[0]), PackRecord(*row[1:])) for row in rows]

    def update_pack_title(self, chat_id: int, message_id: int, title: str) -> None:
        self._conn.execute(
            "UPDATE pack_messages SET title = ? WHERE chat_id = ? AND message_id = ?",
            (title, chat_id, message_id),
        )
        self._conn.commit()

    def delete_pack_message(self, chat_id: int, message_id: int) -> None:
        self._conn.execute(
            "DELETE FROM pack_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        self._conn.commit()

    def set_pending_rename(self, pending: PendingRename) -> None:
        self._conn.execute(
            """
            INSERT INTO pending_renames(
                user_id, chat_id, pack_message_id, prompt_message_id, set_name
            )
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                pack_message_id = excluded.pack_message_id,
                prompt_message_id = excluded.prompt_message_id,
                set_name = excluded.set_name
            """,
            (
                pending.user_id,
                pending.chat_id,
                pending.pack_message_id,
                pending.prompt_message_id,
                pending.set_name,
            ),
        )
        self._conn.commit()

    def get_pending_rename(self, user_id: int) -> PendingRename | None:
        row = self._conn.execute(
            """
            SELECT user_id, chat_id, pack_message_id, prompt_message_id, set_name
            FROM pending_renames
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return PendingRename(*row) if row else None

    def clear_pending_rename(self, user_id: int) -> None:
        self._conn.execute("DELETE FROM pending_renames WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def get_padding_example_set(self, user_id: int) -> PaddingExampleSet | None:
        row = self._conn.execute(
            "SELECT user_id, set_name FROM padding_example_sets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return PaddingExampleSet(*row) if row else None

    def save_padding_example_set(self, example_set: PaddingExampleSet) -> None:
        self._conn.execute(
            """
            INSERT INTO padding_example_sets(user_id, set_name)
            VALUES(?, ?)
            ON CONFLICT(user_id) DO UPDATE SET set_name = excluded.set_name
            """,
            (example_set.user_id, example_set.set_name),
        )
        self._conn.commit()

    def clear_padding_example_set(self, user_id: int) -> None:
        self._conn.execute("DELETE FROM padding_example_sets WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def get_size_example_set(self, user_id: int) -> SizeExampleSet | None:
        row = self._conn.execute(
            "SELECT user_id, set_name FROM size_example_sets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return SizeExampleSet(*row) if row else None

    def save_size_example_set(self, example_set: SizeExampleSet) -> None:
        self._conn.execute(
            """
            INSERT INTO size_example_sets(user_id, set_name)
            VALUES(?, ?)
            ON CONFLICT(user_id) DO UPDATE SET set_name = excluded.set_name
            """,
            (example_set.user_id, example_set.set_name),
        )
        self._conn.commit()

    def clear_size_example_set(self, user_id: int) -> None:
        self._conn.execute("DELETE FROM size_example_sets WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def save_last_view_message(self, view_message: LastViewMessage) -> None:
        self._conn.execute(
            """
            INSERT INTO last_view_messages(user_id, chat_id, message_id)
            VALUES(?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                message_id = excluded.message_id
            """,
            (view_message.user_id, view_message.chat_id, view_message.message_id),
        )
        self._conn.commit()

    def get_last_view_message(self, user_id: int) -> LastViewMessage | None:
        row = self._conn.execute(
            """
            SELECT user_id, chat_id, message_id
            FROM last_view_messages
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return LastViewMessage(*row) if row else None

    def clear_last_view_message(self, user_id: int) -> None:
        self._conn.execute("DELETE FROM last_view_messages WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
