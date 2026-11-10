"""Админ-панель: просмотр, одобрение, отклонение, правка и удаление анкет."""
from __future__ import annotations

import re
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import db
import lotm_data as lotm
import rules
import ui
from filters_admin import IsAdmin
from ui import btn, esc

router = Router()
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(IsAdmin())

PAGE_SIZE = 8
LIST_TITLES = {
    "pending": "⏳ На проверке",
    "approved": "✅ Одобренные",
    "rejected": "❌ Отклонённые",
    "all": "📚 Все анкеты",
}


class AdminForm(StatesGroup):
    reason = State()   # ввод причины отказа (data: uid)


class AdminNPCForm(StatesGroup):
    waiting_for_data = State()


async def panel() -> tuple[str, InlineKeyboardMarkup]:
    n = await db.counts()
    total = sum(n.values())
    rows: list[list[InlineKeyboardButton]] = [
        [btn(f"⏳ На проверке ({n.get('pending', 0)})", "ls:pending:0")],
        [
            btn(f"✅ Одобренные ({n.get('approved', 0)})", "ls:approved:0"),
            btn(f"❌ Отклонённые ({n.get('rejected', 0)})", "ls:rejected:0"),
        ],
        [btn(f"📚 Все анкеты ({total})", "ls:all:0")],
        [btn("👾 Добавить врага (NPC)", "adm:add_npc")],
        [btn("👾 Бестиарий / Список NPC", "adm:npc_list")],
    ]
    return (
        "🛠 <b>Админ-панель</b>\nПоиск по имени, @username или ID: /find текст",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


def results_kb(chars: list[dict]) -> list[list[InlineKeyboardButton]]:
    return [
        [btn(f"{ui.STATUS_ICON[c['status']]} {c['name'] or 'Без имени'} — Посл. {c.get('sequence', 9)}", f"open:{c['user_id']}")]
        for c in chars
    ]


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    text, kb = await panel()
    await message.answer(text, reply_markup=kb)


async def notify_other_admins(bot: Bot, actor_id: int, text: str) -> None:
    """Рассылает уведомление о действии модератора всем остальным администраторам."""
    for adm_id in config.ADMIN_IDS:
        if adm_id == actor_id:
            continue
        try:
            await bot.send_message(adm_id, text)
        except Exception:
            pass


@router.callback_query(F.data == "adm")
async def cb_panel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await panel()
    await ui.safe_edit(cb.message, text, kb)
    await cb.answer()


@router.callback_query(F.data.startswith("ls:"))
async def cb_list(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = cb.data.split(":")
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        await cb.answer()
        return
    _, status, page_s = parts
    flt = None if status == "all" else status
    total = await db.count(flt)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(int(page_s), 0), pages - 1)
    chars = await db.list_characters(flt, PAGE_SIZE, page * PAGE_SIZE)

    rows = results_kb(chars)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(btn("◀️", f"ls:{status}:{page - 1}"))
    nav.append(btn(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(btn("▶️", f"ls:{status}:{page + 1}"))
    rows.append(nav)
    rows.append([btn("⬅️ В панель", "adm")])

    title = f"{LIST_TITLES.get(status, 'Анкеты')}: {total}" + ("" if total else "\nПока пусто.")
    await ui.safe_edit(cb.message, title, InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    q = (command.args or "").strip()
    if not q:
        await message.answer("Использование: /find часть имени, @username или ID")
        return
    chars = await db.search(q)
    if not chars:
        await message.answer("Ничего не найдено.")
        return
    await message.answer(
        f"Найдено: {len(chars)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=results_kb(chars)),
    )


@router.callback_query(F.data.startswith("open:"))
async def cb_open(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    c = await db.get_character(int(parts[1]))
    if c is None:
        await cb.answer("Анкета не найдена", show_alert=True)
        return
    await ui.send_card(bot, cb.message.chat.id, c, cb.from_user.id)
    await cb.answer()


@router.callback_query(F.data.startswith("ap:"))
async def cb_approve(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Единоличное одобрение анкеты любым администратором."""
    await state.clear()
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    res = await db.decide_character(uid, cb.from_user.id, "approved")
    if not res.get("ok"):
        err = res.get("error")
        if err == "decided":
            await cb.answer("Решение по анкете уже принято", show_alert=True)
        else:
            await cb.answer("Анкета не найдена", show_alert=True)
        return

    fresh = await db.get_character(uid)
    if fresh is not None:
        await ui.refresh_cards(bot, fresh)
    await ui.notify_player(
        bot,
        uid,
        "🎉 Твоя анкета одобрена! Теперь можно совершать проверки в чате ролевой: /check, /save, /roll. Справка: /help",
    )
    adm_name = esc(cb.from_user.full_name or str(cb.from_user.id))
    char_name = esc(res.get("name") or "Без имени")
    await notify_other_admins(
        bot, cb.from_user.id,
        f"✅ Администратор <b>{adm_name}</b> одобрил анкету «<b>{char_name}</b>» (ID <code>{uid}</code>).",
    )
    await cb.answer("Анкета одобрена ✅")


@router.callback_query(F.data.startswith("rj:"))
async def cb_reject_ask(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    await state.set_state(AdminForm.reason)
    await state.set_data({"uid": uid, "card_msg_id": cb.message.message_id})
    await cb.message.answer(
        "Напиши причину возврата анкеты — игрок её получит.\n/skip — без причины, /cancel — отмена."
    )
    await cb.answer()


async def do_reject(message: Message, state: FSMContext, bot: Bot, reason: str | None):
    data = await state.get_data()
    uid = data.get("uid")
    await state.clear()
    if uid is None:
        await message.answer("Сессия отклонения истекла.")
        return
    res = await db.decide_character(int(uid), message.from_user.id, "rejected", reason)
    if not res.get("ok"):
        if res.get("error") == "decided":
            st = res.get("status")
            await message.answer(
                "Решение по анкете уже принято: "
                + ("✅ одобрена" if st == "approved" else "❌ отклонена") + "."
            )
        else:
            await message.answer("Анкета не найдена (возможно, уже удалена).")
        return

    char_name = esc(res.get("name") or "Без имени")
    await message.answer(f"❌ Анкета «{char_name}» отклонена.")
    text = "❌ Твоя анкета возвращена на доработку."
    if reason:
        text += f"\nПричина: {esc(reason)}"
    text += "\n\nИсправь её через /me → ✏️ Изменить и нажми «🔁 Отправить на проверку»."
    await ui.notify_player(bot, int(uid), text)

    adm_name = esc(message.from_user.full_name or str(message.from_user.id))
    reason_str = f"\nПричина: <i>{esc(reason)}</i>" if reason else ""
    await notify_other_admins(
        bot, message.from_user.id,
        f"❌ Администратор <b>{adm_name}</b> отклонил анкету «<b>{char_name}</b>» (ID <code>{uid}</code>).{reason_str}",
    )

    fresh = await db.get_character(int(uid))
    if fresh is not None:
        await ui.refresh_cards(bot, fresh)


@router.message(AdminForm.reason, Command("skip"))
async def reject_skip(message: Message, state: FSMContext, bot: Bot):
    await do_reject(message, state, bot, None)


@router.message(AdminForm.reason, F.text & ~F.text.startswith("/"))
async def reject_reason(message: Message, state: FSMContext, bot: Bot):
    await do_reject(message, state, bot, message.text.strip()[:500])


@router.callback_query(F.data.startswith("del:"))
async def cb_delete_ask(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Запрос подтверждения удаления анкеты (анкеты админов удаляет только Главный админ)."""
    await state.clear()
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    c = await db.get_character(uid)
    if c is None:
        await cb.answer("Уже удалена", show_alert=True)
        return

    admin_id = cb.from_user.id
    if uid in config.ADMIN_IDS and not config.is_chief(admin_id):
        await cb.answer(
            "Анкеты администраторов может удалять только Главный администратор.",
            show_alert=True,
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("⚠️ Да, удалить", f"delok:{uid}"), btn("Отмена", f"vw:{uid}")]
    ])
    await cb.message.edit_reply_markup(reply_markup=kb)
    await cb.answer("Подтвердите удаление анкеты")


@router.callback_query(F.data.startswith("delok:"))
async def cb_delete_ok(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Финальное подтверждение удаления анкеты."""
    await state.clear()
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    admin_id = cb.from_user.id
    if uid in config.ADMIN_IDS and not config.is_chief(admin_id):
        await cb.answer("Анкеты администраторов может удалять только Главный администратор.", show_alert=True)
        return
    c = await db.get_character(uid)
    if c is None or not await db.delete_character_if(uid, admin_id):
        await cb.answer("Уже удалена", show_alert=True)
        return
    char_name = esc(c.get("name") or "Без имени")
    await ui.safe_edit(cb.message, f"🗑 Анкета «{char_name}» удалена.")
    await ui.notify_player(bot, uid, "Твоя анкета была удалена администратором. Создать новую: /newchar")
    adm_name = esc(cb.from_user.full_name or str(admin_id))
    await notify_other_admins(
        bot, admin_id,
        f"🗑 Администратор <b>{adm_name}</b> удалил анкету «<b>{char_name}</b>» (ID <code>{uid}</code>).",
    )
    await cb.answer("Удалено")


# Быстрое создание врага (NPC) для боевых энкаунтеров
# ---------------------------------------------------------------------------

STAT_KEYS = ("str", "dex", "con", "int", "wis", "cha")

NPC_TEMPLATE = (
    "👾 Введи данные противника одним сообщением или несколькими строками по шаблону:\n\n"
    "<code>Имя: Теневой Охотник\n"
    "Здоровье: 45\n"
    "Защита: 14\n"
    "Посл: 7\n"
    "Путь: hunter\n"
    "Фракция: hunter\n"
    "Характеристики: 14, 16, 14, 10, 12, 8 (СИЛ, ЛОВ, ТЕЛ, ИНТ, МУД, ХАР)\n"
    "Умения: Огненный шар, Выпад клинком</code>\n\n"
    "Всё, что не указано, подставится по умолчанию: Здоровье 30, Защита 10+ЛОВ, Посл. 8, путь и фракция hunter.\n"
    "/cancel — отмена"
)

_FIELD_RE = re.compile(
    r"^\s*(имя|хп|здоровье|оз|кд|защита|посл|путь|фракция|статы|характеристики|скиллы|умения)\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _parse_npc_data(text: str) -> dict:
    """Парсит шаблон NPC построчно. Возвращает словарь разобранных полей."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        m = _FIELD_RE.match(line)
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
    if "имя" not in fields:
        # Одна строка без метки — считаем её именем
        stripped = text.strip().splitlines()[0] if text.strip() else ""
        if stripped and ":" not in stripped:
            fields["имя"] = stripped
    return fields


@router.callback_query(F.data == "adm:add_npc")
async def cb_add_npc(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminNPCForm.waiting_for_data)
    await state.set_data({})
    await cb.message.edit_text(NPC_TEMPLATE)
    await cb.answer()


@router.message(AdminNPCForm.waiting_for_data, Command("cancel"))
async def npc_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Создание NPC отменено.")


@router.message(AdminNPCForm.waiting_for_data, F.text & ~F.text.startswith("/"))
async def npc_save(message: Message, state: FSMContext):
    fields = _parse_npc_data(message.text or "")

    # --- имя (по умолчанию "Противник") ---
    name = fields.get("имя") or "Противник"

    # --- характеристики: 6 чисел СИЛ, ЛОВ, ТЕЛ, ИНТ, МУД, ХАР ---
    stats = dict(db.DEFAULT_STATS)
    raw_stats = fields.get("характеристики") or fields.get("статы") or ""
    if raw_stats:
        nums = re.findall(r"-?\d+", raw_stats)
        if len(nums) >= 6:
            stats = dict(zip(STAT_KEYS, (int(n) for n in nums[:6])))

    # --- Здоровье (по умолчанию 30) ---
    raw_hp = fields.get("здоровье") or fields.get("оз") or fields.get("хп") or ""
    hp = int(re.search(r"\d+", raw_hp).group()) if re.search(r"\d+", raw_hp) else 30

    # --- Защита (по умолчанию 10 + модификатор ЛОВ) ---
    raw_ac = fields.get("защита") or fields.get("кд") or ""
    ac = int(re.search(r"\d+", raw_ac).group()) if re.search(r"\d+", raw_ac) else 10 + (stats["dex"] - 10) // 2

    # --- Последовательность 9..4 (по умолчанию 8) ---
    seq = 8
    if "посл" in fields:
        m = re.search(r"\d+", fields["посл"])
        if m:
            seq = max(4, min(9, int(m.group())))

    # --- путь (валидный ключ PATHWAYS, иначе дефолт) ---
    pathway = "hunter"
    if "путь" in fields:
        key = fields["путь"].strip().lower()
        if key in lotm.PATHWAYS:
            pathway = key

    # --- фракция (ключ FACTIONS, иначе "hunter") ---
    faction = "hunter"
    if "фракция" in fields:
        key = fields["фракция"].strip().lower()
        if key in lotm.FACTIONS:
            faction = key

    # --- умения (список через запятую) ---
    raw_skills = fields.get("умения") or fields.get("скиллы") or ""
    skills = [s.strip() for s in raw_skills.split(",") if s.strip()]

    # --- генерация отрицательного ID ---
    npc_uid = -int(time.time() % 10000000)
    while await db.get_character(npc_uid) is not None:
        npc_uid -= 1

    await db.create_draft(npc_uid, "npc")
    await db.update_character(
        npc_uid,
        name=name,
        stats=stats,
        hp=hp,
        max_hp=hp,
        ac=ac,
        sequence=seq,
        start_sequence=seq,
        pathway=pathway,
        faction=faction,
        status="approved",
        char_class=f"{lotm.path_name(pathway)} {seq}",
        skills={"custom": skills} if skills else {},
    )
    await state.clear()
    await message.answer(
        f"✅ Враг «{esc(name)}» (ID: {npc_uid}) успешно создан! "
        f"Здоровье: {hp}, Защита: {ac}. Он доступен в /encounter и /hp.",
    )


# ---------------------------------------------------------------------------
# Бестиарий: список NPC, карточка, удаление
# ---------------------------------------------------------------------------

NPC_EMPTY_TEXT = "Бестиарий пуст. Создайте первого врага через кнопку добавления."


def _npc_line(c: dict) -> str:
    return (
        f"👾 {c.get('name') or 'Без имени'} (ID: {c['user_id']}) | "
        f"Здоровье: {c.get('hp', 0)}/{c.get('max_hp', 0)} | Защита: {c.get('ac', 0)}"
    )


def _npc_card(c: dict) -> str:
    stats = c.get("stats") or {}
    stat_line = ", ".join(
        f"{rules.ABILITIES[k][1]} {stats.get(k, 10)}"
        for k in STAT_KEYS
        if k in rules.ABILITIES
    )
    skills = c.get("skills") or {}
    if isinstance(skills, dict):
        raw = skills.get("custom") or []
        skill_line = ", ".join(str(s) for s in raw) if raw else "—"
    else:
        skill_line = ", ".join(str(s) for s in skills) or "—"
    return (
        "👾 <b>Враг</b>\n"
        f"<b>Имя:</b> {esc(c.get('name') or 'Без имени')}\n"
        f"<b>ID:</b> {c['user_id']}\n"
        f"<b>Путь:</b> {esc(lotm.path_name(c.get('pathway') or ''))}\n"
        f"<b>Последовательность:</b> {c.get('sequence') or 9}\n"
        f"<b>Фракция:</b> {esc(c.get('faction') or '—')}\n"
        f"<b>Здоровье:</b> {c.get('hp', 0)}/{c.get('max_hp', 0)}\n"
        f"<b>Защита:</b> {c.get('ac', 0)}\n"
        f"<b>Характеристики:</b> {stat_line}\n"
        f"<b>Умения:</b> {esc(skill_line)}"
    )


@router.callback_query(F.data == "adm:npc_list")
async def cb_npc_list(cb: CallbackQuery):
    total = await db.count_npcs()
    if not total:
        await cb.message.edit_text(NPC_EMPTY_TEXT, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[btn("🛠 Админ-панель", "adm")]]
        ))
        await cb.answer()
        return

    pages = max(1, -(-total // PAGE_SIZE))
    page = 0
    npcs = await db.list_npcs(PAGE_SIZE, 0)

    rows = [
        [btn(_npc_line(c), f"adm:view_npc:{c['user_id']}")]
        for c in npcs
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(btn("◀️", f"adm:npc_list:{page - 1}"))
    nav.append(btn(f"{page + 1}/{pages}", "adm:npc_list:0"))
    if page + 1 < pages:
        nav.append(btn("▶️", f"adm:npc_list:{page + 1}"))
    rows.append(nav)
    rows.append([btn("🛠 Админ-панель", "adm")])

    await cb.message.edit_text(
        f"👾 <b>Бестиарий</b> — всего противников: {total}\nВыбери врага:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:npc_list:"))
async def cb_npc_page(cb: CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        await cb.answer()
        return
    page = int(parts[2])
    total = await db.count_npcs()
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    npcs = await db.list_npcs(PAGE_SIZE, page * PAGE_SIZE)

    rows = [
        [btn(_npc_line(c), f"adm:view_npc:{c['user_id']}")]
        for c in npcs
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(btn("◀️", f"adm:npc_list:{page - 1}"))
    nav.append(btn(f"{page + 1}/{pages}", f"adm:npc_list:{page}"))
    if page + 1 < pages:
        nav.append(btn("▶️", f"adm:npc_list:{page + 1}"))
    rows.append(nav)
    rows.append([btn("🛠 Админ-панель", "adm")])

    await cb.message.edit_text(
        f"👾 <b>Бестиарий</b> — всего противников: {total}\nВыбери врага:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:view_npc:"))
async def cb_view_npc(cb: CallbackQuery):
    raw = cb.data.rsplit(":", 1)[-1]
    try:
        uid = int(raw)
    except ValueError:
        await cb.answer()
        return
    c = await db.get_character(uid)
    if c is None or uid >= 0:
        await cb.answer("NPC не найден", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("⚔️ Добавить в бой", f"adm:npc_join:{uid}")],
        [btn("🗑 Удалить NPC", f"adm:npc_del:{uid}")],
        [btn("« Назад в бестиарий", "adm:npc_list")],
    ])
    await cb.message.edit_text(_npc_card(c), reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:npc_join:"))
async def cb_npc_join(cb: CallbackQuery):
    raw = cb.data.rsplit(":", 1)[-1]
    try:
        uid = int(raw)
    except ValueError:
        await cb.answer()
        return
    c = await db.get_character(uid)
    if c is None or uid >= 0:
        await cb.answer("NPC не найден", show_alert=True)
        return
    await cb.answer(
        f"Добавь его в бой: /encounter add_npc {uid}",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("adm:npc_del:"))
async def cb_npc_del(cb: CallbackQuery):
    raw = cb.data.rsplit(":", 1)[-1]
    try:
        uid = int(raw)
    except ValueError:
        await cb.answer()
        return
    c = await db.get_character(uid)
    if c is None or uid >= 0:
        await cb.answer("NPC не найден", show_alert=True)
        return
    await db.delete_character(uid)
    await cb.message.edit_text(
        f"🗑 NPC «{esc(c.get('name') or 'Без имени')}» (ID: {uid}) удалён.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[btn("« Назад в бестиарий", "adm:npc_list")]]
        ),
    )
    await cb.answer("NPC удалён")
