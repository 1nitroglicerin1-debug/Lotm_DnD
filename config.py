"""Настройки бота. Всё берётся из файла .env (или переменных окружения)."""
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()


def _parse_int_list(raw: str) -> list[int]:
    """Парсинг списка целочисленных ID (разделители ',' и ';')."""
    out: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.append(int(chunk))
    return out


ADMIN_IDS: list[int] = sorted(set(_parse_int_list(os.getenv("ADMIN_IDS", ""))))

_chief_raw = os.getenv("CHIEF_ADMIN", "").strip()
CHIEF_ADMIN: int = int(_chief_raw) if _chief_raw.isdigit() else (ADMIN_IDS[0] if ADMIN_IDS else 0)


def is_chief(user_id: int | None) -> bool:
    """True, если это главный администратор."""
    return user_id is not None and CHIEF_ADMIN != 0 and int(user_id) == CHIEF_ADMIN


def is_captain(user_id: int | None) -> bool:
    """Устаревшая функция: оставлена для совместимости, всегда возвращает False."""
    return False


DB_PATH = os.getenv("DB_PATH", "data/bot.db").strip()
FSM_DB_PATH = os.getenv("FSM_DB_PATH", "").strip()


def make_fsm_path() -> str:
    """Путь к SQLite-хранилищу FSM: по умолчанию рядом с основной БД."""
    if FSM_DB_PATH:
        return FSM_DB_PATH
    return os.path.join(os.path.dirname(DB_PATH) or ".", "fsm.db")


LLM_API_KEY: str = os.getenv("LLM_API_KEY", "").strip()
LLM_API_URL: str = os.getenv("LLM_API_URL", "https://api.proxyapi.ru/v1/chat/completions").strip()
LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek/deepseek-v4.1-flash-1017").strip()
TRIBUTE_LINK: str = os.getenv("TRIBUTE_LINK", "").strip()

WELCOME_TURNS = 15           # Разовый стартовый пакет при регистрации
FREE_DAILY_TURNS = 3         # Ежедневный минимум бесплатного игрока (не копится выше 3)

# --- ЭКОНОМИКА ХОДОВ (единый источник истины, см. h_shop.PACKS) -------------
# Эталон розницы: turns_40 = 40 ходов за 65 ⭐️ -> ~1.63 ⭐️ за ход.
# VIP не «дарит» пул, а ДЕЙСТВИТЕЛЬНО пополняет баланс каждые сутки до уровня тарифа
# (раньше в описании обещали «80 ходов каждый день», а код пополнял только до 3).
VIP_DAILY_TURNS: int = int(os.getenv("VIP_DAILY_TURNS", "13"))        # 13/день × 30 ≈ 390 ходов за 390 ⭐️ (~1.0 ⭐️/ход)
VIP_PLUS_DAILY_TURNS: int = int(os.getenv("VIP_PLUS_DAILY_TURNS", "25"))  # 25/день × 30 = 750 ходов за 590 ⭐️ (~0.79 ⭐️/ход)
VIP_START_POOL: int = int(os.getenv("VIP_START_POOL", "13"))          # моментальное пополнение в момент покупки
VIP_PLUS_START_POOL: int = int(os.getenv("VIP_PLUS_START_POOL", "25"))
VIP_MEMORY_MULT = 2          # Глубина летописи ИИ для VIP (x2 к бесплатному лимиту)

# «Дело дня»: один бесплатный ход за сутки за вход в игру (ретеншн-механика)
DAILY_CASE_TURNS: int = int(os.getenv("DAILY_CASE_TURNS", "1"))

# Рефералка: бонус начисляется ТОЛЬКО после первого реального хода приглашённого (анти-фарм фейками)
REFERRAL_INVITER_TURNS: int = int(os.getenv("REFERRAL_INVITER_TURNS", "10"))
REFERRAL_INVITER_FATE: int = int(os.getenv("REFERRAL_INVITER_FATE", "1"))
REFERRAL_NEWCOMER_TURNS: int = int(os.getenv("REFERRAL_NEWCOMER_TURNS", "5"))
REFERRAL_MAX_AGE_DAYS: int = int(os.getenv("REFERRAL_MAX_AGE_DAYS", "3"))   # бонус только «свежим» аккаунтам
REFERRAL_DAILY_CAP: int = int(os.getenv("REFERRAL_DAILY_CAP", "5"))         # макс. наград пригласившему в сутки

# Месячные пулы (для вебхука Lava и админ-выдачи): производные от суточных тарифов
VIP_MONTHLY_POOL: int = int(os.getenv("VIP_MONTHLY_POOL", str(VIP_DAILY_TURNS * 30)))
VIP_PLUS_MONTHLY_POOL: int = int(os.getenv("VIP_PLUS_MONTHLY_POOL", str(VIP_PLUS_DAILY_TURNS * 30)))

GLOBAL_FREE_DAILY_CAP: int = int(os.getenv("GLOBAL_FREE_DAILY_CAP", "1500"))  # Макс. бесплатных ИИ-ходов в сутки на весь сервер
LAVA_WEBHOOK_SECRET = os.getenv("LAVA_WEBHOOK_SECRET", "").strip()

# DEV / GOD-MODE: по умолчанию ВЫКЛЮЧЕН (в проде чит-панель не должна висеть открытой)
DEV_MODE: bool = os.getenv("DEV_MODE", "0").strip() in ("1", "true", "yes")

# Веб-платформа: разрешать ли запросы без подписи Telegram initData.
# 1 — публичный сайт-витрина (любой может играть под своим локальным uid),
# 0 — жёстко требовать подписанный initData (только Mini App).
ALLOW_ANON_WEB: bool = os.getenv("ALLOW_ANON_WEB", "1").strip() in ("1", "true", "yes")
# Список разрешённых Origin через запятую. Пусто -> "*" (без credentials).
WEB_ORIGINS: list[str] = [o.strip() for o in os.getenv("WEB_ORIGINS", "").split(",") if o.strip()]


def is_dev(user_id: int | None) -> bool:
    """True только для администраторов из ADMIN_IDS или локального тестового аккаунта при DEV_MODE=1."""
    if user_id is None:
        return False
    uid = int(user_id)
    if uid in ADMIN_IDS:
        return True
    return DEV_MODE and uid == 664506059


WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0").strip()
WEB_PORT: int = int(os.getenv("WEB_PORT", "8000"))
WEBAPP_URL: str = os.getenv("WEBAPP_URL", "").strip()  # Публичный HTTPS URL туннеля или домена
