"""Команда /abilities — просмотр, переключение веток и боевое применение умений пути."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import db
import h_perks
import lotm_data as lotm
import lotm_restrictions
import rules
import ui
from ui import btn, esc

router = Router()


def _is_offensive_skill(sk: dict) -> bool:
    if sk.get("action") == "passive" or not sk.get("dice"):
        return False
    low = (sk.get("desc") or "").lower()
    if "пассив" in low:
        return False
    if sk.get("action") == "reaction":
        return any(w in low for w in ("ответн", "в ответ", "атакующему"))
    if "следующ" in low or "повышает ваш урон" in low:
        return False
    if sk.get("save"):
        return True
    has_attack_verb = any(
        w in low for w in (
            "бьёт", "атакует", "кусает", "взрывая", "взрыв", "выстрел",
            "выпад", "серия ударов", "пронзающий луч", "залп", "сжигает",
            "касание", "метание", "сокрушительный удар", "двойной удар",
            "рукопашный", "удар любым", "удар на ", "бросок реагента",
        )
    )
    if any(w in low for w in ("лечит", "исцел", "восстанавлив", "снимает", "сращивает", "удваивает", "temp_hp", "временн")):
        return has_attack_verb
    if any(w in low for w in ("снижает", "поглощает", "смягчает", "задерживает", "сокращает урон")):
        return False
    return has_attack_verb or "урон" in low


def _abilities_text(c: dict) -> str:
    path = c["pathway"]
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(path, choices, c["sequence"], c["start_sequence"])
    seq_title = lotm.SEQUENCE_TITLES.get(path, {}).get(c["sequence"], f"Посл. {c['sequence']}")
    p_mech = lotm.PATHWAY_MECHANICS.get(path, {})
    res_left = int((c.get("lair_uses") or {}).get("path_res", p_mech.get("max_base", 3)))
    lines = [
        f"📖 <b>{esc(c['name'])}</b> — {esc(lotm.PATHWAYS[path]['name'])}",
        f"🔮 Ступень: <b>Посл. {c['sequence']} «{esc(seq_title)}»</b>",
        f"🧠 Рассудок: <b>{c.get('sanity', 100)}%</b> | 🧪 Переваривание зелья: <b>{c.get('digestion', 0)}%</b>",
        f"⚡ {esc(p_mech.get('resource', 'Ресурс'))}: <b>{res_left}/{p_mech.get('max_base', 3)}</b>",
        "",
    ]
    for sk in sorted(skills, key=lambda s: -s["seq"]):
        act = lotm.action_label(sk["action"])
        cost = f" · {sk['slot_cost']} яч. Р{sk['slot_rank']}" if sk["slot_cost"] else " · бесплатно"
        branch = "А" if sk["key"] != "choice_b" else "Б"
        if sk["key"] == "core":
            branch = "★"
        lines.append(
            f"<b>[{branch}] {esc(sk['name'])}</b> ({act}, Посл. {sk['seq']}{cost})\n"
            f"<i>{esc(sk['desc'])}</i>"
        )
    if c.get("feats"):
        lines.append("\n<b>Черты Потустороннего:</b>")
        for f in c["feats"]:
            ft = lotm.FEATS.get(f)
            if ft:
                lines.append(f"• <b>{esc(ft['name'])}</b> — {esc(ft['desc'])}")
    return "\n".join(lines)


def _unspent_feats(c: dict) -> int:
    seq = int(c.get("sequence") or 9)
    target = lotm.FEATS_AT_SEQUENCE.get(seq, 0)
    taken = len(c.get("feats") or [])
    return max(0, target - taken)


def _branch_kb(c: dict) -> InlineKeyboardMarkup:
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    open_seqs = [s for s in lotm.SEQUENCES if s >= c["sequence"]]
    rows: list[list[InlineKeyboardButton]] = []
    for seq in open_seqs:
        cur = choices.get(seq, "a")
        other_key = "b" if cur == "a" else "a"
        tiers = lotm.PATHWAYS[c["pathway"]]["tiers"][seq]
        name = tiers["choice_a" if other_key == "a" else "choice_b"][0]
        rows.append([btn(f"Посл. {seq}: сменить на «{name}»", f"ab:{seq}:{other_key}:{c['user_id']}")])
    due = _unspent_feats(c)
    if due > 0:
        rows.append([btn(f"🎁 Добрать черту Потустороннего ({due})", f"ab:feat_menu:{c['user_id']}")])
    rows.append([btn("⚔️ Боевое применение умений", f"ab:cast_menu:{c['user_id']}")])
    rows.append([btn("🔄 Обновить", f"ab:refresh:{c['user_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cast_kb(c: dict, is_private: bool = False) -> InlineKeyboardMarkup:
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    rows: list[list[InlineKeyboardButton]] = []
    p_mech = lotm.PATHWAY_MECHANICS.get(c["pathway"], {})
    res_left = int((c.get("lair_uses") or {}).get("path_res", p_mech.get("max_base", 3)))
    if p_mech:
        rows.append([btn(f"⚡ {p_mech['name']} ({res_left})", f"cast_mech:{c['user_id']}")])
    if int(c.get("sequence", 9)) <= 4:
        rows.append([btn("👁 Раскрыть Форму Мифического Существа (-20🧠)", f"cast_myth:{c['user_id']}")])
    for sk in sorted(skills, key=lambda s: -s["seq"]):
        if sk["action"] == "passive":
            continue
        cost = f" ({sk['slot_cost']} яч. Р{sk['slot_rank']})" if sk["slot_cost"] else " (бесплатно)"
        rows.append([btn(f"✨ {sk['name']}{cost}", f"cast:{c['user_id']}:{sk['seq']}:{sk['key']}")])
    if is_private:
        rows.append([btn("« К списку умений", f"ab:menu:{c['user_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("abilities"))
async def cmd_abilities(message: Message):
    c = await db.get_character(message.from_user.id)
    if not c or not c.get("pathway"):
        await message.answer("Сначала собери билд: /build")
        return
    if c.get("status") != "approved":
        await message.answer("Боевые способности и артефакты доступны только после одобрения анкеты администратором.")
        return
    if message.chat.type != "private":
        await message.answer(_abilities_text(c), reply_markup=_cast_kb(c, is_private=False))
        return
    await message.answer(_abilities_text(c), reply_markup=_branch_kb(c))


@router.callback_query(F.data.startswith("ab:feat_menu:"))
async def cb_feat_menu(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer("Выбирать черты можно только в личке с ботом!", show_alert=True)
        return
    parts = cb.data.split(":")
    uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else cb.from_user.id
    if cb.from_user.id != uid:
        await cb.answer("Это не твоя анкета", show_alert=True)
        return
    if await db.is_in_combat(uid):
        await cb.answer("Нельзя менять билд во время боя!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c or not c.get("pathway"):
        await cb.answer("Нет персонажа", show_alert=True)
        return
    due = _unspent_feats(c)
    if due <= 0:
        await cb.answer("Все положенные черты уже выбраны ✅", show_alert=True)
        await ui.safe_edit(cb.message, _abilities_text(c), _branch_kb(c))
        return
    taken = set(c.get("feats") or [])
    available = [k for k in lotm.FEATS if k not in taken]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn(lotm.FEATS[k]["name"], f"ab:feat_pick:{k}")]
        for k in available
    ] + [[btn("⬅️ Назад", f"ab:menu:{uid}")]])

    lines = [
        f"🎁 <b>Выбери черту Потустороннего</b> (осталось: {due}):\n",
    ]
    for k in available:
        ft = lotm.FEATS[k]
        lines.append(f"• <b>{esc(ft['name'])}</b>\n  <i>{esc(ft['desc'])}</i>\n")

    await ui.safe_edit(cb.message, "\n".join(lines), kb)
    await cb.answer()


@router.callback_query(F.data.startswith("ab:feat_pick:"))
async def cb_feat_pick(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer("Выбирать черты можно только в личке с ботом!", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer()
        return
    key = parts[2]
    uid = cb.from_user.id
    if await db.is_in_combat(uid):
        await cb.answer("Нельзя менять билд во время боя!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c or not c.get("pathway"):
        await cb.answer("Нет персонажа", show_alert=True)
        return
    feat = lotm.FEATS.get(key)
    if not feat:
        await cb.answer("Неизвестная черта", show_alert=True)
        return
    feats = list(c.get("feats") or [])
    if key in feats:
        await cb.answer("Эта черта уже выбрана", show_alert=True)
        return
    if _unspent_feats(c) <= 0:
        await cb.answer("Нет доступных черт", show_alert=True)
        return

    feats.append(key)
    new_path = c["pathway"]
    new_seq = int(c.get("sequence") or 9)
    stats = c.get("stats") or {}
    con_mod = rules.ability_mod(int(stats.get("con", 10)))
    old_max_hp = int(c.get("max_hp") or 0)
    max_hp = rules.sequence_max_hp(
        new_path,
        new_seq,
        con_mod,
        feats,
        faction_perk=c.get("faction_perk", ""),
        pathway_choices=c.get("pathway_choices"),
    )
    hp_diff = max(0, max_hp - old_max_hp)
    new_hp = min(max_hp, int(c.get("hp") or max_hp) + hp_diff)

    max_slots = lotm.spell_slots_for(new_seq, feats)
    cur_slots = {str(k): dict(v) for k, v in (c.get("spell_slots") or {}).items()}
    for r, n in max_slots.items():
        rk = str(r)
        old_tot = int(cur_slots.get(rk, {}).get("total", 0))
        old_left = int(cur_slots.get(rk, {}).get("left", 0))
        slot_diff = max(0, n - old_tot)
        cur_slots[rk] = {"total": n, "left": min(n, old_left + slot_diff)}

    uses = dict(c.get("lair_uses") or {})
    fresh_uses = h_perks.reset_combat_uses({**c, "feats": feats})
    for uk, uv in fresh_uses.items():
        if uv > 0 and uses.get(uk, 0) == 0:
            uses[uk] = uv

    await db.update_character(
        uid,
        feats=feats,
        max_hp=max_hp,
        hp=new_hp,
        spell_slots=cur_slots,
        lair_uses=uses,
    )
    c = await db.get_character(uid)
    if _unspent_feats(c) > 0:
        await cb_feat_menu(cb)
    else:
        await ui.safe_edit(cb.message, _abilities_text(c), _branch_kb(c))
        await cb.answer("Черты выбраны ✅")


@router.callback_query(F.data.startswith("ab:"))
async def cb_switch(cb: CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) < 2:
        await cb.answer()
        return

    if parts[1] == "refresh":
        owner_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else cb.from_user.id
        if cb.from_user.id != owner_id:
            await cb.answer("Это панель другого персонажа!", show_alert=True)
            return
        c = await db.get_character(owner_id)
        if not c or not c.get("pathway"):
            await cb.answer("Нет билда", show_alert=True)
            return
        if c.get("status") != "approved":
            await cb.answer("Боевые способности и артефакты доступны только после одобрения анкеты администратором.", show_alert=True)
            return
        await ui.safe_edit(cb.message, _abilities_text(c), _branch_kb(c))
        await cb.answer()
        return

    if parts[1] in ("cast_menu", "menu"):
        if cb.message.chat.type != "private":
            await cb.answer("Менять ветки умений можно только в личке с ботом вне боя!", show_alert=True)
            return
        uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else cb.from_user.id
        if cb.from_user.id != uid:
            await cb.answer("Это не твоя анкета", show_alert=True)
            return
        if await db.is_in_combat(uid):
            await cb.answer("Нельзя менять ветки во время боя!", show_alert=True)
            return
        c = await db.get_character(uid)
        if not c or not c.get("pathway"):
            await cb.answer("Нет билда", show_alert=True)
            return
        kb = _cast_kb(c, is_private=True) if parts[1] == "cast_menu" else _branch_kb(c)
        await ui.safe_edit(cb.message, _abilities_text(c), kb)
        await cb.answer()
        return

    if len(parts) != 4:
        await cb.answer()
        return
    try:
        seq, new_key = int(parts[1]), parts[2]
    except ValueError:
        await cb.answer()
        return
    if new_key not in ("a", "b"):
        await cb.answer()
        return
    if cb.message.chat.type != "private":
        await cb.answer("Сменить ветку умений можно только в личке с ботом вне боя!", show_alert=True)
        return
    owner_id = int(parts[3]) if parts[3].isdigit() else cb.from_user.id
    if cb.from_user.id != owner_id:
        await cb.answer("Это панель другого персонажа!", show_alert=True)
        return
    if await db.is_in_combat(owner_id):
        await cb.answer("Нельзя менять ветки во время боя!", show_alert=True)
        return
    c = await db.get_character(owner_id)
    if not c or not c.get("pathway"):
        await cb.answer("Нет билда", show_alert=True)
        return
    if seq not in [s for s in lotm.SEQUENCES if s >= c["sequence"]]:
        await cb.answer("Эта последовательность ещё не открыта", show_alert=True)
        return
    if c.get("status") != "approved":
        await cb.answer("Боевые способности доступны только после одобрения анкеты администратором.", show_alert=True)
        return

    choices = {str(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    choices[str(seq)] = new_key
    con_mod = rules.ability_mod(int((c.get("stats") or {}).get("con", 10)))
    new_max_hp = rules.sequence_max_hp(
        c["pathway"],
        int(c["sequence"]),
        con_mod,
        list(c.get("feats") or []),
        faction_perk=c.get("faction_perk", ""),
        pathway_choices=choices,
    )
    old_max_hp = int(c.get("max_hp") or new_max_hp)
    cur_hp = int(c.get("hp") or new_max_hp)
    new_hp = min(new_max_hp, cur_hp + max(0, new_max_hp - old_max_hp))

    await db.update_character(owner_id, pathway_choices=choices, max_hp=new_max_hp, hp=new_hp)
    c = await db.get_character(owner_id)
    await ui.safe_edit(cb.message, _abilities_text(c), _branch_kb(c))
    await cb.answer("Ветка переключена")


def _slot_pay(c: dict, rank: int, cost: int) -> tuple[bool, dict]:
    slots = {str(k): dict(v) for k, v in (c.get("spell_slots") or {}).items()}
    bucket = slots.get(str(rank)) or {}
    left = int(bucket.get("left", 0))
    if left < cost:
        return False, slots
    bucket["left"] = left - cost
    slots[str(rank)] = bucket
    return True, slots


def _cast_save_dc(c: dict) -> int:
    prof = rules.effective_prof_bonus(c)
    _, stat_mod = rules.best_path_stat(c)
    dc = 8 + prof + stat_mod
    mech = h_perks.feat_mech(c)
    dc += int(mech.get("save_bonus", 0))
    if c["pathway"] == "black_emperor" and c["sequence"] <= 5:
        choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
        if choices.get(5) == "b":
            dc += 2
    return dc


@router.callback_query(F.data.startswith("cast_mech:"))
async def cb_cast_pathway_mechanic(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой персонаж!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c or int(c.get("hp") or 0) <= 0:
        await cb.answer("Недоступно!", show_alert=True)
        return
    p_mech = lotm.PATHWAY_MECHANICS.get(c["pathway"])
    if not p_mech:
        await cb.answer()
        return
    uses = dict(c.get("lair_uses") or {})
    left = int(uses.get("path_res", p_mech.get("max_base", 3)))
    if left <= 0:
        await cb.answer(f"Заряды «{p_mech['resource']}» исчерпаны! Отдохни (/rest).", show_alert=True)
        return
    uses["path_res"] = left - 1
    # Скейлинг кубов классовой механики от открытого тира
    tier_mult = max(1, lotm.OPEN_TIERS.get(int(c["sequence"]), 1) // 2 + 1)
    die_side = p_mech["dice"].split("d")[1]
    expr = f"{tier_mult}d{die_side}"
    roll = rules.roll_expression(expr)
    _, stat_mod = rules.best_path_stat(c)
    total_dmg = roll.total + stat_mod
    dig = min(100, int(c.get("digestion") or 0) + 5)
    await db.update_character(uid, lair_uses=uses, digestion=dig)
    effect_text = p_mech["effect"].format(dmg=f"<b>{total_dmg}</b> ({expr}{stat_mod:+d})")
    await cb.message.reply(
        f"⚡ <b>{esc(c['name'])}</b> активирует классовую механику Пути: <b>{esc(p_mech['name'])}</b>!\n"
        f"{effect_text}\n"
        f"🧪 Метод отыгрыша: переваривание зелья <b>{dig}%</b> (осталось зарядов: {left - 1})."
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cast_myth:"))
async def cb_cast_mythical_form(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    if cb.from_user.id != uid:
        await cb.answer("Это не твой персонаж!", show_alert=True)
        return
    c = await db.get_character(uid)
    if not c or int(c.get("sequence", 9)) > 4:
        await cb.answer("Форма Мифического Существа доступна только Полубогам и выше (Посл. 4–0)!", show_alert=True)
        return
    uses = dict(c.get("lair_uses") or {})
    if uses.get("mythical_used"):
        await cb.answer("Форма Мифического Существа уже раскрывалась в этом бою!", show_alert=True)
        return
    sanity = int(c.get("sanity", 100))
    cost = 10 if "mythical_anchor" in (c.get("feats") or []) else 20
    if sanity < cost:
        await cb.answer(f"Недостаточно Рассудка ({sanity}%)! Вы рискуете потерять контроль.", show_alert=True)
        return
    uses["mythical_used"] = 1
    seq = int(c["sequence"])
    shield = (10 - seq) * 10
    new_temp = max(int(c.get("temp_hp") or 0), shield)
    dc = _cast_save_dc(c) + 2
    roll = rules.roll_expression(f"{10 - seq}d10")
    await db.update_character(uid, sanity=sanity - cost, temp_hp=new_temp, lair_uses=uses)
    form_kind = "Полную Божественную Форму" if seq <= 2 else "Неполную Форму Мифического Существа"
    await cb.message.reply(
        f"👁 <b>{esc(c['name'])}</b> раскрывает <b>{form_kind}</b>!\n"
        f"💥 Все враги, видящие истинный облик Потустороннего, проходят спасбросок МДР (Сл <b>{dc}</b>) "
        f"или получают <b>{roll.total}</b> психического урона и статус «Оглушён / Ослеплён»!\n"
        f"🛡 Вы получаете <b>+{shield} временных ОЗ</b>. Рассудок: <b>{sanity - cost}%</b>."
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cast:"))
async def cb_cast_ability(cb: CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) == 4:
        try:
            owner_id, seq = int(parts[1]), int(parts[2])
        except ValueError:
            await cb.answer()
            return
        key = parts[3]
        if cb.from_user.id != owner_id:
            await cb.answer("Это не твоё умение", show_alert=True)
            return
    elif len(parts) == 3:
        try:
            seq = int(parts[1])
        except ValueError:
            await cb.answer()
            return
        key, owner_id = parts[2], cb.from_user.id
    else:
        await cb.answer()
        return

    c = await db.get_character(owner_id)
    if not c or not c.get("pathway"):
        await cb.answer("Нет билда", show_alert=True)
        return
    if c.get("status") != "approved":
        await cb.answer("Боевые способности и артефакты доступны только после одобрения анкеты администратором.", show_alert=True)
        return
    if int(c.get("hp") or 0) <= 0:
        await cb.answer("Персонаж без сознания (0 ОЗ) и не может применять способности!", show_alert=True)
        return
    if {"stunned", "paralyzed", "incapacitated"} & set(c.get("conditions") or []):
        await cb.answer("Персонаж парализован, оглушён или недееспособен!", show_alert=True)
        return

    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    sk = next((s for s in skills if s["seq"] == seq and s["key"] == key), None)
    if sk is None:
        await cb.answer("Умение недоступно", show_alert=True)
        return

    uses = dict(c.get("lair_uses") or {})
    use_key = f"sk_{sk['seq']}_{sk['key']}"

    # once_per_battle: явное «раз за бой», реакция с полным избеганием урона
    # или из явного списка ограничений (lotm_restrictions).
    low_desc = sk["desc"].lower()
    full_avoid = sk["action"] == "reaction" and any(
        w in low_desc for w in ("полностью избега", "полностью неуязвим", "полностью игнорир",
                                "промахнуться мимо", "неуязвимым", "избегая урона", "бесплотным")
    )
    restricted = (c["pathway"], sk["seq"], sk["key"].rsplit("_", 1)[-1]) in lotm_restrictions.ONCE_PER_BATTLE
    once_per_combat = "раз за бой" in low_desc or full_avoid or restricted
    if once_per_combat and int(uses.get(use_key, 1)) <= 0:
        await cb.answer(f"Умение «{sk['name']}» уже использовано в этом бою!", show_alert=True)
        return

    # перезарядка бонусных/реакционных умений (действует только во время активного боя)
    combat = None
    try:
        if cb.message.chat.type != "private":
            combat = await db.get_combat(cb.message.chat.id)
    except Exception:
        combat = None

    cd_key = f"cd_{use_key}"
    cd_left = int(uses.get(cd_key, 0))
    if cd_left > 0 and sk["action"] in ("bonus", "reaction"):
        if combat and int(combat["round"]) < cd_left:
            await cb.answer(
                f"⏳ «{sk['name']}» на перезарядке: ещё {cd_left - int(combat['round'])} раунд(ов).",
                show_alert=True,
            )
            return
        if not combat:
            uses.pop(cd_key, None)

    cost, rank = int(sk["slot_cost"]), int(sk["slot_rank"])
    if cost > 0:
        ok, slots = _slot_pay(c, rank, cost)
        if not ok:
            await cb.answer(
                f"Не хватает ячеек духовности: нужно {cost} ранга {rank}.",
                show_alert=True,
            )
            return
        await db.update_character(owner_id, spell_slots=slots)

    if once_per_combat:
        uses[use_key] = 0
    sk_restr = (c["pathway"], sk["seq"], sk["key"].rsplit("_", 1)[-1])
    if sk_restr in lotm_restrictions.SUMMON_KEYS:
        # призыв: 1 раз за бой, живёт 3 раунда
        uses[use_key] = 0
        uses[f"summ_{use_key}"] = lotm_restrictions.SUMMON_DURATION_ROUNDS
        lines_summon = f"⏳ Призыв активен {lotm_restrictions.SUMMON_DURATION_ROUNDS} раунда."
    else:
        lines_summon = ""
    if combat and sk["action"] in ("bonus", "reaction") and int(sk.get("slot_rank", 0)) > 0:
        uses[cd_key] = int(combat["round"]) + lotm.COOLDOWN_ROUNDS_SUB
    # Переваривание зелья: +5% за каждое применённое умение
    new_dig = min(100, int(c.get("digestion") or 0) + 5)
    await db.update_character(owner_id, lair_uses=uses, digestion=new_dig)

    lines = [f"✨ <b>{esc(c['name'])}</b> применяет <b>{esc(sk['name'])}</b>"]
    if lines_summon:
        lines.append(lines_summon)
    if sk["save"]:
        dc = _cast_save_dc(c)
        save_label = rules.SHORT_ABILITY.get(sk["save"], sk["save"])
        lines.append(
            f"🎯 Сл спасброска: <b>{dc}</b> ({esc(save_label)}) — "
            f"при провале: полный урон и эффект; при успехе: <b>50% урона</b>, без эффекта."
        )
    if sk["dice"]:
        try:
            roll = rules.roll_expression(sk["dice"])
            _, stat_mod = rules.best_path_stat(c)
            dice_out = ", ".join(str(r) for t in roll.terms for r in t.rolls)
            lines.append(
                f"🎲 {sk['dice']}: [{dice_out}] = <b>{roll.total + stat_mod}</b> "
                f"(кубы {roll.total}, мод {stat_mod:+d})"
            )
        except ValueError:
            lines.append(f"🎲 Урон: {sk['dice']} + модификатор характеристики")
    lines.append(f"<i>{esc(sk['desc'])}</i>")
    await cb.message.reply("\n".join(lines))
    await cb.message.reply("\n".join(lines))
    await cb.answer()
