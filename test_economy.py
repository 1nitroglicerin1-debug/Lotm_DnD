"""test_economy.py — регрессионные тесты экономики ходов, VIP-тарифов и анти-фарма.

Покрывают реальные дыры, найденные в аудите:
  1. Ежедневное пополнение доводит баланс до уровня тарифа, но не копит бесконечно.
  2. VIP реально пополняется каждые сутки (раньше обещали 80/день, выдавали 3).
  3. Повторная покупка VIP не даёт пул дважды.
  4. Списание хода атомарно при параллельных запросах (TOCTOU).
  5. «Дело дня» выдаётся ровно один раз в сутки.
  6. Реферальная награда пригласившему ждёт первого хода новичка.
  7. Рефералку нельзя накрутить самим собой / повторно.
  8. Откат хода при сбое ИИ не затирает купленные ходы.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="lotm_econ_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_econ.db")
os.environ["FSM_DB_PATH"] = os.path.join(_TMP, "test_econ_fsm.db")
os.environ["BOT_TOKEN"] = "test:token"
os.environ["DEV_MODE"] = "0"
# Важно для полного прогона (discover): config кэшируется, и если этот модуль
# импортируется первым, он фиксирует секрет вебхука для всех остальных тестов.
os.environ["LAVA_WEBHOOK_SECRET"] = "s3cret-key-for-tests"

import config  # noqa: E402
import db  # noqa: E402


def _yesterday() -> int:
    return int(time.strftime("%Y%m%d", time.gmtime(time.time() - 86400)))


class EconomyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()

    # ---------- 1. Ежедневное пополнение до уровня базового тарифа ----------
    async def test_daily_refill_to_free_target(self):
        uid = 1001
        s = await db.get_story_session(uid)
        self.assertEqual(int(s["turns_left"]), config.WELCOME_TURNS)

        # Игрок потратил всё и вернулся на следующий день
        await db.update_story_session(uid, turns_left=0, daily_day=_yesterday())
        s = await db.get_story_session(uid)
        self.assertEqual(int(s["turns_left"]), config.FREE_DAILY_TURNS,
                         "Бесплатный игрок должен получить ровно FREE_DAILY_TURNS за сутки")

    # ---------- 2. Суточный уровень VIP реально выше бесплатного ----------
    async def test_vip_daily_target_is_higher(self):
        uid = 1002
        await db.get_story_session(uid)
        await db.activate_vip(uid, days=30, plan="vip")
        await db.update_story_session(uid, turns_left=0, daily_day=_yesterday())
        s = await db.get_story_session(uid)
        self.assertEqual(int(s["turns_left"]), config.VIP_DAILY_TURNS)
        self.assertGreater(config.VIP_DAILY_TURNS, config.FREE_DAILY_TURNS,
                           "VIP обязан получать больше бесплатного игрока")

    async def test_vip_plus_is_stronger_than_vip(self):
        uid = 1003
        await db.get_story_session(uid)
        await db.activate_vip(uid, days=30, plan="plus")
        await db.update_story_session(uid, turns_left=0, daily_day=_yesterday())
        s = await db.get_story_session(uid)
        self.assertEqual(int(s["turns_left"]), config.VIP_PLUS_DAILY_TURNS)
        self.assertGreater(config.VIP_PLUS_DAILY_TURNS, config.VIP_DAILY_TURNS)

    # ---------- 3. Повторная покупка VIP не задваивает пул ----------
    async def test_vip_repeat_purchase_does_not_stack_pool(self):
        uid = 1004
        await db.get_story_session(uid)
        s1 = await db.activate_vip(uid, days=30, plan="vip")
        first = int(s1["turns_left"])
        s2 = await db.activate_vip(uid, days=30, plan="vip")
        self.assertEqual(int(s2["turns_left"]), first,
                         "Повторная активация не должна начислять пул ещё раз")
        self.assertGreater(int(s2["vip_until"]), int(s1["vip_until"]),
                           "Срок VIP при этом обязан продлиться")

    # ---------- 4. Атомарность списания при гонке ----------
    async def test_concurrent_consumption_cannot_overspend(self):
        uid = 1005
        await db.get_story_session(uid)
        await db.update_story_session(uid, turns_left=3)
        results = await asyncio.gather(*[db.try_consume_turn(uid) for _ in range(12)])
        ok_count = sum(1 for ok, _ in results if ok)
        self.assertEqual(ok_count, 3, "Из 3 ходов можно списать ровно 3, не больше")
        s = await db.get_story_session(uid)
        self.assertEqual(int(s["turns_left"]), 0)

    # ---------- 5. «Дело дня» — ровно один раз в сутки ----------
    async def test_daily_case_once_per_day(self):
        uid = 1006
        await db.get_story_session(uid)
        granted1, _ = await db.claim_daily_case(uid)
        granted2, reason2 = await db.claim_daily_case(uid)
        self.assertTrue(granted1)
        self.assertFalse(granted2, "Повторное получение в тот же день запрещено")
        self.assertEqual(reason2, "already_claimed")

    async def test_daily_case_atomic_under_race(self):
        uid = 1007
        await db.get_story_session(uid)
        results = await asyncio.gather(*[db.claim_daily_case(uid) for _ in range(8)])
        self.assertEqual(sum(1 for ok, _ in results if ok), 1,
                         "Параллельные клики не должны выдавать несколько наград")

    # ---------- 6. Рефералка: награда после первого хода ----------
    async def test_referral_reward_waits_for_first_turn(self):
        inviter, newcomer = 1008, 1009
        await db.get_story_session(inviter)
        await db.get_story_session(newcomer)

        accepted = await db.apply_referral(newcomer, inviter)
        self.assertTrue(accepted)

        s_inv = await db.get_story_session(inviter)
        self.assertEqual(int(s_inv["referrals_count"]), 0,
                         "До первого хода новичка награда не начисляется")

        paid = await db.claim_referral_reward(newcomer)
        self.assertTrue(paid)

        s_inv = await db.get_story_session(inviter)
        self.assertEqual(int(s_inv["referrals_count"]), 1)
        self.assertEqual(int(s_inv["fate_tokens"]), 2 + config.REFERRAL_INVITER_FATE)

    async def test_referral_reward_paid_only_once(self):
        inviter, newcomer = 1010, 1011
        await db.get_story_session(inviter)
        await db.get_story_session(newcomer)
        await db.apply_referral(newcomer, inviter)

        first = await db.claim_referral_reward(newcomer)
        second = await db.claim_referral_reward(newcomer)
        self.assertTrue(first)
        self.assertFalse(second, "Повторная выплата за того же новичка запрещена")

    # ---------- 7. Нельзя пригласить самого себя ----------
    async def test_self_referral_rejected(self):
        uid = 1012
        await db.get_story_session(uid)
        self.assertFalse(await db.apply_referral(uid, uid))

    async def test_second_referral_on_same_newcomer_rejected(self):
        a, b, newbie = 1013, 1014, 1015
        for u in (a, b, newbie):
            await db.get_story_session(u)
        self.assertTrue(await db.apply_referral(newbie, a))
        self.assertFalse(await db.apply_referral(newbie, b),
                         "Новичок может быть приглашён только один раз")

    # ---------- 8. Возврат хода не затирает покупки ----------
    async def test_refund_turn_preserves_purchases(self):
        uid = 1016
        await db.get_story_session(uid)
        await db.update_story_session(uid, turns_left=5)
        ok, _ = await db.try_consume_turn(uid)
        self.assertTrue(ok)
        # Пока шла генерация ИИ, игрок докупил 40 ходов
        await db.add_turns(uid, 40)
        # Генерация сорвалась -> возвращаем ход
        await db.refund_turn(uid)
        s = await db.get_story_session(uid)
        self.assertEqual(int(s["turns_left"]), 45,
                         "Возврат хода не должен обнулять купленные ходы")

    # ---------- 9. Глобальный бесплатный бюджет сервера ----------
    async def test_global_free_cap_enforced(self):
        old = config.GLOBAL_FREE_DAILY_CAP
        config.GLOBAL_FREE_DAILY_CAP = 2
        try:
            # Изоляция: предыдущие тесты уже израсходовали серверный бюджет этого дня
            await db._q("DELETE FROM server_daily_usage")
            await db.get_story_session(1017)
            await db.get_story_session(1018)
            await db.get_story_session(1019)
            for u in (1017, 1018, 1019):
                await db.update_story_session(u, turns_left=1)
            r1 = await db.try_consume_turn(1017)
            r2 = await db.try_consume_turn(1018)
            r3 = await db.try_consume_turn(1019)
            self.assertTrue(r1[0])
            self.assertTrue(r2[0])
            self.assertFalse(r3[0], "Третий бесплатный ход в сутки должен быть заблокирован")
            self.assertEqual(r3[1], "global_cap_reached")
        finally:
            config.GLOBAL_FREE_DAILY_CAP = old


if __name__ == "__main__":
    unittest.main()
