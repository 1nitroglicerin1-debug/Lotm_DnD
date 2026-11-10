"""Команда /promote — Повышение Последовательности (9 → 4).

Сохраняет фракционные бонусы HP, ветки пути и структуру ячеек духовности.
"""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import db
import h_perks
import lotm_data as lotm
import rules
import ui
import config
from filters_admin import is_admin
from ui import btn, esc

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class Promote(StatesGroup):
    confirm = State()
    choose_path = State()
    choose_feat = State()


PROMOTE_SLOT_COST: dict[int, tuple[int, int]] = {
    8: (1, 2),  # 9 -> 8: 2 ячейки Р1
    7: (1, 3),  # 8 -> 7: 3 ячейки Р1
    6: (2, 2),  # 7 -> 6: 2 ячейки Р2
    5: (3, 2),  # 6 -> 5: 2 ячейки Р3
    4: (4, 2),  # 5 -> 4 (Полубог / Святой): 2 ячейки Р4
    3: (5, 2),  # 4 -> 3 (Святой Тайн): 2 ячейки Р5
    2: (6, 2),  # 3 -> 2 (Ангел): 2 ячейки Р6
    1: (7, 2),  # 2 -> 1 (Архангел / Король Ангелов): 2 ячейки Р7
    0: (8, 1),  # 1 -> 0 (Истинный Бог): 1 ячейка Р8
}

ADMIN_ONLY_SEQUENCES: set[int] = set()
SELF_PROMOTE_MAX = 6
_TARGET_RE = re.compile(r"^(?:(?P<uname>@[\w]{4,64})|(?P<uid>\d{4,20}))$")


def _forbidden_alert(faction: str, path: str) -> str:
    """Текст алерта для запрещённого пути (фракций больше нет — путь всегда доступен)."""
    return "Этот путь недоступен."


def _slots_left(c: dict, rank: int) -> int:
    slots = c.get("spell_slots") or {}
    for key in (str(rank), rank):
        entry = slots.get(key)
        if entry:
            return int(entry.get("left", 0))
    return 0


def _feats_due(old_seq: int, new_seq: int) -> int:
    return max(0, lotm.FEATS_AT_SEQUENCE.get(new_seq, 0) - lotm.FEATS_AT_SEQUENCE.get(old_seq, 0))


def _feats_due_for_char(c: dict, new_seq: int) -> int:
    target_total = lotm.FEATS_AT_SEQUENCE.get(new_seq, 0)
    current_taken = len(c.get("feats") or [])
    return max(0, target_total - current_taken)


def _card(c: dict) -> str:
    seq = int(c["sequence"])
    table = lotm.sequence_table()
    cur = table.get(seq, {})
    nxt_seq = seq - 1
    nxt = table.get(nxt_seq)
    lines = [
        f"🔮 <b>{esc(c['name'])}</b> — {esc(lotm.PATHWAYS[c['pathway']]['name'])}",
        f"Текущая ступень: <b>Посл. {seq} «{esc(seq_title)}»</b> (тир {cur.get('tier')}, мастерство + {rules.sequence_prof_bonus(seq)})",
        f"Слоты духовности: {_slots_left(c, 1)} Р1 · {_slots_left(c, 2)} Р2 · "
        f"{_slots_left(c, 3)} Р3 · {_slots_left(c, 4)} Р4",
    ]
    if nxt is None or nxt_seq < 0:
        lines.append("Ты достиг предела доступного на старте. Дальше — только по квесту Мастера.")
        return "\n".join(lines)

    rank, count = PROMOTE_SLOT_COST.get(nxt_seq, (nxt["tier"], 0))
    admin_only = nxt_seq in ADMIN_ONLY_SEQUENCES
    lines.append(f"<b>Повышение до Посл. {nxt_seq} «{nxt_title}»</b> (тир {nxt['tier']}):")
    lines.append(f"• Цена: {count} яч. духовности ранга {rank}")
    if admin_only:
        lines.append("• ⚠️ Только для администраторов (требуется квест Мастера)")
    lines.append(f"• Мастерство: + {rules.sequence_prof_bonus(seq)} → + {rules.sequence_prof_bonus(nxt_seq)}")
    lines.append(f"• Новых умений: {nxt['skills'] - cur.get('skills', 0)}")
    feats = _feats_due(seq, nxt_seq)
    if feats:
        lines.append(f"• Открывается черт Потустороннего: {feats}")
    return "\n".join(lines)


