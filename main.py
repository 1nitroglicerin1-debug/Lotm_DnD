"""Точка входа: python main.py"""
import asyncio
import logging

import uvicorn

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

import config
import db
import h_admin
import h_abilities
import h_build
import h_combat
import h_dice
import h_encounter
import h_mute
import h_perk_cmd
import h_profile
import h_promote
import h_shop
import h_story
import web_app
from fsm_storage import SqliteStorage

DICE_COMMANDS = [
    BotCommand(command="roll", description="Бросок: /roll 2d6+3"),
    BotCommand(command="check", description="Проверка навыка: /check Скрытность"),
    BotCommand(command="save", description="Спасбросок: /save Ловкость"),
    BotCommand(command="init", description="Инициатива"),
    BotCommand(command="skills", description="Мои бонусы"),
    BotCommand(command="iti", description="Порядок хода"),
    BotCommand(command="encounter", description="Бой: start/next/end/add_npc <id>"),
    BotCommand(command="add_npc", description="Добавить врага в бой: /add_npc <id>"),
    BotCommand(command="hp", description="Здоровье: /hp, /hp -5"),
    BotCommand(command="rest", description="Отдых: /rest short|long"),
    BotCommand(command="slots", description="Слоты заклинаний"),
    BotCommand(command="cond", description="Состояния: /cond отравлен"),
    BotCommand(command="dm", description="Мастер: /dm @username"),
    BotCommand(command="last", description="Последние броски"),
    BotCommand(command="mute", description="Тишина: /mute причина"),
    BotCommand(command="unmute", description="Снять тишину"),
    BotCommand(command="help", description="Справка"),
]
PRIVATE_COMMANDS = [
    BotCommand(command="story", description="🌫 Сюжет с ИИ-Мастером"),
    BotCommand(command="hunt", description="⚔️ Соло-бой (переваривание зелья)"),
    BotCommand(command="build", description="🔮 Создать Потустороннего (Посл. 9)"),
    BotCommand(command="me", description="👤 Моя карточка"),
    BotCommand(command="abilities", description="✨ Способности и механика Пути"),
    BotCommand(command="promote", description="🧪 Повысить Последовательность"),
    BotCommand(command="shop", description="💎 Духовность и VIP-пропуск"),
    BotCommand(command="journal", description="📓 Дневник улик и Метод Отыгрыша"),
    BotCommand(command="mode", description="🎭 Стиль Мастера (Канон / Угар)"),
    BotCommand(command="lang", description="🌐 Язык / Language (RU/EN)"),
    *DICE_COMMANDS,
    BotCommand(command="cancel", description="Отменить ввод"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен.")
    if not config.ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст — админ-панель никому не доступна!")

    await db.init_db()

    fsm_path = config.make_fsm_path()
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=SqliteStorage(fsm_path))
    # Перехватчик тишины: самый первый, до всех хэндлеров
    dp.message.outer_middleware(h_mute.MuteMiddleware())
    # порядок важен: админ → тишина → бой → анкеты → кубы
    dp.include_routers(
        h_admin.router, h_mute.router, h_shop.router,
        h_build.router, h_promote.router, h_abilities.router, h_perk_cmd.router,
        h_encounter.router, h_combat.router, h_profile.router, h_dice.router,
        h_story.router,
    )

    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(DICE_COMMANDS, scope=BotCommandScopeAllGroupChats())
    await bot.delete_webhook()

    me = await bot.me()
    logging.info("Бот запущен: @%s", me.username)

    web_app.set_bot(bot)
    uv_config = uvicorn.Config(web_app.app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="info")
    uv_server = uvicorn.Server(uv_config)
    await asyncio.gather(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
        uv_server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
