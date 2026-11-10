"""Тексты, клавиатуры и карточка анкеты."""
from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import rules
from rules import ABILITIES, SKILLS

log = logging.getLogger(__name__)

STATUS = {
    "draft": "✍️ заполняется",
    "pending": "⏳ на проверке",
    "approved": "✅ одобрена",
    "rejected": "❌ отклонена",
}
STATUS_ICON = {"draft": "✍️", "pending": "⏳", "approved": "✅", "rejected": "❌"}

# Порядок шагов при создании анкеты
# Порядок шагов при создании анкеты
FIELDS = ["name", "bio", "photo_id"]
MECHANICAL = {"stats", "sequence", "pathway", "faction"}  # правка игроком → повторная проверка админом

FIELD_BUTTONS = {
    "name": "Имя", "bio": "Анкета", "photo_id": "Изображение",
}

PROMPTS = {
    "name": "Придумай имя персонажа (2–40 символов). Это имя увидят все в карточке.",
    "bio": "Напиши анкету Потустороннего: внешность, характер, история (кто ты был до Прикосновения и как обрёл Силу). До 7000 символов.",
    "photo_id": "Пришли изображение персонажа (именно как фото, не файлом).",
}


def _calc_ac(c: dict) -> int:
    """КД = 10 + мод. ЛОВ + бонусы КД от черт/пассивок (h_perks.ac_bonus)."""
    st = c.get("stats") or {}
    dex_mod = rules.ability_mod(st.get("dex", 10))
    ac = 10 + dex_mod
    try:
        import h_perks
        ac += h_perks.ac_bonus(c)
    except Exception:
        pass
    return ac


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# --------------------------------------------------------------------------
# Проверка введённых значений
# --------------------------------------------------------------------------

def parse_field(field: str, text: str):
    """-> (значение, None) или (None, текст ошибки)."""
    text = text.strip()
    if field == "name":
        if not 2 <= len(text) <= 40:
            return None, f"Имя должно быть от 2 до 40 символов (сейчас {len(text)})."
        return text, None
    if field == "bio":
        if not 1 <= len(text) <= 7000:
            return None, f"Нужно от 1 до 7000 символов (сейчас {len(text)})."
        return text, None
    return None, "Этот параметр так не меняется."


# --------------------------------------------------------------------------
# Карточка анкеты
# --------------------------------------------------------------------------

def fmt_mod(n: int) -> str:
    """Модификатор для сообщений: '+ 2' / '+ 0' / '− 1' (со знаком и пробелом)."""
    return f"+ {n}" if n >= 0 else f"− {abs(n)}"