def _main_kb(seq: int, is_adm: bool = False) -> InlineKeyboardMarkup:
    nxt_seq = seq - 1
    if nxt_seq < 0:
        return InlineKeyboardMarkup(inline_keyboard=[])
    if nxt_seq in ADMIN_ONLY_SEQUENCES and not is_adm:
        return InlineKeyboardMarkup(inline_keyboard=[[btn("« Закрыть", "bpr:cancel")]])
    rows: list[list[InlineKeyboardButton]] = [
        [btn("✨ Повыситься", "bpr:do"), btn("« Закрыть", "bpr:cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("promote"))
async def cmd_promote(message: Message, command: CommandObject, state: FSMContext):
    arg = (command.args or "").strip()

    if arg:
        if not is_admin(message.from_user.id):
            await message.answer("Указывать цель может только администратор.")
            return
        m = _TARGET_RE.match(arg)
        if not m:
            await message.answer("Формат: /promote @username или /promote user_id")
            return
        if m.group("uid"):
            uid = int(m.group("uid"))
            c = await db.get_character(uid)
            if c is None:
                await message.answer(f"Анкета пользователя {uid} не найдена.")
                return
        else:
            uname = m.group("uname")[1:].lower()
            c = await db.get_character_by_username(uname)
            if c is None:
                await message.answer(f"Анкета @{esc(uname)} не найдена.")
                return
            uid = c["user_id"]
    else:
        uid = message.from_user.id
        c = await db.get_character(uid)
        if c is None:
            await message.answer("Сначала создай анкету: /newchar")
            return

    if c["status"] != "approved":
        await message.answer("Анкета должна быть одобрена админом.")
        return
    if not c.get("pathway"):
        await message.answer("Сначала собери билд: /build")
        return

    await state.set_state(Promote.confirm)
    await state.set_data({
        "uid": int(uid),
        "admin_ritual": bool(arg and is_admin(message.from_user.id)),
    })
    kb = _main_kb(int(c["sequence"]), is_adm=is_admin(message.from_user.id))
    if kb.inline_keyboard:
        await message.answer(
            _card(c) + "\n\n<i>Ячейки духовности тратятся до длинного отдыха.</i>",
            reply_markup=kb,
        )
    else:
        await message.answer(_card(c))
    if arg:
        await message.answer(f"🎯 Цель повышения: {esc(c['name'])} (ID {uid})")


@router.callback_query(Promote.confirm, F.data == "bpr:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await ui.safe_edit(cb.message, "Отменено.")
    await cb.answer()


@router.callback_query(Promote.confirm, F.data == "bpr:do")
async def cb_do(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get("uid")
    is_ritual_admin = bool(data.get("admin_ritual")) or is_admin(cb.from_user.id)
    if uid is None:
        await cb.answer("Сессия истекла — введи /promote заново.", show_alert=True)
        return
    c = await db.get_character(int(uid))
    if c is None:
        await cb.answer("Анкета не найдена.", show_alert=True)
        return

    old_seq = int(c["sequence"])
    new_seq = old_seq - 1
    if new_seq >= old_seq or old_seq > 9 or old_seq < 1:
        await cb.answer("Некорректная последовательность.", show_alert=True)
        return

    if dig < 100 and not is_ritual_admin:
        await cb.answer(
            "Переваривание зелья не завершено: нужно 100% (сейчас "
            f"{dig}%). Продолжай отслеживать прогресс через /promote.",
            show_alert=True,
        )
        return
    if not ADMIN_ONLY_SEQUENCES and not is_ritual_admin:
        await state.clear()
        await _do_promote(cb, state, int(uid), new_seq)
        return

    rank, count = PROMOTE_SLOT_COST.get(new_seq, (lotm.sequence_table()[new_seq]["tier"], 0))
    if dig >= 100:
        count = max(1, count // 2)
    have = _slots_left(c, rank)
    if have < count and not is_ritual_admin:
        await cb.answer(
            f"Не хватает ячеек: нужно {count} ранга Р{rank}, у тебя {have}. "
            "Отдохни (/rest long) и попробуй снова.",
            show_alert=True,
        )
        return

    if not is_ritual_admin:
        await state.clear()
        await _do_promote(cb, state, int(uid), new_seq)
        return

    rank, count = PROMOTE_SLOT_COST.get(new_seq, (lotm.sequence_table()[new_seq]["tier"], 0))
    # Переваривание зелья: при digestion >= 100 цена в ячейках снижается вдвое
    dig = int(c.get("digestion") or 0)
    if dig >= 100:
        count = max(1, count // 2)
    have = _slots_left(c, rank)
    if have < count and not is_ritual_admin:
        await cb.answer(
            f"Не хватает ячеек: нужно {count} ранга Р{rank}, у тебя {have}. "
            "Отдохни (/rest long) и попробуй снова.",
            show_alert=True,
        )
        return

    if not is_ritual_admin:
        await state.clear()
        await _do_promote(cb, state, int(uid), new_seq)
        return

    req_kb = InlineKeyboardMarkup(inline_keyboard=[[
        btn(f"Повысить до Посл. {new_seq}", f"adm_prom:ok:{uid}:{new_seq}"),
        btn("Отклонить", f"adm_prom:no:{uid}:{new_seq}"),
    ]])
    for admin_id in config.ADMIN_IDS:
        try:
            await cb.bot.send_message(
                admin_id,
                f"Запрос на повышение Последовательности\n"
                f"Игрок: <b>{esc(c['name'])}</b> (ID <code>{uid}</code>)\n"
                f"Путь: {esc(lotm.PATHWAYS[c['pathway']]['name'])}\n"
                f"Переход: Посл. <b>{old_seq} → {new_seq}</b> (ячейки Р{rank}: {have}/{count} в наличии)",
                reply_markup=req_kb,
            )
        except Exception:
            pass
    await ui.safe_edit(
        cb.message,
        f"Запрос на повышение до <b>Посл. {new_seq}</b> отправлен Мастеру/администраторам!\n"
        "Как только ритуал одобрят, тебе придёт уведомление.",
    )
    await cb.answer("Запрос отправлен")
    return

    await _apply_promote(cb, state, c, new_seq, c["pathway"], rank, count)

@router.callback_query(Promote.choose_path, F.data.startswith("wof:"))
async def cb_wof_choice(cb: CallbackQuery, state: FSMContext):
    """Выбор компенсации для Колеса Фортуны (нет смежного пути)."""
    data = await state.get_data()
    uid = data.get("uid")
    if uid is None:
        await cb.answer("Сессия истекла — введи /promote заново.", show_alert=True)
        return
    c = await db.get_character(int(uid))
    if c is None:
        await cb.answer("Анкета не найдена.", show_alert=True)
        return
    rank = int(data.get("promote_rank") or 4)
    count = int(data.get("promote_count") or 1)
    mode = cb.data.split(":", 1)[1]

    if mode == "rechoice":
        # Перевыбор веток: сбрасываем choice_a/choice_b на ступенях 9–5 в "a" (первыбор в меню умений).
        await state.set_state(Promote.confirm)
        await _apply_promote(cb, state, c, 4, c["pathway"], rank, count, reset_choices=True)
        return

    # bonus_feat: +1 дополнительная черта
    await state.set_state(Promote.confirm)
    await _apply_promote(cb, state, c, 4, c["pathway"], rank, count, bonus_feat=True)


@router.callback_query(Promote.choose_path, F.data.startswith("bpp:"))
async def cb_choose_path(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get("uid")
    if uid is None:
        await cb.answer("Сессия истекла — введи /promote заново.", show_alert=True)
        return
    c = await db.get_character(int(uid))
    if c is None:
        await cb.answer("Анкета не найдена.", show_alert=True)
        return
    new_path = c["pathway"]
    if cb.data != "bpp:keep":
        new_path = cb.data.split(":", 1)[1]
        if new_path not in lotm.sibling_paths(c["pathway"]):
            await cb.answer("Недопустимый путь.", show_alert=True)
            return
        if new_path not in lotm.get_allowed_pathways(c["faction"]):
            await cb.answer(_forbidden_alert(c["faction"], new_path), show_alert=True)
            return
    rank = int(data.get("promote_rank") or 4)
    count = int(data.get("promote_count") or 1)
    await state.set_state(Promote.confirm)
    await _apply_promote(cb, state, c, 4, new_path, rank, count)


async def _apply_promote(
    cb: CallbackQuery,
    state: FSMContext,
    c: dict,
    new_seq: int,
    new_path: str,
    rank: int,
    count: int,
    reset_choices: bool = False,
    bonus_feat: bool = False,
) -> None:
    """Списывает ячейки, обновляет ветки, HP и слоты, запускает выбор новой черты."""
    feats = list(c.get("feats") or [])
    uid = c["user_id"]

    slots_raw = c.get("spell_slots") or {}
    slots: dict[int, dict] = {}
    for k, v in slots_raw.items():
        try:
            slots[int(k)] = dict(v)
        except (TypeError, ValueError):
            continue
    if _slots_left(c, rank) >= count:
        entry = slots.setdefault(rank, {"total": 0, "left": 0})
        entry["left"] = max(0, int(entry.get("left", 0)) - count)

    # Сначала формируем актуальный набор веток для расчёта HP
    choices = {str(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    if reset_choices:
        choices = {str(s): "a" for s in lotm.SEQUENCES if s >= new_seq}
    elif new_path != c["pathway"]:
        choices = {str(s): "a" for s in lotm.SEQUENCES if s >= new_seq}
    else:
        choices.setdefault(str(new_seq), "a")

    old_max_hp = int(c.get("max_hp") or 0)
    max_hp = rules.sequence_max_hp(
        new_path,
        new_seq,
        rules.ability_mod(c["stats"]["con"]),
        feats,
        faction_perk=c.get("faction_perk", ""),
        pathway_choices=choices,
    )
    hp_gain = max(0, max_hp - old_max_hp)
    new_hp = min(max_hp, int(c.get("hp") or 0) + hp_gain) if old_max_hp else max_hp

    max_slots = lotm.spell_slots_for(new_seq, feats)
    cur_slots = {str(k): dict(v) for k, v in slots.items()}
    for r, n in max_slots.items():
        rk = str(r)
        old_tot = int(cur_slots.get(rk, {}).get("total", 0))
        old_left = int(cur_slots.get(rk, {}).get("left", 0))
        slot_diff = max(0, n - old_tot)
        cur_slots[rk] = {"total": n, "left": min(n, old_left + slot_diff)}

    update_fields = dict(
        sequence=new_seq,
        pathway=new_path,
        char_class=f"{lotm.PATHWAYS[new_path]['name']} — {lotm.SEQUENCE_TITLES.get(new_path, {}).get(new_seq, f'Посл. {new_seq}')}",
        sanity=100,
        digestion=0,
        saves=lotm.PATHWAYS[new_path].get("saves", c.get("saves") or []),
        level=lotm.OPEN_TIERS[new_seq],
        max_hp=max_hp,
        hp=new_hp,
        hit_dice_left=lotm.OPEN_TIERS[new_seq],
        spell_slots=cur_slots,
        pathway_choices=choices,
    )

    due = _feats_due_for_char(c, new_seq)
    if bonus_feat:
        due += 1
    await db.update_character(uid, **update_fields)

    if due > 0 and cb.from_user.id == uid:
        await state.update_data(feat_target=new_seq, feats_left=due)
        await state.set_state(Promote.choose_feat)
        await _ask_feat(cb, state)
        return

    await state.clear()
    await _promo_summary(cb, uid, new_seq, new_path, due)


async def _ask_feat(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get("uid")
    if uid is None:
        await cb.answer("Сессия истекла — введи /promote заново.", show_alert=True)
        return
    left = int(data.get("feats_left") or 0)
    c = await db.get_character(int(uid))
    if c is None:
        await cb.answer("Анкета не найдена.", show_alert=True)
        return
    taken = set(c.get("feats") or [])
    taken = set(c.get("feats") or [])
    available = [f for f in lotm.FEATS if f not in taken]

    lines = [
        f"🎁 <b>Выбери черту Потустороннего</b> (осталось выбрать: {left})\n",
    ]
    for f in available:
        ft = lotm.FEATS[f]
        lines.append(f"• <b>{esc(ft['name'])}</b>\n  <i>{esc(ft['desc'])}</i>\n")

    rows: list[list[InlineKeyboardButton]] = [
        [btn(lotm.FEATS[f]["name"], f"bpf:{f}")]
        for f in available
    ]
    rows.append([btn("Отложить выбор (позже через /abilities)", "bpf:skip")])

    await ui.safe_edit(
        cb.message,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(Promote.choose_feat, F.data.startswith("bpf:"))
async def cb_choose_feat(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get("uid")
    if uid is None:
        await cb.answer("Сессия истекла — введи /promote заново.", show_alert=True)
        return
    uid = int(uid)
    arg = cb.data.split(":", 1)[1]
    c = await db.get_character(uid)
    if c is None:
        await cb.answer("Анкета не найдена.", show_alert=True)
        return

    if arg != "skip":
        if arg not in lotm.FEATS:
            await cb.answer("Неизвестная черта.", show_alert=True)
            return
        feats = list(c.get("feats") or [])
        if arg in feats:
            await cb.answer("Эта черта уже выбрана.", show_alert=True)
            return
        feats.append(arg)
        new_path = c["pathway"]
        new_seq = int(c["sequence"])
        stats = c.get("stats") or {}
        con_mod = rules.ability_mod(int(stats.get("con", 10)))
        old_max_hp = int(c.get("max_hp") or 0)
        max_hp = rules.sequence_max_hp(
            new_path,
            new_seq,
            con_mod,
            feats,
            faction_perk=c.get("faction_perk", ""),
            pathway_choices=c.get("pathway_choices"),
        )
        hp_diff = max(0, max_hp - old_max_hp)
        new_hp = min(max_hp, int(c.get("hp") or max_hp) + hp_diff)
        max_slots = lotm.spell_slots_for(new_seq, feats)

        uses = dict(c.get("lair_uses") or {})
        fresh_uses = h_perks.reset_combat_uses({**c, "feats": feats})
        for uk, uv in fresh_uses.items():
            if uv > 0 and uses.get(uk, 0) == 0:
                uses[uk] = uv

        cur_slots = {str(k): dict(v) for k, v in (c.get("spell_slots") or {}).items()}
        for r, n in max_slots.items():
            rk = str(r)
            old_tot = int(cur_slots.get(rk, {}).get("total", 0))
            old_left = int(cur_slots.get(rk, {}).get("left", 0))
            slot_diff = max(0, n - old_tot)
            cur_slots[rk] = {"total": n, "left": min(n, old_left + slot_diff)}

        await db.update_character(
            uid,
            feats=feats,
            max_hp=max_hp,
            hp=new_hp,
            spell_slots=cur_slots,
            lair_uses=uses,
        )

    left = int(data.get("feats_left") or 1) - 1
    if left > 0 and arg != "skip":
        await state.update_data(feats_left=left)
        await _ask_feat(cb, state)
        return

    c_after = await db.get_character(uid)
    new_seq = int(data.get("feat_target") or c_after["sequence"])
    due_left = _feats_due_for_char(c_after, new_seq)
    await state.clear()
    await _promo_summary(cb, uid, new_seq, c_after["pathway"], due_left)


async def _promo_summary(cb: CallbackQuery, uid: int, new_seq: int, path: str, due: int) -> None:
    c2 = await db.get_character(uid)
    path_label = lotm.PATHWAYS[path]["name"] if path else "—"
    seq_title = lotm.SEQUENCE_TITLES.get(path, {}).get(new_seq, f"Посл. {new_seq}")
    lines = [
        f"✨ <b>Повышение!</b> {esc(c2['name'])} — теперь Посл. <b>{new_seq} «{esc(seq_title)}»</b>",
        f"🛤 Путь: <b>{esc(path_label)}</b>",
        f"❤️ Здоровье: {c2['max_hp']} | 🎯 Мастерство: + {rules.sequence_prof_bonus(new_seq)}",
        "",
        "<b>Новые умения</b> (выбери ветку в /abilities):",
    ]
    choices = {int(k): v for k, v in (c2.get("pathway_choices") or {}).items()}
    for sk in rules.path_skills(path, choices, new_seq, new_seq):
        if sk["seq"] == new_seq:
            lines.append(f"• <b>{esc(sk['name'])}</b> — {esc(sk['desc'])}")
    if due:
        lines.append("\n🎁 Доступны новые черты Потустороннего: /abilities → черты")
    await ui.safe_edit(cb.message, "\n".join(lines))
    await cb.answer("Готово!")
    if cb.from_user.id != uid:
        await ui.notify_player(cb.bot, uid, "\n".join(lines))

@router.callback_query(F.data.startswith("adm_prom:"))
async def cb_admin_promote_decision(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("Только для администраторов", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await cb.answer()
        return
    decision, uid, target_seq = parts[1], int(parts[2]), int(parts[3])
    c = await db.get_character(uid)
    if c is None:
        await cb.answer("Анкета не найдена", show_alert=True)
        return
    if int(c["sequence"]) != target_seq + 1:
        await cb.answer(f"Персонаж уже на Посл. {c['sequence']}", show_alert=True)
        return
    if decision == "no":
        await ui.safe_edit(cb.message, f"❌ Запрос на повышение {esc(c['name'])} до Посл. {target_seq} отклонён.")
        await ui.notify_player(cb.bot, uid, f"❌ Мастер отклонил запрос на повышение до Посл. {target_seq}.")
        adm_name = esc(cb.from_user.full_name or str(cb.from_user.id))
        for adm_id in config.ADMIN_IDS:
            if adm_id != cb.from_user.id:
                try:
                    await cb.bot.send_message(
                        adm_id,
                        f"🔮 Администратор <b>{adm_name}</b> отклонил запрос на повышение «<b>{esc(c['name'])}</b>» до Посл. {target_seq}.",
                    )
                except Exception:
                    pass
        await cb.answer("Отклонено")
        return

    rank, count = PROMOTE_SLOT_COST.get(target_seq, (lotm.sequence_table()[target_seq]["tier"], 0))
    have = _slots_left(c, rank)
    if have < count:
        await cb.answer(
            f"У игрока больше нет нужных ячеек духовности (нужно {count} Р{rank}, есть {have}). Пусть сделает /rest long.",
            show_alert=True,
        )
        return
    await _apply_promote(cb, state, c, target_seq, c["pathway"], rank, count)
    adm_name = esc(cb.from_user.full_name or str(cb.from_user.id))
    for adm_id in config.ADMIN_IDS:
        if adm_id != cb.from_user.id:
            try:
                await cb.bot.send_message(
                    adm_id,
                    f"🔮 Администратор <b>{adm_name}</b> одобрил запрос на повышение «<b>{esc(c['name'])}</b>» до Посл. {target_seq}.",
                )
            except Exception:
                pass
