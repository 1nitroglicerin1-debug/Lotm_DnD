"""Правила и расчёты механик «Повелителя Тайн» (LotM) и бросков кубов.

Чистая логика без привязки к Telegram.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

import lotm_data as lotm

_rng = random.SystemRandom()


def sequence_prof_bonus(seq: int) -> int:
    """Бонус мастерства по текущей последовательности (9→4)."""
    return lotm.sequence_table().get(int(seq or 9), lotm.sequence_table()[9])["prof"]


def sequence_max_hp(
    path: str,
    seq: int,
    con_mod: int,
    feats: list[str] | None = None,
    faction_perk: str | None = None,
    pathway_choices: dict | None = None,
) -> int:
    """Макс. HP по системе LotM + бонусы фракции, черт и веток пути."""
    seq_int = int(seq or 9)
    eff_feats = list(feats or [])
    hp = lotm.max_hp_for(path, seq_int, con_mod, eff_feats)

    choices = pathway_choices or {}
    ch9 = choices.get("9") or choices.get(9) or ""
    if str(ch9).lower() == "a" and path in ("mother", "twilight_giant"):
        hp += 2 * lotm.OPEN_TIERS.get(seq_int, 1)

    ch4 = choices.get("4") or choices.get(4) or ""
    if seq_int <= 4 and path == "fool" and str(ch4).lower() == "a":
        hp += 30
    if seq_int <= 4 and path == "paragon" and str(ch4).lower() == "a":
        hp += 20

    ch1 = str(choices.get("1") or choices.get(1) or "").lower()
    ch0 = str(choices.get("0") or choices.get(0) or "").lower()
    if seq_int <= 1 and path == "mother" and ch1 == "b":
        hp += 60
    if seq_int == 0:
        if path in ("prisoner", "hanged_man", "paragon") and ch0 == "b":
            hp += 80
        elif path == "tyrant" and ch0 == "b":
            hp += 70
        elif path == "twilight_giant" and ch0 == "b":
            hp += 100

    return max(hp, 1)


def path_skills(path: str, choices: dict, current_seq: int, start_seq: int = 9) -> list[dict]:
    """Все умения билда от Посл. 9 до current_seq включительно."""
    tiers = lotm.PATHWAYS.get(path, {}).get("tiers", {})
    out: list[dict] = []
    ch: dict[int, str] = {}
    for k, v in (choices or {}).items():
        try:
            ch[int(k)] = v
        except (TypeError, ValueError):
            continue
    for seq in [s for s in lotm.SEQUENCES if s >= int(current_seq or 9)]:
        tier = lotm.TIER_OF_SEQUENCE.get(seq, 0)
        rank = lotm.SLOT_RANK_OF_TIER.get(tier, 0)
        if seq not in tiers:
            continue
        for key in ("core", "choice_a" if ch.get(seq, "a") == "a" else "choice_b"):
            name, act, save, dice, desc = tiers[seq][key]
            # Экономика ячеек: action платит полную цену ранга, bonus/reaction —
            # ранг на 1 ниже (Посл.8 = 0), но с перезарядкой 3 раунда.
            if seq == 9 and key == "core" and dice and "d" in dice:
                die_side = dice.split("d", 1)[1]
                cur_s = int(current_seq or 9)
                cantrip_mult = 1 if cur_s >= 8 else (2 if cur_s >= 6 else (3 if cur_s >= 4 else (4 if cur_s >= 2 else 6)))
                dice = f"{cantrip_mult}d{die_side}"
            if seq == 9 or act == "passive":
                cost, eff_rank, cd = 0, 0, 0
            elif act == "action":
                cost = 1
                eff_rank, cd = rank, 0
            else:
                eff_rank = max(0, rank - 1)
                cost = 1 if eff_rank > 0 else 0
                cd = 0 if cost == 0 else lotm.COOLDOWN_ROUNDS_SUB
            out.append({
                "seq": seq,
                "tier": tier,
                "key": key,
                "name": name,
                "action": act,
                "save": save,
                "dice": dice,
                "desc": desc,
                "slot_rank": eff_rank,
                "slot_cost": cost,
                "cooldown": cd,
                "once_per_battle": "раз за бой" in (desc or "").lower(),
            })
    return out


def best_path_stat(char: dict) -> tuple[str, int]:
    """Лучшая характеристика среди профильных для Пути: (ключ, модификатор)."""
    path = char.get("pathway") or ""
    raw_stat = lotm.PATHWAYS.get(path, {}).get("stat", "int")
    candidates = [s.strip() for s in raw_stat.split("/") if s.strip() in ABILITIES]
    if not candidates:
        candidates = ["int"]
    stats = char.get("stats") or {}
    best = max(candidates, key=lambda k: int(stats.get(k, 10)))
    return best, ability_mod(int(stats.get(best, 10)))


def effective_prof_bonus(char: dict) -> int:
    """Бонус мастерства: по последовательности LotM (если есть путь), иначе по уровню."""
    if char.get("pathway"):
        seq = int(char.get("sequence") or 9)
        return lotm.prof_bonus(seq)
    level = int(char.get("level") or 1)
    return proficiency_bonus(level)


ABILITIES: dict[str, tuple[str, str]] = {
    "str": ("Сила", "СИЛ"),
    "dex": ("Ловкость", "ЛОВ"),
    "con": ("Телосложение", "ТЕЛ"),
    "int": ("Интеллект", "ИНТ"),
    "wis": ("Мудрость", "МУД"),
    "cha": ("Харизма", "ХАР"),
}

SHORT_ABILITY: dict[str, str] = {k: v[1] for k, v in ABILITIES.items()}

# --------------------------------------------------------------------------
# Point-Buy (шаг 3 конструктора): 27 очков, база 10, минимум 8 / максимум 15
# --------------------------------------------------------------------------

POINT_BUY_BUDGET = 27
POINT_BUY_MIN = 8
POINT_BUY_MAX = 15

STAT_POOL = POINT_BUY_BUDGET
STAT_POOL_MIN = POINT_BUY_MIN
STAT_POOL_MAX = POINT_BUY_MAX

POINTBUY_DEFAULT = {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}
STAT_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 6, 15: 8}


def stat_cost(value: int) -> int:
    return STAT_COST.get(int(value), 99)


def stat_pool_spent(stats: dict[str, int]) -> int:
    return sum(stat_cost(v) for v in stats.values())


def stat_pool_available(stats: dict[str, int]) -> int:
    return POINT_BUY_BUDGET - stat_pool_spent(stats)


SKILLS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "athletics": ("Атлетика", "str", ()),
    "brawling": ("Силовой напор", "str", ("захват", "борьба")),
    "acrobatics": ("Акробатика", "dex", ()),
    "sleight_of_hand": ("Ловкость рук", "dex", ("кража",)),
    "stealth": ("Скрытность", "dex", ("стелс",)),
    "firearms": ("Стрельба и Механика боя", "dex", ("стрельба", "меткость", "оружие")),
    "arcana": ("Оккультизм и Зелья", "int", ("магия", "аркана", "оккультизм", "мистика")),
    "history": ("История Эпох", "int", ("история",)),
    "investigation": ("Криминалистика и Анализ", "int", ("анализ", "расследование", "дедукция")),
    "nature": ("Паровая Инженерия и Алхимия", "int", ("природа", "инженерия", "алхимия", "механика")),
    "religion": ("Ритуалистика и Астрология", "int", ("религия", "ритуалы", "астрология")),
    "animal_handling": ("Духовное зрение", "wis", ("животные", "уход за животными", "аура", "духовное зрение")),
    "insight": ("Проницательность и Психоанализ", "wis", ("проницательность", "психоанализ")),
    "medicine": ("Медицина и Токсикология", "wis", ("медицина", "яды")),
    "perception": ("Восприятие", "wis", ("внимательность", "перцепция")),
    "survival": ("Выживание и Выслеживание", "wis", ("выживание", "охота")),
    "deception": ("Обман и Актёрство", "cha", ("обман", "блеф")),
    "intimidation": ("Запугивание и Давление", "cha", ("запугивание", "устрашение")),
    "performance": ("Светский Этикет и Бюрократия", "cha", ("выступление", "этикет", "закон")),
    "persuasion": ("Убеждение и Дипломатия", "cha", ("убеждение", "дипломатия")),
}

# Алиасы для обратной совместимости старых сохранений БД
SKILL_ALIASES: dict[str, str] = {}

def skill_pick_budget(pathway: str, stats: dict) -> int:
    """Динамическое число навыков от Пути и Интеллекта (от 3 до 7)."""
    p = lotm.PATHWAYS.get(pathway, {})
    bone = int(p.get("bone", 8))
    base = 3 if bone >= 12 else (4 if bone == 10 else 4)
    if pathway in ("white_tower", "hermit", "paragon", "fool", "error", "visionary"):
        base += 1
    int_bonus = max(0, ability_mod(int((stats or {}).get("int", 10))))
    return min(8, max(3, base + int_bonus))

MAX_DICE = 50
MAX_SIDES = 1000


def ability_mod(score: int) -> int:
    return (int(score) - 10) // 2


def proficiency_bonus(level: int) -> int:
    return 2 + (int(level) - 1) // 4


def skill_total(char: dict, skill_key: str) -> int:
    _, ab, _ = SKILLS[skill_key]
    mult = int(char["skills"].get(skill_key, 0))
    total = ability_mod(char["stats"][ab]) + effective_prof_bonus(char) * mult
    path = lotm.PATHWAY_ALIASES.get(char.get("pathway") or "", char.get("pathway") or "")
    seq = int(char.get("sequence") or 9)
    choices = char.get("pathway_choices") or {}
    ch9 = str(choices.get("9") or choices.get(9) or "a").lower()
    ch8 = str(choices.get("8") or choices.get(8) or "a").lower()
    if path == "white_tower" and seq <= 9 and ch9 == "a":
        total += 2
    if path == "prisoner" and seq <= 9 and ch9 == "a" and ab == "dex":
        total += 3
    if path == "door" and seq <= 9 and ch9 == "b" and ab == "int":
        total += 2
    if path == "arbiter" and seq <= 8 and ch8 == "a" and skill_key == "perception":
        total += 4
    if path == "red_priest" and seq <= 9 and ch9 == "a" and skill_key == "survival":
        total += 3
    if path == "visionary" and seq <= 9 and ch9 == "b" and skill_key == "insight":
        total += 4
    if path == "hermit" and seq <= 9 and ch9 == "a" and skill_key == "arcana":
        total += 2
    bg = lotm.BACKGROUNDS.get(char.get("faction") or "")
    if bg and skill_key in bg.get("check_bonus", {}):
        total += int(bg["check_bonus"][skill_key])
    return total


def get_passive_voices(char: dict) -> str:
    """Возвращает 2 самых сильных навыка персонажа для пассивных вставок-подсказок в текст сцены."""
    scored = [(k, skill_total(char, k)) for k in SKILLS]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [f"{SKILLS[k][0]} ({val:+d})" for k, val in scored[:2] if val >= 4]
    return ", ".join(top) if top else f"{SKILLS[scored[0][0]][0]} ({scored[0][1]:+d})"


def save_total(char: dict, ability: str) -> int:
    mult = 1 if ability in char["saves"] else 0
    return ability_mod(char["stats"][ability]) + effective_prof_bonus(char) * mult


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е").replace("_", " ").strip()


def _build_indexes():
    skill_ab: dict[str, tuple[str, str]] = {}
    ab_only: dict[str, tuple[str, str]] = {}
    for key, (name, short) in ABILITIES.items():
        for alias in (name, short, key):
            ab_only[_norm(alias)] = ("ability", key)
    for key, (name, _ab, aliases) in SKILLS.items():
        for alias in (name, key, *aliases):
            skill_ab[_norm(alias)] = ("skill", key)
    full = dict(ab_only)
    full.update(skill_ab)
    return full, ab_only


_INDEX, _ABILITY_INDEX = _build_indexes()


def _label(target: tuple[str, str]) -> str:
    kind, key = target
    return SKILLS[key][0] if kind == "skill" else ABILITIES[key][0]


def _resolve(query: str, index: dict):
    q = _norm(query)
    if not q:
        return None, []
    if q in index:
        return index[q], []
    found = {v for k, v in index.items() if k.startswith(q)}
    if len(found) == 1:
        return next(iter(found)), []
    return None, sorted(_label(v) for v in found)


def resolve_check(query: str):
    return _resolve(query, _INDEX)


def resolve_ability(query: str):
    target, _ = _resolve(query, _ABILITY_INDEX)
    return target[1] if target else None


def parse_check_args(text: str):
    adv = dis = False
    bonus = 0
    words: list[str] = []
    for tok in text.split():
        t = tok.lower()
        if re.fullmatch(r"[+-]\d+", t):
            bonus += int(t)
        elif t.startswith(("adv", "преим")):
            adv = True
        elif t.startswith(("dis", "пом")):
            dis = True
        else:
            words.append(tok)
    mode = "adv" if adv and not dis else "dis" if dis and not adv else "normal"
    return " ".join(words), mode, bonus


@dataclass
class D20Result:
    title: str
    mode: str
    rolls: list[int]
    used_idx: int
    parts: list[tuple[str, int]] = field(default_factory=list)

    @property
    def natural(self) -> int:
        return self.rolls[self.used_idx]

    @property
    def total(self) -> int:
        return self.natural + sum(v for _, v in self.parts)


def roll_d20(mode: str = "normal") -> tuple[list[int], int]:
    if mode == "normal":
        return [_rng.randint(1, 20)], 0
    rolls = [_rng.randint(1, 20), _rng.randint(1, 20)]
    pick = max(rolls) if mode == "adv" else min(rolls)
    return rolls, rolls.index(pick)


def _make_test(
    char: dict,
    title: str,
    ability: str,
    prof_mult: int,
    mode: str,
    extra: int,
) -> D20Result:
    score = int(char["stats"].get(ability, 10))
    pb = effective_prof_bonus(char)

    parts = [(f"Модификатор {ABILITIES[ability][1]} ({score})", ability_mod(score))]
    if prof_mult == 1:
        parts.append((f"Мастерство (PB {pb})", pb))
    elif prof_mult >= 2:
        parts.append(("Компетенция (×2)", pb * 2))
    if extra:
        parts.append(("Доп. бонус", extra))

    rolls, idx = roll_d20(mode)
    return D20Result(title=title, mode=mode, rolls=rolls, used_idx=idx, parts=parts)


def skill_check(char: dict, target: tuple[str, str], mode="normal", extra=0) -> D20Result:
    kind, key = target
    if kind == "skill":
        name, ab, _ = SKILLS[key]
        mult = int(char["skills"].get(key, 0))
        title = f"{name} ({ABILITIES[ab][1]})"
    else:
        ab, mult = key, 0
        title = f"Проверка: {ABILITIES[key][0]}"
    try:
        import h_perks
        mech = h_perks.feat_mech(char)
        perk_mech = h_perks.perk_of(char)
        if perk_mech.get("stealth_adv") and kind == "skill" and key == "stealth":
            mode = "adv" if mode != "dis" else "normal"
        if mech.get("stealth_penalty") and kind == "skill" and key == "stealth":
            mode = "normal" if mode == "adv" else "dis"
        if (
            char.get("pathway") == "white_tower"
            and int(char.get("sequence") or 9) <= 9
            and str((char.get("pathway_choices") or {}).get("9", "")) == "a"
        ):
            extra = int(extra) + 2
        if perk_mech.get("skill_bonus"):
            extra = int(extra) + int(perk_mech["skill_bonus"])
    except Exception:
        pass
    return _make_test(char, title, ab, mult, mode, extra)


def saving_throw(char: dict, ability: str, mode="normal", extra=0) -> D20Result:
    mult = 1 if ability in char["saves"] else 0
    try:
        import h_perks
        sb, sb_adv = h_perks.save_extra(char, ability=ability)
        extra = int(extra) + sb
        if sb_adv and mode == "normal":
            mode = "adv"
    except Exception:
        pass
    return _make_test(char, f"Спасбросок: {ABILITIES[ability][0]}", ability, mult, mode, extra)


def initiative(char: dict, mode="normal", extra=0) -> D20Result:
    try:
        import h_perks
        extra = int(extra) + h_perks.initiative_bonus(char)
    except Exception:
        pass
    return _make_test(char, "Инициатива", "dex", 0, mode, extra)


_TERM = r"(?:\d*d\d+|\d+)"
_FULL = re.compile(rf"[+-]?{_TERM}(?:[+-]{_TERM})*")
_PART = re.compile(r"([+-]?)(?:(\d*)d(\d+)|(\d+))")


@dataclass
class Term:
    label: str
    rolls: list[int]
    value: int


@dataclass
class DiceRoll:
    expr: str
    terms: list[Term]

    @property
    def total(self) -> int:
        return sum(t.value for t in self.terms)


def roll_expression(expr: str) -> DiceRoll:
    s = expr.lower().replace(" ", "").replace("д", "d").replace("к", "d")
    if not _FULL.fullmatch(s):
        raise ValueError("Не понял выражение. Примеры: d20, 2d6+3, 4d8-1, 1d20+1d4+2")

    terms: list[Term] = []
    dice_count = 0
    for sign, cnt, sides, const in _PART.findall(s):
        sgn = -1 if sign == "-" else 1
        if sides:
            n, m = int(cnt or 1), int(sides)
            dice_count += n
            if n < 1 or dice_count > MAX_DICE:
                raise ValueError(f"Слишком много кубов (максимум {MAX_DICE} за бросок).")
            if not 2 <= m <= MAX_SIDES:
                raise ValueError(f"У куба должно быть от 2 до {MAX_SIDES} граней.")
            rolls = [_rng.randint(1, m) for _ in range(n)]
            terms.append(Term(f"{n}d{m}", rolls, sgn * sum(rolls)))
        else:
            terms.append(Term(const, [], sgn * int(const)))
    return DiceRoll(expr=s, terms=terms)


BLOOD_FACTIONS: set[str] = set()

CONDITIONS: dict[str, str] = {
    "blinded": "👁️ Ослеплён",
    "charmed": "💖 Очарован",
    "deafened": "🔕 Оглох",
    "frightened": "😨 Испуган",
    "grappled": "🤼 Захвачен",
    "incapacitated": "🌀 Беспомощен",
    "invisible": "👤 Невидим",
    "paralyzed": "⚡ Парализован",
    "petrified": "🗿 Окаменел",
    "poisoned": "🤢 Отравлен",
    "prone": "🧎 Сбит с ног",
    "restrained": "🕸️ Опутан",
    "stunned": "💫 Оглушён",
    "unconscious": "💤 Без сознания",
    "exhaustion": "📉 Истощение",
}


def resolve_condition(text: str) -> str | None:
    q = _norm(text)
    if not q:
        return None
    for key, name in CONDITIONS.items():
        if q == key or q == _norm(name):
            return key
    found = [k for k, n in CONDITIONS.items() if q in k or q in _norm(n)]
    return found[0] if len(found) == 1 else None


def apply_damage(hp: int, temp_hp: int, amount: int) -> tuple[int, int]:
    amount = max(0, int(amount))
    if amount <= temp_hp:
        return hp, temp_hp - amount
    return max(0, hp - (amount - temp_hp)), 0


def apply_heal(hp: int, max_hp: int, amount: int) -> int:
    amount = max(0, int(amount))
    return min(max_hp, hp + amount)


def apply_temp_hp(temp_hp: int, amount: int) -> int:
    return max(temp_hp, max(0, int(amount)))


# --------------------------------------------------------------------------
# LotM: отдых, духовность, регенерация
# --------------------------------------------------------------------------

def short_rest_heal(con_mod: int, faction: str | None = None) -> int:
    """HP за короткий отдых (медитация/ритуал): 1d8 + мод. ТЕЛ."""
    return max(1, _rng.randint(1, 8) + con_mod)


def spirituality_regen(
    sequence: int,
    faction: str | None = None,
    feats: list[str] | None = None,
) -> dict[int, int]:
    """Сколько ячеек духовности каждого ранга восстанавливает короткий отдых."""
    total = lotm.spell_slots_for(int(sequence or 9), feats)
    out: dict[int, int] = {}
    for rank, n in total.items():
        regen = n // 2 or 1
        out[rank] = min(n, regen)
    return out


def long_rest(char: dict) -> dict:
    """Полное восстановление HP и ячеек духовности с учётом фракции, веток и черт."""
    stats = char.get("stats") or {}
    con_mod = ability_mod(stats.get("con", 10))
    seq = int(char.get("sequence") or 9)
    feats = list(char.get("feats") or [])
    if char.get("pathway"):
        max_hp = sequence_max_hp(
            char["pathway"],
            seq,
            con_mod,
            feats,
            faction_perk=char.get("faction_perk"),
            pathway_choices=char.get("pathway_choices"),
        )
    else:
        max_hp = int(char.get("max_hp") or 1)
    slots = {
        str(rank): {"total": n, "left": n}
        for rank, n in lotm.spell_slots_for(seq, feats).items()
    }
    return {"max_hp": max_hp, "hp": max_hp, "temp_hp": 0, "spell_slots": slots}


def rest_heal_amount(char: dict) -> int:
    """Сколько HP даёт короткий отдых (медитация/ритуал) конкретному персонажу."""
    stats = char.get("stats") or {}
    con_mod = ability_mod(stats.get("con", 10))
    return short_rest_heal(con_mod, char.get("faction"))
