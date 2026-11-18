"""Правила и расчёты механик «Повелителя Тайн» (LotM) и бросков кубов.

Чистая логика без привязки к Telegram.
"""
from __future__ import annotations

import random
import re
import time
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


def skill_breakdown(char: dict, skill_key: str) -> list[tuple[str, int]]:
    """Возвращает полный список слагаемых бонуса навыка: [(название_источника, значение), ...]."""
    if skill_key not in SKILLS:
        skill_key = "perception"
    skill_name, ab, _ = SKILLS[skill_key]
    stats = char.get("stats") or {}
    score = int(stats.get(ab, 10))
    mod = ability_mod(score)
    parts: list[tuple[str, int]] = [(f"{ABILITIES[ab][0]} ({score})", mod)]

    pb = effective_prof_bonus(char)
    mult = int((char.get("skills") or {}).get(skill_key, 0))
    if mult == 1:
        parts.append((f"Владение навыком (Посл. {char.get('sequence', 9)})", pb))
    elif mult >= 2:
        parts.append(("Компетенция ★ (×2)", pb * 2))

    # Пассивные бонусы веток Пути (сбалансированные под Bounded Accuracy)
    path = lotm.PATHWAY_ALIASES.get(char.get("pathway") or "", char.get("pathway") or "")
    seq = int(char.get("sequence") or 9)
    choices = char.get("pathway_choices") or {}
    ch9 = str(choices.get("9") or choices.get(9) or "a").lower()
    ch8 = str(choices.get("8") or choices.get(8) or "a").lower()

    if path == "white_tower" and seq <= 9 and ch9 == "a":
        parts.append(("Пассивка «Энциклопедическая память»", 2))
    if path == "prisoner" and seq <= 9 and ch9 == "a" and ab == "dex":
        parts.append(("Пассивка «Тихая поступь»", 2))
    if path == "door" and seq <= 9 and ch9 == "b" and ab == "int":
        parts.append(("Пассивка «Знание рун»", 2))
    if path == "arbiter" and seq <= 8 and ch8 == "a" and skill_key == "perception":
        parts.append(("Пассивка «Бдительный взор»", 2))
    if path == "red_priest" and seq <= 9 and ch9 == "a" and skill_key == "survival":
        parts.append(("Пассивка «Знание повадок»", 2))
    if path == "visionary" and seq <= 9 and ch9 == "b" and skill_key == "insight":
        parts.append(("Пассивка «Абсолютная эмпатия»", 2))
    if path == "hermit" and seq <= 9 and ch9 == "a" and skill_key == "arcana":
        parts.append(("Пассивка «Глаз тайновидца»", 2))

    # Бонус Происхождения (Background)
    bg = lotm.BACKGROUNDS.get(char.get("faction") or "")
    if bg and skill_key in bg.get("check_bonus", {}):
        b_val = int(bg["check_bonus"][skill_key])
        if b_val:
            parts.append((f"Происхождение «{bg['name'].split()[-1]}»", b_val))

    # Экипированные Запечатанные Артефакты (+ к конкретному навыку или ко всем)
    for art in (char.get("artifacts") or []):
        if not isinstance(art, dict) or not art.get("equipped", True):
            continue
        if art.get("skill") == skill_key or art.get("skill") == "all":
            try:
                parts.append((f"Артефакт «{str(art.get('name', 'Реликвия'))[:18]}»", int(art.get("bonus", 0))))
            except (TypeError, ValueError):
                continue
    # Рассудок ≤25% (Грань Потери Контроля): механическое влияние безумия
    try:
        sanity = int(char.get("sanity", 100))
    except (TypeError, ValueError):
        sanity = 100
    if sanity <= 25:
        mind_skills = ("persuasion", "performance", "investigation", "history", "medicine", "insight")
        wild_skills = ("intimidation", "brawling", "arcana")
        if skill_key in mind_skills:
            parts.append(("Шёпот безумия (Рассудок ≤25%)", -2))
        elif skill_key in wild_skills:
            parts.append(("Искажение плоти (Рассудок ≤25%)", 2))

    return parts


def skill_total(char: dict, skill_key: str) -> int:
    return sum(v for _, v in skill_breakdown(char, skill_key))


