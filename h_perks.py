"""Фракционные пассивки, черты Потустороннего и боевые состояния.

Интеграция с h_combat, h_perk_cmd, h_build, h_promote, h_abilities и ui.
"""
from __future__ import annotations

import random

import db
import lotm_data as lotm
import rules
from ui import esc

_rng = random.SystemRandom()

PERK_MECHANICS: dict[str, dict] = {}

FEAT_MECHANICS: dict[str, dict] = {
    "deep_sea": {"extra_slots": {1: 1, 2: 1}},
    "sealed_artifact_attack": {"artifact_dice": "1d6", "self_damage": "1d6", "per_combat": 1, "artifact_scale_by_tier": True},
    "sealed_artifact_defense": {"ac_bonus": 2, "stealth_penalty": True},
    "method_of_rehearsal": {"save_bonus": 1, "hit_bonus": 1},
    "inhuman_vitality": {"hp_per_tier": 3},
    "otherworldly_sense": {"init_bonus": 5, "cannot_be_surprised": True, "see_invisible": True},
    "sefirot_aura": {"save_bonus": 2, "extra_slots": {3: 1, 4: 1}},
    "law_of_convergence": {"hit_bonus": 2},
}

VULNERABLE_CONDITIONS = ("charmed", "frightened", "poisoned")


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def perk_of(c: dict | None) -> dict:
    """Механика фракционной пассивки персонажа (или {})."""
    if not c:
        return {}
    perk_id = c.get("faction_perk") or ""
    if not perk_id:
        for cond in (c.get("conditions") or []):
            if isinstance(cond, str) and cond.startswith("perk:"):
                perk_id = cond.split(":", 1)[1]
                break
    mech = PERK_MECHANICS.get(perk_id)
    return dict(mech) if mech else {}


def feat_mech(c: dict | None) -> dict:
    """Суммарная механика всех черт персонажа."""
    if not c:
        return {}
    out: dict = {}
    for key in (c.get("feats") or []):
        for k, v in FEAT_MECHANICS.get(key, {}).items():
            if isinstance(v, dict):
                acc = out.setdefault(k, {})
                for rk, rv in v.items():
                    acc[rk] = acc.get(rk, 0) + rv
            elif isinstance(v, bool):
                out[k] = out.get(k, False) or v
            elif isinstance(v, int):
                out[k] = out.get(k, 0) + v
            else:
                # Для строк (например, "2d6") просто перезаписываем значение
                out[k] = v
    return out


def _has_branch(c: dict, path: str, seq: int, branch: str) -> bool:
    char_path = lotm.PATHWAY_ALIASES.get(c.get("pathway") or "", c.get("pathway") or "")
    target_path = lotm.PATHWAY_ALIASES.get(path, path)
    if char_path != target_path or int(c.get("sequence") or 9) > seq:
        return False
    choices = c.get("pathway_choices") or {}
    picked = str(choices.get(str(seq)) or choices.get(seq) or "a").lower()
    return picked == branch


