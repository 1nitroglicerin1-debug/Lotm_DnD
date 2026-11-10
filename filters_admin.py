"""Фильтры aiogram: проверка прав администратора по списку config.ADMIN_IDS."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

import config


def is_admin(user_id: int | None) -> bool:
    """True, если пользователь входит в список администраторов."""
    return user_id is not None and user_id in config.ADMIN_IDS


class IsAdmin(BaseFilter):
    """Пропускает апдейт только от ID из config.ADMIN_IDS.

    Работает и для Message, и для CallbackQuery — берёт from_user.id.
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = getattr(event, "from_user", None)
        return is_admin(getattr(user, "id", None))
