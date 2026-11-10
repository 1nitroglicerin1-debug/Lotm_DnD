"""Персистентное FSM-хранилище на SQLite (стандартная библиотека).

Заменяет MemoryStorage: состояния и данные форм переживают рестарт бота.
Плагинов не требует — наследует BaseStorage из aiogram. Каждая операция
выполняется в отдельном потоке, как и в db.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from typing import Any, Mapping

from aiogram.fsm.storage.base import (
    BaseStorage,
    StateType,
    StorageKey,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm (
    key   TEXT PRIMARY KEY,
    state TEXT,
    data  TEXT NOT NULL DEFAULT '{}'
);
"""


class SqliteStorage(BaseStorage):
    def __init__(self, path: str) -> None:
        self.path = path
        # Инициализация схемы и WAL — один раз при создании хранилища
        with closing(self._connect()) as conn:
            conn.execute(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        return conn

    @staticmethod
    def _key_name(key: StorageKey) -> str:
        # bot_id : chat_id : thread_id : user_id : destiny
        return ":".join(str(x) for x in (
            key.bot_id, key.chat_id, key.thread_id or 0, key.user_id, key.destiny,
        ))

    def _run(self, sql: str, params: tuple = (), mode: str = "none"):
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            conn.commit()
        if mode == "one":
            return dict(rows[0]) if rows else None
        if mode == "val":
            return rows[0][0] if rows and rows[0] else None
        return None

    async def _q(self, sql: str, params: tuple = (), mode: str = "none"):
        return await asyncio.to_thread(self._run, sql, params, mode)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        name = self._key_name(key)
        value = state.state if hasattr(state, "state") else state
        await self._q(
            "INSERT INTO fsm (key, state, data) VALUES (?, ?, '{}') "
            "ON CONFLICT(key) DO UPDATE SET state=excluded.state",
            (name, value),
        )

    async def get_state(self, key: StorageKey) -> str | None:
        row = await self._q("SELECT state FROM fsm WHERE key=?", (self._key_name(key),), "one")
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        name = self._key_name(key)
        payload = json.dumps(dict(data), ensure_ascii=False)
        await self._q(
            "INSERT INTO fsm (key, state, data) VALUES (?, NULL, ?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data",
            (name, payload),
        )

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await self._q("SELECT data FROM fsm WHERE key=?", (self._key_name(key),), "one")
        if not row:
            return {}
        try:
            return json.loads(row["data"])
        except (TypeError, ValueError):
            return {}

    async def close(self) -> None:
        pass
