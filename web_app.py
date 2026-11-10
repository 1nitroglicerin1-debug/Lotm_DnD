"""web_app.py — FastAPI сервер для Telegram Mini App и личного веб-сайта LotM RPG."""
from __future__ import annotations

import json
import random
from typing import Any

from aiogram import Bot
from aiogram.types import LabeledPrice
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import db
import h_perks
import h_story
import lotm_data as lotm
import rules

app = FastAPI(title="LotM Beyonder Terminal API")
_bot_instance: Bot | None = None
_rng = random.SystemRandom()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


def set_bot(bot: Bot) -> None:
    global _bot_instance
    _bot_instance = bot


class ActionPayload(BaseModel):
    user_id: int
    option_id: int | None = None
    free_text: str | None = None
    intent: str = "act"  # act | talk | inspect | skill
    reroll: bool = False


class CombatPayload(BaseModel):
    user_id: int
    kind: str  # cantrip | mech | myth | sk | stunt | flee | rest | start_hunt
    seq: int | None = None
    key: str | None = None
    stunt_text: str | None = None


class QuickBuildPayload(BaseModel):
    user_id: int
    name: str
    pathway: str
    background: str = "detective"
    region: str = "tingen"
    branch_9: str = "a"


class InvoicePayload(BaseModel):
    user_id: int
    pack_id: str


@app.get("/")
async def index_page():
    return FileResponse("static/index.html")


@app.get("/api/meta")
async def api_meta():
    """Справочник всех путей, предысторий и регионов для веб-конструктора."""
    pathways = []
    for k in lotm.get_allowed_pathways():
        p = lotm.PATHWAYS[k]
        t9 = p["tiers"][9]
        pathways.append({
            "key": k,
            "name": p["name"],
            "seq9_title": lotm.SEQUENCE_TITLES.get(k, {}).get(9, "Посл. 9"),
            "stat": p["stat"],
            "bone": p["bone"],
            "core_9": {"name": t9["core"][0], "desc": t9["core"][4]},
            "choice_a": {"name": t9["choice_a"][0], "desc": t9["choice_a"][4]},
            "choice_b": {"name": t9["choice_b"][0], "desc": t9["choice_b"][4]},
            "principle": lotm.ACTING_PRINCIPLES.get(k, ""),
        })
    backgrounds = [
        {"key": k, **v} for k, v in getattr(lotm, "BACKGROUNDS", {}).items()
    ]
    regions = [
        {"key": k, **v} for k, v in getattr(lotm, "STARTING_REGIONS", {}).items()
    ]
    return {
        "pathways": pathways,
        "backgrounds": backgrounds,
        "regions": regions,
        "tribute_link": config.TRIBUTE_LINK,
    }


@app.get("/api/state/{user_id}")
async def api_get_state(user_id: int) -> dict[str, Any]:
    c = await db.get_character(user_id)
    session = await db.get_story_session(user_id)
    if not c or not c.get("pathway"):
        return {"has_character": False, "session": session}

    path = c["pathway"]
    seq = int(c.get("sequence", 9))
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills_list = rules.path_skills(path, choices, seq, int(c.get("start_sequence", 9)))
    p_mech = lotm.PATHWAY_MECHANICS.get(path, {})
    uses = c.get("lair_uses") or {}
    res_left = int(uses.get("path_res", p_mech.get("max_base", 3)))

    # Последний текст сцены из истории
    last_narr = ""
    for item in reversed(session.get("history") or []):
        if item.get("role") == "assistant":
            try:
                last_narr = json.loads(item["content"]).get("narrative", "")
                break
            except Exception:
                pass

    # Навыки с итоговыми модификаторами
    skill_bonuses = {}
    for sk_key, (sk_name, ab, _) in rules.SKILLS.items():
        skill_bonuses[sk_key] = {
            "name": sk_name,
            "ability": rules.SHORT_ABILITY[ab],
            "mult": int((c.get("skills") or {}).get(sk_key, 0)),
            "total": rules.skill_total(c, sk_key),
        }

    # Обогащаем опции бонусом навыка игрока
    enriched_options = []
    for opt in (session.get("options") or [])[:3]:
        sk_k = opt.get("skill", "perception")
        if sk_k not in rules.SKILLS:
            sk_k = "perception"
        enriched_options.append({
            **opt,
            "skill_name": rules.SKILLS[sk_k][0],
            "player_bonus": rules.skill_total(c, sk_k),
        })

    return {
        "has_character": True,
        "character": {
            **c,
            "pathway_name": lotm.PATHWAYS.get(path, {}).get("name", path),
            "seq_title": lotm.SEQUENCE_TITLES.get(path, {}).get(seq, f"Посл. {seq}"),
            "acting_principle": lotm.ACTING_PRINCIPLES.get(path, ""),
            "effective_ac": int(c.get("ac", 10)) + h_perks.ac_bonus(c),
            "prof_bonus": rules.effective_prof_bonus(c),
            "pathway_mechanic": {**p_mech, "left": res_left},
            "abilities": skills_list,
            "skill_bonuses": skill_bonuses,
        },
        "session": {
            **session,
            "last_narrative": last_narr,
            "enriched_options": enriched_options,
        },
    }


