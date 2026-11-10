"""Кубы и проверки — работают и в группах, и в личке."""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import config
import db
import rules
from rules import ABILITIES, SKILLS
from ui import esc

router = Router()

HELP = (
    "🎲 <b>Кубы и проверки</b>\n"
    "/roll 2d6+3 (или /r) — любой бросок: d20, 4d6, 1d8+2, 1d20+1d4…\n"
    "/check Скрытность (или /ch) — проверка навыка: модификатор + мастерство\n"
    "/check Сила — проверка характеристики\n"
    "/save Ловкость — спасбросок\n"
    "/init — инициатива\n"
    "/iti — порядок хода (кто и когда бросил /init)\n"
    "/skills — все мои бонусы\n"
    "/last — последние броски (в чате — общие, в личке — твои)\n"
    "/mute причина — тишина в чате: писать может только админ (снять: /unmute)\n\n"
    "<b>Дополнительно</b> (можно добавлять к /check, /save, /init, /roll):\n"
    "• <code>преим</code> или <code>adv</code> — преимущество (2d20, берём большее)\n"
    "• <code>пом</code> или <code>dis</code> — помеха (2d20, берём меньшее)\n"
    "• <code>+2</code> / <code>-1</code> — дополнительный бонус или штраф\n"
    "Например: <code>/check Восприятие преим +1</code> или <code>/roll d20 преим</code>\n\n"
    "Названия можно сокращать: <code>/check скрыт</code>.\n"
    "Бросать за персонажа можно только с одобренной анкетой. Создать её — в личке с ботом: /newchar\n\n"
    "<i>🕯️ Создатели: Великий Виета и Блудный Амон</i>"
)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.reply(HELP)


# ---------------------------------------------------------------------------
# Шаги 1–2: найти игрока по Telegram ID и проверить, что анкета одобрена
# ---------------------------------------------------------------------------

async def load_approved(message: Message, bot: Bot) -> dict | None:
    if message.from_user is None:
        return None
    c = await db.get_character(message.from_user.id)
    if c is None or c["status"] == "draft":
        me = await bot.me()
        await message.reply(f"У тебя ещё нет анкеты. Создай её в личке с ботом: @{me.username}")
        return None
    if c["status"] != "approved":
        text = "Твоя анкета ещё не одобрена."
        if c["status"] == "rejected":
            text += " Админ вернул её на доработку — исправь в личке с ботом (/me)."
        await message.reply(text)
        return None
    return c


# ---------------------------------------------------------------------------
# Форматирование результатов
# ---------------------------------------------------------------------------

def fmt_mod(n: int) -> str:
    """Модификатор для сообщений: '+ 2' / '+ 0' / '− 1' (со знаком и пробелом)."""
    return f"+ {n}" if n >= 0 else f"− {abs(n)}"


def fmt_d20(name: str, r: rules.D20Result) -> str:
    lines = [f"🎲 <b>{esc(name)}</b> — {esc(r.title)}"]
    if r.mode == "normal":
        lines.append(f"d20: <b>{r.rolls[0]}</b>")
    else:
        shown = ", ".join(
            f"<b>{v}</b>" if i == r.used_idx else f"<s>{v}</s>" for i, v in enumerate(r.rolls)
        )
        label = "преимущество" if r.mode == "adv" else "помеха"
        lines.append(f"2d20 ({label}): {shown}")
    for label, value in r.parts:
        lines.append(f"{esc(label)}: {fmt_mod(value)}")
    formula = " ".join([str(r.natural)] + [fmt_mod(v) for _, v in r.parts if v])
    lines.append(f"Итого: {formula} = <b>{r.total}</b>")
    if r.natural == 20:
        lines.append("🔥 Натуральная 20!")
    elif r.natural == 1:
        lines.append("💀 Натуральная 1!")
    return "\n".join(lines)


