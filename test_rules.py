"""Тесты чистой логики правил D&D (без Telegram и БД).

Запуск:  python -m unittest test_rules -v
"""
from __future__ import annotations

import unittest

import lotm_data as lotm
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

class TestFreeTextArbitration(unittest.TestCase):
    def setUp(self):
        import h_story
        self.h_story = h_story
        self.char = {
            "pathway": "seer",
            "sequence": 9,
            "start_sequence": 9,
            "pathway_choices": {},
            "skills": {"sleight_of_hand": 1, "stealth": 1, "perception": 1},
        }

    def test_pickpocket_strange_npc_dc(self):
        # Заявка из ТЗ: кража у странного незнакомца должна иметь адекватную Сл 14-15 вместо 8
        text = "Аккуратно пройдя мимоход возле него залезть к нему в карман попытавшись что-то найти и присвоить, азарт со странным человеком оказался сильнее моей адекватности"
        sk, dc, combat, summary = self.h_story._classify_free_text(text, self.char)
        self.assertEqual(sk, "sleight_of_hand")
        self.assertIn(dc, (14, 15))
        self.assertFalse(combat)
        self.assertEqual(summary, "Карманная кража")

    def test_pickpocket_crowd_standard_dc(self):
        text = "Нырнуть в толпу и незаметно залезть в карман прохожему"
        sk, dc, _, summary = self.h_story._classify_free_text(text, self.char)
        self.assertEqual(sk, "sleight_of_hand")
        self.assertIn(dc, (13, 14))
        self.assertEqual(summary, "Карманная кража")

    def test_pickpocket_drunk_easy_dc(self):
        text = "Пока пьяный зевака спит в переулке, залезть в его карман"
        sk, dc, _, summary = self.h_story._classify_free_text(text, self.char)
        self.assertEqual(sk, "sleight_of_hand")
        self.assertIn(dc, (10, 11))
        self.assertEqual(summary, "Карманная кража")

    def test_steal_from_beyonder_extreme_dc(self):
        text = "Попытаться обворовать Потустороннего капитана стражи"
        sk, dc, _, summary = self.h_story._classify_free_text(text, self.char)
        self.assertEqual(sk, "sleight_of_hand")
        self.assertIn(dc, (17, 18))

    def test_manner_words_do_not_reduce_danger_dc(self):
        text = "Очень тихо, осторожно и аккуратно подойти со спины к подозрительному стражнику и срезать кошель"
        sk, dc, _, summary = self.h_story._classify_free_text(text, self.char)
        self.assertEqual(sk, "sleight_of_hand")
        self.assertGreaterEqual(dc, 14)
        self.assertEqual(summary, "Карманная кража")

    def test_context_danger_escalates_dc(self):
        text = "Залезть к нему в карман и вытащить письмо"
        # Без контекста опасной цели: стандартная кража Сл 13-14
        sk1, dc1, _, _ = self.h_story._classify_free_text(text, self.char)
        self.assertEqual(sk1, "sleight_of_hand")
        self.assertIn(dc1, (13, 14))
        # С контекстом опасной цели из последнего сообщения мастера: Сл 14-15
        sk2, dc2, _, _ = self.h_story._classify_free_text(text, self.char, context="Перед вами стоит настороженный страж")
        self.assertEqual(sk2, "sleight_of_hand")
        self.assertIn(dc2, (14, 15))

    def test_min_dc_floor_for_active_checks(self):
        text = "Аккуратно осмотреться по сторонам"
        sk, dc, _, summary = self.h_story._classify_free_text(text, self.char)
        self.assertGreaterEqual(dc, 10)
        self.assertEqual(summary, "Осмотр окружения")

    def test_action_summary_labels_and_custom_clipping(self):
        # Проверка ярлыков в стиле BG3 / Disco Elysium
        cases = [
            ("Быстро нырнуть в тень и затаиться", "stealth", "Уход в тень"),
            ("Смешаться со столпом зевак на площади", "stealth", "Растворение в толпе"),
            ("Выпрыгнуть через окно на соседнюю крышу", "acrobatics", "Прыжок в окно"),
            ("Выбить ногой старую дубовую дверь", "athletics", "Силовой прорыв двери"),
            ("Попытаться соврать констеблю о своем алиби", "deception", "Блеф и отвлечение"),
            ("Осмотреть старые часы на каминной полке", "investigation", "Осмотр окружения"),
        ]
        for txt, expected_sk, expected_lbl in cases:
            sk, _, _, summary = self.h_story._classify_free_text(txt, self.char)
            self.assertEqual(summary, expected_lbl, f"Failed for '{txt}': got '{summary}' instead of '{expected_lbl}'")

        # Длинная заявка на 600 символов: ярлык кражи и ограничение длины
        long_text = "Аккуратно и незаметно подойти к господину в цилиндре, пока он отвлечен уличным фокусником, и осторожно срезать кожаный кошель с его пояса, а затем немедленно раствориться в толпе зевак и свернуть в ближайший темный переулок, делая вид, что я просто случайный прохожий, спешащий по своим обычным делам в нижнем районе Тингена."
        sk_long, dc_long, _, sum_long = self.h_story._classify_free_text(long_text, self.char)
        self.assertLessEqual(len(sum_long), 45)
        self.assertEqual(sum_long, "Карманная кража")

        # Нешаблонная заявка: обрезка первой смысловой фразы до 45 символов
        unusual = "Провести ритуальным мелом черту на полу и прислушаться к вибрациям эфира"
        _, _, _, sum_unusual = self.h_story._classify_free_text(unusual, self.char)
        self.assertLessEqual(len(sum_unusual), 45)
        self.assertTrue(len(sum_unusual.split()) <= 5)