SKILL_DOMAINS: dict[str, str] = {
    "athletics": "физическая мощь, преодоление препятствий, выбивание дверей, прыжки",
    "brawling": "рукопашный бой, захваты, болевые приёмы и баланс тела",
    "acrobatics": "равновесие, уклонение, акробатика и бесшумные кульбиты",
    "sleight_of_hand": "моторика пальцев, карманная кража, подмена предметов, тайники на теле",
    "stealth": "маскировка в тенях, шаг без звука, избегание взглядов и слежка",
    "firearms": "баллистика, огнестрел, траектории выстрелов и оружейная механика",
    "arcana": "мистические законы, свойства зелий, потусторонние силы и тайные символы",
    "history": "история эпох, древние цивилизации, династии и забытые хроники",
    "investigation": "физические следы, давность событий, логика, реконструкция и дедукция",
    "nature": "паровые механизмы, алхимические составы, яды и машинерия Тингена",
    "religion": "церковные догматы семи богов, астрология, ритуалы и молитвы",
    "animal_handling": "духовное зрение, цвета аур, призрачные эманации и звериное чутьё",
    "insight": "микровыражения, скрытые эмоции, язык тела и оценка искренности людей",
    "medicine": "анатомия, токсикология, характер ранений и физическое состояние",
    "perception": "слух, зрение, запахи, обнаружение скрытых деталей в окружении",
    "survival": "выслеживание следов, городские подворотни, пути отхода и опасности среды",
    "deception": "ложь, блеф, сокрытие истинных мотивов и фальшивые эмоции",
    "intimidation": "психологическое давление, подавление воли и угрозы",
    "performance": "светский этикет, правила приличия высшего общества и актёрская игра",
    "persuasion": "дипломатия, убеждение, переговоры и поиск компромиссов",
}


def get_passive_voices(char: dict) -> str:
    """Возвращает 2 самых сильных навыка персонажа с указанием их доменной сферы для пассивных вставок в текст сцены."""
    scored = [(k, skill_total(char, k)) for k in SKILLS]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [
        f"{SKILLS[k][0]} ({val:+d} | домен: {SKILL_DOMAINS.get(k, '')})"
        for k, val in scored[:2]
        if val >= 4
    ]
    if top:
        return "; ".join(top)
    k0, v0 = scored[0]
    return f"{SKILLS[k0][0]} ({v0:+d} | домен: {SKILL_DOMAINS.get(k0, '')})"


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


def base_dc_for_sequence(seq: int) -> tuple[int, int, int]:
    """Возвращает (Обычная_Сл, Сложная_Сл, Героическая_Сл) под текущую Последовательность (9->0)."""
    s = int(seq or 9)
    if s >= 8:
        return (12, 14, 17)
    if s >= 5:
        return (14, 17, 20)
    if s >= 3:
        return (17, 20, 23)
    return (20, 24, 28)


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
        if perk_mech.get("skill_bonus"):
            extra = int(extra) + int(perk_mech["skill_bonus"])
    except Exception:
        pass
    if kind == "skill":
        parts = skill_breakdown(char, key)
        if extra:
            parts.append(("Доп. модификатор", int(extra)))
        rolls, idx = roll_d20(mode)
        return D20Result(title=title, mode=mode, rolls=rolls, used_idx=idx, parts=parts)
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


# ============================================================================
# РЕАЛЬНЫЙ ИНВЕНТАРЬ ПЕРСОНАЖА (INVENTORY & DELTA ENGINE)
# ============================================================================

INVENTORY_CATEGORIES = ("gear", "clue", "consumable", "currency")


def normalize_item_name(name: str) -> str:
    """Нормализует имя предмета для надёжного сопоставления без учёта регистра."""
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def guess_item_category(name: str, desc: str = "") -> str:
    """Определяет категорию предмета по ключевым словам."""
    text = f"{name} {desc}".lower()
    if any(w in text for w in ("фунт", "солей", "соли", "пенс", "золот", "монет", "банкнот", "кошел", "капитал", "деньг", "наличн", "валют")):
        return "currency"
    if any(w in text for w in ("зель", "флакон", "соль", "порох", "спичк", "бинт", "экстракт", "мазь", "яд", "эликсир", "патрон", "пул", "лекарств", "сыворот")):
        return "consumable"
    if any(w in text for w in ("улик", "записк", "письм", "дневник", "блокнот", "книг", "отчет", "шифр", "документ", "фото", "конверт", "газет", "удостоверен", "пропуск", "чек", "вексель", "карт")):
        return "clue"
    return "gear"


