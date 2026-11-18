"""combat_engine.py — ЕДИНЫЙ чистый движок пошагового боя 1-на-1 (LotM DnD).

Единственный источник боевой математики для ОБОИХ транспортов:
  * Telegram-слой  (h_story._handle_combat_free_text / h_story.cb_combat_attack)
  * WebApp-слой    (web_app.api_combat_action)

Принципы движка:
  1. Чистое ядро: никаких обращений к БД, aiogram или FastAPI. Слой вызова
     применяет итоговый CombatOutcome к базе и рендерит события на своём UI.
  2. Честный рандом: ВСЕ броски идут через d20()/d()/roll()/randint() этого
     модуля — тесты подменяют их для детерминизма.
  3. События вместо строк: движок возвращает список событий-словарей
     (kind, icon, text, bold, roll), а render_html()/render_plain() превращают
     их в Telegram-HTML или plain-текст для веба (журнал сам экранирует).
  4. Броски ИГРОКА дублируются в event["roll"] со side="player" — слои пишут
     их в roll_log (Хроника подвигов); броски врага (side="enemy") не логируются
     как броски игрока.
"""
from __future__ import annotations

import random
import re

import lotm_data as lotm
import rules

_rng = random.SystemRandom()

_SIMPLE_DICE = re.compile(r"^(?:(?P<n>\d+)d(?P<sides>\d+)|d(?P<sides2>\d+))$")


class CombatError(Exception):
    """Недоступное действие: нет зарядов/ячеек/Рассудка, умение не найдено."""


# ---------------------------------------------------------------------------
# RNG-шлюзы (точки внедрения детерминизма в тестах)
# ---------------------------------------------------------------------------
def d20() -> int:
    return _rng.randint(1, 20)


def d(n: int) -> int:
    return _rng.randint(1, n)


def roll(expr: str) -> rules.DiceRoll:
    """Бросок выражения: простые кубы идут через локальный _rng (детерминизм тестов),
    сложные выражения — через rules.roll_expression (у него свой источник случайности)."""
    m = _SIMPLE_DICE.fullmatch(str(expr or "").strip().lower())
    if m:
        n = int(m.group("n") or 1)
        sides = int(m.group("sides") or m.group("sides2"))
        rolls = [_rng.randint(1, sides) for _ in range(n)]
        total = sum(rolls)
        return rules.DiceRoll(
            expr=str(expr),
            terms=[rules.Term(f"{n}d{sides}", rolls, total)],
        )
    return rules.roll_expression(expr)


def randint(a: int, b: int) -> int:
    return _rng.randint(a, b)


def _mkroll(label: str, detail: str, total: int, natural: int | None,
            side: str = "player", crit: bool = False) -> dict:
    return {"label": label, "detail": detail, "total": total,
            "natural": natural, "side": side, "crit": crit}


# ---------------------------------------------------------------------------
# Контекст раунда и итог
# ---------------------------------------------------------------------------
def _ctx(c: dict) -> dict:
    """Общие модификаторы персонажа, нужные каждому боевому действию."""
    _, stat_mod = rules.best_path_stat(c)
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(
        c["pathway"], choices, int(c.get("sequence", 9)), int(c.get("start_sequence", 9)),
    )
    return {
        "stat_mod": stat_mod,
        "prof": rules.effective_prof_bonus(c),
        "skills": skills,
        "uses": dict(c.get("lair_uses") or {}),
    }


class CombatOutcome:
    """Итог одного боевого раунда: события + дельта персонажа + статус боя."""

    __slots__ = ("events", "delta", "uses", "spell_slots", "enemy", "victory", "gray_fog_save")

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.delta: dict[str, int] = {}   # hp/temp_hp/sanity (+digestion при победе)
        self.uses: dict | None = None     # обновлённые lair_uses (если менялись)
        self.spell_slots: dict | None = None  # обновлённые ячейки (если тратились)
        self.enemy: dict | None = None    # мутированный враг (None = бой окончен)
        self.victory: bool = False
        self.gray_fog_save: bool = False

    def ev(self, kind: str, icon: str, text: str, bold: str = "",
           roll: dict | None = None) -> None:
        self.events.append({"kind": kind, "icon": icon, "text": text,
                           "bold": bold, "roll": roll})