@app.post("/api/build_quick")
async def api_quick_build(req: QuickBuildPayload):
    """Быстрое создание персонажа из веб-интерфейса (Mini App / Сайта)."""
    if req.pathway not in lotm.get_allowed_pathways():
        raise HTTPException(400, "Invalid pathway")
    uid = req.user_id
    path = req.pathway
    seq = 9
    stats = {k: 12 for k in rules.ABILITIES}
    for stat, bonus in lotm.pathway_stat_bonus(path).items():
        stats[stat] = min(30, stats.get(stat, 12) + int(bonus))

    bgs = getattr(lotm, "BACKGROUNDS", {})
    bg = bgs.get(req.background, {})
    for stat, bonus in bg.get("stat_bonus", {}).items():
        stats[stat] = min(30, stats.get(stat, 12) + int(bonus))

    budget = rules.skill_pick_budget(path, stats)
    all_sk = list(rules.SKILLS.keys())
    free_sk = bg.get("free_skill", "investigation")
    if free_sk not in all_sk:
        free_sk = all_sk[0]
    picked = [free_sk] + [k for k in all_sk if k != free_sk][: max(0, budget - 1)]
    skill_map = {k: (2 if i == 0 else 1) for i, k in enumerate(picked)}

    choices = {"9": req.branch_9 if req.branch_9 in ("a", "b") else "a"}
    con_mod = rules.ability_mod(stats["con"])
    max_hp = rules.sequence_max_hp(path, seq, con_mod, [], faction_perk="", pathway_choices=choices)
    slots = lotm.spell_slots_for(seq, [])
    ac = 10 + rules.ability_mod(stats["dex"])
    seq_title = lotm.SEQUENCE_TITLES.get(path, {}).get(seq, "Посл. 9")

    await db.create_draft(uid, "web_player")
    char_data = dict(
        name=req.name.strip()[:40] or "Путник Тумана",
        bio=f"Потусторонний пути {lotm.PATHWAYS[path]['name']}.",
        faction=req.background,
        faction_perk=req.region,
        pathway=path,
        sequence=seq,
        start_sequence=seq,
        level=1,
        stats=stats,
        conditions=[],
        race=bg.get("name", "Потусторонний"),
        char_class=f"{lotm.PATHWAYS[path]['name']} — {seq_title}",
        pathway_choices=choices,
        feats=[],
        skills=skill_map,
        sanity=100,
        digestion=0,
        class_resource=lotm.PATHWAY_MECHANICS.get(path, {}).get("max_base", 3),
        max_hp=max_hp,
        hp=max_hp,
        temp_hp=0,
        ac=ac,
        saves=lotm.PATHWAYS[path].get("saves", []),
        hit_dice_left=1,
        spell_slots={str(r): {"total": n, "left": n} for r, n in slots.items()},
        status="approved",
    )
    char_data["lair_uses"] = h_perks.reset_combat_uses(char_data)
    await db.update_character(uid, **char_data)

    regs = getattr(lotm, "STARTING_REGIONS", {})
    reg_info = regs.get(req.region, {"location": "Тинген, улица Железного Креста", "arc_ru": "Туман над Тингеном"})
    await db.get_story_session(uid)
    await db.update_story_session(
        uid,
        location=reg_info.get("location", "Тинген"),
        current_arc=reg_info.get("arc_ru", ""),
        summary=f"Начал путь как {seq_title}.",
        clues=[f"💰 Капитал: {bg.get('gold', 25)} золотых фунтов", f"🎒 {bg.get('gear', 'Револьвер и плащ')}"],
        npcs=[],
        history=[],
        options=[],
        enemy={},
    )
    return await api_get_state(uid)


