"""Активация перков / черт / артефактов: /perk + инлайн-кнопки.

Активные действия (раз за бой):
  • bite     — «Кровавый хищник»: укус бонусным действием
  • artifact — «Запечатанный Артефакт (Атака)»: урон с отдачей HP
  • shadow   — «Владыка теней»: реакция, урон вдвое (информация)
  • status   — сводка активных пассивок

D-02: все callback-данные содержат ":uid" владельца панели; чужие нажатия
в группах отклоняются алертом.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
import h_perks
import rules
from ui import esc

router = Router()

_ACTIONS = ("bite", "artifact", "shadow", "status")


def _actions_kb(c: dict) -> InlineKeyboardMarkup:
    mech = h_perks.perk_of(c)
    fmech = h_perks.feat_mech(c)
    uses = c.get("lair_uses") or {}
    uid = c["user_id"]
    rows: list[list[InlineKeyboardButton]] = []

    if mech.get("bite"):
        left = int(uses.get("bite", mech.get("per_combat", 1)))
        rows.append([InlineKeyboardButton(
            text=f"🦇 Укус ({left})" if left > 0 else "🦇 Укус — использован",
            callback_data=f"pk:bite:{uid}" if left > 0 else "pk:none")])
    if fmech.get("artifact_dice"):
        left = int(uses.get("artifact", fmech.get("per_combat", 1)))
        rows.append([InlineKeyboardButton(
            text=f"📕 Артефакт: атака ({left})" if left > 0 else "📕 Артефакт — использован",
            callback_data=f"pk:artifact:{uid}" if left > 0 else "pk:none")])
    if mech.get("react_half_damage_per_combat"):
        left = int(uses.get("shadow", mech["react_half_damage_per_combat"]))
        rows.append([InlineKeyboardButton(
            text=f"🛡 Реакция: урон вдвое ({left})" if left > 0 else "🛡 Реакция — использована",
            callback_data=f"pk:shadow:{uid}" if left > 0 else "pk:none")])
    if mech.get("save_reroll_add"):
        left = int(uses.get("diplomacy", mech.get("per_combat", 2)))
        rows.append([InlineKeyboardButton(
            text=f"📜 Дипломатия ±1d6 ({left})" if left > 0 else "📜 Дипломатия — использована",
            callback_data=f"pk:diplomacy:{uid}" if left > 0 else "pk:none")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"pk:status:{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _owner_guard(cb: CallbackQuery) -> int | None:
    """D-02: проверка владельца панели. Возвращает uid или None (ответ уже дан)."""
    parts = cb.data.split(":")
    uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else cb.from_user.id
    if cb.from_user.id != uid:
        return None
    return uid


async def _perk_panel(message: Message, c: dict) -> None:
    status = "\n".join("• " + line for line in h_perks.build_status_lines(c))
    extra = h_perks.ac_bonus(c)
    ac = c.get("ac", 10) + extra
    text = (
        f"⚡ <b>Активные способности — {esc(c['name'])}</b>\n"
        f"Защита: <b>{ac}</b>"
        + (f" (вкл. + {extra} от черт)" if extra else "")
        + ("\n\n" + status if status else "\n\nПассивок нет.")
    )
    await message.reply(text, reply_markup=_actions_kb(c))


async def _perk_panel_edit(cb: CallbackQuery, c: dict) -> None:
    status = "\n".join("• " + line for line in h_perks.build_status_lines(c))
    extra = h_perks.ac_bonus(c)
    ac = int(c.get("ac", 10)) + extra
    try:
        await cb.message.edit_text(
            f"⚡ <b>Активные способности — {esc(c['name'])}</b>\n"
            f"Защита: <b>{ac}</b>"
            + (f" (вкл. + {extra} от черт)" if extra else "")
            + ("\n\n" + status if status else "\n\nПассивок нет."),
            reply_markup=_actions_kb(c))
    except Exception:
        pass


@router.message(Command("perk"))
async def cmd_perk(message: Message) -> None:
    c = await db.get_character(message.from_user.id)
    if not c:
        await message.reply("Сначала создай анкету: /build")
        return
    # E-02: боевые перки/артефакты — только после одобрения анкеты
    if c.get("status") != "approved":
        await message.reply("Боевые способности и артефакты доступны только после одобрения анкеты администратором.")
        return
    await _perk_panel(message, c)


@router.callback_query(F.data.startswith("pk:status"))
async def cb_status(cb: CallbackQuery) -> None:
    uid = _owner_guard(cb)
    if uid is None:
        await cb.answer("Это панель другого персонажа!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c:
        await cb.answer("Нет анкеты", show_alert=True)
        return
    await _perk_panel_edit(cb, c)
    await cb.answer()


@router.callback_query(F.data == "pk:none")
async def cb_none(cb: CallbackQuery) -> None:
    await cb.answer("Уже использовано в этом бою", show_alert=True)


@router.callback_query(F.data.startswith("pk:bite"))
async def cb_bite(cb: CallbackQuery) -> None:
    uid = _owner_guard(cb)
    if uid is None:
        await cb.answer("Это панель другого персонажа!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c:
        await cb.answer("Нет анкеты", show_alert=True)
        return
    # E-02: боевые перки — только после одобрения анкеты
    if c.get("status") != "approved":
        await cb.answer("Боевые способности и артефакты доступны только после одобрения анкеты администратором.", show_alert=True)
        return
    if int(c.get("hp") or 0) <= 0:
        await cb.answer("Персонаж без сознания (0 ОЗ)!", show_alert=True)
        return
    if {"stunned", "paralyzed", "incapacitated"} & set(c.get("conditions") or []):
        await cb.answer("Персонаж обездвижен или оглушён!", show_alert=True)
        return
    stats = c.get("stats") or {}
    str_mod = rules.ability_mod(int(stats.get("str", 10)))
    dex_mod = rules.ability_mod(int(stats.get("dex", 10)))
    prof = rules.effective_prof_bonus(c)
    mod = max(str_mod, dex_mod) + prof
    result = await h_perks.bite_attack(c, mod)
    if result is None:
        await cb.answer("Укус недоступен", show_alert=True)
        return
    c = await db.get_character(uid)
    await _perk_panel_edit(cb, c)
    await cb.answer()
    await cb.message.reply(result)


@router.callback_query(F.data.startswith("pk:artifact"))
async def cb_artifact(cb: CallbackQuery) -> None:
    uid = _owner_guard(cb)
    if uid is None:
        await cb.answer("Это панель другого персонажа!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c:
        await cb.answer("Нет анкеты", show_alert=True)
        return
    # E-02: боевые артефакты — только после одобрения анкеты
    if c.get("status") != "approved":
        await cb.answer("Боевые способности и артефакты доступны только после одобрения анкеты администратором.", show_alert=True)
        return
    if int(c.get("hp") or 0) <= 0:
        await cb.answer("Персонаж без сознания (0 ОЗ)!", show_alert=True)
        return
    if {"stunned", "paralyzed", "incapacitated"} & set(c.get("conditions") or []):
        await cb.answer("Персонаж обездвижен или оглушён!", show_alert=True)
        return
    result = await h_perks.artifact_strike(c)
    if result is None:
        await cb.answer("Артефакт недоступен", show_alert=True)
        return
    c = await db.get_character(uid)
    await _perk_panel_edit(cb, c)
    await cb.answer()
    await cb.message.reply(result)


@router.callback_query(F.data.startswith("pk:diplomacy"))
async def cb_diplomacy(cb: CallbackQuery) -> None:
    uid = _owner_guard(cb)
    if uid is None:
        await cb.answer("Это панель другого персонажа!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c:
        await cb.answer("Нет анкеты", show_alert=True)
        return
    if c.get("status") != "approved":
        await cb.answer("Боевые способности и артефакты доступны только после одобрения анкеты администратором.", show_alert=True)
        return
    if int(c.get("hp") or 0) <= 0:
        await cb.answer("Персонаж без сознания (0 ОЗ)!", show_alert=True)
        return
    if {"stunned", "paralyzed", "incapacitated"} & set(c.get("conditions") or []):
        await cb.answer("Персонаж обездвижен или оглушён!", show_alert=True)
        return
    result = await h_perks.diplomatic_roll(c)
    if result is None:
        await cb.answer("Дипломатия недоступна", show_alert=True)
        return
    c = await db.get_character(uid)
    await _perk_panel_edit(cb, c)
    await cb.answer()
    await cb.message.reply(result)


@router.callback_query(F.data.startswith("pk:shadow"))
async def cb_shadow(cb: CallbackQuery) -> None:
    uid = _owner_guard(cb)
    if uid is None:
        await cb.answer("Это панель другого персонажа!", show_alert=True)
        return
    uses = (await db.get_character(uid) or {}).get("lair_uses") or {}
    left = int(uses.get("shadow", 1))
    if left > 0:
        await cb.answer(f"Реакция готова: когда получишь урон (/hp -N), она сработает сама. Осталось: {left}.")
    else:
        await cb.answer("Реакция уже использована в этом бою", show_alert=True)