def fmt_roll(name: str, roll: rules.DiceRoll) -> str:
    lines = [f"🎲 <b>{esc(name)}</b> — {esc(roll.expr)}"]
    for t in roll.terms:
        if t.rolls:
            sign = "−" if t.value < 0 else ""
            extra = f" = {abs(t.value)}" if len(t.rolls) > 1 else ""
            lines.append(f"{sign}{t.label}: [{', '.join(map(str, t.rolls))}]{extra}")
        else:
            # константа (+3, -1) — выводим отдельной строкой
            val = t.value
            label = "постоянный бонус" if val > 0 else "постоянный штраф" if val < 0 else "константа"
            lines.append(f"{esc(label)}: {val:+d}")
    if len(roll.terms) == 1:
        lines.append(f"Итого: <b>{roll.total}</b>")
    else:
        formula = " ".join(f"{t.value}" if i == 0 else f"{t.value:+d}" for i, t in enumerate(roll.terms))
        lines.append(f"Итого: {formula} = <b>{roll.total}</b>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

async def _log_d20(message: Message, c: dict, command: str, title: str, r: rules.D20Result) -> None:
    """Пишет бросок d20-проверки в журнал (молча, ошибки не роняют команду)."""
    try:
        await db.log_roll(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            username=message.from_user.username,
            char_name=c["name"],
            command=command,
            label=title,
            detail=", ".join(f"{l} {v:+d}" for l, v in r.parts) or None,
            total=r.total,
            natural=r.natural,
        )
    except Exception:  # noqa: BLE001 — журнал не должен ломать бросок
        pass


@router.message(Command("roll", "r"))
async def cmd_roll(message: Message, command: CommandObject, bot: Bot):
    if message.from_user is None:
        return
    words, mode, bonus = rules.parse_check_args(command.args or "d20")
    expr = words or "d20"
    try:
        roll = rules.roll_expression(expr)
    except ValueError as e:
        await message.reply(str(e))
        return

    c = await db.get_character(message.from_user.id)
    name = c["name"] if c and c["status"] == "approved" else message.from_user.full_name

    # Преимущество/помеха имеют смысл только для броска одного d20.
    is_d20 = len(roll.terms) == 1 and bool(roll.terms[0].rolls) and roll.terms[0].label == "1d20"

    if mode != "normal" and is_d20:
        rolls, idx = rules.roll_d20(mode)
        natural = rolls[idx]
        total = natural + bonus
        shown = ", ".join(
            f"<b>{v}</b>" if i == idx else f"<s>{v}</s>" for i, v in enumerate(rolls)
        )
        label = "преимущество" if mode == "adv" else "помеха"
        lines = [f"🎲 <b>{esc(name)}</b> — d20", f"2d20 ({label}): {shown}"]
        if bonus:
            lines.append(f"Доп. бонус: {bonus:+d}")
        formula = str(natural) + (f" {bonus:+d}" if bonus else "")
        lines.append(f"Итого: {formula} = <b>{total}</b>")
        if natural == 20:
            lines.append("🔥 Натуральная 20!")
        elif natural == 1:
            lines.append("💀 Натуральная 1!")
    else:
        natural = None
        total = roll.total + bonus
        lines = [fmt_roll(name, roll)]
        if mode != "normal":
            lines.append("⚠️ Преимущество/помеха применимы только к d20 — бросок сделан обычным.")
        if bonus:
            lines.append(f"Доп. бонус: {bonus:+d}")
            base_formula = str(roll.total)
            lines.append(f"Итого: {base_formula} {bonus:+d} = <b>{total}</b>")
        else:
            lines.append(f"Итого: <b>{total}</b>")

    await message.reply("\n".join(lines))

    try:
        await db.log_roll(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            username=message.from_user.username,
            char_name=c["name"] if c and c["status"] == "approved" else None,
            command="roll",
            label=expr,
            detail=None,
            total=total,
            natural=natural,
        )
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("check", "ch"))
async def cmd_check(message: Message, command: CommandObject, bot: Bot):
    words, mode, bonus = rules.parse_check_args(command.args or "")
    if not words:
        await message.reply("Укажи навык, например: <code>/check Скрытность</code>\nСписок: /skills, справка: /help")
        return
    c = await load_approved(message, bot)                      # шаги 1–2
    if c is None:
        return
    target, hints = rules.resolve_check(words)                 # шаг 3: навык → характеристика
    if target is None:
        text = f"Не знаю навыка «{esc(words)}»."
        if hints:
            text += " Возможно, ты имел в виду: " + ", ".join(hints)
        await message.reply(text + "\nВсе названия: /skills")
        return
    result = rules.skill_check(c, target, mode, bonus)         # шаги 4–8
    await message.reply(fmt_d20(c["name"], result))            # шаг 9
    await _log_d20(message, c, "check", result.title, result)