@app.post("/api/story/turn")
async def api_story_turn(req: ActionPayload):
    """Выполняет сюжетный ход (по кнопке, свободному тексту или перебросу Судьбы) и возвращает бросок d20 + ответ ИИ."""
    uid = req.user_id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    if not c or not c.get("pathway"):
        raise HTTPException(400, "Create character first")

    # Стартовая генерация первой сцены, если опций ещё нет и игрок нажал Старт
    if not req.option_id and not req.free_text and not req.reroll:
        if int(session.get("turns_left") or 0) <= 0:
            raise HTTPException(402, "Out of Spirituality turns")
        prompt = "Open an atmospheric opening scene tailored to my Starting Region, Background, and Pathway."
        data = await h_story._call_ai_gm(c, session, prompt, "Scene Start")
        c, session, dig_gain = await h_story._apply_ai_memory_and_state(uid, c, session, prompt, data, True)
        state = await api_get_state(uid)
        return {"roll": None, "ai": data, "dig_gain": dig_gain, "state": state}

    mode = "normal"
    if req.reroll:
        last_fail = session.get("last_fail") or {}
        if not last_fail or int(session.get("fate_tokens") or 0) <= 0:
            raise HTTPException(400, "No failed roll or Fate Tokens left")
        await db.add_fate_tokens(uid, -1)
        await db.update_character(uid, sanity=min(100, int(c.get("sanity", 100)) + 15))
        c = await db.get_character(uid)
        picked = last_fail
        mode = "adv"
    elif req.free_text:
        if int(session.get("turns_left") or 0) <= 0:
            raise HTTPException(402, "Out of Spirituality turns")
        prefix_map = {"talk": "[Реплика/Диалог] ", "inspect": "[Осмотр Духовным зрением] ", "skill": "[Применение способности Пути] "}
        full_txt = prefix_map.get(req.intent, "") + req.free_text.strip()
        sk_key, dc, is_combat = h_story._classify_free_text(full_txt, c)
        picked = {"id": 99, "text": full_txt[:200], "skill": sk_key, "dc": dc, "combat": is_combat}
    else:
        if int(session.get("turns_left") or 0) <= 0:
            raise HTTPException(402, "Out of Spirituality turns")
        options = session.get("options") or []
        picked = next((o for o in options if int(o.get("id", 0)) == req.option_id), options[0] if options else None)
        if not picked:
            raise HTTPException(400, "Option expired")

    sk_key = picked.get("skill", "perception")
    if sk_key not in rules.SKILLS:
        sk_key = "perception"
    dc = int(picked.get("dc", 12))
    roll_res = rules.skill_check(c, ("skill", sk_key), mode=mode)
    success = roll_res.natural == 20 or (roll_res.natural != 1 and roll_res.total >= dc)

    roll_data = {
        "action_text": picked.get("text", ""),
        "skill_key": sk_key,
        "skill_name": rules.SKILLS[sk_key][0],
        "dc": dc,
        "natural": roll_res.natural,
        "rolls": roll_res.rolls,
        "total": roll_res.total,
        "mode": mode,
        "success": success,
    }

    if picked.get("combat"):
        enemy = h_story._spawn_enemy_for(c, session.get("lang", "ru"))
        if success:
            enemy["hp"] = max(1, enemy["hp"] - _rng.randint(3, 7))
        await db.update_story_session(uid, enemy=enemy, last_fail={})
        state = await api_get_state(uid)
        return {"roll": roll_data, "combat_started": True, "state": state}

    roll_info = (
        f"Skill '{sk_key}' ({rules.SKILLS[sk_key][0]}): rolled {roll_res.total} (natural d20={roll_res.natural}) vs DC {dc} "
        f"-> {'SUCCESS' if success else 'FAILURE'}."
    )
    action_prompt = f"Player exact action: \"{picked.get('text', '')}\""
    data = await h_story._call_ai_gm(c, session, action_prompt, roll_info)
    c, session, dig_gain = await h_story._apply_ai_memory_and_state(uid, c, session, f"{action_prompt} [{roll_info}]", data, success)

    await db.update_story_session(uid, last_fail={} if success else picked)
    if data.get("spawn_enemy"):
        enemy = h_story._spawn_enemy_for(c, session.get("lang", "ru"), str(data.get("enemy_name") or ""))
        await db.update_story_session(uid, enemy=enemy)

    state = await api_get_state(uid)
    return {"roll": roll_data, "ai": data, "dig_gain": dig_gain, "state": state}