def _build_card(c: dict, admin: bool, short: bool) -> str:
    st = c.get("stats") or {}
    try:
        pb = rules.effective_prof_bonus(c)
    except Exception:
        pb = 0
    lines = [
        f"<b>{esc(c['name']) or 'Без имени'}</b>",
        f"{esc(c.get('race') or '') or '—'} · {esc(c.get('char_class') or '') or '—'} · Посл. {c.get('sequence', c.get('level', '—'))} (мастерство {fmt_mod(pb)})",
        f"Статус: {STATUS.get(c['status'], c['status'])}",
    ]
    if admin:
        who = f"@{esc(c['username'])}" if c.get("username") else "без username"
        lines.append(f"Игрок: {who} · <code>{c['user_id']}</code>")
    if c["status"] == "rejected" and c.get("reject_reason"):
        lines.append(f"Причина отказа: {esc(c['reject_reason'])}")

    cells = [
        f"{short_name} <b>{st.get(k, 10)}</b> ({fmt_mod(rules.ability_mod(st.get(k, 10)))})"
        for k, (_, short_name) in ABILITIES.items()
    ]
    lines += ["", " · ".join(cells[:3]), " · ".join(cells[3:])]

    hp_line = f"ОЗ <b>{c.get('hp', 0)}</b>/{c.get('max_hp', 0)} · Защита <b>{_calc_ac(c)}</b>"
    temp = int(c.get("temp_hp") or 0)
    if temp:
        hp_line += f" (+ {temp} временных)"
    lines.append(hp_line)

    slots = c.get("spell_slots") or {}
    if slots:
        # Суммируем ФАКТИЧЕСКИЕ оставшиеся и максимальные ячейки по рангам (Р1/Р2/Р3)
        try:
            per_rank = {}
            for rank, v in slots.items():
                r = int(rank)
                if isinstance(v, dict):
                    per_rank.setdefault(r, [0, 0])
                    per_rank[r][0] += int(v.get("left", 0))
                    per_rank[r][1] += int(v.get("total", 0))
            parts = [f"Р{r} {l}/{t}" for r, (l, t) in sorted(per_rank.items()) if t > 0]
            if parts:
                lines.append("Ячейки духовности: " + " · ".join(parts))
        except Exception:
            pass

    # ---- Блок билда «Повелителя Тайн» ----
    import lotm_data as lotm

    if c.get("pathway"):
        seq = c.get("sequence", 9)
        seq_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(seq, f"Посл. {seq}")
        path = lotm.PATHWAYS.get(c["pathway"], {})
        start = c.get("start_sequence", seq)
        lines.append(
            f"Путь: <b>{esc(path.get('name', c['pathway']))}</b> · "
            f"Посл. <b>{seq}</b> «<b>{esc(seq_title)}</b>» (старт {start}) · мастерство {fmt_mod(rules.sequence_prof_bonus(seq))}"
        )
        lines.append(f"Рассудок: <b>{c.get('sanity', 100)}%</b> · Переваривание: <b>{c.get('digestion', 0)}%</b>")
        choices = c.get("pathway_choices") or {}
        lines.append(f"Открыто умений: {lotm.count_skills(seq)} · веток выбрано: {len(choices)}")
    if c.get("feats"):
        names = [lotm.FEATS[f]["name"] for f in c["feats"] if f in lotm.FEATS]
        if names:
            lines.append("Черты: " + ", ".join(names))

    bio = c["bio"][:400] if short else c["bio"]
    lines += ["", "<b>Анкета</b>", esc(bio) or "—"]
    return "\n".join(lines)


def card_texts(c: dict, admin: bool = False, limit: int = 3800) -> list[str]:
    """Карточка, разбитая на несколько сообщений (полная анкета, без обрезки).

    Разбивает по абзацам («\\n\\n»), чтобы не рвать посреди строки.
    """
    text = _build_card(c, admin, short=False)
    if len(text) <= 4000:
        return [text]
    # Шапка — всё до блока анкеты
    marker = "<b>Анкета</b>"
    idx = text.find(marker)
    if idx == -1:
        # неформатированный текст — режем жёстко
        return [text[i:i + limit] for i in range(0, len(text), limit)]
    head = text[:idx].rstrip()
    bio = text[idx:].strip()
    chunks: list[str] = [head]
    current = ""
    for para in bio.split("\n\n"):
        # Один абзац длиннее лимита — режем жёстко по строкам/символам
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            piece = para[:cut].rstrip()
            if current:
                chunks.append(current)
                current = ""
            if piece:
                chunks.append(piece)
            para = para[cut:].lstrip()
        if not para:
            continue
        piece = (current + "\n\n" + para).strip() if current else para
        if len(piece) > limit and current:
            chunks.append(current)
            current = para
        else:
            current = piece
    if current:
        chunks.append(current)
    # Объединяем мелкие хвосты, чтобы не плодить лишние сообщения
    merged: list[str] = []
    for ch in chunks:
        if merged and len(merged[-1]) + len(ch) + 2 <= limit:
            merged[-1] += "\n\n" + ch
        else:
            merged.append(ch)
    return merged


def card_text(c: dict, admin: bool = False) -> str:
    """Одно сообщение: полная карточка или короткая версия (для edit_text)."""
    text = _build_card(c, admin, short=False)
    if len(text) > 4000:          # лимит Telegram — 4096 символов
        text = _build_card(c, admin, short=True)
    return text