def apply_inventory_delta(
    char_or_inv: dict | list,
    add_list: list | None = None,
    remove_list: list | None = None,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Применяет дельту инвентаря от ИИ-Мастера или игровой механики.

    - Объединяет предметы с одинаковыми именами, увеличивая qty.
    - Удаляет или уменьшает количество при расходе.
    - Возвращает кортеж: (обновлённый список инвентаря, {'added': [...], 'removed': [...]}).
    """
    is_char = isinstance(char_or_inv, dict)
    inv: list[dict] = list(char_or_inv.get("inventory") or []) if is_char else list(char_or_inv or [])

    # Очистка и нормализация существующих записей
    cleaned_inv: list[dict] = []
    for item in inv:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        cleaned_inv.append({
            "id": str(item.get("id") or f"inv_{int(time.time())}_{_rng.randint(1000, 9999)}"),
            "name": name,
            "qty": max(1, int(item.get("qty") or 1)),
            "category": str(item.get("category") or guess_item_category(name, item.get("description", ""))).lower(),
            "description": str(item.get("description") or f"Предмет: {name}").strip(),
        })
    inv = cleaned_inv

    added_records: list[dict] = []
    removed_records: list[dict] = []

    # 1. Применение добавлений (inventory_add)
    for raw in (add_list or []):
        if isinstance(raw, str):
            raw = {"name": raw.strip()}
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        qty = max(1, int(raw.get("qty") or 1))
        cat = str(raw.get("category") or "").strip().lower()
        if cat not in INVENTORY_CATEGORIES:
            cat = guess_item_category(name, raw.get("description", ""))
        desc = str(raw.get("description") or "").strip()

        norm_target = normalize_item_name(name)
        existing = next((it for it in inv if normalize_item_name(it["name"]) == norm_target), None)
        if existing:
            existing["qty"] = int(existing.get("qty", 1)) + qty
            if desc and (not existing.get("description") or existing["description"].startswith("Предмет:")):
                existing["description"] = desc
            added_records.append({
                "id": existing["id"],
                "name": existing["name"],
                "qty": qty,
                "category": existing.get("category", cat),
                "description": existing.get("description", desc),
            })
        else:
            new_item = {
                "id": str(raw.get("id") or f"inv_{int(time.time())}_{_rng.randint(1000, 9999)}"),
                "name": name,
                "qty": qty,
                "category": cat,
                "description": desc or f"Предмет: {name}",
            }
            inv.append(new_item)
            added_records.append(dict(new_item))

    # 2. Применение удалений / трат (inventory_remove)
    for raw in (remove_list or []):
        if isinstance(raw, str):
            raw = {"name": raw.strip()}
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        qty_to_rem = max(1, int(raw.get("qty") or 1))
        norm_target = normalize_item_name(name)

        target_item = next((it for it in inv if normalize_item_name(it["name"]) == norm_target), None)
        if not target_item:
            target_item = next(
                (it for it in inv if norm_target in normalize_item_name(it["name"]) or normalize_item_name(it["name"]) in norm_target),
                None,
            )

        if target_item:
            curr_qty = int(target_item.get("qty", 1))
            actual_rem = min(curr_qty, qty_to_rem)
            target_item["qty"] = curr_qty - qty_to_rem
            removed_records.append({
                "id": target_item["id"],
                "name": target_item["name"],
                "qty": actual_rem,
                "category": target_item.get("category", "gear"),
            })

    # Оставляем только предметы с qty > 0
    inv = [it for it in inv if int(it.get("qty", 0)) > 0]

    if is_char:
        char_or_inv["inventory"] = inv

    return inv, {"added": added_records, "removed": removed_records}


def starting_inventory(pathway: str, background_key: str) -> list[dict]:
    """Генерирует богатый стартовый инвентарь на основе Предыстории и Пути Потустороннего."""
    bg_key = (background_key or "detective").lower()
    bg = lotm.BACKGROUNDS.get(bg_key, {})
    gold_amount = int(bg.get("gold", 25))

    items: list[dict] = [
        {
            "id": f"inv_gold_{_rng.randint(100, 999)}",
            "name": f"{gold_amount} золотых фунтов",
            "qty": gold_amount,
            "category": "currency",
            "description": "Чеканные золотые соверены Королевства Лоэн с профилем короля Георга III.",
        }
    ]

    if bg_key == "detective":
        items.extend([
            {
                "id": "inv_det_gun",
                "name": "Револьвер .38 калибра",
                "qty": 1,
                "category": "gear",
                "description": "Шестизарядный барабанный револьвер системы Смита с нарезным стволом.",
            },
            {
                "id": "inv_det_lens",
                "name": "Медная лупа сыщика",
                "qty": 1,
                "category": "gear",
                "description": "Увеличительное стекло в тяжёлой оправе для криминалистического анализа мест преступлений.",
            },
            {
                "id": "inv_det_notes",
                "name": "Записная книжка сыщика и карандаш",
                "qty": 1,
                "category": "clue",
                "description": "Карманный блокнот в кожаном переплёте для заметок, чертежей и улик.",
            },
        ])
    elif bg_key == "noble":
        items.extend([
            {
                "id": "inv_noble_ring",
                "name": "Фамильный перстень с печатью",
                "qty": 1,
                "category": "gear",
                "description": "Тяжёлое золотое кольцо с родовым гербом древней дворянской фамилии.",
            },
            {
                "id": "inv_noble_cane",
                "name": "Трость со скрытым клинком",
                "qty": 1,
                "category": "gear",
                "description": "Лакированная эбеновая трость с набалдашником и спрятанной тонкой стальной шпагой.",
            },
            {
                "id": "inv_noble_bill",
                "name": "Банковский вексель Баклунда",
                "qty": 1,
                "category": "clue",
                "description": "Ценная бумага с водяными знаками на предъявителя.",
            },
        ])
    elif bg_key == "scholar":
        items.extend([
            {
                "id": "inv_sch_dict",
                "name": "Словарь древнего Гермеса",
                "qty": 1,
                "category": "clue",
                "description": "Фолиант в тиснёной кожаной обложке с расшифровкой ритуальных символов и рун.",
            },
            {
                "id": "inv_sch_pass",
                "name": "Пропуск в закрытый архив",
                "qty": 1,
                "category": "clue",
                "description": "Официальное разрешение ректората университета на работу со спецхраном древностей.",
            },
            {
                "id": "inv_sch_glass",
                "name": "Золотое пенсне",
                "qty": 1,
                "category": "gear",
                "description": "Оптические линзы для чтения мелких манускриптов и изучения древних надписей.",
            },
        ])
    elif bg_key == "smuggler":
        items.extend([
            {
                "id": "inv_smug_picks",
                "name": "Набор стальных отмычек",
                "qty": 1,
                "category": "gear",
                "description": "Комплект тонких воровских щупов и воротка в кожаном футляре.",
            },
            {
                "id": "inv_smug_knife",
                "name": "Сапожный кинжал",
                "qty": 1,
                "category": "gear",
                "description": "Узкое зазубренное лезвие из воронёной стали, спрятанное в голенище.",
            },
            {
                "id": "inv_smug_box",
                "name": "Свинцовая шкатулка",
                "qty": 1,
                "category": "gear",
                "description": "Шкатулка со свинцовыми стенками, экранирующая духовное излучение потусторонних материалов.",
            },
        ])
    elif bg_key == "veteran":
        items.extend([
            {
                "id": "inv_vet_gun",
                "name": "Армейский офицерский револьвер",
                "qty": 1,
                "category": "gear",
                "description": "Надёжное крупнокалиберное оружие времён колониальных кампаний в Баламе.",
            },
            {
                "id": "inv_vet_knuckle",
                "name": "Траншейный кастет",
                "qty": 1,
                "category": "gear",
                "description": "Тяжёлый латунный кастет с зазубринами для рукопашного боя.",
            },
            {
                "id": "inv_vet_flask",
                "name": "Фляга колониального рома",
                "qty": 1,
                "category": "consumable",
                "description": "Обожжённая стальная фляга с крепким терпким алкоголем.",
            },
        ])
    elif bg_key == "surgeon":
        items.extend([
            {
                "id": "inv_surg_kit",
                "name": "Хирургический саквояж со скальпелями",
                "qty": 1,
                "category": "gear",
                "description": "Кожаный саквояж с набором стерильных зажимов, игл и скальпелей.",
            },
            {
                "id": "inv_surg_chloroform",
                "name": "Флакон с хлороформом",
                "qty": 2,
                "category": "consumable",
                "description": "Летучее анестезирующее вещество для медицинских и экстренных ситуаций.",
            },
            {
                "id": "inv_surg_salts",
                "name": "Нюхательная соль",
                "qty": 1,
                "category": "consumable",
                "description": "Резкий состав, мгновенно проясняющий помутнённое сознание и возвращающий рассудок.",
            },
        ])
    elif bg_key == "acolyte":
        items.extend([
            {
                "id": "inv_acol_dagger",
                "name": "Серебряный ритуальный кинжал",
                "qty": 1,
                "category": "gear",
                "description": "Клинок из чистого серебра для черчения духовных барьеров и направления духовности.",
            },
            {
                "id": "inv_acol_mask",
                "name": "Маска тайного собрания",
                "qty": 1,
                "category": "gear",
                "description": "Гладкая маска с капюшоном, маскирующая черты лица и духовную ауру.",
            },
            {
                "id": "inv_acol_candles",
                "name": "Свечи из глубоководного жира",
                "qty": 3,
                "category": "consumable",
                "description": "Ритуальные свечи с бледно-голубым пламенем для умиротворения духов.",
            },
        ])
    elif bg_key == "watcher":
        items.extend([
            {
                "id": "inv_watch_lantern",
                "name": "Фонарь из шкуры монстра",
                "qty": 1,
                "category": "gear",
                "description": "Тёмный фонарь с фокусированным лучом, пробивающим аномальный туман.",
            },
            {
                "id": "inv_watch_blade",
                "name": "Тяжёлый тесак дозорного",
                "qty": 1,
                "category": "gear",
                "description": "Массивный закалённый клинок для разрубания плотных панцирей тварей ночи.",
            },
            {
                "id": "inv_watch_salt",
                "name": "Мешочек очищенной соли",
                "qty": 2,
                "category": "consumable",
                "description": "Крупная ритуальная соль для изгнания низших духов и очищения порога.",
            },
        ])
    elif bg_key == "broker":
        items.extend([
            {
                "id": "inv_brok_watch",
                "name": "Золотые карманные часы на цепочке",
                "qty": 1,
                "category": "gear",
                "description": "Высокоточный часовой механизм баклундских мастеров с гравировкой.",
            },
            {
                "id": "inv_brok_ledger",
                "name": "Книга биржевых котировок",
                "qty": 1,
                "category": "clue",
                "description": "Сводка акций колониальных синдикатов и пароходных компаний.",
            },
            {
                "id": "inv_brok_pen",
                "name": "Позолоченное перьевое перо",
                "qty": 1,
                "category": "gear",
                "description": "Элегантное перо для быстрого подписания контрактов и долговых расписок.",
            },
        ])
    else:
        items.extend([
            {
                "id": "inv_def_journal",
                "name": "Записная книжка и графитовый карандаш",
                "qty": 1,
                "category": "clue",
                "description": "Карманный блокнот в плотной обложке для фиксации улик и расчётов.",
            },
        ])

    has_matches = any("спичк" in it["name"].lower() for it in items)
    if not has_matches:
        items.append({
            "id": "inv_matches_box",
            "name": "Коробок влагостойких спичек",
            "qty": 10,
            "category": "consumable",
            "description": "Надёжные серные спички фабрики Баклунда в просмоленной коробочке.",
        })
    has_salt = any("соль" in it["name"].lower() for it in items)
    if not has_salt:
        items.append({
            "id": "inv_spirit_salt",
            "name": "Мешочек духовной соли",
            "qty": 1,
            "category": "consumable",
            "description": "Очищенная ритуальная соль для создания барьеров против злых духов.",
        })

    p_low = (pathway or "").lower()
    path_item = None
    if p_low in ("seer", "fool"):
        path_item = {
            "id": "inv_path_tarot",
            "name": "Колода карт Таро",
            "qty": 1,
            "category": "gear",
            "description": "Полная колода из 22 Старших и 56 Младших Арканов для прорицания и гадания на снах.",
        }
    elif p_low in ("apprentice", "door"):
        path_item = {
            "id": "inv_path_key",
            "name": "Медный ключ ученика",
            "qty": 1,
            "category": "gear",
            "description": "Универсальный ключ из сплава меди и звёздного песка, резонирующий с замками.",
        }
    elif p_low in ("marauder", "error"):
        path_item = {
            "id": "inv_path_gloves",
            "name": "Тонкие перчатки Вора",
            "qty": 1,
            "category": "gear",
            "description": "Перчатки из тонкой оленьей кожи, передающие малейшие вибрации механизмов.",
        }
    elif p_low in ("spectator", "visionary"):
        path_item = {
            "id": "inv_path_monocle",
            "name": "Круглое стеклянное монокль",
            "qty": 1,
            "category": "gear",
            "description": "Оптическое монокль для скрытного наблюдения за мимикой и зрачками собеседника.",
        }
    elif p_low in ("sailor", "tyrant"):
        path_item = {
            "id": "inv_path_compass",
            "name": "Латунный морской компас",
            "qty": 1,
            "category": "gear",
            "description": "Морской компас, стрелка которого реагирует на шторма и духовные возмущения.",
        }
    elif p_low in ("sleepless", "darkness"):
        path_item = {
            "id": "inv_path_dust",
            "name": "Флакон со снотворной пыльцой",
            "qty": 2,
            "category": "consumable",
            "description": "Вытяжка ночного цветка, погружающая в глубокий спокойный сон.",
        }
    elif p_low in ("corpse_collector", "death"):
        path_item = {
            "id": "inv_path_balm",
            "name": "Бальзам нетления",
            "qty": 1,
            "category": "consumable",
            "description": "Мазь с запахом мирры, маскирующая запах живой плоти от нежити.",
        }
    elif p_low in ("hunter", "red_priest"):
        path_item = {
            "id": "inv_path_trap",
            "name": "Складной стальной капкан",
            "qty": 1,
            "category": "gear",
            "description": "Карманный зубчатый капкан с пружиной мгновенного взведения.",
        }
    elif p_low in ("assassin", "demoness"):
        path_item = {
            "id": "inv_path_poison",
            "name": "Флакон с бесцветным ядом",
            "qty": 2,
            "category": "consumable",
            "description": "Быстродействующий парализующий яд без вкуса и запаха.",
        }
    elif p_low in ("mystery_pryer", "hermit"):
        path_item = {
            "id": "inv_path_tonic",
            "name": "Успокаивающий травяной тоник",
            "qty": 2,
            "category": "consumable",
            "description": "Густой горький отвар, снимающий шум и навязчивые слуховые галлюцинации.",
        }
    elif p_low in ("warrior", "twilight_giant"):
        path_item = {
            "id": "inv_path_stone",
            "name": "Точильный камень оружейника",
            "qty": 1,
            "category": "gear",
            "description": "Мелкозернистый брусок для идеальной правки боевых лезвий.",
        }
    elif p_low in ("monster", "wheel_of_fortune"):
        path_item = {
            "id": "inv_path_coin",
            "name": "Счастливая медная монета",
            "qty": 1,
            "category": "gear",
            "description": "Монета неизвестной древней чеканки с идеально сбалансированным центром тяжести.",
        }

    if path_item:
        items.append(path_item)

    return items


def format_pathway_mechanic(mech: dict | None, char: dict | None = None) -> dict:
    """Форматирует описание классовой механики пути для отображения в UI (веб-интерфейс, досье).

    Подставляет значение урона/кости вместо плейсхолдера {dmg} и устраняет неразрешённые шаблоны.
    Если у способности указана кость (например, 2d6) и модификатор характеристики, формирует формулу.
    """
    if not mech or not isinstance(mech, dict):
        return {}
    res = dict(mech)
    effect = str(res.get("effect") or "")
    dice = str(res.get("dice") or "").strip()

    stat_mod = 0
    if char and isinstance(char, dict) and char.get("pathway"):
        _, stat_mod = best_path_stat(char)

    if dice:
        if stat_mod > 0:
            dmg_repr = f"{dice} + {stat_mod}"
        elif stat_mod < 0:
            dmg_repr = f"{dice} - {abs(stat_mod)}"
        else:
            dmg_repr = dice
    elif stat_mod != 0:
        dmg_repr = f"{stat_mod:+d}"
    else:
        dmg_repr = "урон"

    if "{dmg}" in effect:
        effect = effect.replace("{dmg}", dmg_repr)
    # Защитная зачистка любых нераскрытых плейсхолдеров
    effect = re.sub(r"\{([^}]+)\}", r"\1", effect)
    # Замена битых символов замены UTF-8 (\ufffd) на длинное тире
    effect = effect.replace("\ufffd", "—")
    res["effect"] = effect

    if "name" in res:
        res["name"] = str(res["name"]).replace("\ufffd", "—")
    if "resource" in res:
        res["resource"] = str(res["resource"]).replace("\ufffd", "—")

    return res