@app.post("/api/combat/action")
async def api_combat_action(req: CombatPayload):
    """Бесплатный пошаговый бой и охота 1-на-1 на чистом движке rules.py."""
    uid = req.user_id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    if not c:
        raise HTTPException(400, "No character")

    if req.kind == "start_hunt":
        enemy = h_story._spawn_enemy_for(c, session.get("lang", "ru"))
        await db.update_story_session(uid, enemy=enemy)
        return {"log": ["⚔️ Вы выследили опасного Потустороннего в тумане!"], "state": await api_get_state(uid)}

    if req.kind == "rest":
        restored = rules.long_rest(c)
        uses = h_perks.reset_combat_uses(c)
        await db.update_character(uid, sanity=100, lair_uses=uses, **restored)
        return {"log": ["🧘 Когитация завершена: ОЗ, Рассудок и Духовность полностью восстановлены!"], "state": await api_get_state(uid)}

    if req.kind == "flee":
        await db.update_story_session(uid, enemy={})
        return {"log": ["🏃 Вы растворились в сером тумане, разорвав дистанцию."], "state": await api_get_state(uid)}

    enemy = dict(session.get("enemy") or {})
    if not enemy or int(enemy.get("hp", 0)) <= 0:
        raise HTTPException(400, "No active enemy")

    log_lines: list[str] = []
    _, stat_mod = rules.best_path_stat(c)
    prof = rules.effective_prof_bonus(c)
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    uses = dict(c.get("lair_uses") or {})
    new_hp, new_temp = int(c["hp"]), int(c.get("temp_hp") or 0)
    new_sanity = int(c.get("sanity", 100))

    if req.kind == "stunt" and req.stunt_text:
        sk_key, dc, _ = h_story._classify_free_text(req.stunt_text, c)
        roll_res = rules.skill_check(c, ("skill", sk_key))
        win = roll_res.natural == 20 or (roll_res.natural != 1 and roll_res.total >= dc)
        dmg = max(2, _rng.randint(4, 10) + stat_mod) if win else max(1, _rng.randint(1, 4))
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        log_lines.append(
            f"🎲 Манёвр «{req.stunt_text[:60]}» ({rules.SKILLS[sk_key][0]} Сл {dc}): "
            f"выпало {roll_res.total} — {'УСПЕХ' if win else 'ОСЕЧКА'}! Враг теряет {dmg} ОЗ."
        )
    elif req.kind == "mech":
        p_mech = lotm.PATHWAY_MECHANICS.get(c["pathway"])
        left = int(uses.get("path_res", p_mech.get("max_base", 3) if p_mech else 0))
        if not p_mech or left <= 0:
            raise HTTPException(400, "Заряды механики Пути исчерпаны")
        uses["path_res"] = left - 1
        tier_mult = max(1, lotm.OPEN_TIERS.get(int(c["sequence"]), 1) // 2 + 1)
        die_side = p_mech["dice"].split("d")[1]
        roll = rules.roll_expression(f"{tier_mult}d{die_side}")
        dmg = max(2, roll.total + stat_mod)
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        new_temp = max(new_temp, tier_mult * 3)
        log_lines.append(f"⚡ {p_mech['name']}: наносит {dmg} урона и укрепляет щит!")
    elif req.kind == "myth":
        if uses.get("mythical_used") or int(c.get("sequence", 9)) > 4:
            raise HTTPException(400, "Форма Мифического Существа недоступна")
        cost = 10 if "mythical_anchor" in (c.get("feats") or []) else 20
        uses["mythical_used"] = 1
        new_sanity = max(5, new_sanity - cost)
        seq = int(c["sequence"])
        dmg = rules.roll_expression(f"{10 - seq}d10").total + stat_mod
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        new_temp = max(new_temp, (10 - seq) * 10)
        log_lines.append(f"👁 Форма Мифического Существа! Нанесено {dmg} урона, щит +{new_temp} ОЗ!")
    else:
        cantrip = next((s for s in skills if s["seq"] == 9 and s["key"] == "core"), None)
        c_dice = cantrip["dice"] if cantrip and cantrip.get("dice") else "1d8"
        c_name = cantrip["name"] if cantrip else "Удар Потустороннего"
        d20 = _rng.randint(1, 20)
        atk_tot = d20 + prof + stat_mod
        if d20 == 20 or atk_tot >= int(enemy["ac"]):
            roll = rules.roll_expression(c_dice)
            dmg = max(1, roll.total + stat_mod + (roll.total if d20 == 20 else 0))
            enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
            log_lines.append(f"🗡 {c_name}: попадание ({atk_tot} vs КД {enemy['ac']}) → −{dmg} ОЗ врагу!")
        else:
            log_lines.append(f"🗡 {c_name}: промах ({atk_tot} vs КД {enemy['ac']})!")

    victory = False
    if int(enemy["hp"]) <= 0:
        victory = True
        dig_reward = h_story._scaled_digestion_gain(int(c.get("sequence", 9)), 15)
        new_dig = min(100, int(c.get("digestion") or 0) + dig_reward)
        await db.update_character(uid, hp=new_hp, temp_hp=new_temp, sanity=new_sanity, digestion=new_dig, lair_uses=h_perks.reset_combat_uses(c))
        await db.update_story_session(uid, enemy={})
        log_lines.append(f"🏆 Победа! {enemy['name']} повержен! Переваривание зелья +{dig_reward}% (итого {new_dig}%).")
    else:
        e_d20 = _rng.randint(1, 20)
        e_atk = e_d20 + int(enemy.get("atk_bonus", 3))
        eff_ac = int(c.get("ac", 10)) + h_perks.ac_bonus(c)
        if e_d20 == 20 or e_atk >= eff_ac:
            e_dmg = max(1, rules.roll_expression(enemy.get("dmg_dice", "1d8")).total + int(enemy.get("dmg_mod", 1)))
            new_hp, new_temp = rules.apply_damage(new_hp, new_temp, e_dmg)
            log_lines.append(f"👹 {enemy['name']} контратакует ({e_atk} vs КД {eff_ac}) → −{e_dmg} ОЗ вам!")
        else:
            log_lines.append(f"🛡 {enemy['name']} промахивается ({e_atk} vs КД {eff_ac})!")
        if new_hp <= 0:
            new_hp = max(1, int(c["max_hp"]) // 2)
            new_sanity = max(10, new_sanity - 15)
            await db.update_character(uid, hp=new_hp, temp_hp=0, sanity=new_sanity, lair_uses=h_perks.reset_combat_uses(c))
            await db.update_story_session(uid, enemy={})
            log_lines.append("🕯 Серый Туман спас вас от гибели! Вы отступили.")
        else:
            enemy["round"] = int(enemy.get("round", 1)) + 1
            await db.update_character(uid, hp=new_hp, temp_hp=new_temp, sanity=new_sanity, lair_uses=uses)
            await db.update_story_session(uid, enemy=enemy)

    return {"log": log_lines, "victory": victory, "state": await api_get_state(uid)}


@app.post("/api/shop/invoice")
async def api_create_invoice(req: InvoicePayload):
    """Создаёт нативную ссылку на оплату в Telegram Stars (XTR) для вызова Telegram.WebApp.openInvoice."""
    if not _bot_instance:
        raise HTTPException(503, "Bot instance not attached")
    import h_shop
    packs = getattr(h_shop, "PACKS", {})
    pack = packs.get(req.pack_id)
    if not pack:
        raise HTTPException(400, "Unknown pack")
    link = await _bot_instance.create_invoice_link(
        title=pack.get("title_ru", "Пакет Духовности"),
        description=pack.get("desc_ru", "Пополнение ходов и Печатей Судьбы"),
        payload=f"shop:{req.pack_id}:{req.user_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=pack.get("title_ru", "Духовность"), amount=int(pack.get("stars", 50)))],
    )
    return {"invoice_link": link}
