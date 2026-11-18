"""Тесты единого боевого движка combat_engine (без Telegram и БД).

Запуск:  python -m unittest test_combat_engine -v
"""
from __future__ import annotations

import unittest

import combat_engine as ce
import rules


class FakeRng:
    """Детерминированная подмена SystemRandom: выдаёт значения по спискам."""

    def __init__(self):
        self.queue: list[int] = []

    def push(self, *vals: int) -> "FakeRng":
        self.queue.extend(vals)
        return self

    def randint(self, a: int, b: int) -> int:
        if self.queue:
            v = self.queue.pop(0)
        else:
            v = a
        return max(a, min(b, v))


def _char() -> dict:
    return {
        "user_id": 1,
        "name": "Тест",
        "pathway": "moon",
        "sequence": 9,
        "start_sequence": 9,
        "stats": {"int": 14, "cha": 12, "wis": 10},
        "hp": 30, "max_hp": 30, "temp_hp": 0,
        "sanity": 100, "digestion": 10,
        "ac": 12,
        "spell_slots": {"1": {"total": 2, "left": 2}},
        "lair_uses": {},
    }


def _enemy() -> dict:
    return {"name": "Враг", "seq": 9, "hp": 20, "max_hp": 20, "ac": 11,
            "atk_bonus": 3, "dmg_dice": "1d8", "dmg_mod": 1, "save_bonus": 2, "round": 1}


class TestCantrip(unittest.TestCase):
    def test_hit_and_enemy_counter(self):
        rng = FakeRng()
        ce._rng = rng
        # атака игрока: d20=15 → 15+2+2=19 против КД 11 → попадание;
        # урон 1d8=5 + мод 2 = 7; ответ врага: d20=10+3=13 против КД 12 → попадание, 1d8=4+1=5
        rng.push(15, 5, 10, 4)
        out = ce.run_round(_char(), _enemy(), "cantrip", ac_bonus=0)
        self.assertFalse(out.victory)
        self.assertEqual(out.enemy["hp"], 20 - 7)
        self.assertEqual(out.delta["hp"], 30 - 5)
        self.assertEqual(out.enemy["round"], 2)

    def test_crit_doubles_damage_dice(self):
        rng = FakeRng()
        ce._rng = rng
        # нат20 → урон = куб 5 + мод 2 + куб ещё раз 5 = 12; враг мажет: d20=2
        rng.push(20, 5, 2)
        out = ce.run_round(_char(), _enemy(), "cantrip", ac_bonus=0)
        self.assertEqual(out.enemy["hp"], 20 - 12)
        self.assertEqual(out.enemy["round"], 2)

    def test_miss(self):
        rng = FakeRng()
        ce._rng = rng
        # 5+2+2=9 < 11 → промах; враг: d20=20 → крит, урон 6+1=7
        rng.push(5, 20, 6)
        out = ce.run_round(_char(), _enemy(), "cantrip", ac_bonus=0)
        self.assertEqual(out.enemy["hp"], 20)
        self.assertEqual(out.delta["hp"], 30 - 7)


class TestMech(unittest.TestCase):
    def test_mech_consumes_charge_and_shields(self):
        rng = FakeRng()
        ce._rng = rng
        char = _char()
        char["lair_uses"] = {"path_res": 1}
        # Луна: 2d8, Посл.9 tier_mult=1 → 1d8: выпало 4 → dmg = max(2, 4+2)=6; враг мажет d20=3
        rng.push(4, 3)
        out = ce.run_round(char, _enemy(), "mech", ac_bonus=0)
        self.assertEqual(out.enemy["hp"], 20 - 6)
        self.assertEqual(out.uses["path_res"], 0)
        self.assertEqual(out.delta["temp_hp"], 3)  # tier_mult 1 * 3

    def test_mech_no_charges_raises(self):
        char = _char()
        char["lair_uses"] = {"path_res": 0}
        with self.assertRaises(ce.CombatError):
            ce.run_round(char, _enemy(), "mech", ac_bonus=0)


class TestMyth(unittest.TestCase):
    def test_myth_costs_sanity(self):
        rng = FakeRng()
        ce._rng = rng
        char = _char()
        char["sequence"] = 4  # Форма Мифического Существа доступна только на Посл. ≤ 4
        # (10-4)=6d10: все кубы по 1 → total=6 → dmg 6+2=8; враг мажет d20=3
        rng.push(1, 1, 1, 1, 1, 1, 3)
        out = ce.run_round(char, _enemy(), "myth", ac_bonus=0)
        self.assertEqual(out.delta["sanity"], 100 - 20)
        self.assertEqual(out.enemy["hp"], 20 - 8)
        self.assertEqual(out.delta["temp_hp"], 60)  # (10-4) * 10
        self.assertEqual(out.uses["mythical_used"], 1)

    def test_myth_low_sanity_raises(self):
        char = _char()
        char["sanity"] = 15
        with self.assertRaises(ce.CombatError):
            ce.run_round(char, _enemy(), "myth", ac_bonus=0)

    def test_myth_high_seq_raises(self):
        char = _char()
        char["sequence"] = 5
        with self.assertRaises(ce.CombatError):
            ce.run_round(char, _enemy(), "myth", ac_bonus=0)