def ac_bonus(c: dict | None) -> int:
    """Суммарный бонус Защиты от черт, фракционных пассивок и пассивных веток пути."""
    if not c:
        return 0
    bonus = int(perk_of(c).get("ac_bonus", 0)) + int(feat_mech(c).get("ac_bonus", 0))
    # Anti-Stunlock: пока действует окно иммунитета к жёсткому контролю, Защита −2
    if c.get("lair_uses", {}).get("cc_immunity", 0):
        bonus -= 2
    if _has_branch(c, "prisoner", 9, "a"):
        bonus += 2
    if _has_branch(c, "arbiter", 9, "b"):
        bonus += 1
    if _has_branch(c, "black_emperor", 8, "b"):
        bonus += 2
    if _has_branch(c, "door", 5, "a"):
        bonus += 3
    if _has_branch(c, "error", 7, "a"):
        bonus += 2
    if _has_branch(c, "tyrant", 9, "a"):
        bonus += 1
    if _has_branch(c, "hanged_man", 4, "a"):
        bonus += 2
    if _has_branch(c, "twilight_giant", 9, "a"):
        bonus += 1
    if _has_branch(c, "death", 5, "b"):
        bonus += 2
    if _has_branch(c, "paragon", 9, "a"):
        bonus += 2
    if _has_branch(c, "paragon", 4, "a"):
        bonus += 3
    if _has_branch(c, "wheel_of_fortune", 9, "a"):
        bonus += 1
    if _has_branch(c, "visionary", 4, "a"):
        base_ac = int(c.get("ac", 10)) + bonus
        if base_ac < 18:
            bonus += 18 - base_ac
    if _has_branch(c, "prisoner", 3, "b"):
        bonus += 3
    if _has_branch(c, "arbiter", 2, "b") or _has_branch(c, "darkness", 2, "b") or _has_branch(c, "paragon", 2, "b") or _has_branch(c, "wheel_of_fortune", 2, "b"):
        bonus += 4
    if _has_branch(c, "demoness", 2, "b"):
        bonus += 4
    if _has_branch(c, "sun", 1, "b") or _has_branch(c, "white_tower", 1, "b"):
        bonus += 5
    if _has_branch(c, "visionary", 0, "b"):
        base_ac = int(c.get("ac", 10)) + bonus
        if base_ac < 22:
            bonus += 22 - base_ac
    if int(c.get("sequence") or 9) == 0:
        for p0, b0 in (
            ("moon", 5), ("mother", 6), ("prisoner", 5), ("arbiter", 6),
            ("black_emperor", 5), ("door", 7), ("fool", 6), ("error", 6),
            ("red_priest", 6), ("demoness", 6), ("hanged_man", 6),
            ("twilight_giant", 7), ("darkness", 7), ("death", 6),
            ("hermit", 7), ("paragon", 7), ("wheel_of_fortune", 7),
        ):
            if _has_branch(c, p0, 0, "b"):
                bonus += b0
    return bonus


def initiative_bonus(c: dict | None) -> int:
    """Суммарный бонус к инициативе от черт, пассивок и веток пути."""
    if not c:
        return 0
    bonus = int(perk_of(c).get("init_bonus", 0)) + int(feat_mech(c).get("init_bonus", 0))
    if _has_branch(c, "arbiter", 8, "a"):
        bonus += 4
    if _has_branch(c, "error", 9, "b"):
        bonus += 2
    if _has_branch(c, "wheel_of_fortune", 9, "a"):
        bonus += 3
    if _has_branch(c, "abyss", 2, "b") or _has_branch(c, "white_tower", 1, "b") or _has_branch(c, "door", 0, "b"):
        bonus += 5
    if _has_branch(c, "error", 0, "b"):
        bonus += 6
    return bonus


def max_hp_bonus(c: dict | None) -> int:
    """Бонус к макс. HP от черт (Нечеловеческая Живучесть) и фракции (Аристократ крови)."""
    if not c:
        return 0
    bonus = int(perk_of(c).get("hp_flat", 0))
    mech = feat_mech(c)
    if mech.get("hp_per_tier"):
        seq = int(c.get("sequence") or 9)
        open_tiers = lotm.OPEN_TIERS.get(seq, 1)
        bonus += int(mech["hp_per_tier"]) * open_tiers
    return bonus


def roll_dice(expr: str) -> int:
    """Бросок строки вида '1d6' / '2d6'. Без модификаторов."""
    if not expr or "d" not in expr:
        return int(expr or 0)
    n, die = expr.split("d", 1)
    n, die = int(n), int(die)
    return sum(_rng.randint(1, die) for _ in range(n))


# ---------------------------------------------------------------------------
# Боевые хуки и активные перки
# ---------------------------------------------------------------------------

async def on_turn_start(c: dict | None) -> str | None:
    """Регенерация «Аристократа крови» в начале хода. Возвращает текст или None."""
    if not c:
        return None
    mech = perk_of(c)
    expr = mech.get("regen")
    if not expr:
        return None
    hp, max_hp = int(c.get("hp") or 0), int(c.get("max_hp") or 0)
    if max_hp <= 0 or hp <= 0 or hp >= max_hp * mech.get("regen_threshold", 0.5):
        return None
    seq = int(c.get("sequence") or 9)
    if seq <= 6:
        expr = mech.get("regen_hi", expr)
    con_mod = max(0, rules.ability_mod(int((c.get("stats") or {}).get("con", 10))))
    base_dice = expr.split("+")[0].strip()
    healed = roll_dice(base_dice) + con_mod
    new_hp = rules.apply_heal(hp, max_hp, healed)
    await db.update_character(c["user_id"], hp=new_hp)
    return f"🩸 Регенерация крови: + {healed} ОЗ → <b>{new_hp}</b>/{max_hp}"