class TestPathwayMechanicFormatting(unittest.TestCase):
    """Тесты форматирования описаний классовых механик Путей и чистоты кодировки."""

    def test_marauder_theft_no_dmg_placeholder_and_no_corrupt_chars(self):
        # 1. Сырые данные Пути Ошибки (Посл. 9) не содержат битых символов \ufffd
        raw_mech = lotm.pathway_mechanic_for("error", 9)
        self.assertEqual(raw_mech["name"], "Воровство Мародёра")
        self.assertNotIn("\ufffd", raw_mech["effect"])
        self.assertIn("—", raw_mech["effect"])

        # 2. Форматирование без персонажа подставляет кость урона 2d6
        formatted = rules.format_pathway_mechanic(raw_mech)
        self.assertNotIn("{dmg}", formatted["effect"])
        self.assertNotIn("{", formatted["effect"])
        self.assertNotIn("}", formatted["effect"])
        self.assertIn("2d6 урона", formatted["effect"])
        self.assertIn("сбивает врага с толку — крадёт +2 Защиты", formatted["effect"])

        # 3. Форматирование с персонажем с положительным модификатором характеристики
        char_with_dex = {
            "pathway": "error",
            "stats": {"dex": 14},  # +2 мод
        }
        fmt_with_char = rules.format_pathway_mechanic(raw_mech, char_with_dex)
        self.assertNotIn("{dmg}", fmt_with_char["effect"])
        self.assertIn("2d6 + 2 урона", fmt_with_char["effect"])
        self.assertIn("сбивает врага с толку — крадёт +2 Защиты", fmt_with_char["effect"])

    def test_all_pathways_mechanics_format_without_placeholders_or_corrupt_chars(self):
        for path_key in lotm.PATHWAYS:
            mech_9 = lotm.pathway_mechanic_for(path_key, 9)
            formatted_9 = rules.format_pathway_mechanic(mech_9)
            eff_9 = formatted_9.get("effect", "")
            self.assertNotIn("{dmg}", eff_9, f"Path {path_key} still contains {{dmg}}")
            self.assertNotIn("{", eff_9, f"Path {path_key} still contains unparsed '{{'")
            self.assertNotIn("}", eff_9, f"Path {path_key} still contains unparsed '}}'")
            self.assertNotIn("\ufffd", eff_9, f"Path {path_key} contains corrupt character \ufffd")

            # Проверка для высоких рангов (Посл. 4)
            mech_4 = lotm.pathway_mechanic_for(path_key, 4)
            formatted_4 = rules.format_pathway_mechanic(mech_4)
            eff_4 = formatted_4.get("effect", "")
            self.assertNotIn("{dmg}", eff_4, f"High-tier {path_key} contains {{dmg}}")
            self.assertNotIn("\ufffd", eff_4, f"High-tier {path_key} contains corrupt character \ufffd")


if __name__ == "__main__":
    unittest.main()
