"""Бой, здоровье, состояния, отдых и ячейки духовности LotM.

Работает и в группах, и в личке. В группе здоровье/бой ведёт Мастер (DM) или владелец анкеты.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import db
import h_perks
import lotm_data as lotm
import rules
from ui import esc

router = Router()

HELP = (
    "⚔️ <b>Бой и здоровье</b>\n"
    "/hp — твоё здоровье, временные ОЗ, Защита и ячейки духовности\n"
    "/hp -5 — получить 5 урона (временные ОЗ поглощаются первыми)\n"
    "/hp +3 — вылечить 3 (не выше максимума)\n"
    "/hp 7 — установить текущее здоровье равным 7\n"
    "/temp 5 — временные ОЗ (заменяют, если больше текущего значения)\n"
    "/death — бросок на спасение от смерти (при 0 ОЗ)\n\n"
    "🧪 <b>Состояния</b>\n"
    "/cond отравлен — повесить состояние\n"
    "/cond -отравлен — снять состояние (префикс «-»)\n"
    "/cond — список текущих состояний и доступных названий\n\n"
    "🔮 <b>Ячейки духовности</b>\n"
    "/slots — сколько ячеек осталось\n"
    "/slots 3 -1 — потратить 1 ячейку ранга Р3\n"
    "/slots 3 +1 — вернуть 1 ячейку ранга Р3\n\n"
    "⏳ <b>Отдых</b>\n"
    "/rest short — медитация: 1d8+ТЕЛ ОЗ и реген духовности\n"
    "/rest long — ритуал: полное восстановление ОЗ, зарядов боя и ячеек\n"
    "🩸 Кровавые фракции (Сангвинар, Вурдалак) лечатся от крови,\n"
    "🏹 Охотники — от перевязки/зелий (тот же /rest, другой текст).\n\n"
    "🛡️ <b>Мастеру</b> (в группе)\n"
    "/dm @username — добавить Мастера (контроль здоровья всех)\n"
    "/dm list — список Мастеров; /dm -@username — убрать\n\n"
    "⚔️ <b>Полноценный бой</b> (Мастер или /dm)\n"
    "/init — каждый боец бросает очерёдность хода\n"
    "/iti — порядок хода\n"
    "/encounter start — начать бой (раунд 1, первым ходит самый быстрый)\n"
    "/encounter next (или /next) — передать ход следующему\n"
    "/encounter — статус боя, кто ходит и здоровье всех\n"
    "/encounter end — завершить бой и очистить очередь"
)


@router.callback_query(F.data == "noop")
async def cb_noop_combat(cb: CallbackQuery):
    await cb.answer()


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _is_id(s: str) -> bool:
    return bool(s) and s.lstrip("-").isdigit()


def _split_target(args: str | None) -> tuple[str | None, str]:
    parts = (args or "").strip().split(None, 1)
    if not parts:
        return None, ""
    head = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    if head.startswith("@") or (_is_id(head) and bool(tail)):
        return head, tail
    return None, (args or "").strip()


async def target_for(message: Message, target: str | None) -> dict | None:
    """Персонаж по явному таргету (Мастер) или свой."""
    if target:
        arg = target.lstrip("@")
        c = await db.get_character(int(arg)) if _is_id(arg) else await db.get_character_by_username(arg)
        if c is None or c["status"] != "approved":
            await message.reply(f"Не нашёл одобренную анкету по {esc(target)}.")
            return None
        if not await can_control(message, c):
            await message.reply(f"Нет прав на управление персонажем {esc(c['name'])} ({esc(target)}).")
            return None
        return c
    return await target_character(message, None)


async def target_character(message: Message, args: str | None) -> dict | None:
    """Персонаж для команды /hp: @username, ID (в т.ч. отрицательный NPC), либо свой."""
    arg = (args or "").strip().lstrip("@")
    if arg and _is_id(arg):
        c = await db.get_character(int(arg))
    elif arg:
        c = await db.get_character_by_username(arg.lstrip("@"))
    else:
        c = await db.get_character(message.from_user.id)
    if c is None or c["status"] != "approved":
        await message.reply("Не нашёл одобренную анкету. Укажи: /hp @username или ID")
        return None
    return c


async def can_control(message: Message, c: dict) -> bool:
    """Своим персонажем управляет владелец, глобальный админ или Мастер (DM) чата."""
    if message.from_user.id == c["user_id"]:
        return True
    if message.chat.type != "private":
        if message.from_user.id in config.ADMIN_IDS:
            return True
        if await db.get_dm(message.chat.id, message.from_user.id):
            return True
    return False


async def can_heal_or_buff(message: Message, c: dict) -> tuple[bool, str]:
    """Проверка прав на лечение, восстановление ресурсов, отдых и снятие состояний."""
    if message.from_user.id in config.ADMIN_IDS:
        return True, ""
    if message.chat.type != "private" and await db.get_dm(message.chat.id, message.from_user.id):
        return True, ""
    if message.from_user.id != c["user_id"]:
        return False, "Управлять ресурсами другого персонажа может только Мастер (/dm) или админ."
    if await db.is_in_combat(c["user_id"]):
        return False, "Во время активного боя лечиться, отдыхать и восстанавливать ресурсы без Мастера (/dm) нельзя!"
    if message.chat.type != "private" and await db.get_dms(message.chat.id):
        return False, "В этом чате есть Мастер (/dm) — лечение, отдых и снятие состояний проводит он."
    return True, ""


# ---------------------------------------------------------------------------
# HP / temp / death
# ---------------------------------------------------------------------------

@router.message(Command("hp"))
async def cmd_hp(message: Message, command: CommandObject):
    args = command.args
    target, rest = _split_target(args)
    parts = rest.split(maxsplit=1)
    first = parts[0] if parts else ""

    target_explicit = target is not None
    if target_explicit:
        c = await target_for(message, target)
        if c is None:
            return
        delta = parse_int(first) if first else None
    else:
        c = await target_character(message, None)
        if c is None:
            return
        delta = parse_int(first) if first else None

    set_value: int | None = None
    if delta is not None and first and not first.startswith(("-", "+")):
        set_value, delta = delta, 0

    hp = int(c.get("hp") or 0)
    max_hp = int(c.get("max_hp") or 0)
    temp_hp = int(c.get("temp_hp") or 0)

    if delta is not None and not await can_control(message, c):
        await message.reply("Здоровье другого персонажа меняет его владелец или Мастер (/dm).")
        return

    if delta is None:
        filled = int(hp * 10 / max_hp) if max_hp else 0
        bar = "█" * max(0, filled) + "░" * (10 - max(0, filled))
        await message.reply(
            f"❤️ <b>{esc(c['name'])}</b>\n"
            f"Здоровье: <b>{hp}</b>/{max_hp}"
            + (f" (+ {temp_hp} врем.)" if temp_hp else "")
            + f"\nЗащита: <b>{c.get('ac', 10) + h_perks.ac_bonus(c)}</b>\n"
            + (f"[{bar}]" if max_hp else "")
        )
        return

    if delta < 0:
        react = await h_perks.react_half_damage(c, -delta)
        if react is not None:
            cut, cut_msg = react
        else:
            cut, cut_msg = None, None
        amount = cut if cut is not None else -delta
        new_hp, new_temp = rules.apply_damage(hp, temp_hp, amount)
        await db.update_character(c["user_id"], hp=new_hp, temp_hp=new_temp)
        lines = [f"💥 <b>{esc(c['name'])}</b> получил {amount} урона"]
        if cut_msg:
            lines.append(cut_msg)
        if temp_hp and new_temp < temp_hp:
            lines.append(f"Временные ОЗ поглотили часть: осталось {new_temp}.")
        lines.append(f"Здоровье: <b>{new_hp}</b>/{max_hp}")
        if new_hp <= 0:
            lines.append("💀 <b>0 ОЗ!</b> Бросай /death.")
        await message.reply("\n".join(lines))

    elif delta > 0:
        ok, err = await can_heal_or_buff(message, c)
        if not ok:
            await message.reply(err)
            return
        new_hp = rules.apply_heal(hp, max_hp, delta)
        uses = dict(c.get("lair_uses") or {})
        if new_hp > 0 and ("death_succ" in uses or "death_fail" in uses):
            uses.pop("death_succ", None)
            uses.pop("death_fail", None)
            await db.update_character(c["user_id"], hp=new_hp, lair_uses=uses)
        else:
            await db.update_character(c["user_id"], hp=new_hp)
        await message.reply(
            f"✨ <b>{esc(c['name'])}</b> исцелён на {delta}\nЗдоровье: <b>{new_hp}</b>/{max_hp}"
        )
    elif delta == 0 and set_value is not None:
        if set_value > hp:
            ok, err = await can_heal_or_buff(message, c)
            if not ok:
                await message.reply(err)
                return
        elif not await can_control(message, c):
            await message.reply("Здоровье другого персонажа меняет его владелец или Мастер (/dm).")
            return
        new_hp = max(0, min(max_hp, set_value))
        uses = dict(c.get("lair_uses") or {})
        if new_hp > 0 and ("death_succ" in uses or "death_fail" in uses):
            uses.pop("death_succ", None)
            uses.pop("death_fail", None)
            await db.update_character(c["user_id"], hp=new_hp, lair_uses=uses)
        else:
            await db.update_character(c["user_id"], hp=new_hp)
        await message.reply(
            f"❤️ <b>{esc(c['name'])}</b>: здоровье установлено <b>{new_hp}</b>/{max_hp}"
        )
    else:
        await message.reply(f"Здоровье без изменений: <b>{hp}</b>/{max_hp}")


@router.message(Command("temp"))
async def cmd_temp(message: Message, command: CommandObject):
    target, rest = _split_target(command.args)
    amount = parse_int(rest)
    c = await (target_for(message, target) if target else target_character(message, None))
    if c is None:
        return
    if amount is None:
        await message.reply(f"🛡️ <b>{esc(c['name'])}</b>: временные ОЗ {c.get('temp_hp', 0)}")
        return
    ok, err = await can_heal_or_buff(message, c)
    if not ok:
        await message.reply(err)
        return
    new_temp = rules.apply_temp_hp(int(c.get("temp_hp") or 0), amount)
    await db.update_character(c["user_id"], temp_hp=new_temp)
    await message.reply(f"🛡️ <b>{esc(c['name'])}</b>: временные ОЗ теперь <b>{new_temp}</b>")


@router.message(Command("death"))
async def cmd_death(message: Message, command: CommandObject | None = None):
    target, _ = _split_target(command.args if command else None)
    c = await (target_for(message, target) if target else target_character(message, None))
    if c is None:
        return
    hp = int(c.get("hp") or 0)
    if hp > 0:
        await message.reply("Персонаж не при смерти — сначала снизь здоровье до 0 (/hp -N).")
        return
    if not await can_control(message, c):
        await message.reply("Спасбросок смерти другого персонажа бросает его владелец или Мастер (/dm).")
        return

    uses = dict(c.get("lair_uses") or {})
    succ = int(uses.get("death_succ", 0))
    fail = int(uses.get("death_fail", 0))
    rolls, _ = rules.roll_d20("normal")
    n = rolls[0]

    if n == 20:
        uses.pop("death_succ", None)
        uses.pop("death_fail", None)
        await db.update_character(c["user_id"], hp=1, lair_uses=uses)
        await message.reply(f"🎲 <b>{esc(c['name'])}</b>: 20! Чудесное спасение — поднимается с 1 ОЗ 🔥")
        return

    if n == 1:
        fail += 2
    elif n >= 10:
        succ += 1
    else:
        fail += 1

    if succ >= 3:
        uses.pop("death_succ", None)
        uses.pop("death_fail", None)
        await db.update_character(c["user_id"], hp=1, lair_uses=uses)
        await message.reply(f"🎲 <b>{esc(c['name'])}</b>: {n} — 3 успеха! Персонаж стабилизирован и приходит в себя с 1 ОЗ ✅")
    elif fail >= 3:
        uses["death_succ"] = succ
        uses["death_fail"] = fail
        await db.update_character(c["user_id"], lair_uses=uses)
        await message.reply(f"🎲 <b>{esc(c['name'])}</b>: {n} — 3 провала спасброска от смерти. Персонаж погиб 💀")
    else:
        uses["death_succ"] = succ
        uses["death_fail"] = fail
        await db.update_character(c["user_id"], lair_uses=uses)
        mark = "✅ успех" if n >= 10 else ("💀 два провала" if n == 1 else "❌ провал")
        await message.reply(
            f"🎲 <b>{esc(c['name'])}</b>: {n} ({mark})\n"
            f"Спасброски от смерти: успехов <b>{succ}/3</b> · провалов <b>{fail}/3</b>"
        )


    # ---------------------------------------------------------------------------
# Состояния
# ---------------------------------------------------------------------------

@router.message(Command("cond", "condition"))
async def cmd_cond(message: Message, command: CommandObject):
    target, rest = _split_target(command.args)
    c = await (target_for(message, target) if target else target_character(message, None))
    if c is None:
        return

    arg = rest.strip()
    cur = set(c.get("conditions") or [])

    if not arg:
        active = ", ".join(rules.CONDITIONS[k] for k in cur if k in rules.CONDITIONS) or "нет"
        await message.reply(
            f"🧪 <b>{esc(c['name'])}</b> — состояния: {active}\n\n"
            "Доступные: " + ", ".join(rules.CONDITIONS.values())
        )
        return

    remove = arg.startswith("-")
    key = rules.resolve_condition(arg.lstrip("-"))
    if key is None:
        await message.reply("Не знаю такого состояния. Доступные: " + ", ".join(rules.CONDITIONS.values()))
        return

    if remove:
        ok, err = await can_heal_or_buff(message, c)
        if not ok:
            await message.reply(err)
            return
        cur.discard(key)
    else:
        if not await can_control(message, c):
            await message.reply("Вешать состояния на другого персонажа может его владелец или Мастер (/dm).")
            return
        cur.add(key)
    await db.update_character(c["user_id"], conditions=sorted(cur))
    name = rules.CONDITIONS[key]
    action = "снято" if remove else "наложено"
    await message.reply(f"🧪 <b>{esc(c['name'])}</b>: «{name}» {action}.")


# ---------------------------------------------------------------------------
# Ячейки духовности
# ---------------------------------------------------------------------------

@router.message(Command("slots", "spells"))
async def cmd_slots(message: Message, command: CommandObject):
    target, rest = _split_target(command.args)
    c = await (target_for(message, target) if target else target_character(message, None))
    if c is None:
        return
    args = rest.split()
    slots = {str(k): dict(v) for k, v in (c.get("spell_slots") or {}).items()}

    if not args:
        if not slots:
            await message.reply(
                f"🔮 <b>{esc(c['name'])}</b>: ячеек духовности нет "
                "(способности Посл. 9 применяются бесплатно)."
            )
            return
        lines = [f"🔮 <b>{esc(c['name'])}</b> — ячейки духовности"]
        for rank in sorted(slots, key=lambda x: int(x) if str(x).isdigit() else 99):
            s = slots[rank]
            filled = "◼" * s["left"] + "◻" * max(0, s["total"] - s["left"])
            lines.append(f"Р{rank}: {filled} {s['left']}/{s['total']}")
        await message.reply("\n".join(lines))
        return

    if len(args) != 2:
        await message.reply("Формат: /slots 3 -1 (потратить) или /slots 3 +1 (вернуть)")
        return
    rank, delta_s = args
    if not rank.isdigit():
        await message.reply("Ранг ячейки — число, например: /slots 3 -1")
        return
    rank = str(int(rank))
    delta = parse_int(delta_s)
    if delta is None:
        await message.reply("Изменение: +N или -N, например: /slots 2 -1")
        return

    entry = slots.get(rank)
    if entry is None:
        await message.reply(f"У {esc(c['name'])} нет ячеек ранга Р{rank}.")
        return
    if delta > 0:
        ok, err = await can_heal_or_buff(message, c)
        if not ok:
            await message.reply(err)
            return
    elif not await can_control(message, c):
        await message.reply("Ячейки другого персонажа меняет его владелец или Мастер (/dm).")
        return
    new_left = entry["left"] + delta
    if new_left < 0 or new_left > entry["total"]:
        await message.reply(
            f"Нельзя: ячейки Р{rank} в пределах 0…{entry['total']} (сейчас {entry['left']})."
        )
        return
    slots[rank]["left"] = new_left
    await db.update_character(c["user_id"], spell_slots=slots)
    await message.reply(
        f"🔮 <b>{esc(c['name'])}</b>: ячейки Р{rank} — {new_left}/{entry['total']}"
    )


# ---------------------------------------------------------------------------
# Отдых (механика LotM без костей хитов D&D)
# ---------------------------------------------------------------------------

@router.message(Command("rest"))
async def cmd_rest(message: Message, command: CommandObject):
    target, rest = _split_target(command.args)
    arg = rest.strip().lower()
    c = await (target_for(message, target) if target else target_character(message, None))
    if c is None:
        return

    ok, err = await can_heal_or_buff(message, c)
    if not ok:
        await message.reply(err)
        return

    if arg in ("long", "длин", "длинный"):
        rest_data = rules.long_rest(c)
        max_hp = int(rest_data["max_hp"])
        await db.update_character(
            c["user_id"],
            max_hp=max_hp,
            hp=max_hp,
            temp_hp=0,
            spell_slots=rest_data["spell_slots"],
            lair_uses=h_perks.reset_combat_uses(c),
            sanity=100,
        )
        flavor = _rest_flavor(c)
        await message.reply(
            f"🌙 <b>{esc(c['name'])}</b> {flavor['long']}.\n"
            f"Здоровье: <b>{max_hp}</b>/{max_hp}, духовность и боевые заряды полностью восстановлены."
        )
        return

    if arg in ("short", "корот", "короткий"):
        healed = rules.rest_heal_amount(c)
        max_hp = int(c.get("max_hp") or 0)
        new_hp = rules.apply_heal(int(c.get("hp") or 0), max_hp, healed)

        seq = int(c.get("sequence") or 9)
        feats = list(c.get("feats") or [])
        max_slots = lotm.spell_slots_for(seq, feats)
        slots = {str(k): dict(v) for k, v in (c.get("spell_slots") or {}).items()}
        gained: dict[int, int] = {}

        for rank, regen in rules.spirituality_regen(seq, c.get("faction"), feats).items():
            rk = str(rank)
            default_tot = int(max_slots.get(rank, 0))
            entry = slots.setdefault(rk, {"total": default_tot, "left": 0})
            total = int(entry.get("total") or default_tot)
            entry["total"] = total
            if total > 0:
                before = int(entry.get("left", 0))
                entry["left"] = min(total, before + int(regen))
                if entry["left"] > before:
                    gained[rank] = entry["left"] - before

        await db.update_character(c["user_id"], hp=new_hp, spell_slots=slots)
        flavor = _rest_flavor(c)
        restored = " · ".join(f"Р{r}: + {g}" for r, g in sorted(gained.items()))
        await message.reply(
            f"🔥 <b>{esc(c['name'])}</b> {flavor['short']}.\n"
            f"Восстановлено здоровья: <b>{healed}</b> → <b>{new_hp}</b>/{max_hp}."
            + (f"\n🔮 Духовность: {restored}." if restored else
               "\n🔮 Ячейки духовности уже полны.")
        )
        return

    await message.reply("Формат: /rest short (медитация) или /rest long (ритуал).")


def _rest_flavor(c: dict) -> dict[str, str]:
    """Фразы отдыха с учётом фракции (кровь / перевязка / медитация)."""
    faction = c.get("faction") or ""
    if faction in rules.BLOOD_FACTIONS:
        return {
            "short": "утолил жажду крови — регенерация ускорилась",
            "long": "провёл ночной ритуал на свежей крови",
        }
    if faction == "hunter":
        return {
            "short": "перевязал раны и хлебнул укрепляющего зелья",
            "long": "отдохнул в укреплении Ордена под присмотром целителей",
        }
    return {
        "short": "провёл медитацию, выравнивая поток духовности",
        "long": "завершил ритуал восстановления",
    }


# ---------------------------------------------------------------------------
# Мастер (DM)
# ---------------------------------------------------------------------------

@router.message(Command("dm"))
async def cmd_dm(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if message.chat.type == "private":
        await db.add_dm(message.chat.id, message.from_user.id)
        await message.reply("Ты теперь Мастер (в личке это автоматически).")
        return

    if not arg:
        await message.reply(
            "Использование:\n/dm @username — назначить Мастера\n"
            "/dm -@username — снять\n/dm list — список"
        )
        return

    if arg.lower() == "list":
        dms = await db.get_dms(message.chat.id)
        if not dms:
            await message.reply("Мастеров в этом чате нет.")
            return
        names = []
        for uid in dms:
            c = await db.get_character(uid)
            names.append(esc(c["name"] if c else str(uid)))
        await message.reply("Мастера: " + ", ".join(names))
        return

    is_chat_dm = await db.get_dm(message.chat.id, message.from_user.id) is not None
    if message.from_user.id not in config.ADMIN_IDS and not is_chat_dm:
        await message.reply("Назначать и снимать Мастеров может только администратор или действующий Мастер чата.")
        return

    user = arg[1:].lstrip("@") if arg.startswith("-") else arg.lstrip("@")
    c = await db.get_character_by_username(user)
    if c is None:
        await message.reply(f"Не нашёл одобренную анкету @{esc(user)}.")
        return

    if arg.startswith("-"):
        await db.remove_dm(message.chat.id, c["user_id"])
        await message.reply(f"Снят Мастер: {esc(c['name'])}.")
    else:
        await db.add_dm(message.chat.id, c["user_id"])
        await message.reply(f"Назначен Мастер: {esc(c['name'])}.")


# ---------------------------------------------------------------------------
# Энкаунтер: раунды и очередь ходов (по одному бою на чат)
# ---------------------------------------------------------------------------

async def _can_lead_combat(message: Message) -> bool:
    if message.chat.type == "private":
        return True
    if message.from_user.id in config.ADMIN_IDS:
        return True
    dms = await db.get_dms(message.chat.id)
    if dms:
        return message.from_user.id in dms
    c = await db.get_character(message.from_user.id)
    return c is not None and c["status"] == "approved"


async def _combatant_name(row: dict) -> str:
    name = row.get("char_name") or "Без имени"
    c = await db.get_character(row["user_id"])
    if c:
        return f"{esc(c['name'])} [{c.get('hp', 0)}/{c.get('max_hp', 0)} ОЗ]"
    return esc(name)


# Статусы полной потери хода (Anti-Stunlock)
HARD_CC = {"stunned", "paralyzed", "incapacitated"}


async def _tick_round(rnd: int, order: list) -> None:
    """В начале нового раунда: тик перезарядок и снятие жёсткого контроля."""
    for row in order:
        cid = int(row["user_id"]) if isinstance(row, dict) else int(row)
        c = await db.get_character(cid)
        if not c:
            continue
        uses = dict(c.get("lair_uses") or {})
        changed = False
        # перезарядки умений (cd_<use_key> = абсолютный раунд, до которого нельзя)
        for k in [k for k in uses if str(k).startswith("cd_")]:
            if int(uses[k]) <= rnd:
                uses.pop(k)
                changed = True
        # призывы: тик длительности
        for k in [k for k in uses if str(k).startswith("summ_")]:
            left = int(uses[k]) - 1
            if left <= 0:
                uses.pop(k)
            else:
                uses[k] = left
            changed = True
        # анти-станлок: жёсткий КК длится 1 раунд, затем иммунитет на 1 раунд
        conds = set(c.get("conditions") or [])
        if conds & HARD_CC:
            conds -= HARD_CC
            uses["cc_immunity"] = rnd + 1
            changed = True
        if "cc_immunity" in conds:
            conds.discard("cc_immunity")
            changed = True
        # lair_uses хранит cc_immunity как строковый ключ, значения — int.
        # Работаем по ключу, чтобы не трогать БД при его отсутствии.
        if "cc_immunity" in uses and int(uses.get("cc_immunity", 0)) < rnd:
            uses.pop("cc_immunity", None)
            changed = True
        if changed:
            await db.update_character(cid, conditions=sorted(conds), lair_uses=uses)


async def _do_next(message: Message) -> None:
    combat = await db.get_combat(message.chat.id)
    if not combat:
        await message.reply("Бой не идёт. Начни: /encounter start")
        return
    if not await _can_lead_combat(message):
        await message.reply("Ход передаёт Мастер (/dm) или админ.")
        return
    order = await db.get_initiative(message.chat.id)
    if not order:
        await message.reply("Очередь пуста — бой прерван.")
        return
    rnd = combat["round"]
    turn = combat["turn"] + 1
    new_round = False
    if turn >= len(order):
        rnd += 1
        turn = 0
        new_round = True
    await db.set_combat(message.chat.id, round_n=rnd, turn=turn)
    if new_round:
        await _tick_round(rnd, order)
    cur = order[turn]
    char = await db.get_character(int(cur["user_id"])) if isinstance(cur, dict) else None
    extra = await h_perks.on_turn_start(char) if char else None
    await message.reply(
        f"🎯 <b>Раунд {rnd}</b> — ходит: {await _combatant_name(cur)} ({turn + 1}/{len(order)})"
        + (("\n" + extra) if extra else "")
    )


@router.message(Command("encounter", "fight"))
async def cmd_encounter(message: Message, command: CommandObject):
    arg = (command.args or "").strip().lower()
    order = await db.get_initiative(message.chat.id)
    combat = await db.get_combat(message.chat.id)

    if arg in ("start", "старт", "начать"):
        if not await _can_lead_combat(message):
            await message.reply("Бой начинает Мастер (/dm) или админ.")
            return
        if not order:
            await message.reply("Сначала бросьте очерёдность хода: /init. Затем /encounter start.")
            return
        await db.set_combat(message.chat.id, round_n=1, turn=0)
        all_uids = [int(r["user_id"]) for r in order]
        start_hooks: list[str] = []
        for r in order:
            uid = int(r["user_id"])
            pc = await db.get_character(uid)
            if pc and pc.get("pathway"):
                fresh_uses = h_perks.reset_combat_uses(pc)
                await db.update_character(uid, lair_uses=fresh_uses)
                pc["lair_uses"] = fresh_uses
                enemies = [x for x in all_uids if x != uid]
                mark_msg = await h_perks.on_combat_start(pc, enemies)
                if mark_msg:
                    start_hooks.append(f"<b>{esc(pc['name'])}</b>: {mark_msg}")
        first_char = await db.get_character(int(order[0]["user_id"]))
        turn_hook = await h_perks.on_turn_start(first_char) if first_char else None
        if turn_hook:
            start_hooks.append(turn_hook)
        extra_text = ("\n\n" + "\n".join(start_hooks)) if start_hooks else ""
        await message.reply(
            "⚔️ <b>Бой начался!</b> Раунд 1. Очередь:\n"
            + "\n".join(
                f"{i}. {esc(r['char_name'] or 'Без имени')} — {r['score']}"
                for i, r in enumerate(order, 1)
            )
            + "\n\nПервый ход: " + await _combatant_name(order[0])
            + extra_text
        )
        return

    if arg in ("end", "стоп", "закончить"):
        if not await _can_lead_combat(message):
            await message.reply("Бой завершает Мастер (/dm) или админ.")
            return
        for r in order:
            uid = int(r["user_id"])
            pc = await db.get_character(uid)
            if pc:
                uses = {
                    k: v for k, v in (pc.get("lair_uses") or {}).items()
                    if not str(k).startswith(("cd_", "summ_")) and k != "cc_immunity"
                }
                await db.update_character(uid, lair_uses=uses)
        await db.clear_combat(message.chat.id)
        await db.clear_initiative(message.chat.id)
        await message.reply("🕊️ Бой завершён. Очерёдность хода и боевые перезарядки очищены.")
        return

    if arg in ("next", "дальше", "следующий"):
        await _do_next(message)
        return

    if combat:
        rnd, turn = combat["round"], combat["turn"]
        cur = order[turn] if order and turn < len(order) else None
        lines = [f"⚔️ <b>Бой идёт</b> — раунд {rnd}"]
        if cur:
            lines.append("Ходит: " + await _combatant_name(cur))
            lines.append("\n".join(
                f"{'➤ ' if i == turn else ''}{i + 1}. {await _combatant_name(r)}"
                for i, r in enumerate(order)
            ))
        lines.append("\n/encounter next — следующий ход · /encounter end — завершить")
        await message.reply("\n".join(lines))
    else:
        msg = "Бой ещё не начат."
        if order:
            msg += f"\nОчерёдность хода брошена ({len(order)}): /encounter start"
        else:
            msg += "\nСначала бросьте очерёдность хода: /init"
        await message.reply(msg)


@router.message(Command("next", "turn"))
async def cmd_next(message: Message):
    await _do_next(message)