class TestSkill(unittest.TestCase):
    def test_skill_consumes_slot(self):
        rng = FakeRng()
        ce._rng = rng
        char = _char()
        char["sequence"] = 8  # Багровый эликсир: 2d8, лечение
        char["hp"] = 20
        rng.push(3, 3, 3)  # кубы 2d8: 3+3 → val=8; враг мажет d20=3
        out = ce.run_round(char, _enemy(), "sk", ac_bonus=0, seq=8, key="core")
        self.assertEqual(out.spell_slots["1"]["left"], 1)
        self.assertEqual(out.delta["hp"], 28)   # 20 + 8 (2d8=6 + мод 2)
        self.assertFalse(out.victory)

    def test_skill_no_slot_raises(self):
        char = _char()
        char["sequence"] = 8
        char["spell_slots"] = {"1": {"total": 2, "left": 0}}
        with self.assertRaises(ce.CombatError):
            ce.run_round(char, _enemy(), "sk", ac_bonus=0, seq=8, key="core")


class TestStunt(unittest.TestCase):
    def test_stunt_success_disorients_enemy(self):
        rng = FakeRng()
        ce._rng = rng
        char = _char()
        char["skills"] = {"acrobatics": 1}
        # проверка навыка через rules.skill_check (ЛОВ 12, владение pb=2 → +3+2=+5):
        # d20=15 → 20 ≥ Сл 12 → успех. Но rules бросает своим _rng! Подменяем его тоже.
        rules._rng = rng
        rng.push(15, 3, 3, 10, 4)
        # 15: проверка трюка; 3+3: заговор 1d8=3 +стат2+ бонус randint(2,6)=3 → 8 урона
        # 10: враг с помехой... disoriented → min(d20,d20): 10 и 4 → 4+3=7 < КД 12 → промах
        out = ce.run_round(char, _enemy(), "stunt", ac_bonus=0,
                          stunt_text="Опрокинуть фонарь",
                          classify=("acrobatics", 12, "Опрокинуть фонарь"))
        self.assertEqual(out.enemy["hp"], 20 - 8)
        self.assertTrue(any(ev["kind"] == "stunt_hit" for ev in out.events))

    def test_stunt_failure_grazes(self):
        rng = FakeRng()
        ce._rng = rng
        char = _char()
        char["skills"] = {"acrobatics": 1}
        rules._rng = rng
        # проверка d20=2 → 7 < 12 → осечка; graze = (1d8=2 +2)//2 = 2; враг бьёт: 15+3=18 ≥ 12 → 1d8=5+1=6
        rng.push(2, 2, 15, 5)
        out = ce.run_round(char, _enemy(), "stunt", ac_bonus=0,
                          stunt_text="Кувырок под удар",
                          classify=("acrobatics", 12, "Кувырок"))
        self.assertEqual(out.enemy["hp"], 20 - 2)
        self.assertFalse(out.victory)


class TestEnemyAndFate(unittest.TestCase):
    def test_victory_resets_and_rewards(self):
        rng = FakeRng()
        ce._rng = rng
        enemy = _enemy()
        enemy["hp"] = 5
        # игрок: d20=15 → попадание, урон 1d8=5+2=7 → враг мёртв
        rng.push(15, 5)
        out = ce.run_round(_char(), enemy, "cantrip", ac_bonus=0)
        self.assertTrue(out.victory)
        self.assertIsNone(out.enemy)
        self.assertEqual(out.delta["digestion"], 13)  # 10 + 3 (Посл.9 ≥ 7)

    def test_gray_fog_save_on_death(self):
        rng = FakeRng()
        ce._rng = rng
        char = _char()
        char["hp"] = 4
        # игрок мажет (d20=2); враг: 15+3=18 ≥ 12 → урон 1d8=8+1=9 → hp ≤ 0 → Серый Туман
        rng.push(2, 15, 8)
        out = ce.run_round(char, _enemy(), "cantrip", ac_bonus=0)
        self.assertTrue(out.gray_fog_save)
        self.assertIsNone(out.enemy)
        self.assertEqual(out.delta["hp"], 15)      # max_hp//2 = 30//2
        self.assertEqual(out.delta["sanity"], 85)
        self.assertEqual(out.delta["temp_hp"], 0)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            ce.run_round(_char(), _enemy(), "banana", ac_bonus=0)


class TestRenderers(unittest.TestCase):
    def test_html_and_plain(self):
        rng = FakeRng()
        ce._rng = rng
        rng.push(15, 5, 10, 4)
        out = ce.run_round(_char(), _enemy(), "cantrip", ac_bonus=0)
        html = ce.render_html(out)
        plain = ce.render_plain(out)
        self.assertEqual(len(html), len(plain), "Одинаковая длина по событиям")
        self.assertIn("<b>", html[0])
        self.assertNotIn("<b>", plain[0])
        # Бросок игрока логируется, врага — нет (side=enemy)
        player_rolls = [ev["roll"] for ev in out.events
                        if ev.get("roll") and ev["roll"].get("side") == "player"]
        self.assertEqual(len(player_rolls), 1)


if __name__ == "__main__":
    unittest.main()
