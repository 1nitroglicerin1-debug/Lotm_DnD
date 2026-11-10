"""Магазин Святилища Серого Тумана (Telegram Stars XTR): Ходы, Печати Судьбы, Артефакты и VIP."""
from __future__ import annotations

import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import config
import db
import h_perks
import lotm_data as lotm
import rules
import ui
from filters_admin import is_admin
from ui import btn, esc

router = Router()

ARTIFACT_FEATS = (
    "sealed_artifact_attack",
    "sealed_artifact_defense",
    "historical_void_walk",
    "otherworldly_sense",
)

PACKS: dict[str, dict] = {
    "turns_40": {
        "title_ru": "🕯 40 Ходов Духовности",
        "title_en": "🕯 40 Spirituality Turns",
        "desc_ru": "Мгновенно добавляет +40 сюжетных ходов сверх дневного лимита.",
        "desc_en": "Instantly adds +40 story turns on top of your daily allowance.",
        "stars": 65,
        "turns": 40,
        "fate": 0,
        "heal": False,
        "artifact": False,
        "vip_days": 0,
    },
    "fate_pack": {
        "title_ru": "🎲 5 Печатей Судьбы + Очищение Рассудка",
        "title_en": "🎲 5 Fate Rerolls + Sanity Cleansing",
        "desc_ru": "5 Печатей для переброса проваленных бросков d20 с Преимуществом (2d20) + 100% ОЗ и Рассудка.",
        "desc_en": "5 Fate Seals to reroll failed d20 checks with Advantage (2d20) + 100% HP and Sanity restoration.",
        "stars": 55,
        "turns": 10,
        "fate": 5,
        "heal": True,
        "artifact": False,
        "vip_days": 0,
    },
    "artifact_box": {
        "title_ru": "🏺 Реликвия: Запечатанный Артефакт",
        "title_en": "🏺 Relic: Sealed Artifact",
        "desc_ru": "Открывает на выбор 1 мистический Запечатанный Артефакт (или Черту) в профиль + 25 ходов.",
        "desc_en": "Unlocks 1 canonical Sealed Artifact (or Beyonder Feat) of your choice + 25 story turns.",
        "stars": 110,
        "turns": 25,
        "fate": 1,
        "heal": False,
        "artifact": True,
        "vip_days": 0,
    },
    "vip_30": {
        "title_ru": "👑 Святилище Серого Тумана (VIP 30 дней)",
        "title_en": "👑 Gray Fog Sanctuary Pass (30 Days)",
        "desc_ru": "80 ходов КАЖДЫЙ ДЕНЬ, двойная глубина памяти летописи ИИ, +3 Печати Судьбы и полное исцеление!",
        "desc_en": "80 turns EVERY DAY, double AI chronicle memory depth, +3 Fate Seals & full restoration!",
        "stars": 390,
        "turns": 80,
        "fate": 3,
        "heal": True,
        "artifact": False,
        "vip_days": 30,
    },
}


