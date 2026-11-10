"""Тесты чистой логики правил D&D (без Telegram и БД).

Запуск:  python -m unittest test_rules -v
"""
from __future__ import annotations

import unittest

import rules


class TestModifiers(unittest.TestCase):
    def test_ability_mod_rounds_down(self):
        self.assertEqual(rules.ability_mod(10), 0)
        self.assertEqual(rules.ability_mod(11), 0)
        self.assertEqual(rules.ability_mod(9), -1)
        self.assertEqual(rules.ability_mod(16), 3)
        self.assertEqual(rules.ability_mod(30), 10)

    def test_proficiency_bonus(self):
        cases = {1: 2, 4: 2, 5: 3, 8: 3, 9: 4, 12: 4, 13: 5, 16: 5, 17: 6, 20: 6}
        for level, pb in cases.items():
            self.assertEqual(rules.proficiency_bonus(level), pb, f"level {level}")


CHAR = {
    "stats": {"str": 10, "dex": 16, "con": 14, "int": 12, "wis": 10, "cha": 8},
    "skills": {"stealth": 1, "perception": 2},
    "saves": ["dex", "int"],
    "level": 5,
}


class TestTotals(unittest.TestCase):
    def test_skill_total(self):
        # Скрытность: ЛОВ +3 + владение (pb=3) = 6
        self.assertEqual(rules.skill_total(CHAR, "stealth"), 6)
        # Восприятие: МУД +0 + компетенция (pb*2=6) = 6
        self.assertEqual(rules.skill_total(CHAR, "perception"), 6)
        # Без владения: Атлетика СИЛ +0 = 0
        self.assertEqual(rules.skill_total(CHAR, "athletics"), 0)

    def test_save_total(self):
        # dex в спасбросках: +3 + pb 3 = 6
        self.assertEqual(rules.save_total(CHAR, "dex"), 6)
        # str не в спасбросках: +0
        self.assertEqual(rules.save_total(CHAR, "str"), 0)


class TestResolve(unittest.TestCase):
    def test_exact_skill(self):
        target, hints = rules.resolve_check("Скрытность")
        self.assertEqual(target, ("skill", "stealth"))
        self.assertEqual(hints, [])

    def test_abbreviation(self):
        target, _ = rules.resolve_check("скрыт")
        self.assertEqual(target, ("skill", "stealth"))

    def test_alias(self):
        target, _ = rules.resolve_check("стелс")
        self.assertEqual(target, ("skill", "stealth"))

    def test_ability(self):
        target, _ = rules.resolve_check("Ловкость")
        self.assertEqual(target, ("ability", "dex"))

    def test_unknown(self):
        target, hints = rules.resolve_check("zzz")
        self.assertIsNone(target)
        self.assertEqual(hints, [])


class TestParseCheckArgs(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(rules.parse_check_args("Скрытность"), ("Скрытность", "normal", 0))

    def test_adv(self):
        self.assertEqual(rules.parse_check_args("скрыт adv"), ("скрыт", "adv", 0))

    def test_preim_and_bonus(self):
        self.assertEqual(rules.parse_check_args("Скрытность преим +2"), ("Скрытность", "adv", 2))

    def test_cancel_adv_dis(self):
        # преимущество и помеха взаимно гасятся
        self.assertEqual(rules.parse_check_args("скрыт adv пом"), ("скрыт", "normal", 0))

    def test_multiple_bonuses(self):
        self.assertEqual(rules.parse_check_args("скрыт +2 -1"), ("скрыт", "normal", 1))


class TestRollExpression(unittest.TestCase):
    def test_single(self):
        r = rules.roll_expression("2d6+3")
        self.assertEqual(r.total, sum(t.value for t in r.terms))
        self.assertEqual(len(r.terms), 2)
        self.assertEqual(r.terms[0].label, "2d6")
        self.assertEqual(r.terms[1].value, 3)

    def test_constants_visible(self):
        r = rules.roll_expression("d20+5")
        labels = [t.label for t in r.terms]
        self.assertIn("5", labels)

    def test_cyrillic_d(self):
        r = rules.roll_expression("2к6")
        self.assertEqual(r.terms[0].label, "2d6")
        self.assertEqual(len(r.terms[0].rolls), 2)
        # значения отдельных граней в диапазоне 1..6
        self.assertTrue(all(1 <= v <= 6 for v in r.terms[0].rolls))

    def test_reject_bad(self):
        for bad in ("", "abc", "d0", "d1001", "51d6"):
            with self.assertRaises(ValueError, msg=bad):
                rules.roll_expression(bad)


class TestCombatRules(unittest.TestCase):
    def test_hit_dice_not_dnd(self):
        # D&D-костяшек Hit Dice больше нет: отдых лечит напрямую, а не кубиками
        self.assertFalse(hasattr(rules, "hit_die_for_class"))
        self.assertFalse(hasattr(rules, "hit_dice_at_level"))
        self.assertFalse(hasattr(rules, "max_hp_at_level"))
    def test_resolve_condition(self):
        self.assertEqual(rules.resolve_condition("отравлен"), "poisoned")
        self.assertEqual(rules.resolve_condition("poisoned"), "poisoned")
        self.assertEqual(rules.resolve_condition("сбит с ног"), "prone")
        self.assertIsNone(rules.resolve_condition("несуществующее"))

    def test_apply_damage_temp_first(self):
        hp, temp = rules.apply_damage(20, 8, 5)
        self.assertEqual((hp, temp), (20, 3))        # временные поглотили
        hp, temp = rules.apply_damage(20, 8, 10)
        self.assertEqual((hp, temp), (18, 0))        # 8 во временные, 2 в HP
        hp, temp = rules.apply_damage(20, 0, 25)
        self.assertEqual((hp, temp), (0, 0))         # не уходит в минус

    def test_apply_heal_capped(self):
        self.assertEqual(rules.apply_heal(10, 20, 5), 15)
        self.assertEqual(rules.apply_heal(18, 20, 10), 20)   # не выше максимума
        self.assertEqual(rules.apply_heal(0, 20, 100), 20)

    def test_temp_hp_replaces_only_if_bigger(self):
        self.assertEqual(rules.apply_temp_hp(8, 5), 8)    # меньше → оставить
        self.assertEqual(rules.apply_temp_hp(8, 10), 10)  # больше → заменить



if __name__ == "__main__":
    unittest.main()
