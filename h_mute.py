"""Режим тишины в групповом чате: админ пишет /mute — писать может только он.

Два уровня защиты:
1. Перехватчик (outer middleware) на все сообщения: в муте сообщение нарушителя
   удаляется, а сам он получает ограничение прав на час (если у бота есть
   право «Блокировать участников»). Без этого права сообщения всё равно удаляются.
2. Команды /mute и /unmute: включают и снимают режим, чистят ограничения.

Кого мут не касается: админы бота (config.ADMIN_IDS), админы самого чата
Telegram, Мастера из /dm и анонимные админы группы.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatPermissions, ChatMemberUpdated, Message

import config
import db
from ui import esc

router = Router()

# Пользователь без имени, от которого приходят посты «от имени группы».
ANONYMOUS_ADMIN_ID = 1087968824

# Сообщения, которые нельзя удалять: служебные события чата.
SERVICE_CONTENT = {
    "new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo",
    "delete_chat_photo", "group_chat_created", "supergroup_chat_created",
    "channel_chat_created", "migrate_to_chat_id", "migrate_from_chat_id",
    "pinned_message", "message_auto_delete_timer_changed", "video_chat_started",
    "video_chat_ended", "video_chat_participants_invited", "forum_topic_created",
    "forum_topic_closed", "forum_topic_reopened", "forum_topic_edited",
    "general_forum_topic_hidden", "general_forum_topic_unhidden",
    "write_access_allowed", "users_shared", "chat_shared",
}

MUTE_HOURS = 1          # на сколько часов ограничиваем нарушителя
WARN_COOLDOWN = 30      # не чаще одного предупреждения в чат
WARN_TTL = 8            # предупреждение самоудаляется через N секунд

# Кэш проверок «админ ли этот участник в этом чате»: (chat, user) -> (is_admin, до какого времени)
_admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}
_last_warn: dict[int, float] = {}


HELP = (
    "🔇 <b>Режим тишины</b>\n"
    "/mute — писать может только админ\n"
    "/mute &lt;причина&gt; — включить с пояснением\n"
    "/mute off или /unmute — снять тишину\n\n"
    "Пока режим включён, сообщения остальных участников удаляются, "
    "а авторам на час запрещается писать (если у бота есть право "
    "«Блокировать участников»).\n"
    "Админы бота, админы чата и Мастера (<code>/dm</code>) пишут свободно."
)


def _is_service(message: Message) -> bool:
    """Служебное сообщение чата (вошёл участник, закреп и т.п.) — не трогаем."""
    return message.content_type in SERVICE_CONTENT


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Админ ли участник в этом чате. Результат кэшируется на 3 минуты."""
    now = time.monotonic()
    cached = _admin_cache.get((chat_id, user_id))
    if cached and cached[1] > now:
        return cached[0]

    allowed = False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        allowed = member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)
    except TelegramAPIError:
        allowed = False

    _admin_cache[(chat_id, user_id)] = (allowed, now + 180)
    return allowed


async def _is_privileged(bot: Bot, message: Message) -> bool:
    """Может ли автор сообщения писать во время тишины."""
    user = message.from_user
    if user is None:
        return True                                  # пост от имени канала
    if user.id == ANONYMOUS_ADMIN_ID:
        return True                                  # анонимный админ группы
    if user.is_bot:
        return user.id == bot.id                     # другие боты пусть молчат
    if user.id in config.ADMIN_IDS:
        return True
    if await db.get_dm(message.chat.id, user.id):
        return True
    return await _is_chat_admin(bot, message.chat.id, user.id)


async def _warn(bot: Bot, message: Message) -> None:
    """Короткое предупреждение в чат с автоудалением, не чаще раза в 30 секунд."""
    chat_id = message.chat.id
    now = time.monotonic()
    if now - _last_warn.get(chat_id, 0.0) < WARN_COOLDOWN:
        return
    _last_warn[chat_id] = now

    mute = await db.get_mute(chat_id)
    reason = (mute or {}).get("reason") or ""
    text = "🔇 Сейчас режим тишины — писать может только администратор."
    if reason:
        text += f"\nПричина: {esc(reason)}"

    try:
        sent = await bot.send_message(chat_id, text)
    except TelegramAPIError:
        return

    async def _cleanup() -> None:
        await asyncio.sleep(WARN_TTL)
        try:
            await bot.delete_message(chat_id, sent.message_id)
        except TelegramAPIError:
            pass

    asyncio.create_task(_cleanup())