def shop_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    is_en = lang == "en"
    rows: list[list[InlineKeyboardButton]] = []
    for pid, p in PACKS.items():
        title = p["title_en"] if is_en else p["title_ru"]
        rows.append([btn(f"{title} — {p['stars']} ⭐️", f"shop:buy:{pid}")])
    if config.TRIBUTE_LINK:
        card_label = "💳 Pay with Card (International)" if is_en else "💳 Оплатить картой (Tribute)"
        rows.append([InlineKeyboardButton(text=card_label, url=config.TRIBUTE_LINK)])
    ref_label = "🃏 Invite a Friend (+10 ⚡ & +1 🎲 FREE)" if is_en else "🃏 Клуб Таро: Пригласить друга (+10⚡ и +1🎲)"
    rows.append([btn(ref_label, "shop:ref")])
    back_label = "🌫 Back to Story" if is_en else "🌫 Вернуться в сюжет"
    rows.append([btn(back_label, "st:resume")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_text(session: dict) -> str:
    lang = session.get("lang", "ru")
    turns = session.get("turns_left", 0)
    fate = session.get("fate_tokens", 0)
    is_vip = session.get("is_vip", False)
    vip_until = int(session.get("vip_until") or 0)
    if lang == "en":
        vip_str = f"👑 <b>ACTIVE</b> (until {time.strftime('%Y-%m-%d', time.gmtime(vip_until))}, 80 turns/day)" if is_vip else f"Standard ({config.FREE_DAILY_TURNS} free turns/day)"
        return (
            "🏛 <b>Sanctuary above the Gray Fog</b>\n\n"
            f"⚡ Spirituality Turns: <b>{turns}</b> | 🎲 Fate Seals: <b>{fate}</b>\n"
            f"🔮 Status: {vip_str}\n\n"
            "• <b>40 Turns (65 ⭐️)</b> — instant energy refill to continue your story right now.\n"
            "• <b>5 Fate Seals + Full Heal (55 ⭐️)</b> — reroll failed d20 checks on your crazy plans with Advantage (2d20) + restore 100% HP & Sanity.\n"
            "• <b>Relic: Sealed Artifact (110 ⭐️)</b> — claim a powerful Sealed Artifact into your build (+25 turns).\n"
            "• <b>Sanctuary Pass 30 Days (390 ⭐️)</b>:\n"
            "  — <b>80 Turns EVERY DAY</b> (auto-refilled daily)\n"
            "  — <b>Expanded AI Chronicle Memory</b> (2x deeper plot & NPC memory)\n"
            "  — <b>+3 Fate Seals</b> & instant full restoration."
        )
    vip_str = f"👑 <b>АКТИВЕН</b> (до {time.strftime('%d.%m.%Y', time.gmtime(vip_until))}, 80 ходов/день)" if is_vip else f"Базовый ({config.FREE_DAILY_TURNS} бесплатных ходов каждые сутки)"
    return (
        "🏛 <b>Святилище над Серым Туманом</b>\n\n"
        f"⚡ Ходов Духовности: <b>{turns}</b> | 🎲 Печатей Судьбы: <b>{fate}</b>\n"
        f"🔮 Статус: {vip_str}\n\n"
        "• <b>40 Ходов (65 ⭐️)</b> — мгновенно продолжить сюжет на самом интересном месте.\n"
        "• <b>5 Печатей Судьбы + Исцеление (55 ⭐️)</b> — переброс проваленных проверок d20 с Преимуществом (2d20), когда твой гениальный план сорвался + 100% ОЗ и Рассудка.\n"
        "• <b>Реликвия: Запечатанный Артефакт (110 ⭐️)</b> — выбор редкого Артефакта в свой билд (+25 ходов).\n"
        "• <b>Святилище Серого Тумана — 30 дней (390 ⭐️)</b>:\n"
        "  — <b>80 ходов КАЖДЫЙ ДЕНЬ</b> (обновляются каждые сутки)\n"
        "  — <b>Глубокая летопись памяти ИИ</b> (вдвое больше деталей, улик и NPC)\n"
        "  — <b>+3 Печати Судьбы</b> и полное восстановление."
    )


@router.message(Command("shop", "vip", "donate"))
async def cmd_shop(message: Message):
    s = await db.get_story_session(message.from_user.id)
    await message.answer(shop_text(s), reply_markup=shop_kb(s.get("lang", "ru")))


@router.callback_query(F.data == "shop:open")
async def cb_shop_open(cb: CallbackQuery):
    s = await db.get_story_session(cb.from_user.id)
    await ui.safe_edit(cb.message, shop_text(s), shop_kb(s.get("lang", "ru")))
    await cb.answer()


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_shop_buy(cb: CallbackQuery, bot: Bot):
    pack_id = cb.data.split(":", 2)[2]
    pack = PACKS.get(pack_id)
    if not pack:
        await cb.answer("Пакет не найден", show_alert=True)
        return
    s = await db.get_story_session(cb.from_user.id)
    is_en = s.get("lang") == "en"
    title = pack["title_en"] if is_en else pack["title_ru"]
    desc = pack["desc_en"] if is_en else pack["desc_ru"]
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=title,
        description=desc,
        payload=f"lotm:{pack_id}:{cb.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=int(pack["stars"]))],
    )
    await cb.answer()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    sp = message.successful_payment
    payload = sp.invoice_payload or ""
    parts = payload.split(":")
    pack_id = parts[1] if len(parts) >= 2 else "turns_40"
    pack = PACKS.get(pack_id, PACKS["turns_40"])
    uid = message.from_user.id

    await db.log_payment(uid, pack_id, sp.total_amount, sp.telegram_payment_charge_id)
    if pack["turns"] > 0:
        await db.add_turns(uid, pack["turns"])
    if pack["fate"] > 0:
        await db.add_fate_tokens(uid, pack["fate"])
    if pack["vip_days"] > 0:
        await db.activate_vip(uid, pack["vip_days"])
    if pack["heal"]:
        c = await db.get_character(uid)
        if c:
            restored = rules.long_rest(c)
            await db.update_character(uid, sanity=100, **restored)

    s = await db.get_story_session(uid)
    is_en = s.get("lang") == "en"

    if pack.get("artifact"):
        c = await db.get_character(uid)
        taken = set((c or {}).get("feats") or [])
        avail = [k for k in ARTIFACT_FEATS if k not in taken] or list(lotm.FEATS.keys())
        rows = [[btn(f"🏺 {lotm.FEATS[k]['name']}", f"shop:art:{k}")] for k in avail if k in lotm.FEATS]
        rows.append([btn("🌫 В сюжет", "st:resume")])
        await message.answer(
            "🏺 <b>Серый Туман открывает хранилище реликвий!</b>\nВыбери Запечатанный Артефакт (или Черту), который навсегда войдёт в твой профиль:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🌫 Continue Story" if is_en else "🌫 Продолжить сюжет", "st:resume")]])
    title = pack["title_en"] if is_en else pack["title_ru"]
    await message.answer(
        f"✨ <b>Серый Туман принял подношение!</b>\n"
        f"Активировано: <b>{esc(title)}</b>\n"
        f"⚡ Ходов: <b>{s['turns_left']}</b> | 🎲 Печатей Судьбы: <b>{s['fate_tokens']}</b> | 👑 VIP: <b>{'АКТИВЕН' if s['is_vip'] else 'Нет'}</b>.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("shop:art:"))
async def cb_pick_purchased_artifact(cb: CallbackQuery):
    key = cb.data.split(":", 2)[2]
    uid = cb.from_user.id
    c = await db.get_character(uid)
    if not c or key not in lotm.FEATS:
        await cb.answer("Сначала создай персонажа (/build)", show_alert=True)
        return
    feats = list(c.get("feats") or [])
    if key not in feats:
        feats.append(key)
    con_mod = rules.ability_mod(int((c.get("stats") or {}).get("con", 10)))
    max_hp = rules.sequence_max_hp(c["pathway"], int(c["sequence"]), con_mod, feats, pathway_choices=c.get("pathway_choices"))
    uses = h_perks.reset_combat_uses({**c, "feats": feats})
    await db.update_character(uid, feats=feats, max_hp=max_hp, hp=max(int(c["hp"]), max_hp), lair_uses=uses)
    ft = lotm.FEATS[key]
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🌫 Вернуться в сюжет", "st:resume")]])
    await ui.safe_edit(
        cb.message,
        f"🏺 Получена реликвия: <b>{esc(ft['name'])}</b>!\n<i>{esc(ft['desc'])}</i>",
        kb,
    )
    await cb.answer("Реликвия добавлена в профиль!")


@router.message(Command("lang"))
async def cmd_lang(message: Message):
    s = await db.get_story_session(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("🇷🇺 Русский", "lang:set:ru"), btn("🇬🇧 English", "lang:set:en")],
    ])
    await message.answer(
        f"🌐 Выбери язык сюжета ИИ-Мастера / Choose AI Game Master language (текущий: <b>{s['lang'].upper()}</b>):",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("lang:set:"))