# ---------------------------------------------------------------------------
# Действия игрока
# ---------------------------------------------------------------------------
def act_cantrip(c: dict, enemy: dict, o: CombatOutcome, ctx: dict) -> None:
    """Бесплатный Заговор (core-умение Посл. 9)."""
    cantrip = next((s for s in ctx["skills"] if s["seq"] == 9 and s["key"] == "core"), None)
    name = cantrip["name"] if cantrip else "Удар Потустороннего"
    dice = cantrip["dice"] if cantrip and cantrip.get("dice") else "1d8"
    stat_mod, prof = ctx["stat_mod"], ctx["prof"]

    nat = d20()
    total = nat + prof + stat_mod
    crit = nat == 20
    ac = int(enemy["ac"])
    rl = _mkroll(name, f"atk vs КД {ac}", total, nat, crit=crit)
    if crit or total >= ac:
        r = roll(dice)
        dmg = max(1, r.total + stat_mod + (r.total if crit else 0))
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        o.ev("hit", "🗡",
             f"{name}: попадание {total} против КД {ac}" + (" (КРИТ!)" if crit else ""),
             f"−{dmg} ОЗ врагу!", rl)
    else:
        o.ev("miss", "🗡", f"{name}: бросок {total} (d20={nat}) против КД {ac} — промах!",
             roll=rl)