def card_kb(c: dict, viewer_id: int) -> InlineKeyboardMarkup:
    uid = c["user_id"]
    rows = []
    if viewer_id in config.ADMIN_IDS:
        if c["status"] == "pending":
            rows.append([
                btn("✅ Одобрить", f"ap:{uid}"),
                btn("❌ Отклонить", f"rj:{uid}"),
            ])
        edit_row = [btn("✏️ Изменить", f"em:{uid}")]
        edit_row.append(btn("🗑 Удалить", f"del:{uid}"))
        rows.append(edit_row)
    else:
        rows.append([btn("✏️ Изменить", f"em:{uid}")])
        if c["status"] in ("draft", "rejected"):
            rows.append([btn("🔁 Отправить на проверку", f"rs:{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_menu_kb(uid: int) -> InlineKeyboardMarkup:
    items = [btn(label, f"ed:{uid}:{field}") for field, label in FIELD_BUTTONS.items()]
    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    rows.append([btn("⬅️ Назад", f"vw:{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)




# --------------------------------------------------------------------------
# Отправка
# --------------------------------------------------------------------------

async def send_card(bot: Bot, chat_id: int, c: dict, viewer_id: int) -> None:
    """Картинка (если есть) + текст анкеты с кнопками."""
    if c.get("photo_id"):
        try:
            await bot.send_photo(chat_id, c["photo_id"], caption=f"<b>{esc(c['name'])}</b>")
        except TelegramAPIError as e:
            log.warning("Не удалось отправить фото: %s", e)
    texts = card_texts(c, admin=viewer_id in config.ADMIN_IDS)
    kb = card_kb(c, viewer_id)
    for i, part in enumerate(texts):
        markup = kb if i == len(texts) - 1 else None
        msg = await bot.send_message(chat_id, part, reply_markup=markup)
        # Запоминаем карточку, чтобы обновлять её у всех админов после решения
        if viewer_id in config.ADMIN_IDS:
            try:
                await db.save_card_message(c["user_id"], viewer_id, chat_id, msg.message_id)
            except Exception as e:
                log.warning("Не удалось сохранить карточку: %s", e)


async def refresh_cards(bot: Bot, c: dict) -> None:
    """Обновляет текст и кнопки карточки анкеты у всех админов."""
    uid = c["user_id"]
    cards = await db.get_card_messages(uid)
    for row in cards:
        admin_id = int(row["admin_id"])
        try:
            await bot.edit_message_text(
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                text=card_text(c, admin=True),
                reply_markup=card_kb(c, admin_id),
            )
        except TelegramBadRequest:
            pass
        except TelegramAPIError as e:
            log.warning("Не удалось обновить карточку у %s: %s", admin_id, e)


async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:   # например «message is not modified»
        pass


async def notify_admins(bot: Bot, c: dict, title: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"🔔 {esc(title)}")
            await send_card(bot, admin_id, c, admin_id)
        except TelegramAPIError as e:   # админ ещё не нажимал /start у бота и т.п.
            log.warning("Не удалось уведомить админа %s: %s", admin_id, e)


async def notify_admins_action(bot: Bot, actor_user, char_name: str, char_id: int,
                               action_text: str) -> None:
    """Короткое уведомление всем админам, кроме совершившего действие."""
    actor_id = getattr(actor_user, "id", None)
    if getattr(actor_user, "username", None):
        actor_name = f"@{actor_user.username}"
    else:
        actor_name = getattr(actor_user, "full_name", None) or str(actor_id)
    text = (f"🔔 Администратор {actor_name} {action_text} анкету "
            f"«{esc(char_name)}» (ID: <code>{char_id}</code>)")
    for admin_id in config.ADMIN_IDS:
        if int(admin_id) == actor_id:
            continue
        try:
            await bot.send_message(int(admin_id), text)
        except TelegramAPIError as e:
            log.warning("Не удалось уведомить админа %s: %s", admin_id, e)


async def notify_player(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError as e:
        log.warning("Не удалось написать игроку %s: %s", user_id, e)