@router.message(Command("save", "sv"))
async def cmd_save(message: Message, command: CommandObject, bot: Bot):
    words, mode, bonus = rules.parse_check_args(command.args or "")
    if not words:
        await message.reply("Укажи характеристику, например: <code>/save Ловкость</code>")
        return
    c = await load_approved(message, bot)
    if c is None:
        return
    ability = rules.resolve_ability(words)
    if ability is None:
        await message.reply("Не знаю такой характеристики. Есть: " + ", ".join(n for n, _ in ABILITIES.values()))
        return
    result = rules.saving_throw(c, ability, mode, bonus)
    await message.reply(fmt_d20(c["name"], result))
    await _log_d20(message, c, "save", result.title, result)


@router.message(Command("init"))
async def cmd_init(message: Message, command: CommandObject, bot: Bot):
    _, mode, bonus = rules.parse_check_args(command.args or "")
    c = await load_approved(message, bot)
    if c is None:
        return
    result = rules.initiative(c, mode, bonus)
    await message.reply(fmt_d20(c["name"], result))
    await _log_d20(message, c, "init", result.title, result)
    # сохранить в очередь инициативы текущего чата
    try:
        await db.set_initiative(
            message.chat.id, message.from_user.id, c["name"],
            result.total, rules.ability_mod(c["stats"]["dex"]),
        )
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("iti"))
async def cmd_iti(message: Message, command: CommandObject):
    if (command.args or "").strip().lower() in ("clear", "сброс", "сбросить"):
        is_dm = await db.get_dm(message.chat.id, message.from_user.id) is not None
        if (
            message.chat.type != "private"
            and message.from_user.id not in config.ADMIN_IDS
            and not is_dm
        ):
            await message.reply("Очистить инициативу может только Мастер (/dm) или админ.")
            return
        await db.clear_initiative(message.chat.id)
        await db.clear_combat(message.chat.id)
        await message.reply("🌀 Очередь инициативы и активный бой очищены.")
        return
    rows = await db.get_initiative(message.chat.id)
    if not rows:
        await message.reply("Очередь инициативы пуста. Бросай: /init (в группе — за каждого участника).")
        return
    lines = ["⚔️ <b>Порядок хода</b>"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {esc(r['char_name'] or 'Без имени')} — <b>{r['score']}</b>")
    lines.append("\n/iti clear — очистить очередь")
    await message.reply("\n".join(lines))


@router.message(Command("skills"))
async def cmd_skills(message: Message, bot: Bot):
    c = await load_approved(message, bot)
    if c is None:
        return
    pb = rules.effective_prof_bonus(c)
    saves = ", ".join(
        f"{short} {fmt_mod(rules.save_total(c, k))}" + ("✅" if k in c["saves"] else "")
        for k, (_, short) in ABILITIES.items()
    )
    lines = [
        f"📜 <b>{esc(c['name'])}</b> — Посл. {c.get('sequence', 9)}, мастерство + {pb}",
        f"<b>Спасброски:</b> {saves}",
        "<b>Навыки</b> (▫️ нет, ✅ владение, ⭐ компетенция):",
    ]
    for key, (name, ab, _) in SKILLS.items():
        mult = c["skills"].get(key, 0)
        mark = "⭐" if mult >= 2 else "✅" if mult == 1 else "▫️"
        lines.append(f"{mark} {name} ({ABILITIES[ab][1]}): {fmt_mod(rules.skill_total(c, key))}")
    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# Журнал последних бросков
# ---------------------------------------------------------------------------

@router.message(Command("last"))
async def cmd_last(message: Message):
    limit = 10
    if message.chat.type == "private":
        rows = await db.last_rolls(None, message.from_user.id, limit)
        pre = "Последние броски"
    else:
        rows = await db.last_rolls(message.chat.id, None, limit)
        pre = "Последние броски в чате"
    if not rows:
        await message.reply(f"{pre}: пока пусто.")
        return

    lines = [f"🕘 <b>{pre}</b>"]
    for r in rows:
        who = r["char_name"] or ("@" + r["username"] if r["username"] else str(r["user_id"]))
        label = r["label"] or r["command"]
        total = f" = {r['total']}" if r["total"] is not None else ""
        crit = " 🔥" if r.get("command") in ("check", "save", "init") and r.get("natural") == 20 else ""
        lines.append(f"{esc(who)} · {esc(label)}{total}{crit}")
    await message.reply("\n".join(lines))