def act_mech(c: dict, enemy: dict, o: CombatOutcome, ctx: dict) -> None:
    """Механика Пути: тратит заряд path_res, урон + духовный щит."""
    p_mech = (lotm.pathway_mechanic_for(c["pathway"], int(c.get("sequence", 9)))
              if hasattr(lotm, "pathway_mechanic_for")
              else lotm.PATHWAY_MECHANICS.get(c["pathway"]))
    left = int(ctx["uses"].get("path_res", p_mech.get("max_base", 3) if p_mech else 0))
    if not p_mech or left <= 0:
        raise CombatError("Заряды механики Пути закончились!")

    ctx["uses"]["path_res"] = left - 1
    tier_mult = max(1, lotm.OPEN_TIERS.get(int(c.get("sequence", 9)), 1) // 2 + 1)
    die_side = int(str(p_mech["dice"]).split("d")[1])
    r = roll(f"{tier_mult}d{die_side}")
    dmg = max(2, r.total + ctx["stat_mod"])
    enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
    o.delta["temp_hp"] = max(int(c.get("temp_hp") or 0), tier_mult * 3)
    o.ev("mech", "⚡", f"{p_mech['name']}: наносит {dmg} урона врагу и укрепляет духовный щит (+{tier_mult * 3} ОЗ)!",
         roll=_mkroll(p_mech["name"], f"{tier_mult}d{die_side}", r.total, None))


def act_myth(c: dict, enemy: dict, o: CombatOutcome, ctx: dict) -> None:
    """Форма Мифического Существа: раз в главу, цена — Рассудок."""
    if ctx["uses"].get("mythical_used"):
        raise CombatError("Форма Мифического Существа уже использована в этой главе!")
    if int(c.get("sequence", 9)) > 4:
        raise CombatError("Форма Мифического Существа доступна только на Посл. 4 и выше!")
    cost = 10 if "mythical_anchor" in (c.get("feats") or []) else 20
    if o.delta.get("sanity", int(c.get("sanity", 100))) < cost:
        raise CombatError("Недостаточно Рассудка для поддержания Формы!")

    ctx["uses"]["mythical_used"] = 1
    seq = int(c.get("sequence", 9))
    r = roll(f"{10 - seq}d10")
    dmg = r.total + ctx["stat_mod"]
    enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
    o.ev("myth", "👁",
         f"Форма Мифического Существа! Враг получает {dmg} сокрушительного урона",
         f"духовный щит: +{(10 - seq) * 10} ОЗ!",
         _mkroll("Форма Мифического Существа", f"{10 - seq}d10", r.total, None))
    o.delta["sanity"] = int(c.get("sanity", 100)) - cost
    o.delta["temp_hp"] = max(int(c.get("temp_hp") or 0), (10 - seq) * 10)


def act_skill(c: dict, enemy: dict, o: CombatOutcome, ctx: dict, seq: int, key: str) -> None:
    """Именованное умение Пути: тратит ячейку, лечит или бьёт (спасбросок врага)."""
    sk = next((s for s in ctx["skills"] if s["seq"] == seq and s["key"] == key), None)
    if not sk:
        raise CombatError("Умение не найдено")

    cost, rank = int(sk["slot_cost"]), int(sk["slot_rank"])
    if cost > 0:
        slots = {str(k): dict(v) for k, v in (c.get("spell_slots") or {}).items()}
        bucket = slots.get(str(rank)) or {}
        if int(bucket.get("left", 0)) < cost:
            raise CombatError(f"Не хватает ячеек Р{rank}! Используй бесплатный Заговор, "
                              "опиши трюк или отдохни.")
        bucket["left"] = int(bucket.get("left", 0)) - cost
        slots[str(rank)] = bucket
        o.spell_slots = slots

    val = roll(sk["dice"]).total + ctx["stat_mod"] if sk.get("dice") \
        else (ctx["stat_mod"] + ctx["prof"] * 2)
    low_desc = (sk.get("desc") or "").lower()
    if any(w in low_desc for w in ("лечит", "исцел", "восстанавлив")) and "урон" not in low_desc:
        o.delta["hp"] = rules.apply_heal(int(c["hp"]), int(c["max_hp"]), val)
        o.ev("heal", "✨", f"{sk['name']}: восстанавливает тебе +{val} ОЗ!",
             roll=_mkroll(sk["name"], "heal", val, None))
        return

    dc = 8 + ctx["prof"] + ctx["stat_mod"]
    e_nat = d20()
    e_save = e_nat + int(enemy.get("save_bonus", 2))
    if sk.get("save") and e_save >= dc:
        dmg = max(1, val // 2)
        o.ev("hit", "✨", f"{sk['name']}: враг выбросил спасбросок {e_save} (Сл {dc})",
             f"−{dmg} ОЗ (50%)!",
             _mkroll(enemy.get("name", "Враг"), f"save vs Сл {dc}", e_save, e_nat, side="enemy"))
    else:
        dmg = max(1, val)
        o.ev("hit", "✨", f"{sk['name']}: прямое поражение цели", f"−{dmg} ОЗ врагу!",
             _mkroll(sk["name"], "spell dmg", val, None))
    enemy["hp"] = max(0, int(enemy["hp"]) - dmg)


def act_stunt(c: dict, enemy: dict, o: CombatOutcome, ctx: dict,
              sk_key: str, dc: int, summary: str, stunt_text: str) -> bool:
    """Свободный боевой трюк: проверка навыка; успех дезориентирует врага.

    Возвращает True, если враг дезориентирован (атакует с помехой).
    """
    roll_res = rules.skill_check(c, ("skill", sk_key))
    success = roll_res.natural == 20 or (roll_res.natural != 1 and roll_res.total >= dc)
    sk_name = rules.SKILLS[sk_key][0]
    cantrip = next((s for s in ctx["skills"] if s["seq"] == 9 and s["key"] == "core"), None)
    dice = cantrip["dice"] if cantrip and cantrip.get("dice") else "1d8"
    label = f"Трюк: {summary or stunt_text[:45]}"
    rl = _mkroll(label, f"{sk_name} Сл {dc}", roll_res.total, roll_res.natural,
                 crit=roll_res.natural == 20)

    if success:
        base = roll(dice)
        bonus = randint(2, 6) + (base.total if roll_res.natural == 20 else 0)
        dmg = max(2, base.total + ctx["stat_mod"] + bonus)
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        o.ev("stunt_hit", "🎲",
             f"{sk_name} (Сл {dc}): {roll_res.total} (d20={roll_res.natural}) — "
             "твой нестандартный ход застаёт врага врасплох",
             f"трюк удался! −{dmg} ОЗ врагу и помеха его атаке!", rl)
        return True

    grazed = max(1, (roll(dice).total + ctx["stat_mod"]) // 2)
    enemy["hp"] = max(0, int(enemy["hp"]) - grazed)
    o.ev("stunt_miss", "🎲",
         f"{sk_name} (Сл {dc}): {roll_res.total} (d20={roll_res.natural}) — "
         "трюк сорвался, удар прошёл вскользь",
         f"всего −{grazed} ОЗ врагу.", rl)
    return False


# ---------------------------------------------------------------------------
# Враг, победа и Серый Туман
# ---------------------------------------------------------------------------
def enemy_attack(c: dict, enemy: dict, o: CombatOutcome, ctx: dict,
                 ac_bonus: int, disoriented: bool) -> None:
    nat = min(d20(), d20()) if disoriented else d20()
    total = nat + int(enemy.get("atk_bonus", 3))
    eff_ac = int(c.get("ac", 10)) + ac_bonus
    rl = _mkroll(enemy.get("name", "Враг"), f"atk vs КД {eff_ac}", total, nat,
                 side="enemy", crit=nat == 20)

    if nat == 20 or total >= eff_ac:
        r = roll(enemy.get("dmg_dice", "1d8"))
        dmg = max(1, r.total + int(enemy.get("dmg_mod", 1)))
        cur_hp = o.delta.get("hp", int(c["hp"]))
        cur_temp = o.delta.get("temp_hp", int(c.get("temp_hp") or 0))
        o.delta["hp"], o.delta["temp_hp"] = rules.apply_damage(cur_hp, cur_temp, dmg)
        verb = ("дезориентирован твоим трюком, но всё же достаёт тебя"
                if disoriented else "контратакует")
        o.ev("enemy_hit", "👹", f"{enemy['name']} {verb} ({total} против КД {eff_ac})",
             f"−{dmg} ОЗ тебе!", rl)
    else:
        verb = ("дезориентирован твоим трюком и мажет"
                if disoriented else "атакует, но ты уходишь от удара")
        o.ev("enemy_miss", "🛡", f"{enemy['name']} {verb} ({total} против КД {eff_ac})!", roll=rl)


def _finish_victory(c: dict, o: CombatOutcome, enemy_name: str) -> None:
    dig = 3 if int(c.get("sequence", 9)) >= 7 else 2
    new_dig = min(100, int(c.get("digestion") or 0) + dig)
    o.delta["digestion"] = new_dig
    o.victory = True
    o.enemy = None
    o.ev("victory", "🏆", f"{enemy_name} повержен, а его потусторонняя черта отделяется в тумане.",
         f"Метод отыгрыша в бою: +{dig}% к перевариванию зелья (итого: {new_dig}%).")


def _gray_fog_save(c: dict, o: CombatOutcome) -> None:
    o.delta["hp"] = max(1, int(c["max_hp"]) // 2)
    o.delta["temp_hp"] = 0
    o.delta["sanity"] = max(10, int(c.get("sanity", 100)) - 15)
    o.gray_fog_save = True
    o.enemy = None
    o.ev("grayfog", "🕯", "Серый Туман спас тебя на грани гибели!",
         "Ты отступаешь в укрытие, потеряв 15% Рассудка, но сохранив жизнь.")


# ---------------------------------------------------------------------------
# Фасад одного раунда
# ---------------------------------------------------------------------------
def run_round(c: dict, enemy: dict, kind: str, ac_bonus: int = 0,
              seq: int | None = None, key: str | None = None,
              stunt_text: str = "", classify: tuple | None = None) -> CombatOutcome:
    """Выполняет ход игрока, затем (если враг жив) контратаку врага.

    :param classify: (sk_key, dc, action_summary) для stunt — результат
        h_story._classify_free_text / web-аналога.
    :raises CombatError: действие недоступно (нет ресурсов, умение не найдено).
    :raises ValueError: неизвестный kind или некорректный stunt.
    """
    o = CombatOutcome()
    ctx = _ctx(c)
    o.uses = ctx["uses"]
    disoriented = False

    if kind == "cantrip":
        act_cantrip(c, enemy, o, ctx)
    elif kind == "mech":
        act_mech(c, enemy, o, ctx)
    elif kind == "myth":
        act_myth(c, enemy, o, ctx)
    elif kind == "sk":
        if seq is None or not key:
            raise ValueError("Для kind='sk' нужны seq и key")
        act_skill(c, enemy, o, ctx, int(seq), str(key))
    elif kind == "stunt":
        text = (stunt_text or "").strip()
        if not text:
            raise CombatError("Опиши свой боевой трюк")
        if not classify:
            raise ValueError("stunt требует classify=(sk_key, dc, summary)")
        sk_key, dc, summary = classify
        o.ev("maneuver", "🗣", f"Твой манёвр: «{text[:150]}»")
        disoriented = act_stunt(c, enemy, o, ctx, sk_key, int(dc), summary or text[:45], text)
    else:
        raise ValueError(f"Неизвестный боевой вид: {kind}")

    if int(enemy["hp"]) <= 0:
        _finish_victory(c, o, enemy["name"])
        return o

    enemy_attack(c, enemy, o, ctx, ac_bonus, disoriented)
    if o.delta.get("hp", int(c["hp"])) <= 0:
        _gray_fog_save(c, o)
        return o

    enemy["round"] = int(enemy.get("round", 1)) + 1
    o.enemy = enemy
    return o


# ---------------------------------------------------------------------------
# Рендер событий (слои не форматируют строки сами!)
# ---------------------------------------------------------------------------
def render_html(out: CombatOutcome) -> list[str]:
    """Строки событий в Telegram-HTML (имена слои экранируют сами до вызова)."""
    lines: list[str] = []
    for ev in out.events:
        h = f"{ev['icon']} <b>{ev['text']}</b>"
        if ev.get("bold"):
            h += f" <b>{ev['bold']}</b>"
        lines.append(h)
    return lines


def render_plain(out: CombatOutcome) -> list[str]:
    """Строки событий без разметки — для веба (журнал применяет esc сам)."""
    return [f"{ev['icon']} {ev['text']}" + (f" {ev['bold']}" if ev.get("bold") else "")
            for ev in out.events]
