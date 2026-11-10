"""Конструктор билда Потустороннего (Lord of the Mysteries).

Шаги:
1. Имя персонажа
2. Выбор Пути (все 22 каноничных Пути)
3. Характеристики (Point-Buy 27 очков + Бонус Зелья Пути)
4. Навыки + Компетенция (звезда x2)
5. Специализация 9-й Последовательности (Ветка А / Б)
6. Предыстория / Квента (по желанию) -> мгновенная активация персонажа (approved).
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
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
from ui import btn, esc

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

STAT_ORDER = ("str", "dex", "con", "int", "wis", "cha")
START_SEQUENCE = 9


class Build(StatesGroup):
    name = State()
    pathway = State()
    stats = State()
    skills = State()
    expertise = State()
    choices = State()
    background = State()
    region = State()
    bio = State()


def _fmt_skill(sk: dict) -> str:
    cost = f", {sk['slot_cost']} яч. Р{sk['slot_rank']}" if sk.get("slot_cost") else ""
    return f"{lotm.action_label(sk['action'])} — <b>{esc(sk['name'])}</b>{cost}\n<i>{esc(sk['desc'])}</i>"


# ---------------------------------------------------------------------------
# Точка входа: /build, /newchar, /reset_me
# ---------------------------------------------------------------------------

@router.message(Command("build", "newchar"))
async def cmd_build(message: Message, state: FSMContext, bot: Bot):
    if message.from_user is None:
        return
    uid = message.from_user.id
    char = await db.get_character(uid)
    if char is not None and char.get("pathway") and char.get("status") != "draft":
        seq = char.get("sequence", 9)
        p_name = lotm.PATHWAYS.get(char["pathway"], {}).get("name", char["pathway"])
        seq_title = lotm.SEQUENCE_TITLES.get(char["pathway"], {}).get(seq, f"Посл. {seq}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("Пересоздать персонажа с нуля", "bf:reset"), btn("Оставить текущего", "bf:reset_no")],
        ])
        await message.answer(
            f"У тебя уже есть активный Потусторонний: <b>{esc(char.get('name') or 'Без имени')}</b> "
            f"({esc(p_name)} — Посл. {seq} «{esc(seq_title)}»).\n\n"
            "Хочешь удалить его и начать путь заново с 9-й Последовательности?",
            reply_markup=kb,
        )
        return
    await _start_build(message, state, user_id=uid)


@router.message(Command("reset_me"))
async def cmd_reset_me(message: Message, state: FSMContext):
    if message.from_user is None:
        return
    c = await db.get_character(message.from_user.id)
    if c is None:
        await message.answer("У тебя пока нет персонажа. Создать: /build")
        return
    await state.clear()
    await db.delete_character(message.from_user.id)
    await message.answer(
        f"Персонаж <b>{esc(c.get('name') or 'Без имени')}</b> удалён. "
        "Начать новое восхождение: /build"
    )


@router.callback_query(F.data == "bf:reset")
async def cb_reset_confirmed(cb: CallbackQuery, state: FSMContext):
    if await db.is_in_combat(cb.from_user.id):
        await cb.answer("Нельзя пересоздать персонажа во время активного боя!", show_alert=True)
        return
    await db.delete_character(cb.from_user.id)
    await state.clear()
    await cb.answer("Старый персонаж удалён")
    if cb.message:
        await ui.safe_edit(cb.message, "Старый персонаж удалён. Начинаем создание нового…")
        await _start_build(cb.message, state, user_id=cb.from_user.id)


@router.callback_query(F.data == "bf:reset_no")
async def cb_reset_declined(cb: CallbackQuery):
    if cb.message:
        await ui.safe_edit(cb.message, "Хорошо, твой текущий персонаж сохранён. Посмотреть профиль: /me")
    await cb.answer()


@router.callback_query(F.data.in_({"bf:cancel", "bd:cancel"}))
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if cb.message:
        await ui.safe_edit(cb.message, "Создание персонажа отменено. Запустить заново: /build")
    await cb.answer()


# ---------------------------------------------------------------------------
# Шаг 1/6 — Имя персонажа
# ---------------------------------------------------------------------------

async def _start_build(message: Message, state: FSMContext, user_id: int | None = None) -> None:
    uid = user_id if user_id is not None else (message.from_user.id if message.from_user else 0)
    default_pb = {s: 10 for s in STAT_ORDER}
    await state.clear()
    await state.set_state(Build.name)
    await state.set_data({
        "uid": uid,
        "start_seq": START_SEQUENCE,
        "stats_pb": dict(default_pb),
        "choices": {},
        "feats": [],
        "skills": [],
    })
    await message.answer(
        "Шаг 1/6 — Имя Потустороннего\n\n"
        "Введи имя своего персонажа (от 2 до 40 символов):"
    )


@router.message(Build.name, F.text & ~F.text.startswith("/"))
async def cb_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not (2 <= len(name) <= 40):
        await message.answer("Имя должно быть от 2 до 40 символов. Попробуй ещё раз:")
        return
    await state.update_data(name=name, uid=message.from_user.id)
    await _ask_pathway_msg(message, state)


@router.message(Build.name)
async def cb_name_bad(message: Message):
    await message.answer("Пришли имя обычным текстом (2–40 символов) или /cancel:")


# ---------------------------------------------------------------------------
# Шаг 2/6 — Выбор одного из 22 Путей (старт строго на Посл. 9)
# ---------------------------------------------------------------------------

def _pathway_kb() -> InlineKeyboardMarkup:
    allowed = lotm.get_allowed_pathways()
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(allowed), 2):
        row = []
        for p in allowed[i:i + 2]:
            seq9_title = lotm.SEQUENCE_TITLES.get(p, {}).get(9, "")
            label = f"{lotm.PATHWAYS[p]['name']} ({seq9_title})" if seq9_title else lotm.PATHWAYS[p]["name"]
            row.append(btn(label, f"bw:{p}"))
        rows.append(row)
    rows.append([btn("« Отмена", "bf:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pathway_prompt_text(name: str) -> str:
    return (
        f"Имя: <b>{esc(name)}</b>\n\n"
        "Шаг 2/6 — Выбор Пути Потустороннего\n\n"
        "Каждый Потусторонний начинает восхождение с <b>9-й Последовательности</b>.\n"
        "Выбери один из 22 каноничных Путей:"
    )


async def _ask_pathway_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Build.pathway)
    await message.answer(_pathway_prompt_text(data.get("name", "")), reply_markup=_pathway_kb())


async def _ask_pathway(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(Build.pathway)
    await ui.safe_edit(cb.message, _pathway_prompt_text(data.get("name", "")), _pathway_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("bw:"))
async def cb_pathway(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 1)[1]
    if key not in lotm.get_allowed_pathways():
        await cb.answer("Неизвестный путь", show_alert=True)
        return
    await state.update_data(pathway=key, start_seq=START_SEQUENCE, choices={}, feats=[], skills=[])
    await _ask_stats(cb, state)


# ---------------------------------------------------------------------------
# Шаг 3/6 — Характеристики (Point-Buy 27 очков + Бонус Зелья Пути)
# ---------------------------------------------------------------------------

def _stat_cost(score: int) -> int:
    return rules.STAT_COST.get(int(score), 99)


def _stats_spent(stats: dict) -> int:
    return sum(_stat_cost(v) for v in stats.values())


def _stats_kb(stats: dict, bonus_map: dict[str, int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for s in STAT_ORDER:
        b = bonus_map.get(s, 0)
        b_str = f" (+{b} зелье = {stats[s] + b})" if b else ""
        rows.append([
            btn(f"{rules.SHORT_ABILITY[s]}: {stats[s]}{b_str}", "noop"),
            btn("−", f"bstat:-:{s}"),
            btn("+", f"bstat:+:{s}"),
        ])
    left = rules.POINT_BUY_BUDGET - _stats_spent(stats)
    rows.append([btn(f"Осталось очков: {left}", "noop")])
    if left != 0:
        rows.append([btn(f"Распредели все {rules.POINT_BUY_BUDGET} очков", "noop")])
    else:
        rows.append([btn("Подтвердить характеристики", "bstat:done")])
    rows.append([btn("« Назад к выбору Пути", "bstat:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_stats(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    path = data.get("pathway", "")
    p = lotm.PATHWAYS.get(path)
    if not p:
        await cb.answer("Сессия конструктора истекла. Запусти /build заново.", show_alert=True)
        return
    raw_pb = data.get("stats_pb")
    if isinstance(raw_pb, dict) and all(k in raw_pb for k in STAT_ORDER):
        stats = {s: int(raw_pb[s]) for s in STAT_ORDER}
    else:
        stats = {s: 10 for s in STAT_ORDER}
    bonus_map = lotm.pathway_stat_bonus(path)
    bonus_desc = ", ".join(f"+{v} {rules.SHORT_ABILITY[k]}" for k, v in bonus_map.items())
    seq9_title = lotm.SEQUENCE_TITLES.get(path, {}).get(9, "Посл. 9")
    await state.update_data(stats_pb=stats)
    await state.set_state(Build.stats)
    await ui.safe_edit(
        cb.message,
        f"Путь: <b>{esc(p['name'])}</b> — Посл. 9 «<b>{esc(seq9_title)}</b>»\n"
        f"Кость хитов: d{p['bone']} | Ключевые параметры: {esc(p['stat'])}\n"
        f"Бонус зелья Пути: {bonus_desc}\n\n"
        "<b>Шаг 3/6 — Характеристики (Point-Buy)</b>\n"
        f"Распредели <b>{rules.POINT_BUY_BUDGET} очков</b> (значения от {rules.POINT_BUY_MIN} до {rules.POINT_BUY_MAX}).\n"
        "Стоимость: 8=0, 9=1, 10=2, 11=3, 12=4, 13=5, 14=6, 15=8.\n"
        "<i>Бонус зелья прибавится к твоим характеристикам автоматически.</i>",
        _stats_kb(stats, bonus_map),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bstat:"))
async def cb_stats(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    data = await state.get_data()
    path = data.get("pathway", "")
    if not path:
        await cb.answer("Сессия конструктора истекла. Запусти /build заново.", show_alert=True)
        return

    raw_pb = data.get("stats_pb")
    stats = {s: int(raw_pb[s]) for s in STAT_ORDER} if isinstance(raw_pb, dict) and all(k in raw_pb for k in STAT_ORDER) else {s: 10 for s in STAT_ORDER}
    bonus_map = lotm.pathway_stat_bonus(path)

    if action == "done":
        spent = rules.stat_pool_spent(stats)
        if spent != rules.POINT_BUY_BUDGET:
            left = rules.POINT_BUY_BUDGET - spent
            await cb.answer(f"Используй ровно {rules.POINT_BUY_BUDGET} очков (осталось: {left}).", show_alert=True)
            return
        await state.update_data(stats=dict(stats), stats_pb=dict(stats))
        await _ask_skills(cb, state)
        return

    if action == "back":
        await _ask_pathway(cb, state)
        return

    if arg not in stats:
        await cb.answer()
        return

    if action == "+":
        if stats[arg] >= rules.POINT_BUY_MAX:
            await cb.answer("Максимум до бонуса зелья — 15")
            return
        cost_diff = _stat_cost(stats[arg] + 1) - _stat_cost(stats[arg])
        if _stats_spent(stats) + cost_diff > rules.POINT_BUY_BUDGET:
            await cb.answer("Не хватает очков")
            return
        stats[arg] += 1
    elif action == "-":
        if stats[arg] <= rules.POINT_BUY_MIN:
            await cb.answer("Минимум — 8")
            return
        stats[arg] -= 1
    else:
        await cb.answer()
        return

    await state.update_data(stats_pb=stats)
    try:
        await cb.message.edit_reply_markup(reply_markup=_stats_kb(stats, bonus_map))
    except Exception:
        pass
    await cb.answer()


# ---------------------------------------------------------------------------
# Шаг 4/6 — Навыки и Компетенция (x2)
# ---------------------------------------------------------------------------

def _effective_stats_preview(data: dict) -> dict[str, int]:
    base = data.get("stats") or data.get("stats_pb") or dict(rules.POINTBUY_DEFAULT)
    stats = {k: int(base.get(k, 10)) for k in rules.ABILITIES}
    for stat, bonus in lotm.pathway_stat_bonus(data.get("pathway", "")).items():
        stats[stat] = min(30, stats.get(stat, 10) + int(bonus))
    return stats


def _skill_budget(data: dict) -> int:
    return rules.skill_pick_budget(data.get("pathway", ""), _effective_stats_preview(data))


def _skills_kb(picked: list[str], budget: int) -> InlineKeyboardMarkup:
    items: list[InlineKeyboardButton] = []
    for k, (name, ab, _) in rules.SKILLS.items():
        mark = "OK " if k in picked else "· "
        items.append(btn(f"{mark}{name} ({rules.SHORT_ABILITY[ab]})", f"bsk:toggle:{k}"))
    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    if len(picked) >= budget:
        rows.append([btn(f"Подтвердить навыки ({len(picked)}/{budget})", "bsk:done")])
    else:
        rows.append([btn(f"Выбери ещё {budget - len(picked)} (выбрано {len(picked)}/{budget})", "noop")])
    rows.append([btn("« Назад к характеристикам", "bsk:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_skills(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    budget = _skill_budget(data)
    picked = [k for k in (data.get("skills") or []) if k in rules.SKILLS][:budget]
    await state.update_data(skills=picked)
    await state.set_state(Build.skills)
    await ui.safe_edit(
        cb.message,
        "<b>Шаг 4/6 — Владение навыками</b>\n\n"
        f"Выбери <b>{budget} навыка</b> (зависит от твоего Пути и Интеллекта), в которых ты обучен:",
        _skills_kb(picked, budget),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bsk:"))
async def cb_skills(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    data = await state.get_data()
    if not data.get("pathway"):
        await cb.answer("Сессия конструктора истекла. Запусти /build заново.", show_alert=True)
        return
    budget = _skill_budget(data)
    picked = list(data.get("skills") or [])

    if action == "done":
        if len(picked) != budget:
            await cb.answer(f"Выбери ровно {budget} навыка!", show_alert=True)
            return
        await _ask_expertise(cb, state)
        return

    if action == "back":
        await _ask_stats(cb, state)
        return

    if action == "toggle" and arg in rules.SKILLS:
        if arg in picked:
            picked.remove(arg)
        else:
            if len(picked) >= budget:
                await cb.answer(f"Максимум {budget} навыка! Сначала сними галочку с другого.", show_alert=True)
                return
            picked.append(arg)
        await state.update_data(skills=picked)
        try:
            await cb.message.edit_reply_markup(reply_markup=_skills_kb(picked, budget))
        except Exception:
            pass
        await cb.answer()


async def _ask_expertise(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    skills = list(data.get("skills") or [])
    await state.set_state(Build.expertise)
    rows = [
        [btn(f"★ {rules.SKILLS[k][0]} (Мастерство x2)", f"bexp:{k}")]
        for k in skills if k in rules.SKILLS
    ]
    rows.append([btn("« Назад к навыкам", "bexp:back")])
    await ui.safe_edit(
        cb.message,
        "<b>Шаг 4.1/6 — Компетенция (Expertise)</b>\n\n"
        "Выбери <b>1 профильный навык</b>, в котором твой бонус мастерства будет удвоен (x2):",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bexp:"))
async def cb_expertise(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 1)[1]
    if key == "back":
        await _ask_skills(cb, state)
        return
    await state.update_data(expertise=key)
    await _ask_choice(cb, state)


# ---------------------------------------------------------------------------
# Шаг 5/6 — Выбор специализации 9-й Последовательности (Ветка А / Б)
# ---------------------------------------------------------------------------

async def _ask_choice(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    path = data.get("pathway")
    if not path:
        await cb.answer("Сессия конструктора истекла. Запусти /build заново.", show_alert=True)
        return
    seq = START_SEQUENCE
    tiers = lotm.PATHWAYS[path]["tiers"][seq]
    core = tiers["core"]
    a, b = tiers["choice_a"], tiers["choice_b"]
    seq_title = lotm.SEQUENCE_TITLES.get(path, {}).get(seq, f"Посл. {seq}")
    await state.set_state(Build.choices)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn(f"Ветка А — {a[0]}", f"bc:{seq}:a")],
        [btn(f"Ветка Б — {b[0]}", f"bc:{seq}:b")],
        [btn("« Назад к Компетенции", "bc:back")],
    ])
    await ui.safe_edit(
        cb.message,
        f"<b>Шаг 5/6 — Специализация Посл. 9 «{esc(seq_title)}»</b>\n\n"
        f"<b>Базовое умение Пути (даётся всегда):</b>\n"
        f"{esc(core[0])} — <i>{esc(core[4])}</i>\n\n"
        f"Выбери дополнительную специализацию 9-й Последовательности:\n\n"
        f"<b>[Ветка А] {esc(a[0])}</b>\n<i>{esc(a[4])}</i>\n\n"
        f"<b>[Ветка Б] {esc(b[0])}</b>\n<i>{esc(b[4])}</i>",
        kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bc:"))
async def cb_choice(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) == 2 and parts[1] == "back":
        await _ask_expertise(cb, state)
        return
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ("a", "b"):
        await cb.answer()
        return
    seq = int(parts[1])
    branch = parts[2]
    await state.update_data(choices={str(seq): branch})
    await _ask_background(cb, state)


async def _ask_background(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Build.background)
    rows = [[btn(bg["name"], f"bbg:{k}")] for k, bg in lotm.BACKGROUNDS.items()]
    rows.append([btn("« Назад к специализации", "bbg:back")])
    lines = ["<b>Шаг 5.1/7 — Предыстория (Background)</b>\n\nКем ты был до того, как выпил зелье?\n"]
    for bg in lotm.BACKGROUNDS.values():
        lines.append(f"• <b>{esc(bg['name'])}</b>\n  <i>{esc(bg['desc'])}</i>")
    await ui.safe_edit(cb.message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("bbg:"))
async def cb_background(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 1)[1]
    if key == "back":
        await _ask_choice(cb, state)
        return
    if key not in lotm.BACKGROUNDS:
        await cb.answer()
        return
    await state.update_data(background=key)
    await _ask_region(cb, state)


async def _ask_region(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Build.region)
    rows = [[btn(r["name"], f"breg:{k}")] for k, r in lotm.STARTING_REGIONS.items()]
    rows.append([btn("« Назад к предыстории", "breg:back")])
    lines = [
        "<b>Шаг 5.2/7 — Стартовый Регион Мира (Осень 1349 года)</b>\n\n"
        "Выбери, в какой точке мира начнётся твоя история:\n"
    ]
    for r in lotm.STARTING_REGIONS.values():
        lines.append(f"• <b>{esc(r['name'])}</b>\n  <i>{esc(r['arc_ru'])}</i>")
    await ui.safe_edit(cb.message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("breg:"))
async def cb_region(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":", 1)[1]
    if key == "back":
        await _ask_background(cb, state)
        return
    if key not in lotm.STARTING_REGIONS:
        await cb.answer()
        return
    await state.update_data(region=key)
    await _ask_bio(cb, state)


# ---------------------------------------------------------------------------
# Шаг 6/6 — Предыстория (по желанию) и мгновенное завершение
# ---------------------------------------------------------------------------

async def _ask_bio(event, state: FSMContext) -> None:
    await state.set_state(Build.bio)
    await state.update_data(bio_parts=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Пропустить и сразу начать игру", "bbio:skip")],
        [btn("Сохранить предысторию", "bbio:done")],
    ])
    text = (
        "<b>Шаг 6/6 — Предыстория Потустороннего (по желанию)</b>\n\n"
        "Опиши внешность, характер и то, как твой персонаж выпил своё первое зелье (до 7000 символов).\n\n"
        "• Хочешь сразу ворваться в сюжет и бои — нажми <b>«Пропустить и сразу начать игру»</b> (дописать анкету можно в любой момент через /me)."
    )
    if isinstance(event, CallbackQuery):
        await ui.safe_edit(event.message, text, kb)
    else:
        await event.answer(text, reply_markup=kb)


def _join_bio(parts: list) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


async def _finish_bio(event, state: FSMContext, bot: Bot, parts: list) -> None:
    raw = _join_bio(parts)
    if not raw:
        await event.answer("Предыстория пустая. Пришли текст или нажми «Пропустить».")
        return
    value, err = ui.parse_field("bio", raw)
    if err:
        await event.answer(err)
        return
    await state.update_data(bio=value, bio_parts=[])
    await _finish(event, state, bot)


@router.callback_query(F.data == "bbio:done")
async def cb_bio_done(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not data.get("pathway"):
        await cb.answer("Сессия конструктора истекла. Запусти /build заново.", show_alert=True)
        return
    await _finish_bio(cb.message, state, bot, data.get("bio_parts") or [])


@router.callback_query(F.data == "bbio:skip")
async def cb_bio_skip(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not data.get("pathway"):
        await cb.answer("Сессия конструктора истекла. Запусти /build заново.", show_alert=True)
        return
    await _finish(cb, state, bot)


@router.message(Build.bio, F.text)
async def cb_bio_text(message: Message, state: FSMContext, bot: Bot):
    raw = (message.text or "").strip()
    if raw.lower() == "/done":
        data = await state.get_data()
        await _finish_bio(message, state, bot, list(data.get("bio_parts") or []))
        return
    if raw.startswith("/"):
        await message.answer("Пришли текст предыстории, нажми кнопку ниже или /cancel.")
        return
    data = await state.get_data()
    parts = list(data.get("bio_parts") or [])
    parts.append(raw)
    total = sum(len(p) for p in parts) + 2 * (len(parts) - 1)
    if total > 7000:
        parts.pop()
        await message.answer(f"Слишком длинно ({total}/7000). Сократи последнюю часть.")
        return
    await state.update_data(bio_parts=parts)
    await message.answer(
        f"Принято ({total}/7000). Нажми «Сохранить предысторию» для завершения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[btn("Сохранить предысторию", "bbio:done")]]),
    )


async def _finish(event, state: FSMContext, bot: Bot):
    cb = event if isinstance(event, CallbackQuery) else None
    user = event.from_user
    data = await state.get_data()
    if not data.get("pathway"):
        if cb is not None:
            await cb.answer("Сессия конструктора истекла. Запусти /build", show_alert=True)
        else:
            await event.answer("Сессия конструктора истекла. Запусти /build")
        return

    uid = int(data.get("uid") or user.id)
    path = data["pathway"]
    seq = START_SEQUENCE

    base = data.get("stats") or data.get("stats_pb") or dict(rules.POINTBUY_DEFAULT)
    stats = {k: int(base.get(k, 10)) for k in rules.ABILITIES}
    for stat, bonus in lotm.pathway_stat_bonus(path).items():
        stats[stat] = min(30, stats.get(stat, 10) + int(bonus))

    bg_key = data.get("background") or "detective"
    reg_key = data.get("region") or "tingen"
    bg_info = lotm.BACKGROUNDS.get(bg_key, lotm.BACKGROUNDS["detective"])
    for stat, bonus in bg_info["stat_bonus"].items():
        stats[stat] = min(30, stats.get(stat, 10) + int(bonus))
    if old is None:
        await db.create_draft(uid, user.username)
        old = await db.get_character(uid) or {}

    choices = {str(k): v for k, v in (data.get("choices") or {"9": "a"}).items()}
    con_mod = rules.ability_mod(stats["con"])
    max_hp = rules.sequence_max_hp(
        path,
        seq,
        con_mod,
        [],
        faction_perk="",
        pathway_choices=choices,
    )
    slots = lotm.spell_slots_for(seq, [])
    ac = 10 + rules.ability_mod(stats["dex"])

    char_name = (data.get("name") or old.get("name") or user.full_name or "Без имени").strip()[:40]
    bio_text = (data.get("bio") or "").strip() or "Потусторонний 9-й Последовательности."

    picked_skills = [k for k in (data.get("skills") or []) if k in rules.SKILLS]
    exp_key = data.get("expertise")
    skill_map = {k: (2 if k == exp_key else 1) for k in picked_skills}
    if bg_info["free_skill"] in rules.SKILLS and bg_info["free_skill"] not in skill_map:
        skill_map[bg_info["free_skill"]] = 1
    seq_title = lotm.SEQUENCE_TITLES.get(path, {}).get(seq, f"Посл. {seq}")

    char_data = dict(
        username=user.username,
        name=char_name,
        bio=bio_text,
        faction=bg_key,
        faction_perk=reg_key,
        pathway=path,
        sequence=seq,
        start_sequence=seq,
        level=lotm.OPEN_TIERS[seq],
        stats=stats,
        conditions=[],
        race=bg_info["name"],
        char_class=f"{lotm.PATHWAYS[path]['name']} — {seq_title}",
        pathway_choices=choices,
        feats=[],
        skills=skill_map,
        sanity=100,
        digestion=0,
        class_resource=lotm.PATHWAY_MECHANICS.get(path, {}).get("max_base", 3),
        max_hp=max_hp,
        hp=max_hp,
        temp_hp=0,
        ac=ac,
        saves=lotm.PATHWAYS[path].get("saves", []),
        hit_dice_left=lotm.OPEN_TIERS[seq],
        spell_slots={str(r): {"total": n, "left": n} for r, n in slots.items()},
        status="approved",
    )
    char_data["lair_uses"] = h_perks.reset_combat_uses(char_data)

    await db.update_character(uid, **char_data)

    reg_info = lotm.STARTING_REGIONS.get(reg_key, lotm.STARTING_REGIONS["tingen"])
    await db.get_story_session(uid)
    await db.update_story_session(
        uid,
        location=reg_info["location"],
        current_arc=reg_info["arc_ru"],
        summary=f"Предыстория: {bg_info['name']}. Регион старта: {reg_info['name']}.",
        clues=[f"💰 Капитал: {bg_info['gold']} золотых фунтов", f"🎒 {bg_info['gear']}"],
        npcs=[],
        history=[],
        options=[],
    )
    await state.clear()

    c2 = await db.get_character(uid)
    skills = rules.path_skills(path, {int(k): v for k, v in c2["pathway_choices"].items()}, seq, seq)
    eff_ac = c2["ac"] + h_perks.ac_bonus(c2)
    p_mech = lotm.PATHWAY_MECHANICS.get(path, {})
    lines = [
        "Зелье выпито! Персонаж готов к игре.\n",
        f"<b>{esc(c2['name'])}</b> — Потусторонний",
        f"<b>{esc(lotm.PATHWAYS[path]['name'])}</b> · Посл. 9 «<b>{esc(seq_title)}</b>»",
        f"ОЗ: <b>{max_hp}</b> | Защита: <b>{eff_ac}</b> | Рассудок: <b>100%</b> | Переваривание: <b>0%</b>",
        f"Ячейки духовности: {', '.join(f'Р{r}x{n}' for r, n in sorted(slots.items())) or 'нет'}",
        f"Механика Пути: <b>{esc(p_mech.get('name', '—'))}</b> ({p_mech.get('max_base', 3)} заряда)\n",
        "<b>Открытые способности:</b>",
    ]
    for sk in skills:
        lines.append(f"• {_fmt_skill(sk)}")
    lines.append("\nИспользуй /abilities для управления способностями, /me для карточки персонажа и /promote для отслеживания переваривания зелья.")
    result_text = "\n".join(lines)
    play_kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("🌫 Начать сюжет с ИИ-Мастером (/story)", "st:resume")],
        [btn("⚔️ Охота на Потустороннего (Бой 1-на-1)", "st:hunt")],
    ])
    if cb is not None:
        await ui.safe_edit(cb.message, result_text, play_kb)
        await cb.answer("Персонаж создан!")
    else:
        await event.answer(result_text, reply_markup=play_kb)