async def bite_attack(c: dict, mod: int) -> str | None:
    """«Кровавый хищник»: бонусным действием раз за бой."""
    mech = perk_of(c)
    if "bite" not in mech:
        return None
    uses = dict(c.get("lair_uses") or {})
    left = int(uses.get("bite", mech.get("per_combat", 1)))
    if left <= 0:
        return None
    seq = int(c.get("sequence") or 9)
    expr = mech["bite"] if seq > 6 else mech.get("bite_hi", mech["bite"])
    dmg = roll_dice(expr) + mod
    heal = int(dmg * mech.get("heal_frac", 1.0))
    max_hp = int(c.get("max_hp") or 0)
    new_hp = rules.apply_heal(int(c.get("hp") or 0), max_hp, heal)
    uses["bite"] = left - 1
    await db.update_character(c["user_id"], hp=new_hp, lair_uses=uses)
    return (
        f"🦇 <b>{esc(c['name'])}</b> кусает: {expr} {mod:+d} = <b>{dmg}</b> урона, "
        f"исцеление + {heal} ОЗ → <b>{new_hp}</b>/{max_hp} (осталось укусов: {left - 1})"
    )


async def artifact_strike(c: dict) -> str | None:
    """«Запечатанный Артефакт (Атака)»: бонусным действием раз за бой."""
    mech = feat_mech(c)
    if not mech.get("artifact_dice"):
        return None
    uses = dict(c.get("lair_uses") or {})
    left = int(uses.get("artifact", mech.get("per_combat", 1)))
    if left <= 0:
        return None
    dmg_expr = mech["artifact_dice"]
    if mech.get("artifact_scale_by_tier"):
        # 1d6 × открытый тир последовательности (Посл.9 = 1d6 ... Посл.4 = 6d6)
        seq = int(c.get("sequence") or 9)
        tiers = int(lotm.OPEN_TIERS.get(seq, 1))
        dmg_expr = f"{tiers}d6"
    dmg = roll_dice(dmg_expr)
    back = roll_dice(mech.get("self_damage", "1d6"))
    hp, max_hp = int(c.get("hp") or 0), int(c.get("max_hp") or 0)
    new_hp = max(0, hp - back)
    uses["artifact"] = left - 1
    await db.update_character(c["user_id"], hp=new_hp, lair_uses=uses)
    return (
        f"📕 Артефакт бьёт на <b>{dmg}</b> чистого урона, но отнимает {back} ОЗ "
        f"→ <b>{new_hp}</b>/{max_hp} (осталось применений: {left - 1})"
    )


async def on_combat_start(c: dict, enemy_ids: list[int] | None = None) -> str | None:
    """«Бюрократический надзор»: метка врага в начале боя."""
    mech = perk_of(c)
    if not mech.get("mark_on_start") or not enemy_ids:
        return None
    uses = dict(c.get("lair_uses") or {})
    uses["marked_enemy"] = enemy_ids[0]
    await db.update_character(c["user_id"], lair_uses=uses)
    return "📋 Враг помечен: союзники получают + 2 к попаданию по нему."


async def react_half_damage(c: dict, incoming: int) -> tuple[int, str | None] | None:
    """«Владыка теней»: реакцией раз за бой вдвое режет входящий урон."""
    mech = perk_of(c)
    if not mech.get("react_half_damage_per_combat"):
        return None
    uses = dict(c.get("lair_uses") or {})
    left = int(uses.get("shadow", mech["react_half_damage_per_combat"]))
    if left <= 0:
        return None
    uses["shadow"] = left - 1
    await db.update_character(c["user_id"], lair_uses=uses)
    return incoming // 2, "🦇 Реакция «Владыка теней»: урон вдвое уменьшен."