async def cb_set_lang(cb: CallbackQuery):
    lang = cb.data.split(":")[2]
    if lang not in ("ru", "en"):
        lang = "ru"
    await db.update_story_session(cb.from_user.id, lang=lang)
    msg = "🇬🇧 Language switched to <b>English</b>! Use /story to play." if lang == "en" else "🇷🇺 Язык переключён на <b>Русский</b>! Нажми /story для игры."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("🌫 Start Story (/story)" if lang == "en" else "🌫 Начать сюжет (/story)", "st:resume")],
        [btn("🔮 Create Character (/build)" if lang == "en" else "🔮 Создать персонажа (/build)", "st:go_build")],
    ])
    await ui.safe_edit(cb.message, msg, kb)
    await cb.answer()


@router.message(Command("give_turns"))
async def cmd_give_turns(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await message.answer("Использование: <code>/give_turns user_id количество</code>")
        return
    s = await db.add_turns(int(parts[1]), int(parts[2]))
    await message.answer(f"✅ Игроку <code>{parts[1]}</code> начислено {parts[2]} ходов (теперь: <b>{s['turns_left']}</b>).")


@router.message(Command("give_vip"))
async def cmd_give_vip(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/give_vip user_id [дней=30]</code>")
        return
    days = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 30
    await db.activate_vip(int(parts[1]), days)
    await message.answer(f"👑 Игроку <code>{parts[1]}</code> активировано Святилище (VIP) на <b>{days} дн.</b>!")


@router.message(Command("invite", "ref"))
async def cmd_invite(message: Message, bot: Bot):
    me = await bot.me()
    s = await db.get_story_session(message.from_user.id)
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    count = int(s.get("referrals_count") or 0)
    await message.answer(
        "🃏 <b>Тайное Приглашение в Клуб Таро</b>\n\n"
        "Отправь эту ссылку другу или в тематический чат. Как только он перейдёт по ней и нажмёт Start:\n"
        "• Ты мгновенно получишь <b>+10 Ходов Духовности ⚡</b> и <b>+1 Печать Судьбы 🎲</b>!\n"
        "• Твой друг получит <b>+5 бонусных ходов ⚡</b> на старте.\n\n"
        f"🔗 <b>Твоя ссылка:</b>\n<code>{link}</code>\n\n"
        f"👥 Уже приглашено Потусторонних: <b>{count}</b>"
    )


@router.callback_query(F.data == "shop:ref")
async def cb_shop_ref(cb: CallbackQuery, bot: Bot):
    me = await bot.me()
    s = await db.get_story_session(cb.from_user.id)
    link = f"https://t.me/{me.username}?start=ref_{cb.from_user.id}"
    count = int(s.get("referrals_count") or 0)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📤 Отправить приглашение другу",
            url=f"https://t.me/share/url?url={link}&text=Попробуй%20крутую%20RPG%20по%20Повелителю%20Тайн%20с%20ИИ-Мастером%20и%2022%20Путями!",
        )],
        [btn("🏛 Назад в Святилище", "shop:open")],
    ])
    await ui.safe_edit(
        cb.message,
        "🃏 <b>Тайное Приглашение в Клуб Таро</b>\n\n"
        "За каждого приглашённого друга ты получаешь <b>+10 Ходов Духовности ⚡</b> и <b>+1 Печать Судьбы 🎲</b> (а друг — +5 ходов)!\n\n"
        f"🔗 <b>Твоя личная ссылка:</b>\n<code>{link}</code>\n\n"
        f"👥 Приглашено Потусторонних: <b>{count}</b>",
        kb,
    )
    await cb.answer()
