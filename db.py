"""База данных (SQLite, стандартная библиотека — ничего ставить не нужно).

Каждый вызов выполняется в отдельном потоке, чтобы не блокировать бота.
Статусы анкеты: draft (заполняется) → pending (на проверке) → approved / rejected.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing

import config

DEFAULT_STATS = {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}
JSON_FIELDS = {"stats", "skills", "saves", "conditions", "spell_slots", "pathway_choices", "feats", "lair_uses", "recorded_skills", "artifacts", "inventory"}
EDITABLE = {
    "username", "name", "race", "char_class", "level", "stats", "skills", "saves",
    "bio", "photo_id", "status", "reject_reason",
    "max_hp", "hp", "temp_hp", "ac", "conditions", "hit_dice_left", "spell_slots",
    "faction", "faction_perk", "pathway", "sequence", "start_sequence", "pathway_choices", "feats",
    "lair_uses",
    "sanity", "digestion", "class_resource", "recorded_skills",
    "artifacts", "inventory",
}

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS characters (
        user_id       INTEGER PRIMARY KEY,           -- Telegram ID игрока
        username      TEXT,
        name          TEXT    NOT NULL DEFAULT '',
        race          TEXT    NOT NULL DEFAULT '',
        char_class    TEXT    NOT NULL DEFAULT '',
        level         INTEGER NOT NULL DEFAULT 1,
        stats         TEXT    NOT NULL DEFAULT '{}', -- {"str": 10, ...}
        skills        TEXT    NOT NULL DEFAULT '{}', -- {"stealth": 1, "perception": 2}  1=владение, 2=компетенция
        saves         TEXT    NOT NULL DEFAULT '[]', -- ["dex", "int"]
        bio           TEXT    NOT NULL DEFAULT '',
        photo_id      TEXT,
        status        TEXT    NOT NULL DEFAULT 'draft',
        reject_reason TEXT,
        created_at    INTEGER NOT NULL,
        updated_at    INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_characters_status ON characters(status);",
    """
    CREATE TABLE IF NOT EXISTS roll_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        chat_id    INTEGER NOT NULL,
        chat_type  TEXT    NOT NULL DEFAULT 'private',
        username   TEXT,
        char_name  TEXT,
        command    TEXT    NOT NULL,     -- roll | check | save | init
        label      TEXT,                 -- навык/характеристика или выражение
        detail     TEXT,                 -- формула, кубы и модификаторы (кратко)
        total      INTEGER,
        natural    INTEGER,
        created_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_roll_chat_time ON roll_log(chat_id, id);",
    "CREATE INDEX IF NOT EXISTS idx_roll_user_time ON roll_log(user_id, id);",
    """
    CREATE TABLE IF NOT EXISTS moderation_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,     -- чья анкета
        admin_id   INTEGER NOT NULL,     -- кто совершил действие
        action     TEXT    NOT NULL,     -- approved | rejected | deleted | edited
        note       TEXT,
        created_at INTEGER NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_mod_user ON moderation_log(user_id);",
    """
    CREATE TABLE IF NOT EXISTS initiative (
        chat_id    INTEGER NOT NULL,       -- чат, где идёт бой
        user_id    INTEGER NOT NULL,       -- участник
        char_name  TEXT,
        score      INTEGER NOT NULL,       -- итог инициативы
        dex_mod    INTEGER NOT NULL,       -- модификатор ЛОВ как тай-брейк (детерминированно)
        created_at INTEGER NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS combat (
        chat_id    INTEGER PRIMARY KEY,    -- один бой на чат
        round      INTEGER NOT NULL DEFAULT 1,
        turn       INTEGER NOT NULL DEFAULT 0,   -- индекс текущего бойца в очереди
        updated_at INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS dm_registry (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (chat_id, user_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_mute (
        chat_id    INTEGER PRIMARY KEY,   -- чат, где включена тишина
        muted_by   INTEGER NOT NULL,      -- кто включил
        reason     TEXT,                  -- необязательная причина
        created_at INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS mute_victims (
        chat_id    INTEGER NOT NULL,      -- чат, где бот ограничил участника
        user_id    INTEGER NOT NULL,      -- кого ограничили
        until_ts   INTEGER NOT NULL,      -- до какого времени действует ограничение
        PRIMARY KEY (chat_id, user_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS card_messages (
        user_id    INTEGER NOT NULL,      -- чья анкета в карточке
        admin_id   INTEGER NOT NULL,      -- у кого эта карточка
        chat_id    INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, admin_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS story_sessions (
        user_id        INTEGER PRIMARY KEY,
        lang           TEXT    NOT NULL DEFAULT 'ru',
        turns_left     INTEGER NOT NULL DEFAULT 15,
        daily_day      INTEGER NOT NULL DEFAULT 0,
        vip_until      INTEGER NOT NULL DEFAULT 0,
        fate_tokens    INTEGER NOT NULL DEFAULT 2,
        fate_seal_used INTEGER NOT NULL DEFAULT 0,
        gm_mode        TEXT    NOT NULL DEFAULT 'canon',
        chapter        INTEGER NOT NULL DEFAULT 1,
        location       TEXT    NOT NULL DEFAULT 'Тинген, улица Железного Креста',
        current_arc    TEXT    NOT NULL DEFAULT '',
        summary        TEXT    NOT NULL DEFAULT '',
        clues_json     TEXT    NOT NULL DEFAULT '[]',
        npcs_json      TEXT    NOT NULL DEFAULT '[]',
        history_json   TEXT    NOT NULL DEFAULT '[]',
        options_json   TEXT    NOT NULL DEFAULT '[]',
        last_fail_json TEXT    NOT NULL DEFAULT '{}',
        enemy_json     TEXT NOT NULL DEFAULT '{}',
        consequences_json TEXT NOT NULL DEFAULT '[]',
        referred_by    INTEGER NOT NULL DEFAULT 0,
        referrals_count INTEGER NOT NULL DEFAULT 0,
        updated_at     INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS server_daily_usage (
        day_int     INTEGER PRIMARY KEY,
        free_turns  INTEGER NOT NULL DEFAULT 0,
        paid_turns  INTEGER NOT NULL DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS payments_log (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id            INTEGER NOT NULL,
        pack_id            TEXT    NOT NULL,
        stars_amount       INTEGER NOT NULL,
        telegram_charge_id TEXT UNIQUE,
        created_at         INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS player_logins (
        user_id    INTEGER PRIMARY KEY,
        last_seen  INTEGER NOT NULL,
        login_days INTEGER NOT NULL DEFAULT 1
    );
    """,
]


def _casefold(s: str | None) -> str:
    """SQLite UDF: регистронезависимое свёртывание (включая кириллицу)."""
    return (s or "").casefold()


def _run(sql: str, params: tuple = (), mode: str = "none"):
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with closing(sqlite3.connect(config.DB_PATH, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.create_function("CASEFOLD", 1, _casefold)
        # WAL включается один раз в init_db (см. ниже); synchronous — на сессию
        conn.execute("PRAGMA synchronous=NORMAL;")
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        rowcount = cur.rowcount
        conn.commit()
    if mode == "one":
        return dict(rows[0]) if rows else None
    if mode == "all":
        return [dict(r) for r in rows]
    if mode == "rowcount":
        return rowcount
    return None


async def _q(sql: str, params: tuple = (), mode: str = "none"):
    return await asyncio.to_thread(_run, sql, params, mode)


def _decode(row: dict | None) -> dict | None:
    if row is None:
        return None
    for f in JSON_FIELDS:
        try:
            row[f] = json.loads(row[f])
        except (TypeError, ValueError):
            row[f] = [] if f in ("saves", "conditions", "feats", "recorded_skills", "artifacts", "inventory") else {}
    stats = dict(DEFAULT_STATS)
    if isinstance(row["stats"], dict):
        stats.update({k: int(v) for k, v in row["stats"].items() if k in DEFAULT_STATS})
    row["stats"] = stats
    if not isinstance(row["skills"], dict):
        row["skills"] = {}
    if not isinstance(row["saves"], list):
        row["saves"] = []
    if not isinstance(row.get("conditions"), list):
        row["conditions"] = []
    if not isinstance(row.get("feats"), list):
        row["feats"] = []
    if not isinstance(row.get("pathway_choices"), dict):
        row["pathway_choices"] = {}
    if not isinstance(row.get("recorded_skills"), list):
        row["recorded_skills"] = []
    if not isinstance(row.get("artifacts"), list):
        row["artifacts"] = []
    if not isinstance(row.get("inventory"), list):
        row["inventory"] = []
    for f in ("sanity", "digestion", "class_resource"):
        try:
            row[f] = int(row.get(f) if row.get(f) is not None else (100 if f == "sanity" else 0))
        except (TypeError, ValueError):
            row[f] = 100 if f == "sanity" else 0
    if not isinstance(row.get("lair_uses"), dict):
        row["lair_uses"] = {}
    slots = row.get("spell_slots")
    if not isinstance(slots, dict):
        slots = {}
    clean_slots: dict[str, dict[str, int]] = {}
    for k, v in slots.items():
        if isinstance(v, dict):
            try:
                clean_slots[str(k)] = {
                    "total": int(v.get("total", 0) or 0),
                    "left": int(v.get("left", 0) or 0),
                }
            except (TypeError, ValueError):
                continue
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            clean_slots[str(k)] = {"total": int(v), "left": int(v)}
        elif isinstance(v, str):
            try:
                n = int(v)
                clean_slots[str(k)] = {"total": n, "left": n}
            except ValueError:
                continue
    row["spell_slots"] = clean_slots
    for f in ("max_hp", "hp", "temp_hp", "ac", "hit_dice_left"):
        try:
            row[f] = int(row.get(f) or 0)
        except (TypeError, ValueError):
            row[f] = 0
    return row


# --------------------------------------------------------------------------

# колонки, добавляемые миграцией к уже существующим таблицам characters
_CHAR_EXTRA_COLUMNS = {
    "max_hp": "INTEGER NOT NULL DEFAULT 0",
    "hp": "INTEGER NOT NULL DEFAULT 0",
    "temp_hp": "INTEGER NOT NULL DEFAULT 0",
    "ac": "INTEGER NOT NULL DEFAULT 10",
    "conditions": "TEXT NOT NULL DEFAULT '[]'",
    "hit_dice_left": "INTEGER NOT NULL DEFAULT 0",
    "spell_slots": "TEXT NOT NULL DEFAULT '{}'",
    # --- система «Повелителя Тайн» ---
    "faction": "TEXT NOT NULL DEFAULT ''",          # устаревшее поле, всегда пусто
    "faction_perk": "TEXT NOT NULL DEFAULT ''",     # ключ выбранной фракционной пассивки
    "pathway": "TEXT NOT NULL DEFAULT ''",          # ключ пути (moon, mother, ...)
    "sequence": "INTEGER NOT NULL DEFAULT 9",       # текущая последовательность 9..4
    "start_sequence": "INTEGER NOT NULL DEFAULT 9", # стартовая последовательность при создании
    "pathway_choices": "TEXT NOT NULL DEFAULT '{}'",# {"9": "a", "8": "b", ...}
    "feats": "TEXT NOT NULL DEFAULT '[]'",          # ["deep_sea", ...]
    "lair_uses": "TEXT NOT NULL DEFAULT '{}'",      # счётчики раз за бой
    "sanity": "INTEGER NOT NULL DEFAULT 100",
    "digestion": "INTEGER NOT NULL DEFAULT 0",
    "class_resource": "INTEGER NOT NULL DEFAULT 0",
    "recorded_skills": "TEXT NOT NULL DEFAULT '[]'",
    "artifacts": "TEXT NOT NULL DEFAULT '[]'",
    "inventory": "TEXT NOT NULL DEFAULT '[]'",
}


async def init_db() -> None:
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    # WAL и synchronous включаются ОДИН раз на уровне БД (persistентно),
    # а не на каждый SQL-запрос (защита от «database is locked» сохраняется).
    def _pragma():
        with closing(sqlite3.connect(config.DB_PATH, timeout=10)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
    await asyncio.to_thread(_pragma)
    for stmt in SCHEMA:
        await _q(stmt)
    await _migrate_columns()
    await _migrate_story_columns()


async def _migrate_columns() -> None:
    """Добавляет недостающие колонки в characters, не трогая существующие данные."""
    cols = await _q("PRAGMA table_info(characters)", mode="all")
    existing = {c["name"] for c in cols}
    for name, decl in _CHAR_EXTRA_COLUMNS.items():
        if name not in existing:
            await _q(f"ALTER TABLE characters ADD COLUMN {name} {decl}")


_STORY_EXTRA_COLUMNS = {
    "daily_day": "INTEGER NOT NULL DEFAULT 0",
    "fate_tokens": "INTEGER NOT NULL DEFAULT 2",
    "fate_seal_used": "INTEGER NOT NULL DEFAULT 0",
    "gm_mode": "TEXT NOT NULL DEFAULT 'canon'",
    "current_arc": "TEXT NOT NULL DEFAULT ''",
    "summary": "TEXT NOT NULL DEFAULT ''",
    "clues_json": "TEXT NOT NULL DEFAULT '[]'",
    "npcs_json": "TEXT NOT NULL DEFAULT '[]'",
    "last_fail_json": "TEXT NOT NULL DEFAULT '{}'",
    "consequences_json": "TEXT NOT NULL DEFAULT '[]'",
    "referred_by": "INTEGER NOT NULL DEFAULT 0",
    "referrals_count": "INTEGER NOT NULL DEFAULT 0",
    "notified_low_turns": "INTEGER NOT NULL DEFAULT 0",
    # --- экономика v2: тариф VIP, «Дело дня», отложенная награда за реферала ---
    "vip_plan": "TEXT NOT NULL DEFAULT ''",             # '' | vip | plus
    "created_at": "INTEGER NOT NULL DEFAULT 0",         # когда игрок впервые открыл сюжет
    "referral_pending": "INTEGER NOT NULL DEFAULT 0",   # 1 = реферал зафиксирован, награда ждёт 1-го хода новичка
    "referral_rewarded": "INTEGER NOT NULL DEFAULT 0",  # 1 = награда пригласившему уже начислена
    "last_daily_case": "INTEGER NOT NULL DEFAULT 0",    # день (YYYYMMDD), когда взято «Дело дня»
    "pending_artifact": "INTEGER NOT NULL DEFAULT 0",   # 1 = оплаченный артефакт ждёт выбора в хранилище
    "ref_day": "INTEGER NOT NULL DEFAULT 0",            # день суточного капа реферальных наград
    "ref_bonus": "INTEGER NOT NULL DEFAULT 0",          # сколько реферальных наград выдано в этот день
}


async def _migrate_story_columns() -> None:
    """Добавляет новые колонки памяти в уже созданную таблицу story_sessions."""
    cols = await _q("PRAGMA table_info(story_sessions)", mode="all") or []
    existing = {c["name"] for c in cols}
    for name, decl in _STORY_EXTRA_COLUMNS.items():
        if name not in existing:
            await _q(f"ALTER TABLE story_sessions ADD COLUMN {name} {decl}")


async def get_character(user_id: int) -> dict | None:
    return _decode(await _q("SELECT * FROM characters WHERE user_id=?", (user_id,), "one"))


async def create_draft(user_id: int, username: str | None) -> None:
    """Создаёт пустой черновик (старая анкета этого игрока, если была, заменяется)."""
    now = int(time.time())
    await _q(
        "INSERT OR REPLACE INTO characters (user_id, username, stats, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'draft', ?, ?)",
        (user_id, username, json.dumps(DEFAULT_STATS), now, now),
    )


async def update_character(user_id: int, **fields) -> None:
    cols, vals = [], []
    for key, value in fields.items():
        if key not in EDITABLE:
            raise ValueError(f"Поле {key} менять нельзя")
        cols.append(f"{key}=?")
        if key in JSON_FIELDS:
            # B-02: не сериализуем повторно, если пришла уже готовая JSON-строка
            vals.append(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        else:
            vals.append(value)
    cols.append("updated_at=?")
    vals.append(int(time.time()))
    await _q(f"UPDATE characters SET {', '.join(cols)} WHERE user_id=?", (*vals, user_id))


async def delete_character(user_id: int) -> None:
    await _q("DELETE FROM characters WHERE user_id=?", (user_id,))


async def counts() -> dict[str, int]:
    """Счётчики по статусам ТОЛЬКО обычных игроков (NPC с user_id < 0 исключены)."""
    rows = await _q(
        "SELECT status, COUNT(*) AS n FROM characters "
        "WHERE status != 'draft' AND user_id > 0 GROUP BY status",
        mode="all",
    )
    return {r["status"]: r["n"] for r in rows}


async def count(status: str | None) -> int:
    """Количество обычных игроков (NPC с user_id < 0 не считаются)."""
    if status is None:
        row = await _q(
            "SELECT COUNT(*) AS n FROM characters WHERE status != 'draft' AND user_id > 0",
            mode="one",
        )
    else:
        row = await _q(
            "SELECT COUNT(*) AS n FROM characters WHERE status = ? AND user_id > 0",
            (status,), "one",
        )
    return row["n"]


async def list_characters(status: str | None, limit: int, offset: int) -> list[dict]:
    """Список анкет обычных игроков. NPC (user_id < 0) сюда не попадают."""
    if status is None:
        where, params = ("status != 'draft' AND user_id > 0", ())
    else:
        where, params = ("status = ? AND user_id > 0", (status,))
    rows = await _q(
        f"SELECT * FROM characters WHERE {where} ORDER BY name LIMIT ? OFFSET ?",
        (*params, limit, offset),
        "all",
    )
    return [_decode(r) for r in rows]


async def count_npcs() -> int:
    """Сколько всего NPC (user_id < 0)."""
    row = await _q(
        "SELECT COUNT(*) AS n FROM characters WHERE user_id < 0", mode="one",
    )
    return row["n"]


async def list_npcs(limit: int = 50, offset: int = 0) -> list[dict]:
    """Список NPC (user_id < 0) — только для бестиария в админке."""
    rows = await _q(
        "SELECT * FROM characters WHERE user_id < 0 ORDER BY name LIMIT ? OFFSET ?",
        (limit, offset),
        "all",
    )
    return [_decode(r) for r in rows]


def _casefold(s: str) -> str:
    """SQLite UDF: регистронезависимое свёртывание (включая кириллицу)."""
    return (s or "").casefold()


async def search(query: str, limit: int = 20) -> list[dict]:
    """Поиск по имени, расе, классу, @username или ID среди игроков (без черновиков и NPC)."""
    q = query.strip().lstrip("@").casefold()
    like = f"%{q}%"
    rows = await _q(
        "SELECT * FROM characters "
        "WHERE user_id > 0 AND status != 'draft' AND ("
        "CASEFOLD(name) LIKE ? OR CASEFOLD(race) LIKE ? OR CASEFOLD(char_class) LIKE ? "
        "OR CASEFOLD(COALESCE(username, '')) LIKE ? OR CAST(user_id AS TEXT) LIKE ?"
        ") ORDER BY name LIMIT ?",
        (like, like, like, like, like, limit),
        "all",
    )
    return [_decode(r) for r in rows]


# --------------------------------------------------------------------------
# Журнал бросков кубов
# --------------------------------------------------------------------------

async def log_roll(**fields) -> None:
    now = int(time.time())
    await _q(
        "INSERT INTO roll_log (user_id, chat_id, chat_type, username, char_name, command, "
        "label, detail, total, natural, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fields.get("user_id"), fields.get("chat_id"), fields.get("chat_type", "private"),
            fields.get("username"), fields.get("char_name"), fields.get("command"),
            fields.get("label"), fields.get("detail"), fields.get("total"),
            fields.get("natural"), now,
        ),
    )


async def last_rolls(chat_id: int | None, user_id: int | None, limit: int = 10) -> list[dict]:
    """Последние броски: либо в чате, либо игрока (что-то одно не None)."""
    if user_id is not None:
        rows = await _q(
            "SELECT * FROM roll_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit), "all",
        )
    else:
        rows = await _q(
            "SELECT * FROM roll_log WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit), "all",
        )
    return list(reversed(rows))   # от более раннего к свежему, как в истории


# --------------------------------------------------------------------------
# Журнал действий модерации
# --------------------------------------------------------------------------

async def log_moderation(user_id: int, admin_id: int, action: str, note: str | None = None) -> None:
    await _q(
        "INSERT INTO moderation_log (user_id, admin_id, action, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, admin_id, action, note, int(time.time())),
    )


def _decide_character_sync(user_id: int, admin_id: int, new_status: str, reason: str | None) -> dict:
    """Единоличное и атомарное изменение статуса анкеты администратором (approved / rejected)."""
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    now = int(time.time())
    with closing(sqlite3.connect(config.DB_PATH, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT status, name FROM characters WHERE user_id=?", (user_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return {"ok": False, "error": "missing"}
            if row["status"] in ("approved", "rejected"):
                conn.rollback()
                return {"ok": False, "error": "decided", "status": row["status"], "name": row["name"]}

            conn.execute(
                "UPDATE characters SET status=?, reject_reason=?, updated_at=? WHERE user_id=?",
                (new_status, reason if new_status == "rejected" else None, now, user_id),
            )
            conn.commit()
            return {"ok": True, "status": new_status, "name": row["name"]}
        except Exception:
            conn.rollback()
            raise


async def decide_character(user_id: int, admin_id: int, new_status: str, reason: str | None = None) -> dict:
    """Одобряет или отклоняет анкету одним нажатием администратора."""
    if new_status not in ("approved", "rejected"):
        return {"ok": False, "error": "bad_choice"}
    res = await asyncio.to_thread(_decide_character_sync, user_id, admin_id, new_status, reason)
    if res.get("ok"):
        await log_moderation(user_id, admin_id, new_status, reason)
    return res


async def save_card_message(user_id: int, admin_id: int, chat_id: int, message_id: int) -> None:
    """Запоминает, где у админа лежит карточка анкеты — чтобы её можно было обновить."""
    await _q(
        "INSERT INTO card_messages (user_id, admin_id, chat_id, message_id, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, admin_id) DO UPDATE SET chat_id=excluded.chat_id, "
        "message_id=excluded.message_id, created_at=excluded.created_at",
        (user_id, admin_id, chat_id, message_id, int(time.time())),
    )


async def get_card_messages(user_id: int) -> list[dict]:
    """Все известные карточки этой анкеты у админов."""
    rows = await _q(
        "SELECT admin_id, chat_id, message_id FROM card_messages WHERE user_id=?", (user_id,), "all"
    ) or []
    return rows


async def drop_card_messages(user_id: int) -> None:
    """Забыть карточки (анкета удалена или отправлена заново — старая карточка неактуальна)."""
    await _q("DELETE FROM card_messages WHERE user_id=?", (user_id,))


async def delete_character_if(user_id: int, admin_id: int) -> bool:
    """Удаляет анкету; возвращает True, если она существовала и была удалена."""
    n = await _q("DELETE FROM characters WHERE user_id=?", (user_id,), "rowcount")
    if n:
        await log_moderation(user_id, admin_id, "deleted")
        await drop_card_messages(user_id)
    return bool(n)

# --------------------------------------------------------------------------
# Инициативный трекер
# --------------------------------------------------------------------------

async def set_initiative(chat_id: int, user_id: int, char_name: str, score: int, dex_mod: int) -> None:
    await _q(
        "INSERT INTO initiative (chat_id, user_id, char_name, score, dex_mod, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(chat_id, user_id) DO UPDATE SET char_name=excluded.char_name, "
        "score=excluded.score, dex_mod=excluded.dex_mod",
        (chat_id, user_id, char_name, score, dex_mod, int(time.time())),
    )


async def get_initiative(chat_id: int) -> list[dict]:
    """Очередь инициативы: выше score, при равенстве — выше ловкость."""
    rows = await _q(
        "SELECT user_id, char_name, score, dex_mod FROM initiative WHERE chat_id=? "
        "ORDER BY score DESC, dex_mod DESC, user_id ASC",
        (chat_id,), "all",
    )
    return rows


async def clear_initiative(chat_id: int) -> None:
    await _q("DELETE FROM initiative WHERE chat_id=?", (chat_id,))


async def get_character_by_username(username: str) -> dict | None:
    """Найти одобренную анкету по @username (без @)."""
    clean = username.strip().lstrip("@")
    row = await _q(
        "SELECT * FROM characters WHERE LOWER(username)=LOWER(?) AND status='approved' "
        "ORDER BY updated_at DESC LIMIT 1",
        (clean,), "one",
    )
    return _decode(row)


# --------------------------------------------------------------------------
# Состояние боя (раунд / текущий ход) — по одному бою на чат
# --------------------------------------------------------------------------

async def get_combat(chat_id: int) -> dict | None:
    return await _q("SELECT * FROM combat WHERE chat_id=?", (chat_id,), "one")


async def set_combat(chat_id: int, round_n: int, turn: int) -> None:
    await _q(
        "INSERT INTO combat (chat_id, round, turn, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET round=excluded.round, turn=excluded.turn, "
        "updated_at=excluded.updated_at",
        (chat_id, round_n, turn, int(time.time())),
    )


async def clear_combat(chat_id: int) -> None:
    await _q("DELETE FROM combat WHERE chat_id=?", (chat_id,))


# --------------------------------------------------------------------------
# Реестр Мастеров (DM) по чатам
# --------------------------------------------------------------------------

async def add_dm(chat_id: int, user_id: int) -> None:
    await _q("INSERT OR IGNORE INTO dm_registry (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id))


async def remove_dm(chat_id: int, user_id: int) -> None:
    await _q("DELETE FROM dm_registry WHERE chat_id=? AND user_id=?", (chat_id, user_id))


async def get_dm(chat_id: int, user_id: int) -> dict | None:
    return await _q(
        "SELECT user_id FROM dm_registry WHERE chat_id=? AND user_id=?",
        (chat_id, user_id), "one",
    )


async def get_dms(chat_id: int) -> list[int]:
    rows = await _q("SELECT user_id FROM dm_registry WHERE chat_id=?", (chat_id,), "all")
    return [r["user_id"] for r in rows]


async def is_in_combat(user_id: int) -> bool:
    """True, если персонаж сейчас находится в очереди активного боя в любом чате."""
    row = await _q(
        "SELECT 1 FROM initiative i "
        "JOIN combat c ON i.chat_id = c.chat_id "
        "WHERE i.user_id = ? LIMIT 1",
        (user_id,),
        "one",
    )
    return row is not None


# --------------------------------------------------------------------------
# Тишина в чате (мут всех, кроме админов и Мастера)
# --------------------------------------------------------------------------

async def set_mute(chat_id: int, by_id: int, reason: str | None = None) -> None:
    """Включает тишину в чате (или обновляет причину, если уже включена)."""
    await _q(
        "INSERT INTO chat_mute (chat_id, muted_by, reason, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET muted_by=excluded.muted_by, "
        "reason=excluded.reason, created_at=excluded.created_at",
        (chat_id, by_id, reason, int(time.time())),
    )


async def clear_mute(chat_id: int) -> None:
    """Снимает тишину в чате."""
    await _q("DELETE FROM chat_mute WHERE chat_id=?", (chat_id,))


async def get_mute(chat_id: int) -> dict | None:
    """Запись о тишине в чате или None, если чат не в муте."""
    return await _q("SELECT * FROM chat_mute WHERE chat_id=?", (chat_id,), "one")


async def is_muted_chat(chat_id: int) -> bool:
    """True, если в чате включён режим тишины."""
    return await get_mute(chat_id) is not None

async def add_victim(chat_id: int, user_id: int, until_ts: int) -> None:
    """Запоминает участника, которому бот выдал ограничение (чтобы потом снять)."""
    await _q(
        "INSERT INTO mute_victims (chat_id, user_id, until_ts) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id, user_id) DO UPDATE SET until_ts=excluded.until_ts",
        (chat_id, user_id, until_ts),
    )


async def list_victims(chat_id: int) -> list[dict]:
    rows = await _q("SELECT * FROM mute_victims WHERE chat_id=?", (chat_id,), "all")
    return rows or []


async def drop_victim(chat_id: int, user_id: int) -> None:
    await _q("DELETE FROM mute_victims WHERE chat_id=? AND user_id=?", (chat_id, user_id))


async def clear_victims(chat_id: int) -> list[int]:
    """Удаляет всех жертв чата и возвращает их ID (для снятия ограничений)."""
    ids = [r["user_id"] for r in await list_victims(chat_id)]
    await _q("DELETE FROM mute_victims WHERE chat_id=?", (chat_id,))
    return ids


# Синонимы для h_mute.py (единый смысл: «жертвы ограничений»)
async def add_muted_victim(chat_id: int, user_id: int, until_ts: int) -> None:
    await add_victim(chat_id, user_id, until_ts)


async def take_muted_victims(chat_id: int) -> list[int]:
    """Возвращает ID ограниченных участников чата и очищает запись о них."""
    return await clear_victims(chat_id)


# --------------------------------------------------------------------------
# Сюжетные сессии ИИ-Мастера, Умная Память и Магазин (h_story / h_shop)
# --------------------------------------------------------------------------

def _today_int() -> int:
    return int(time.strftime("%Y%m%d", time.gmtime()))


def _decode_session(row: dict | None) -> dict | None:
    if row is None:
        return None
    for k, default in (
        ("clues_json", []),
        ("npcs_json", []),
        ("history_json", []),
        ("options_json", []),
        ("last_fail_json", {}),
        ("enemy_json", {}),
        ("consequences_json", []),
    ):
        try:
            val = json.loads(row.get(k) or ("{}" if isinstance(default, dict) else "[]"))
            row[k[:-5]] = val if isinstance(val, type(default)) else default
        except (TypeError, ValueError):
            row[k[:-5]] = dict(default) if isinstance(default, dict) else list(default)
    row["is_vip"] = int(row.get("vip_until") or 0) > int(time.time())
    row["fate_seal_used"] = bool(row.get("fate_seal_used", 0))
    if not row.get("gm_mode"):
        row["gm_mode"] = "canon"
    return row


async def get_story_session(user_id: int) -> dict:
    row = await _q("SELECT * FROM story_sessions WHERE user_id=?", (user_id,), "one")
    today = _today_int()
    if row is None:
        now = int(time.time())
        await _q(
            "INSERT INTO story_sessions (user_id, lang, turns_left, daily_day, vip_until, fate_tokens, gm_mode, chapter, location, created_at, updated_at) "
            "VALUES (?, 'ru', ?, ?, 0, 2, 'canon', 1, 'Тинген, улица Железного Креста', ?, ?)",
            (user_id, config.WELCOME_TURNS, today, now, now),
        )
        row = await _q("SELECT * FROM story_sessions WHERE user_id=?", (user_id,), "one")
    s = _decode_session(row)
    # Ежедневное пополнение: баланс доводится до суточного уровня тарифа,
    # но НЕ копится выше него (защита от «накопил 300 ходов и играешь месяц»).
    if int(s.get("daily_day") or 0) < today:
        target = _daily_target(s)
        balance = int(s.get("turns_left") or 0)
        # Купленные отдельно ходы (баланс выше суточного уровня) не сгорают.
        new_turns = balance if balance > target else target
        await _q(
            "UPDATE story_sessions SET turns_left=?, daily_day=?, updated_at=? WHERE user_id=?",
            (new_turns, today, int(time.time()), user_id),
        )
        s["turns_left"] = new_turns
        s["daily_day"] = today
    return s


def _daily_target(session: dict) -> int:
    """Суточный уровень ходов по тарифу игрока (VIP+ > VIP > базовый)."""
    if session.get("is_vip"):
        plan = str(session.get("vip_plan") or "")
        if plan == "plus":
            return max(1, int(config.VIP_PLUS_DAILY_TURNS))
        return max(1, int(config.VIP_DAILY_TURNS))
    return max(0, int(config.FREE_DAILY_TURNS))


async def try_consume_turn(user_id: int, is_dev: bool = False) -> tuple[bool, str]:
    """Атомарно проверяет и списывает 1 ход ИИ-Мастера с защитой глобального бесплатного бюджета.

    Раньше здесь был классический TOCTOU: SELECT -> проверка -> UPDATE. Два параллельных
    запроса от одного игрока (веб-клиент шлёт /roll и /turn почти одновременно) могли
    пройти проверку оба и получить больше ходов, чем есть на счёте. Теперь списание —
    один атомарный UPDATE ... WHERE turns_left > 0 с проверкой rowcount.
    """
    if is_dev:
        return True, "dev"
    s = await get_story_session(user_id)

    today = _today_int()
    balance = int(s.get("turns_left") or 0)
    if balance <= 0:
        return False, "no_turns"

    # Платный ли это ход (VIP или баланс выше бесплатной суточной нормы)
    is_paid_user = bool(s.get("is_vip")) or balance > int(config.FREE_DAILY_TURNS)

    if not is_paid_user:
        # Атомарный инкремент с условием: бюджет не будет превышен даже при гонке.
        used = await _q(
            "INSERT INTO server_daily_usage (day_int, free_turns, paid_turns) VALUES (?, 1, 0) "
            "ON CONFLICT(day_int) DO UPDATE SET free_turns = free_turns + 1 "
            "WHERE server_daily_usage.free_turns < ?",
            (today, int(config.GLOBAL_FREE_DAILY_CAP)),
            "rowcount",
        )
        if not used:
            return False, "global_cap_reached"

    # Атомарное списание ровно одного хода (не уводит баланс в минус)
    taken = await _q(
        "UPDATE story_sessions SET turns_left = turns_left - 1, updated_at = ? "
        "WHERE user_id = ? AND turns_left > 0",
        (int(time.time()), user_id),
        "rowcount",
    )
    if not taken:
        # Ход не списан (гонка/обнуление) — возвращаем инкремент бесплатного бюджета,
        # чтобы не «съедать» серверную квоту впустую.
        if not is_paid_user:
            await _q(
                "UPDATE server_daily_usage SET free_turns = free_turns - 1 "
                "WHERE day_int = ? AND free_turns > 0",
                (today,),
            )
        return False, "no_turns"

    if is_paid_user:
        await _q(
            "INSERT INTO server_daily_usage (day_int, free_turns, paid_turns) VALUES (?, 0, 1) "
            "ON CONFLICT(day_int) DO UPDATE SET paid_turns = paid_turns + 1",
            (today,),
        )
    return True, "ok"


async def update_story_session(user_id: int, **fields) -> dict:
    allowed = {
        "lang", "turns_left", "daily_day", "vip_until", "fate_tokens", "fate_seal_used", "gm_mode",
        "clues", "npcs", "history", "options", "last_fail", "enemy", "consequences",
        "vip_plan", "referral_rewarded", "referral_pending", "last_daily_case", "pending_artifact",
    }
    json_keys = {"clues", "npcs", "history", "options", "last_fail", "enemy", "consequences"}
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in json_keys:
            cols.append(f"{k}_json=?")
            vals.append(json.dumps(v, ensure_ascii=False))
        else:
            cols.append(f"{k}=?")
            vals.append(v)
    cols.append("updated_at=?")
    vals.append(int(time.time()))
    await _q(f"UPDATE story_sessions SET {', '.join(cols)} WHERE user_id=?", (*vals, user_id))
    return await get_story_session(user_id)


async def add_turns(user_id: int, amount: int) -> dict:
    await get_story_session(user_id)
    await _q(
        "UPDATE story_sessions SET turns_left = MAX(0, turns_left + ?), updated_at = ? WHERE user_id = ?",
        (int(amount), int(time.time()), user_id),
    )
    return await get_story_session(user_id)


async def add_fate_tokens(user_id: int, amount: int) -> dict:
    await get_story_session(user_id)
    # ВРЕМЕННО: печати не тратятся при UNLIMITED_FATE (но и не начисляются сверх)
    if getattr(config, "UNLIMITED_FATE", False) and amount < 0:
        return await get_story_session(user_id)
    await _q(
        "UPDATE story_sessions SET fate_tokens = MAX(0, fate_tokens + ?), updated_at = ? WHERE user_id = ?",
        (int(amount), int(time.time()), user_id),
    )
    return await get_story_session(user_id)


async def activate_vip(user_id: int, days: int = 30, pool: int | None = None, plan: str = "vip") -> dict:
    """Активирует/продлевает VIP и начисляет стартовый пул ходов тарифа.

    Дальше баланс пополняется автоматически каждые сутки до суточного уровня тарифа
    (см. get_story_session -> _daily_target), а не единым куском на весь месяц:
    иначе игрок за один вечер сжигает весь месячный пул и уходит.
    """
    s = await get_story_session(user_id)
    now = int(time.time())
    base = max(now, int(s.get("vip_until") or 0))
    new_until = base + int(days) * 86400
    if pool is None:
        pool = config.VIP_PLUS_START_POOL if plan == "plus" else config.VIP_START_POOL
    # Стартовый пул не копится: доводим баланс до уровня пула, если он ниже.
    balance = int(s.get("turns_left") or 0)
    new_turns = balance if balance > int(pool) else max(balance, int(pool))
    return await update_story_session(
        user_id, vip_until=new_until, vip_plan=plan, turns_left=new_turns
    )


async def log_payment(user_id: int, pack_id: str, stars_amount: int, charge_id: str) -> bool:
    """Идемпотентная запись платежа. True = платёж новый (зачисляем бонусы), False = дубликат (игнорируем)."""
    if charge_id:
        row = await _q(
            "SELECT id FROM payments_log WHERE telegram_charge_id=?",
            (charge_id,),
            "one",
        )
        if row:
            return False
    await _q(
        "INSERT INTO payments_log (user_id, pack_id, stars_amount, telegram_charge_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, pack_id, int(stars_amount), charge_id, int(time.time())),
    )
    return True


async def apply_referral(new_user_id: int, ref_id: int) -> bool:
    """Фиксирует приглашение и выдаёт новичку его бонус.

    Ключевая защита от накрутки: награда ПРИГЛАСИВШЕМУ не начисляется сразу, а
    ставится в отложенную очередь (referral_pending=1) и выплачивается только
    после первого реального хода новичка — см. claim_referral_reward().
    Фейковый «друг», который запустил бота и закрыл, ничего не приносит.
    """
    if new_user_id == ref_id or ref_id <= 0:
        return False
    s_new = await get_story_session(new_user_id)
    if int(s_new.get("referred_by") or 0) != 0:
        return False
    # Приглашённый должен быть новичком: сессия создана только что
    created = int(s_new.get("created_at") or s_new.get("updated_at") or 0)
    if created and (int(time.time()) - created) > config.REFERRAL_MAX_AGE_DAYS * 86400:
        return False
    # Пригласивший обязан существовать
    inviter = await get_character(ref_id)
    inv_sess = await _q("SELECT user_id FROM story_sessions WHERE user_id=?", (ref_id,), "one")
    if not inviter and not inv_sess:
        return False
    # Атомарная фиксация: только если referred_by всё ещё пуст (защита от гонки)
    claimed = await _q(
        "UPDATE story_sessions SET referred_by=?, referral_pending=1, "
        "turns_left=turns_left+?, updated_at=? WHERE user_id=? AND referred_by=0",
        (ref_id, int(config.REFERRAL_NEWCOMER_TURNS), int(time.time()), new_user_id),
        "rowcount",
    )
    return bool(claimed)


async def refund_turn(user_id: int, is_dev: bool = False) -> None:
    """Возвращает 1 ход, если генерация ИИ сорвалась (игрок не должен платить за сбой).

    Именно инкремент, а не запись сохранённого баланса: иначе покупка ходов,
    совершённая во время 85-секундной генерации, будет затёрта старым значением.
    """
    if is_dev:
        return
    await _q(
        "UPDATE story_sessions SET turns_left = turns_left + 1, updated_at = ? WHERE user_id = ?",
        (int(time.time()), user_id),
    )


async def claim_daily_case(user_id: int) -> tuple[bool, str]:
    """«Дело дня»: один бесплатный ход за календарные сутки (UTC).

    Возвращает (начислено, причина). Атомарный UPDATE по last_daily_case
    исключает двойное получение при двойном клике/параллельных запросах.
    """
    s = await get_story_session(user_id)
    today = _today_int()
    if int(s.get("last_daily_case") or 0) >= today:
        return False, "already_claimed"
    # Ключевое условие «сегодня ещё не брали» переносим в сам UPDATE — гонка невозможна.
    claimed = await _q(
        "UPDATE story_sessions SET turns_left=turns_left+?, last_daily_case=?, updated_at=? "
        "WHERE user_id=? AND last_daily_case < ?",
        (int(config.DAILY_CASE_TURNS), today, int(time.time()), user_id, today),
        "rowcount",
    )
    if not claimed:
        return False, "already_claimed"
    return True, "ok"


async def claim_referral_reward(user_id: int) -> bool:
    """Выплата отложенной награды пригласившему — после ПЕРВОГО хода новичка.

    Атомарный переход referral_pending 1 -> 0 гарантирует, что награда не задвоится
    ни при параллельных запросах, ни при повторном вызове.
    """
    s = await get_story_session(user_id)
    if not int(s.get("referral_pending") or 0) or int(s.get("referral_rewarded") or 0):
        return False
    ref_id = int(s.get("referred_by") or 0)
    if ref_id <= 0:
        return False
    closed = await _q(
        "UPDATE story_sessions SET referral_pending=0, referral_rewarded=1, updated_at=? "
        "WHERE user_id=? AND referral_pending=1 AND referral_rewarded=0",
        (int(time.time()), user_id),
        "rowcount",
    )
    if not closed:
        return False
    # Суточный кап наград пригласившему (защита от массового фарма)
    today = _today_int()
    cap_row = await _q(
        "SELECT ref_day, ref_bonus FROM story_sessions WHERE user_id=?", (ref_id,), "one"
    ) or {}
    already = int(cap_row.get("ref_bonus") or 0) if int(cap_row.get("ref_day") or 0) == today else 0
    if already >= int(config.REFERRAL_DAILY_CAP):
        return False
    await _q(
        "UPDATE story_sessions SET turns_left=turns_left+?, fate_tokens=fate_tokens+?, "
        "referrals_count=referrals_count+1, ref_day=?, ref_bonus=?, updated_at=? WHERE user_id=?",
        (
            int(config.REFERRAL_INVITER_TURNS),
            int(config.REFERRAL_INVITER_FATE),
            today,
            already + 1,
            int(time.time()),
            ref_id,
        ),
    )
    return True


# --------------------------------------------------------------------------
# Хроника подвигов (статистика бросков + посещения для ретеншна)
# --------------------------------------------------------------------------

async def roll_stats(user_id: int) -> dict:
    """Агрегированная статистика бросков игрока: криты, фамблы, лучший бросок."""
    try:
        rows = await _q(
            "SELECT command, total, natural FROM roll_log "
            "WHERE user_id=? AND total IS NOT NULL ORDER BY id DESC LIMIT 500",
            (user_id,),
            "all",
        )
    except Exception:
        return {"total_rolls": 0, "nat20": 0, "nat1": 0, "best": 0}
    checks = [r for r in rows if r.get("command") in ("check", "save", "init")]
    nat20 = sum(1 for r in checks if int(r.get("natural") or 0) == 20)
    nat1 = sum(1 for r in checks if int(r.get("natural") or 0) == 1)
    best = max((int(r.get("total") or 0) for r in rows), default=0)
    return {
        "total_rolls": len(rows),
        "checks": len(checks),
        "nat20": nat20,
        "nat1": nat1,
        "best": best,
    }


async def upsert_login(user_id: int) -> None:
    """Отмечает дату последнего визита игрока (для расчёта ретеншна и напоминаний)."""
    now = int(time.time())
    try:
        await _q(
            "INSERT INTO player_logins (user_id, last_seen, login_days) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen=?, login_days=login_days+1",
            (user_id, now, now),
        )
    except Exception:
        pass


async def get_login(user_id: int) -> dict | None:
    try:
        return await _q("SELECT * FROM player_logins WHERE user_id=?", (user_id,), "one")
    except Exception:
        return None


async def idle_players(days: int = 3, limit: int = 200) -> list[dict]:
    """Игроки с созданным персонажем, не заходившие N дней (для мягких напоминаний)."""
    cutoff = int(time.time()) - days * 86400
    try:
        rows = await _q(
            "SELECT s.user_id, s.turns_left, COALESCE(p.last_seen, 0) AS last_seen "
            "FROM story_sessions s "
            "JOIN characters c ON c.user_id = s.user_id "
            "LEFT JOIN player_logins p ON p.user_id = s.user_id "
            "WHERE (p.last_seen IS NULL OR p.last_seen < ?) AND s.turns_left > 0 "
            "ORDER BY s.updated_at DESC LIMIT ?",
            (cutoff, limit),
            "all",
        )
    except Exception:
        return []
    return [dict(r) for r in rows]