class MuteMiddleware(BaseMiddleware):
    """Удаляет сообщения участников, пока в чате включён режим тишины."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return await handler(event, data)
        if _is_service(event):
            return await handler(event, data)
        if not await db.is_muted_chat(event.chat.id):
            return await handler(event, data)

        bot: Bot = data["bot"]
        if await _is_privileged(bot, event):
            return await handler(event, data)

        user = event.from_user
        if user is not None:
            await _restrict(bot, event.chat.id, user.id)

        try:
            await bot.delete_message(event.chat.id, event.message_id)
        except TelegramAPIError:
            pass
        await _warn(bot, event)
        return None      # дальше сообщение не идёт


async def _restrict(bot: Bot, chat_id: int, user_id: int) -> None:
    """Запрещает участнику писать на час и запоминает его для снятия при /unmute."""
    until = int(time.time()) + MUTE_HOURS * 3600
    try:
        await bot.restrict_chat_member(
            chat_id,
            user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except TelegramAPIError:
        return       # нет права «Блокировать участников» — хватит удаления сообщений
    await db.add_muted_victim(chat_id, user_id, until)


async def _may_manage(bot: Bot, message: Message) -> bool:
    """Может ли автор включать и снимать тишину."""
    user = message.from_user
    if user is None:
        return False
    if user.id in config.ADMIN_IDS:
        return True
    if await db.get_dm(message.chat.id, user.id):
        return True
    return await _is_chat_admin(bot, message.chat.id, user.id)


@router.message(Command("mute", "тишина", "мут"))
async def cmd_mute(message: Message, command: CommandObject, bot: Bot):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply("Режим тишины работает только в групповом чате.")
        return
    if not await _may_manage(bot, message):
        await message.reply("Включать тишину может только админ чата или Мастер (/dm).")
        return

    arg = (command.args or "").strip()
    if arg.lower() in ("off", "выкл", "стоп"):
        await _unmute(message, bot)
        return

    if not arg:
        current = await db.get_mute(message.chat.id)
        if current:
            reason = esc(current.get("reason") or "без причины")
            await message.reply(
                f"🔇 Режим тишины уже включён.\nПричина: {reason}\n\n"
                "Снять: /unmute"
            )
            return
        await message.reply(HELP)
        return

    await db.set_mute(message.chat.id, message.from_user.id, arg)
    await message.reply(
        "🔇 <b>Режим тишины включён</b>\n"
        f"Причина: {esc(arg)}\n\n"
        "Писать могут только админы и Мастера. Снять: /unmute"
    )


@router.message(Command("unmute", "размут", "говорите"))
async def cmd_unmute(message: Message, bot: Bot):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply("Команда работает только в групповом чате.")
        return
    if not await _may_manage(bot, message):
        await message.reply("Снимать тишину может только админ чата или Мастер (/dm).")
        return
    await _unmute(message, bot)


async def _unmute(message: Message, bot: Bot) -> None:
    """Снимает тишину и возвращает право писать всем, кого бот ограничивал."""
    chat_id = message.chat.id
    current = await db.get_mute(chat_id)
    if not current:
        await message.reply("Режима тишины и так нет — можно писать всем.")
        return

    victims = await db.take_muted_victims(chat_id)
    await db.clear_mute(chat_id)
    _last_warn.pop(chat_id, None)

    failed = 0
    for user_id in victims:
        try:
            await bot.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_audios=True,
                    can_send_documents=True,
                    can_send_photos=True,
                    can_send_videos=True,
                    can_send_video_notes=True,
                    can_send_voice_notes=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
        except TelegramAPIError:
            failed += 1

    lines = ["🔊 <b>Режим тишины снят</b> — снова можно писать всем."]
    if victims:
        lines.append(f"Ограничение снято с участников: {len(victims) - failed}")
    if failed:
        lines.append(f"Не удалось снять ограничение с {failed} — сними вручную в настройках чата.")
    await message.reply("\n".join(lines))


@router.message(Command("mutestatus", "тишина_статус"))
async def cmd_mutestatus(message: Message, bot: Bot):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply("Команда работает только в групповом чате.")
        return
    current = await db.get_mute(message.chat.id)
    if not current:
        await message.reply("🔊 Тишина выключена — писать может каждый.")
        return
    reason = current.get("reason") or "без причины"
    who = current.get("muted_by")
    since = time.strftime("%H:%M", time.localtime(current.get("created_at") or time.time()))
    await message.reply(
        f"🔇 Тишина включена с {since} (ID {who}).\nПричина: {reason}"
    )
