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
LLM_API_URL: str = os.getenv("LLM_API_URL", "https://api.deepseek.com/v1/chat/completions").strip()
LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat").strip()
TRIBUTE_LINK: str = os.getenv("TRIBUTE_LINK", "").strip()

WELCOME_TURNS = 15       # Разовый стартовый пакет при создании первого персонажа
FREE_DAILY_TURNS = 3     # Ежедневный бесплатный минимум (не копится выше 3)
VIP_DAILY_TURNS = 35     # Ежедневный лимит для VIP-подписчиков

WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0").strip()
WEB_PORT: int = int(os.getenv("WEB_PORT", "8000"))
WEBAPP_URL: str = os.getenv("WEBAPP_URL", "").strip()  # Публичный HTTPS URL туннеля или домена
