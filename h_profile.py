"""Анкеты игроков в личке с ботом: создание, просмотр, редактирование."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

import config
import db
import h_perks
import lotm_data as lotm
import rules
import ui
from filters_admin import is_admin
from ui import FIELDS, MECHANICAL

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


class Form(StatesGroup):
    edit = State()     # правка одного поля (data: uid, field)


WELCOME = (
    "🕯 <b>Добро пожаловать в мир «Повелителя Тайн» (Lord of the Mysteries)!</b>\n\n"
    "Это живая настольная RPG с <b>ИИ-Мастером</b> и всеми <b>22 каноничными Путями</b> (Посл. 9 → 0).\n"
    "💬 <b>Полная свобода D&D:</b> в любой сцене и даже в бою ты можешь жать кнопки ИЛИ <b>просто писать в чат любые свои действия, фразы и безумные трюки</b> — система сама подберёт навык, бросит d20 и встроит это в сюжет!\n\n"
    "<b>Основные команды:</b>\n"
    "🔮 /build — выпить зелье 9-й Последовательности и создать персонажа\n"
    "🌫 /story — сюжет с ИИ-Мастером\n"
    "🎭 /mode — переключить стиль Мастера (Мрачный Канон / Безумный D&D-Угар)\n"
    "📓 /journal — дневник улик, NPC и Метод Отыгрыша\n"
    "⚔️ /hunt — соло-бой 1-на-1\n"
    "👤 /me — карточка Потустороннего\n"
    "✨ /abilities — способности и механика Пути\n"
    "🧪 /promote — ритуал повышения Последовательности\n"
    "🏛 /shop — Святилище Серого Тумана (Духовность, Печати Судьбы, Артефакты)\n"
    "🌐 /lang — язык (RU / EN)"
)



def allowed(editor_id: int, owner_id: int) -> bool:
    if editor_id == owner_id or config.is_chief(editor_id):
        return True
    if owner_id in config.ADMIN_IDS:
        return False
    return editor_id in config.ADMIN_IDS


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uid = message.from_user.id
    args = (message.text or "").partition(" ")[2].strip()
    ref_note = ""
    if args.startswith("ref_"):
        raw_ref = args[4:].strip()
        if raw_ref.isdigit():
            ref_id = int(raw_ref)
            ok = await db.apply_referral(uid, ref_id)
            if ok:
                ref_note = "\n\n🎁 <b>Бонус Клуба Таро:</b> ты пришёл по приглашению Потустороннего и получил <b>+5 дополнительных ходов Духовности</b>!"
                try:
                    await bot.send_message(
                        ref_id,
                        "🃏 <b>Новый участник Клуба Таро!</b>\n"
                        "По твоей тайной ссылке в мир ступил новый Потусторонний.\n"
                        "Тебе начислено: <b>+10 Ходов Духовности ⚡</b> и <b>+1 Печать Судьбы 🎲</b>!",
                    )
                except Exception:
                    pass

    rows = []
    if config.WEBAPP_URL.startswith("https://"):
        from aiogram.types import WebAppInfo
        rows.append([InlineKeyboardButton(text="🔮 Открыть Веб-Терминал (Mini App)", web_app=WebAppInfo(url=config.WEBAPP_URL))])
    rows += [
        [ui.btn("🔮 Создать персонажа (/build)", "st:go_build")],
        [ui.btn("🌫 Играть сюжет с ИИ-Мастером (/story)", "st:resume")],
        [ui.btn("🃏 Пригласить друга (+10⚡ и +1🎲)", "shop:ref")],
        [ui.btn("🇷🇺 Русский", "lang:set:ru"), ui.btn("🇬🇧 English", "lang:set:en")],
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(WELCOME + ref_note, reply_markup=kb)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Отменять нечего.")
        return
    await state.clear()
    await message.answer("Отменено.")


@router.message(Command("me"))
async def cmd_me(message: Message, bot: Bot):
    c = await db.get_character(message.from_user.id)
    if c is None or (c["status"] == "draft" and not c.get("pathway")):
        if is_admin(message.from_user.id):
            await message.answer(
                "У тебя пока нет готовой анкеты.\n"
                "Создать: /newchar\n"
                "Быстрый сброс тестового персонажа: /reset_me"
            )
        else:
            await message.answer("У тебя пока нет готовой анкеты. Создать: /newchar")
        return
    await ui.send_card(bot, message.chat.id, c, message.from_user.id)


# ---------------------------------------------------------------------------
# Редактирование анкеты (игрок — свою, админ — любую)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("em:"))
async def cb_edit_menu(cb: CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    if not allowed(cb.from_user.id, uid):
        await cb.answer("Это не твоя анкета", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=ui.edit_menu_kb(uid))
    await cb.answer()


@router.callback_query(F.data.startswith("vw:"))
async def cb_view_back(cb: CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    if not allowed(cb.from_user.id, uid):
        await cb.answer("Это не твоя анкета", show_alert=True)
        return
    c = await db.get_character(uid)
    if c is None:
        await cb.answer("Анкета не найдена", show_alert=True)
        return
    await cb.message.edit_reply_markup(reply_markup=ui.card_kb(c, cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data.startswith("ed:"))
async def cb_edit_field(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    field = parts[2]
    if not allowed(cb.from_user.id, uid) or field not in FIELDS:
        await cb.answer("Нет доступа", show_alert=True)
        return
    if field in MECHANICAL and not is_admin(cb.from_user.id):
        await cb.answer(
            "Механические параметры (характеристики и владения) задаются при создании персонажа или изменяются администратором.",
            show_alert=True,
        )
        return
    c = await db.get_character(uid)
    if c is None:
        await cb.answer("Анкета не найдена", show_alert=True)
        return
    await state.set_state(Form.edit)
    await state.set_data({"uid": uid, "field": field})
    prompt = ui.PROMPTS[field]
    if field == "bio":
        prompt += (
            "\n\nЕсли текст не влезает в одно сообщение — шли частями: я их склею. "
            "Когда закончишь, нажми «✅ Готово» или отправь /done."
        )
    await cb.message.answer(prompt + "\n\n/cancel — отмена")
    await cb.answer()


async def apply_changes(bot: Bot, editor_id: int, c: dict, **changes) -> dict:
    """Сохраняет правку. Если игрок меняет механику — анкета уходит на повторную проверку."""
    recheck = (
        not is_admin(editor_id)
        and c["status"] in ("approved", "rejected")
        and bool(MECHANICAL & changes.keys())
    )
    if recheck:
        changes["status"] = "pending"
        changes["reject_reason"] = None

    if "stats" in changes and c.get("pathway") and c.get("faction"):
        new_stats = {k: int(v) for k, v in changes["stats"].items()}
        changes["stats"] = new_stats

        con_mod = rules.ability_mod(new_stats.get("con", 10))
        dex_mod = rules.ability_mod(new_stats.get("dex", 10))
        feats = list(c.get("feats") or [])
        max_hp = rules.sequence_max_hp(
            c["pathway"],
            int(c["sequence"]),
            con_mod,
            feats,
            faction_perk=c.get("faction_perk", ""),
            pathway_choices=c.get("pathway_choices"),
        )
        old_hp = int(c.get("hp") or 0)
        old_max = int(c.get("max_hp") or max_hp)
        changes["max_hp"] = max_hp
        changes["hp"] = min(max_hp, old_hp + max(0, max_hp - old_max))
        changes["ac"] = 10 + dex_mod
        changes["lair_uses"] = h_perks.reset_combat_uses({**c, "stats": new_stats})

    await db.update_character(c["user_id"], **changes)
    c = await db.get_character(c["user_id"])
    if recheck:
        # Анкета ушла на повторную проверку — прежние голоса админов больше не действуют
        await db.drop_card_messages(c["user_id"])
        await ui.notify_admins(bot, c, "Игрок изменил анкету — нужна проверка")
    return c


async def finish_edit(message: Message, state: FSMContext, bot: Bot, **changes):
    data = await state.get_data()
    uid = data.get("uid")
    await state.clear()
    if uid is None:
        await message.answer("Сессия редактирования истекла. Открой /me заново.")
        return
    c = await db.get_character(int(uid))
    if c is None:
        await message.answer("Анкета не найдена (возможно, её удалили).")
        return
    c = await apply_changes(bot, message.from_user.id, c, **changes)
    await message.answer("Сохранено ✔️")
    await ui.send_card(bot, message.chat.id, c, message.from_user.id)


@router.message(Form.edit, F.text & ~F.text.startswith("/"))
async def edit_text(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    field = data.get("field")
    if not field:
        await state.clear()
        await message.answer("Сессия редактирования истекла. Открой /me заново.")
        return
    if field == "photo_id":
        await message.answer("Пришли изображение как фото или нажми /cancel.")
        return
    if field == "bio":
        # Многочастный ввод: копим части, завершение — кнопкой или командой /done
        raw = (message.text or "").strip()
        if raw.lower() == "/done":
            parts = data.get("edit_bio_parts") or []
            raw_join = "\n\n".join(p for p in parts)
            if not raw_join:
                await message.answer("Ничего не накоплено. Пришли текст или /cancel.")
                return
            value, err = ui.parse_field("bio", raw_join)
            if err:
                await message.answer(err)
                return
            await finish_edit(message, state, bot, bio=value)
            return
        parts = list(data.get("edit_bio_parts") or [])
        parts.append(raw)
        total = sum(len(p) for p in parts) + 2 * (len(parts) - 1)
        if total > 7000:
            parts.pop()
            await message.answer(
                f"Слишком длинно: суммарно {total} символов, лимит 7000. "
                "Эта часть не сохранена — сократи её или отправь /cancel."
            )
            return
        await state.update_data(edit_bio_parts=parts)
        left = 7000 - total
        if left < 3500:
            await message.answer(
                f"Принято ({total}/7000). Шли ещё часть, отправь /done — сохранить, или /cancel.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Готово", callback_data="edbio:done"),
                ]]),
            )
        else:
            await message.answer(
                f"Принято ({total}/7000). Шли ещё часть, отправь /done — сохранить, или /cancel."
            )
        return
    value, err = ui.parse_field(field, message.text)
    if err:
        await message.answer(err)
        return
    await finish_edit(message, state, bot, **{field: value})


@router.callback_query(F.data == "edbio:done")
async def cb_edit_bio_done(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    parts = data.get("edit_bio_parts") or []
    raw_join = "\n\n".join(p for p in parts)
    if not raw_join:
        await cb.answer("Ничего не накоплено. Пришли текст.", show_alert=True)
        return
    value, err = ui.parse_field("bio", raw_join)
    if err:
        await cb.answer(err, show_alert=True)
        return
    if isinstance(cb.message, Message):
        await finish_edit(cb.message, state, bot, bio=value)
    await cb.answer()


@router.message(Form.edit, F.photo)
async def edit_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if data.get("field") != "photo_id":
        await message.answer("Сейчас нужен текст. Или /cancel.")
        return
    await finish_edit(message, state, bot, photo_id=message.photo[-1].file_id)


@router.callback_query(F.data.startswith("rs:"))
async def cb_resubmit(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    uid = int(parts[1])
    if cb.from_user.id != uid:
        await cb.answer("Это не твоя анкета", show_alert=True)
        return
    c = await db.get_character(uid)
    if c is None or c["status"] not in ("draft", "rejected"):
        await cb.answer("Нечего отправлять", show_alert=True)
        return
    if not (c.get("bio") or "").strip():
        await cb.answer("Сначала заполни поле «Анкета» (биографию) через ✏️ Изменить!", show_alert=True)
        return
    await db.update_character(uid, status="pending", reject_reason=None)
    await db.drop_card_messages(uid)
    c = await db.get_character(uid)
    await ui.safe_edit(cb.message, ui.card_text(c, admin=False), ui.card_kb(c, uid))
    await ui.notify_admins(bot, c, "Анкета отправлена на проверку")
    await cb.answer("Отправлено на проверку ⏳")
