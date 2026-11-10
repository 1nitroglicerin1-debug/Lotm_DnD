"""Добавление NPC (врагов) в активный боевой энкаунтер.

Команды:
  /encounter add_npc <id>  — добавить врага по его отрицательному ID
  /add_npc <id>            — короткий алиас
"""
from __future__ import annotations

import random

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import config
import db
import rules
from ui import esc

router = Router()


async def _can_add_npc(message: Message) -> bool:
    """Добавлять NPC в бой может админ или Мастер чата (dm_registry)."""
    if message.from_user.id in config.ADMIN_IDS:
        return True
    return await db.get_dm(message.chat.id, message.from_user.id) is not None


async def _add_npc_to_encounter(message: Message, npc_id: int) -> None:
    """Общая логика: найти NPC, бросить инициативу, вставить в очередь."""
    if message.chat.type == "private":
        await message.reply("Добавлять врагов в бой можно только в групповом чате.")
        return

    if not await _can_add_npc(message):
        await message.reply("Добавлять врагов в бой может только Мастер чата (/dm) или администратор.")
        return

    if not str(npc_id).lstrip("-").isdigit() or npc_id >= 0:
        await message.reply("ID врага — отрицательное число, например: /add_npc -1001")
        return

    c = await db.get_character(npc_id)
    if c is None or c["user_id"] >= 0:
        await message.reply(f"Враг с ID {npc_id} не найден в бестиарии (создать: /admin).")
        return

    combat = await db.get_combat(message.chat.id)
    old_order = await db.get_initiative(message.chat.id)
    active_uid = None
    if combat and old_order and 0 <= combat["turn"] < len(old_order):
        active_uid = old_order[combat["turn"]]["user_id"]

    dex_mod = rules.ability_mod((c.get("stats") or {}).get("dex", 10))
    roll = random.randint(1, 20)
    init_score = roll + dex_mod

    await db.set_initiative(message.chat.id, c["user_id"], c["name"], init_score, dex_mod)

    if combat and active_uid is not None:
        new_order = await db.get_initiative(message.chat.id)
        for idx, row in enumerate(new_order):
            if row["user_id"] == active_uid:
                if idx != combat["turn"]:
                    await db.set_combat(message.chat.id, round_n=combat["round"], turn=idx)
                break

    await message.reply(
        f"👾 Враг «{esc(c['name'])}» (ID: {c['user_id']}) вступает в бой! "
        f"Очерёдность хода: <b>{init_score}</b> (1d20: {roll} + ЛОВ {dex_mod:+d})."
    )

    # Актуальная очередь хода
    order = await db.get_initiative(message.chat.id)
    lines = ["⚔️ <b>Порядок хода</b>"]
    for i, r in enumerate(order, 1):
        mark = "👾" if r["user_id"] < 0 else "•"
        lines.append(f"{i}. {mark} {esc(r['char_name'] or 'Без имени')} — <b>{r['score']}</b>")
    lines.append("\n/encounter next — следующий ход · /encounter end — завершить")
    await message.answer("\n".join(lines))


@router.message(Command("add_npc"))
async def cmd_add_npc(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if not arg:
        await message.reply("Использование: /add_npc <id> — ID из бестиария (отрицательный).")
        return
    try:
        npc_id = int(arg)
    except ValueError:
        await message.reply("ID должен быть числом, например: /add_npc -1001")
        return
    await _add_npc_to_encounter(message, npc_id)


@router.message(Command("encounter"), F.text.regexp(r"(?i)^/encounter\s+add_npc\b"))
async def cmd_encounter_add_npc(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    npc_arg = arg.split(None, 1)[1].strip() if len(arg.split(None, 1)) > 1 else ""
    if not npc_arg:
        await message.reply("Использование: /encounter add_npc <id>")
        return
    try:
        npc_id = int(npc_arg)
    except ValueError:
        await message.reply("ID должен быть числом, например: /encounter add_npc -1001")
        return
    await _add_npc_to_encounter(message, npc_id)
