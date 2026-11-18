"""web_app.py — FastAPI сервер для Telegram Mini App и личного веб-сайта LotM RPG."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import re
from typing import Any

from aiogram import Bot
from aiogram.types import LabeledPrice
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import combat_engine as combat
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


def _normalize_skill_paragraphs(text: str) -> str:
    """Выносит пассивные/активные проверки навыков в отдельный абзац и очищает артефакты UTF-8."""
    if not text:
        return ""
    clean = str(text).replace("\ufffd", "").replace("\uFFFD", "")
    return re.sub(
        r'([^\n])\s*(\[[^\]\n]+?(?:—|-)\s*(?:Пассивно|Успех|Провал|Passive|Success|Failure)[^\]\n]*\]:?)',
        r'\1\n\n\2',
        clean,
    )


class ActionPayload(BaseModel):
    user_id: int
    option_id: int | None = None
    free_text: str | None = None
    intent: str = "act"  # act | talk | inspect | skill
    reroll: bool = False
    fate_seal_used: bool = False
    pre_rolled: dict | None = None  # результат мгновенного /api/story/roll (кубик уже брошен на клиенте)


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
    stats_pb: dict[str, int] | None = None
    skills: list[str] | None = None
    expertise: str | None = None
    bio: str = ""


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
    skills_catalog = [
        {"key": k, "name": v[0], "ability": rules.SHORT_ABILITY[v[1]], "ability_key": v[1]}
        for k, v in rules.SKILLS.items()
    ]
    return {
        "pathways": pathways,
        "backgrounds": backgrounds,
        "regions": regions,
        "skills_catalog": skills_catalog,
        "stat_cost": rules.STAT_COST,
        "point_buy_budget": rules.POINT_BUY_BUDGET,
        "tribute_link": config.TRIBUTE_LINK,
    }


@app.get("/api/state/{user_id}")
async def api_get_state(user_id: int) -> dict[str, Any]:
    try:
        await db.upsert_login(user_id)
    except Exception:
        pass
    c = await db.get_character(user_id)
    session = await db.get_story_session(user_id)
    if not c or not c.get("pathway"):
        return {"has_character": False, "session": session, "is_dev": config.is_dev(user_id)}

    path = c["pathway"]
    if not c.get("inventory"):
        c["inventory"] = rules.starting_inventory(path, c.get("faction") or "detective")
        await db.update_character(user_id, inventory=c["inventory"])
    seq = int(c.get("sequence", 9))
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills_list = rules.path_skills(path, choices, seq, int(c.get("start_sequence", 9)))
    p_mech = lotm.pathway_mechanic_for(path, seq) if hasattr(lotm, "pathway_mechanic_for") else lotm.PATHWAY_MECHANICS.get(path, {})
    uses = c.get("lair_uses") or {}
    res_left = int(uses.get("path_res", p_mech.get("max_base", 3)))

    # Последний текст сцены из истории
    last_narr = ""
    for item in reversed(session.get("history") or []):
        if item.get("role") == "assistant":
            try:
                last_narr = json.loads(item["content"]).get("narrative", "")
                if last_narr:
                    last_narr = _normalize_skill_paragraphs(last_narr)
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
            "pathway_mechanic": {**rules.format_pathway_mechanic(p_mech, c), "left": res_left},
            "abilities": skills_list,
            "skill_bonuses": skill_bonuses,
        },
        "session": {
            **session,
            "last_narrative": last_narr,
            "enriched_options": enriched_options,
        },
        "roll_stats": await db.roll_stats(user_id),
        "is_dev": config.is_dev(user_id),
    }


class DevCheatPayload(BaseModel):
    user_id: int
    action: str  # inf_resources | max_digestion | full_heal | set_seq | set_pathway | kill_enemy | reset_scene | add_gold | grant_test_artifact
    value: str | int | None = None


@app.post("/api/dev/cheat")
async def api_dev_cheat(req: DevCheatPayload):
    """Тестовая админ-панель разработчика. Отключается через DEV_MODE=0 в .env."""
    if not config.is_dev(req.user_id):
        raise HTTPException(403, "Dev mode disabled")
    uid = req.user_id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)

    if req.action == "inf_resources":
        await db.activate_vip(uid, days=365)
        await db.update_story_session(uid, turns_left=9999, fate_tokens=99)
    elif req.action == "max_digestion" and c:
        await db.update_character(uid, digestion=100)
    elif req.action == "full_heal" and c:
        restored = rules.long_rest(c)
        uses = h_perks.reset_combat_uses(c)
        await db.update_character(uid, sanity=100, lair_uses=uses, **restored)
    elif req.action == "set_seq" and c and req.value is not None:
        new_seq = max(0, min(9, int(req.value)))
        path = c["pathway"]
        choices = {str(s): "a" for s in lotm.SEQUENCES if s >= new_seq}
        choices.update({str(k): v for k, v in (c.get("pathway_choices") or {}).items()})
        con_mod = rules.ability_mod(int((c.get("stats") or {}).get("con", 10)))
        feats = list(c.get("feats") or [])
        max_hp = rules.sequence_max_hp(path, new_seq, con_mod, feats, pathway_choices=choices)
        slots = lotm.spell_slots_for(new_seq, feats)
        seq_title = lotm.SEQUENCE_TITLES.get(path, {}).get(new_seq, f"Посл. {new_seq}")
        await db.update_character(
            uid,
            sequence=new_seq,
            level=lotm.OPEN_TIERS[new_seq],
            char_class=f"{lotm.PATHWAYS[path]['name']} — {seq_title}",
            pathway_choices=choices,
            max_hp=max_hp,
            hp=max_hp,
            spell_slots={str(r): {"total": n, "left": n} for r, n in slots.items()},
            sanity=100,
            lair_uses=h_perks.reset_combat_uses(c),
        )
    elif req.action == "set_pathway" and c and req.value:
        new_path = str(req.value)
        if new_path in lotm.get_allowed_pathways():
            seq = int(c.get("sequence", 9))
            choices = {str(s): "a" for s in lotm.SEQUENCES if s >= seq}
            con_mod = rules.ability_mod(int((c.get("stats") or {}).get("con", 10)))
            feats = list(c.get("feats") or [])
            max_hp = rules.sequence_max_hp(new_path, seq, con_mod, feats, pathway_choices=choices)
            slots = lotm.spell_slots_for(seq, feats)
            seq_title = lotm.SEQUENCE_TITLES.get(new_path, {}).get(seq, f"Посл. {seq}")
            await db.update_character(
                uid,
                pathway=new_path,
                char_class=f"{lotm.PATHWAYS[new_path]['name']} — {seq_title}",
                pathway_choices=choices,
                max_hp=max_hp,
                hp=max_hp,
                spell_slots={str(r): {"total": n, "left": n} for r, n in slots.items()},
                lair_uses=h_perks.reset_combat_uses({**c, "pathway": new_path}),
            )
    elif req.action == "kill_enemy":
        await db.update_story_session(uid, enemy={})
    elif req.action == "reset_scene":
        await db.update_story_session(uid, options=[], enemy={}, last_fail={})
    elif req.action == "grant_test_artifact" and c:
        chassis = lotm.roll_artifact_chassis(c)
        seq = int(c.get("sequence", 9))
        seq_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(seq, f"Посл. {seq}")
        arts = [a for a in (c.get("artifacts") or []) if isinstance(a, dict)]
        new_art = {
            "id": f"art_{uid}_{len(arts)}_{chassis.get('icon', 'ring')}",
            "name": f"Запечатанная реликвия («{seq_title}»)",
            "icon": chassis.get("icon", "ring"),
            "lore": "Пыль веков осела на гравировке; реликвия тихо дышит в такт твоему пульсу.",
            "skill": chassis.get("skill", "all"),
            "stat": chassis.get("stat", ""),
            "bonus": int(chassis.get("bonus", 1)),
            "ac_bonus": int(chassis.get("ac_bonus", 0)),
            "class": int(chassis.get("class", 3)),
            "curse": str(chassis.get("curse", "")),
            "equipped": len([a for a in arts if a.get("equipped")]) < 2,
        }
        if len(arts) >= 6:
            arts = arts[-5:]
        arts.append(new_art)
        await db.update_character(uid, artifacts=arts)
    elif req.action == "add_gold":
        clues = [x for x in (session.get("clues") or []) if not str(x).startswith("💰")]
        clues.insert(0, "💰 Капитал: 10 000 золотых фунтов")
        await db.update_story_session(uid, clues=clues)

    return await api_get_state(uid)


@app.post("/api/character/equip_artifact")
async def api_equip_artifact(req: DevCheatPayload):
    """Включает/выключает экипировку Запечатанного Артефакта (максимум 2 активных одновременно)."""
    uid = req.user_id
    c = await db.get_character(uid)
    if not c or not c.get("pathway"):
        raise HTTPException(404, "No character")
    artifact_id = str(req.value or "")
    arts = [a for a in (c.get("artifacts") or []) if isinstance(a, dict)]
    target = next((a for a in arts if str(a.get("id")) == artifact_id), None)
    if not target:
        raise HTTPException(404, "Artifact not found")

    if target.get("equipped"):
        target["equipped"] = False
    else:
        equipped = sum(1 for a in arts if a.get("equipped"))
        if equipped >= 2:
            raise HTTPException(400, "Максимум 2 экипированных артефакта")
        target["equipped"] = True

    await db.update_character(uid, artifacts=arts)
    return await api_get_state(uid)


@app.post("/api/build_quick")
async def api_quick_build(req: QuickBuildPayload):
    """Быстрое создание персонажа из веб-интерфейса (Mini App / Сайта)."""
    if req.pathway not in lotm.get_allowed_pathways():
        raise HTTPException(400, "Invalid pathway")
    uid = req.user_id
    path = req.pathway
    seq = 9
    # Ручной Point-Buy (27 очков) либо сбалансированный пресет
    pb = req.stats_pb if req.stats_pb else None
    if pb and all(isinstance(pb.get(a), int) and 8 <= pb[a] <= 15 for a in rules.ABILITIES):
        try:
            if rules.stat_pool_spent(pb) <= rules.POINT_BUY_BUDGET:
                stats = {a: int(pb[a]) for a in rules.ABILITIES}
            else:
                stats = dict(rules.POINTBUY_DEFAULT)
        except Exception:
            stats = dict(rules.POINTBUY_DEFAULT)
    else:
        stats = {"str": 10, "dex": 12, "con": 13, "int": 14, "wis": 13, "cha": 10}
    for stat, bonus in lotm.pathway_stat_bonus(path).items():
        stats[stat] = min(30, stats.get(stat, 10) + int(bonus))

    bgs = getattr(lotm, "BACKGROUNDS", {})
    bg = bgs.get(req.background, {})
    for stat, bonus in bg.get("stat_bonus", {}).items():
        stats[stat] = min(30, stats.get(stat, 10) + int(bonus))

    budget = rules.skill_pick_budget(path, stats)
    all_sk = list(rules.SKILLS.keys())
    free_sk = bg.get("free_skill", "investigation")
    if free_sk not in all_sk:
        free_sk = all_sk[0]
    req_skills = [s for s in (req.skills or []) if s in all_sk and s != free_sk]
    picked = [free_sk] + req_skills[: max(0, budget - 1)]
    expertise = req.expertise if req.expertise in picked else None
    skill_map = {k: (2 if k == expertise else 1) for k in picked}
    choices = {"9": req.branch_9 if req.branch_9 in ("a", "b") else "a"}
    con_mod = rules.ability_mod(stats["con"])
    max_hp = rules.sequence_max_hp(path, seq, con_mod, [], faction_perk="", pathway_choices=choices)
    slots = lotm.spell_slots_for(seq, [])
    ac = 10 + rules.ability_mod(stats["dex"])
    seq_title = lotm.SEQUENCE_TITLES.get(path, {}).get(seq, "Посл. 9")

    bio_text = req.bio.strip()[:5000]
    await db.create_draft(uid, "web_player")
    char_data = dict(
        name=req.name.strip()[:40] or "Путник Тумана",
        bio=bio_text or f"Потусторонний пути {lotm.PATHWAYS[path]['name']}.",
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
        class_resource=(lotm.pathway_mechanic_for(path, seq) if hasattr(lotm, "pathway_mechanic_for") else lotm.PATHWAY_MECHANICS.get(path, {})).get("max_base", 3),
        max_hp=max_hp,
        hp=max_hp,
        temp_hp=0,
        ac=ac,
        saves=lotm.PATHWAYS[path].get("saves", []),
        hit_dice_left=1,
        spell_slots={str(r): {"total": n, "left": n} for r, n in slots.items()},
        inventory=rules.starting_inventory(path, req.background),
        status="approved",
    )
    char_data["lair_uses"] = h_perks.reset_combat_uses(char_data)
    await db.update_character(uid, **char_data)

    regs = getattr(lotm, "STARTING_REGIONS", {})
    reg_info = regs.get(req.region, {"location": "Тинген, улица Железного Креста", "arc_ru": "Туман над Тингеном"})
    await db.get_story_session(uid)
    await db.update_story_session(
        uid,
        turns_left=config.WELCOME_TURNS,
        fate_tokens=2,
        location=reg_info.get("location", "Тинген"),
        current_arc=reg_info.get("arc_ru", ""),
        summary=(f"Начал путь как {seq_title}. " + (f"Личная анкета игрока: {bio_text[:400]}." if bio_text else "")).strip(),
        clues=[f"💰 Капитал: {bg.get('gold', 25)} золотых фунтов", f"🎒 {bg.get('gear', 'Револьвер и плащ')}"],
        npcs=[],
        history=[],
        options=[],
        enemy={},
        last_narrative="",
    )
    return await api_get_state(uid)


def _pack_roll_data(action_text: str, sk_key: str, dc: int, roll_res: rules.D20Result, action_summary: str = "") -> dict:
    success = roll_res.natural == 20 or (roll_res.natural != 1 and roll_res.total >= dc)
    mod_sum = sum(v for _, v in roll_res.parts)
    if not action_summary and action_text:
        action_summary = h_story._generate_action_summary(action_text, sk_key)
    return {
        "action_text": action_text,
        "action_summary": action_summary or rules.SKILLS.get(sk_key, [sk_key])[0],
        "skill_key": sk_key,
        "skill_name": rules.SKILLS.get(sk_key, [sk_key])[0],
        "dc": dc,
        "natural": roll_res.natural,
        "rolls": roll_res.rolls,
        "mode": roll_res.mode,
        "mod_total": mod_sum,
        "parts": [{"label": lbl, "value": val} for lbl, val in roll_res.parts],
        "total": roll_res.total,
        "success": success,
        "is_crit": roll_res.natural == 20,
        "is_fumble": roll_res.natural == 1,
    }


def _resolve_picked(uid: int, c: dict, session: dict, req: "ActionPayload") -> tuple[dict, str]:
    """Определяет проверяемое действие (picked) и режим броска (normal/adv) без списания ходов."""
    if req.reroll:
        last_fail = session.get("last_fail") or {}
        if not last_fail or int(session.get("fate_tokens") or 0) <= 0:
            raise HTTPException(400, "No failed roll or Fate Tokens left")
        return dict(last_fail), "adv"
    if req.free_text:
        prefix_map = {"talk": "[Реплика/Диалог] ", "inspect": "[Осмотр Духовным зрением] ", "skill": "[Применение способности Пути] "}
        full_txt = prefix_map.get(req.intent, "") + req.free_text.strip()
        last_ctx = h_story._last_narrative_from_session(session)
        sk_key, dc, is_combat, action_summary = h_story._classify_free_text(full_txt, c, intent=req.intent or "act", context=last_ctx)
        return {"id": 99, "text": full_txt[:1000], "action_summary": action_summary, "skill": sk_key, "dc": dc, "combat": is_combat}, "normal"
    options = session.get("options") or []
    picked = next((o for o in options if int(o.get("id", 0)) == (req.option_id or 0)), options[0] if options else None)
    if not picked:
        raise HTTPException(400, "Option expired")
    res_picked = dict(picked)
    if not res_picked.get("action_summary"):
        res_picked["action_summary"] = h_story._generate_action_summary(
            str(res_picked.get("text") or ""),
            res_picked.get("skill", "perception")
        )
    return res_picked, "normal"


@app.post("/api/story/new_arc")
async def api_story_new_arc(req: ActionPayload):
    """Начать новую сюжетную арку: сбрасывает опции/угрозы, поднимает счётчик глав."""
    uid = req.user_id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    if not c or not c.get("pathway"):
        raise HTTPException(400, "Create character first")
    session = await db.update_story_session(
        uid,
        current_arc="",
        options=[],
        last_fail={},
        chapter=int(session.get("chapter") or 1) + 1,
    )
    seed = h_story._build_procedural_opening_seed(c)
    ok, reason = await db.try_consume_turn(uid, config.is_dev(uid))
    if not ok:
        if reason == "global_cap_reached":
            raise HTTPException(429, "Server free daily AI budget reached, try again tomorrow")
        raise HTTPException(402, "Out of Spirituality turns")
    prompt = "GENERATE OPENING SCENE (Chapter 1). Use the OPENING SCENE SEED below as binding blueprint.\n" + seed
    data = await h_story._call_ai_gm(c, session, prompt, "New Arc")
    if data.get("error") or not data.get("narrative"):
        status_code = int(data.get("status") or 502)
        if status_code < 400 or status_code >= 600:
            status_code = 502
        raise HTTPException(status_code, f"Мастер временно недоступен ({data.get('message') or 'сбой'})")
    data["narrative"] = _normalize_skill_paragraphs(data.get("narrative", ""))
    c, session, dig_gain = await h_story._apply_ai_memory_and_state(uid, c, session, prompt, data, True)
    return {"ai": data, "dig_gain": dig_gain, "state": await api_get_state(uid), "inventory_delta": data.get("inventory_delta", {})}


@app.post("/api/story/reroll")
async def api_story_reroll(req: ActionPayload):
    """Мгновенный переброс проверки d20 через Печать Судьбы (< 10 мс) БЕЗ обращения к LLM."""
    uid = req.user_id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    if not c or not c.get("pathway"):
        raise HTTPException(400, "Create character first")

    fate_tokens = int(session.get("fate_tokens") or 0)
    if fate_tokens <= 0:
        raise HTTPException(400, "Не осталось Печатей Судьбы")

    # Списание 1 Печати Судьбы и восстановление рассудка
    await db.add_fate_tokens(uid, -1)
    await db.update_character(uid, sanity=min(100, int(c.get("sanity", 100)) + 15))
    c = await db.get_character(uid)

    # Восстановление проверяемого действия (picked)
    picked = None
    if req.pre_rolled and isinstance(req.pre_rolled, dict):
        pre_picked = req.pre_rolled.get("picked")
        if isinstance(pre_picked, dict) and pre_picked.get("skill"):
            picked = dict(pre_picked)
        elif req.pre_rolled.get("skill_key") or req.pre_rolled.get("skill"):
            sk = str(req.pre_rolled.get("skill_key") or req.pre_rolled.get("skill") or "perception")
            picked = {
                "id": int(req.pre_rolled.get("option_id") or 0),
                "text": str(req.pre_rolled.get("action_text") or ""),
                "skill": sk,
                "dc": int(req.pre_rolled.get("dc") or 12),
                "combat": bool(req.pre_rolled.get("combat")),
            }

    if not picked:
        last_fail = session.get("last_fail") or {}
        if last_fail and isinstance(last_fail, dict) and last_fail.get("skill"):
            picked = dict(last_fail)
        else:
            picked, _ = _resolve_picked(uid, c, session, req)

    sk_key = picked.get("skill", "perception")
    if sk_key not in rules.SKILLS:
        sk_key = "perception"
    dc = int(picked.get("dc", 12))

    # Новый бросок d20 строго с преимуществом (adv)
    roll_res = rules.skill_check(c, ("skill", sk_key), mode="adv")
    roll_data = _pack_roll_data(picked.get("text", ""), sk_key, dc, roll_res, action_summary=picked.get("action_summary", ""))
    new_fate = max(0, fate_tokens - 1)

    # Фиксируем использование печати и результат броска в сессии
    await db.update_story_session(
        uid,
        fate_seal_used=1,
        last_fail={} if roll_data["success"] else picked,
    )

    return {
        "roll": roll_data,
        "picked": picked,
        "fate_tokens": new_fate,
        "fate_seal_used": True,
    }


@app.post("/api/story/roll")
async def api_story_roll(req: ActionPayload):
    """Фаза 1 (за ~5 мс): мгновенный бросок d20 со всеми слагаемыми бонуса — ДО обращения к ИИ."""
    if req.reroll:
        return await api_story_reroll(req)
    uid = req.user_id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    if not c or not c.get("pathway"):
        raise HTTPException(400, "Create character first")

    picked, mode = _resolve_picked(uid, c, session, req)
    sk_key = picked.get("skill", "perception")
    if sk_key not in rules.SKILLS:
        sk_key = "perception"
    dc = int(picked.get("dc", 12))
    roll_res = rules.skill_check(c, ("skill", sk_key), mode=mode)
    return {"roll": _pack_roll_data(picked.get("text", ""), sk_key, dc, roll_res, action_summary=picked.get("action_summary", "")), "picked": picked}


_ACTIVE_TURNS: dict[int, asyncio.Task] = {}


async def _execute_turn(req: ActionPayload, uid: int) -> dict[str, Any]:
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    if not c or not c.get("pathway"):
        raise HTTPException(400, "Create character first")

    # Стартовая генерация первой сцены, если опций ещё нет и игрок нажал Старт
    if not req.option_id and not req.free_text and not req.reroll:
        ok, reason = await db.try_consume_turn(uid, config.is_dev(uid))
        if not ok:
            if reason == "global_cap_reached":
                raise HTTPException(429, "Server free daily AI budget reached, try again tomorrow")
            raise HTTPException(402, "Out of Spirituality turns")
        prompt = "GENERATE OPENING SCENE (Chapter 1). Use the OPENING SCENE SEED below as binding blueprint.\n" + h_story._build_procedural_opening_seed(c)
        data = await h_story._call_ai_gm(c, session, prompt, "Scene Start")
        if data.get("error") or not data.get("narrative") or not data.get("options"):
            err_msg = data.get("message") or "Сбой ответа модели ИИ-Мастера"
            status_code = int(data.get("status") or 502)
            if status_code < 400 or status_code >= 600:
                status_code = 502
            await db.update_story_session(uid, turns_left=int(session.get("turns_left") or 0))
            raise HTTPException(status_code, f"Мастер временно недоступен ({err_msg})")
        data["narrative"] = _normalize_skill_paragraphs(data.get("narrative", ""))
        c, session, dig_gain = await h_story._apply_ai_memory_and_state(uid, c, session, prompt, data, True)
        state = await api_get_state(uid)
        return {"roll": None, "ai": data, "dig_gain": dig_gain, "state": state, "inventory_delta": data.get("inventory_delta", {})}

    fate_seal_used = bool(
        req.fate_seal_used
        or (isinstance(req.pre_rolled, dict) and req.pre_rolled.get("fate_seal_used"))
        or session.get("fate_seal_used")
        or req.reroll
    )
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
        fate_seal_used = True
    elif req.pre_rolled:
        # Кубик уже брошен на фазе /api/story/roll — переиспользуем готовый результат
        pre = req.pre_rolled
        pre_picked = pre.get("picked") if isinstance(pre.get("picked"), dict) else {}
        pre_roll = pre.get("roll") if isinstance(pre.get("roll"), dict) else (pre if isinstance(pre, dict) else {})

        # Восстанавливаем полный текст заявки игрока без потерь и затираний:
        if req.free_text:
            prefix_map = {"talk": "[Реплика/Диалог] ", "inspect": "[Осмотр Духовным зрением] ", "skill": "[Применение способности Пути] "}
            action_text = prefix_map.get(req.intent, "") + req.free_text.strip()
        else:
            action_text = (
                str(pre_picked.get("text") or "").strip()
                or str(pre_roll.get("action_text") or "").strip()
                or str(pre.get("action_text") or "").strip()
            )
            if not action_text and req.option_id:
                opts = session.get("options") or []
                opt = next((o for o in opts if int(o.get("id", 0)) == int(req.option_id)), None)
                if opt:
                    action_text = str(opt.get("text") or "").strip()

        sk_key = str(pre_picked.get("skill") or pre_roll.get("skill_key") or pre.get("skill_key") or "perception")
        if sk_key not in rules.SKILLS:
            sk_key = "perception"
        dc_val = int(pre_picked.get("dc") or pre_roll.get("dc") or pre.get("dc") or 12)
        opt_id = int(pre_picked.get("id") or pre.get("option_id") or (99 if req.free_text else (req.option_id or 0)))
        is_combat = bool(pre_picked.get("combat") or pre.get("combat") or pre_roll.get("combat"))

        action_summary = (
            str(pre_picked.get("action_summary") or pre_roll.get("action_summary") or pre.get("action_summary") or "").strip()
            or h_story._generate_action_summary(action_text, sk_key)
        )
        picked = {
            "id": opt_id,
            "text": action_text,
            "action_summary": action_summary,
            "skill": sk_key,
            "dc": dc_val,
            "combat": is_combat,
        }
        roll_data = pre_roll
        # Ход списываем здесь: при pre_rolled стандартная ветка списания ниже не выполняется
        ok, reason = await db.try_consume_turn(uid, config.is_dev(uid))
        if not ok:
            if reason == "global_cap_reached":
                raise HTTPException(429, "Server free daily AI budget reached, try again tomorrow")
            raise HTTPException(402, "Out of Spirituality turns")
    elif req.free_text:
        ok, reason = await db.try_consume_turn(uid, config.is_dev(uid))
        if not ok:
            if reason == "global_cap_reached":
                raise HTTPException(429, "Server free daily AI budget reached, try again tomorrow")
            raise HTTPException(402, "Out of Spirituality turns")
        prefix_map = {"talk": "[Реплика/Диалог] ", "inspect": "[Осмотр Духовным зрением] ", "skill": "[Применение способности Пути] "}
        full_txt = prefix_map.get(req.intent, "") + req.free_text.strip()
        last_ctx = h_story._last_narrative_from_session(session)
        sk_key, dc, is_combat, action_summary = h_story._classify_free_text(full_txt, c, intent=req.intent or "act", context=last_ctx)
        picked = {"id": 99, "text": full_txt[:1000], "action_summary": action_summary, "skill": sk_key, "dc": dc, "combat": is_combat}
    elif not req.pre_rolled:
        ok, reason = await db.try_consume_turn(uid, config.is_dev(uid))
        if not ok:
            if reason == "global_cap_reached":
                raise HTTPException(429, "Server free daily AI budget reached, try again tomorrow")
            raise HTTPException(402, "Out of Spirituality turns")
        options = session.get("options") or []
        picked = next((o for o in options if int(o.get("id", 0)) == (req.option_id or 0)), options[0] if options else None)
        if not picked:
            raise HTTPException(400, "Option expired")
        picked = dict(picked)
        if not picked.get("action_summary"):
            picked["action_summary"] = h_story._generate_action_summary(
                str(picked.get("text") or ""),
                picked.get("skill", "perception")
            )

    if req.pre_rolled and isinstance(req.pre_rolled, dict) and req.pre_rolled.get("roll"):
        # Ключевые значения берём строго из roll_data (roll_res в этой ветке не существует!)
        sk_key = str(roll_data.get("skill_key") or "perception")
        if sk_key not in rules.SKILLS:
            sk_key = "perception"
        dc = int(roll_data.get("dc") or 12)
        success = bool(roll_data.get("success"))
        nat_val = int(roll_data.get("natural") or 10)
        tot_val = int(roll_data.get("total") or 10)
    else:
        sk_key = picked.get("skill", "perception")
        if sk_key not in rules.SKILLS:
            sk_key = "perception"
        dc = int(picked.get("dc", 12))
        roll_res = rules.skill_check(c, ("skill", sk_key), mode=mode)
        roll_data = _pack_roll_data(picked.get("text", ""), sk_key, dc, roll_res, action_summary=picked.get("action_summary", ""))
        success = roll_data["success"]
        nat_val = roll_res.natural
        tot_val = roll_res.total

    if picked.get("combat"):
        enemy = h_story._spawn_enemy_for(c, session.get("lang", "ru"))
        if success:
            enemy["hp"] = max(1, enemy["hp"] - _rng.randint(3, 7))
        await db.update_story_session(uid, enemy=enemy, fate_seal_used=0, last_fail={})
        state = await api_get_state(uid)
        return {"roll": roll_data, "combat_started": True, "state": state}

    roll_info = (
        f"Skill '{sk_key}' ({rules.SKILLS[sk_key][0]}): rolled {roll_data['total']} (natural d20={roll_data['natural']}) vs DC {dc} "
        f"-> {'SUCCESS' if success else 'FAILURE'}."
    )
    try:
        await db.log_roll(
            user_id=uid,
            chat_id=uid,
            chat_type="web",
            username=c.get("username") or "",
            char_name=c.get("name") or "",
            command="web_check",
            label=picked.get("action_summary") or picked.get("text", ""),
            detail=f"{rules.SKILLS[sk_key][0]} DC{dc}",
            total=tot_val,
            natural=nat_val,
        )
    except Exception:
        pass
    action_prompt = f"Player exact action: \"{picked.get('text', '')}\""
    data = await h_story._call_ai_gm(c, session, action_prompt, roll_info, fate_seal_used=fate_seal_used)
    if data.get("error") or not data.get("narrative") or (not data.get("options") and not data.get("spawn_enemy")):
        err_msg = data.get("message") or "Сбой ответа модели ИИ-Мастера"
        status_code = int(data.get("status") or 502)
        if status_code < 400 or status_code >= 600:
            status_code = 502
        await db.update_story_session(uid, turns_left=int(session.get("turns_left") or 0))
        raise HTTPException(status_code, f"Мастер временно недоступен ({err_msg})")
    data["narrative"] = _normalize_skill_paragraphs(data.get("narrative", ""))
    c, session, dig_gain = await h_story._apply_ai_memory_and_state(uid, c, session, f"{action_prompt} [{roll_info}]", data, success)

    await db.update_story_session(uid, fate_seal_used=0, last_fail={} if success else picked)
    if data.get("spawn_enemy"):
        enemy = h_story._spawn_enemy_for(c, session.get("lang", "ru"), str(data.get("enemy_name") or ""))
        await db.update_story_session(uid, enemy=enemy)

    state = await api_get_state(uid)
    return {"roll": roll_data, "ai": data, "dig_gain": dig_gain, "state": state, "inventory_delta": data.get("inventory_delta", {})}


@app.post("/api/story/turn")
async def api_story_turn(req: ActionPayload):
    """Выполняет сюжетный ход с in-memory блокировкой от зависания и повторного списания при F5."""
    uid = req.user_id
    existing_task = _ACTIVE_TURNS.get(uid)
    if existing_task and not existing_task.done():
        try:
            return await asyncio.shield(existing_task)
        except Exception:
            state = await api_get_state(uid)
            return {"roll": None, "ai": None, "state": state}

    task = asyncio.create_task(_execute_turn(req, uid))
    _ACTIVE_TURNS[uid] = task
    try:
        return await task
    finally:
        if _ACTIVE_TURNS.get(uid) is task:
            _ACTIVE_TURNS.pop(uid, None)


@app.post("/api/combat/action")
async def api_combat_action(req: CombatPayload):
    """Пошаговый бой 1-на-1 на общем движке combat_engine — идентичен Telegram-боям."""
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
        return {
            "log": ["🧘 Когитация завершена: ОЗ, Рассудок и Духовность полностью восстановлены!"],
            "state": await api_get_state(uid),
        }

    if req.kind == "flee":
        await db.update_story_session(uid, enemy={})
        return {"log": ["🏃 Вы растворились в сером тумане, разорвав дистанцию."], "state": await api_get_state(uid)}

    enemy = dict(session.get("enemy") or {})
    if not enemy or int(enemy.get("hp", 0)) <= 0:
        raise HTTPException(400, "No active enemy")

    classify = None
    if req.kind == "stunt":
        text = (req.stunt_text or "").strip()
        if not text:
            raise HTTPException(400, "Опишите свой боевой трюк")
        sk_key, dc, _, summary = h_story._classify_free_text(text, c, context=str(enemy.get("name") or ""))
        classify = (sk_key, dc, summary)

    try:
        out = combat.run_round(
            c, enemy, req.kind, ac_bonus=h_perks.ac_bonus(c),
            seq=req.seq, key=req.key, classify=classify,
        )
    except (combat.CombatError, ValueError) as e:
        raise HTTPException(400, str(e))

    c, session = await h_story._persist_combat_outcome(uid, c, session, enemy, out)
    await h_story._log_combat_rolls(uid, c, out)
    return {"log": combat.render_plain(out), "victory": out.victory, "state": await api_get_state(uid)}


@app.post("/api/shop/invoice")
async def api_create_invoice(req: InvoicePayload):
    """Создаёт нативную ссылку на оплату в Telegram Stars (XTR) для вызова Telegram.WebApp.openInvoice."""
    if not _bot_instance:
        raise HTTPException(503, "Bot instance not attached")
    import h_shop
    packs = getattr(h_shop, "PACKS", {})
    pack_aliases = {"spark": "turns_40", "vessel": "artifact_box", "vip": "vip_30"}
    pack = packs.get(pack_aliases.get(req.pack_id, req.pack_id))
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


@app.post("/api/webhook/lava")
async def api_lava_webhook(request: Request, payload: dict[str, Any]):
    """Обработчик вебхуков Lava.top (payment.success, subscription.recurring.payment.success, subscription.cancelled)."""
    event_type = str(payload.get("event") or payload.get("type") or "")
    # Проверка HMAC-подписи запроса: секрет задаётся в .env (LAVA_WEBHOOK_SECRET).
    # Подпись приходит в заголовке X-Signature (SHA256 от тела запроса + секрет).
    if config.LAVA_WEBHOOK_SECRET:
        raw_body = await request.body()
        received_sig = request.headers.get("x-signature") or request.headers.get("x-lava-signature") or ""
        expected_sig = hmac.new(
            config.LAVA_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, str(received_sig)):
            return JSONResponse(status_code=403, content={"ok": False, "reason": "bad_signature"})
    elif event_type in ("payment.success", "subscription.recurring.payment.success"):
        # Секрет не настроен: зачисление платных товаров через вебхук запрещено (fail-closed).
        return JSONResponse(status_code=503, content={"ok": False, "reason": "secret_not_configured"})
    # user_id передаётся в client_utm / custom_fields / email-префиксе при создании ссылки оплаты
    raw_uid = (
        payload.get("client_id")
        or (payload.get("custom_fields") or {}).get("user_id")
        or payload.get("utm_term")
    )
    if not raw_uid or not str(raw_uid).isdigit():
        return {"ok": False, "reason": "no_valid_user_id"}
    uid = int(raw_uid)
    product_code = str(payload.get("product_id") or payload.get("offer_id") or "vip_450")

    if event_type in ("payment.success", "subscription.recurring.payment.success"):
        # Шлюз идемпотентности: сначала фиксируем платёж в логе; при повторной
        # доставке вебхука (тот же contract_id) зачисление не повторяется.
        charge_id = str(payload.get("contract_id") or payload.get("payment_id") or "lava")
        if not await db.log_payment(uid, f"lava:{product_code}", 0, charge_id):
            return {"ok": True, "action": "duplicate_ignored"}
        if "plus" in product_code:
            await db.activate_vip(uid, days=30, pool=config.VIP_PLUS_MONTHLY_POOL)
            await db.add_fate_tokens(uid, 5)
        elif "vip" in product_code:
            await db.activate_vip(uid, days=30, pool=config.VIP_MONTHLY_POOL)
            await db.add_fate_tokens(uid, 3)
        else:
            await db.add_turns(uid, 120)
            await db.add_fate_tokens(uid, 2)
        return {"ok": True, "action": "credited"}

    if event_type == "subscription.cancelled":
        return {"ok": True, "action": "cancelled_logged"}

    return {"ok": True, "action": "ignored"}