def save_extra(
    c: dict,
    condition: str | None = None,
    ability: str | None = None,
) -> tuple[int, bool]:
    """(бонус, преимущество) к спасброску от черт, пассивок, веток пути и состояния."""
    bonus, adv = 0, False
    mech = feat_mech(c)
    bonus += int(mech.get("save_bonus", 0))
    p = perk_of(c)
    bonus += int(p.get("save_bonus", 0))
    if _has_branch(c, "door", 9, "b"):
        bonus += 2
    if _has_branch(c, "hermit", 9, "a"):
        bonus += 2
    if _has_branch(c, "twilight_giant", 5, "a"):
        bonus += 2
    if ability == "wis" and _has_branch(c, "wheel_of_fortune", 8, "a"):
        bonus += 2

    has_mind_adv = bool(
        mech.get("adv_vs_charm_fear")
        or p.get("adv_vs_charm_fear")
        or mech.get("mind_adv")
        or p.get("mind_adv")
        or _has_branch(c, "hanged_man", 9, "b")
        or _has_branch(c, "wheel_of_fortune", 8, "a")
    )
    if condition in VULNERABLE_CONDITIONS and has_mind_adv:
        adv = True
    if ability in ("wis", "cha") and has_mind_adv:
        adv = True
    if ability == "wis" and _has_branch(c, "fool", 8, "b"):
        adv = True
    if ability in ("str", "con") and _has_branch(c, "arbiter", 9, "b"):
        adv = True
    if ability == "con" and _has_branch(c, "paragon", 8, "a"):
        adv = True
    return bonus, adv


async def diplomatic_roll(c: dict) -> str | None:
    """«Дипломатический иммунитет» Чиновников Администрации (2 раза за бой)."""
    mech = perk_of(c)
    if not mech.get("save_reroll_add"):
        return None
    uses = dict(c.get("lair_uses") or {})
    left = int(uses.get("diplomacy", mech.get("per_combat", 2)))
    if left <= 0:
        return None
    uses["diplomacy"] = left - 1
    await db.update_character(c["user_id"], lair_uses=uses)
    n = roll_dice("1d6")
    return (
        f"📜 Дипломатия: бросок 1d6 = <b>{n}</b> — выбери эффект:\n"
        f"• + {n} к твоему спасброску, или\n"
        f"• − {n} к атаке врага."
    )


def reset_combat_uses(c: dict) -> dict:
    """Сброс раз-за-бой счётчиков (в начале боя / после длинного отдыха)."""
    mech = perk_of(c)
    fmech = feat_mech(c)
    return {
        "bite": mech.get("per_combat", 0) if "bite" in mech else 0,
        "artifact": fmech.get("per_combat", 0) if fmech.get("artifact_dice") else 0,
        "shadow": mech.get("react_half_damage_per_combat", 0),
        "diplomacy": mech.get("per_combat", 0) if mech.get("save_reroll_add") else 0,
        "path_res": lotm.PATHWAY_MECHANICS.get(c.get("pathway", ""), {}).get("max_base", 3),
        "mythical_used": 0,
    }


def build_status_lines(c: dict) -> list[str]:
    """Короткий блок активных пассивок для /perk, /hp и /abilities."""
    lines: list[str] = []
    p = perk_of(c)
    if p.get("hp_flat"):
        lines.append(f"🩸 + {p['hp_flat']} к максимуму здоровья (Аристократ крови)")
    if p.get("regen"):
        lines.append("🩸 Регенерация крови при здоровье < 50%")
    if p.get("bite"):
        lines.append("🦇 Укус: бонусным действием раз за бой")
    if p.get("react_half_damage_per_combat"):
        lines.append("🦇 Реакция: урон вдвое (раз за бой)")
    if p.get("save_reroll_add"):
        lines.append("📜 Дипломатия: ±1d6 к спасброску/атаке (2 раза за бой)")
    if p.get("mark_on_start"):
        lines.append("📋 Метка врага в начале боя")
    if p.get("adv_vs_charm_fear"):
        lines.append("🛡 Преимущество против очарования/испуга и + 1 к Защите")
    f = feat_mech(c)
    if f.get("artifact_dice"):
        lines.append("📕 Артефакт (атака): раз за бой")
    if f.get("ac_bonus"):
        lines.append(f"🛡 + {f['ac_bonus']} к Защите от черт")
    if f.get("init_bonus"):
        lines.append(f"⚡ + {f['init_bonus']} к инициативе")
    if f.get("save_bonus"):
        lines.append(f"🎯 + {f['save_bonus']} к Сл спасбросков и попаданию")
    if f.get("cannot_be_surprised"):
        lines.append("👁 Нельзя застать врасплох")
    return lines
