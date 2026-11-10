"""h_story.py — ИИ-Мастер со свободным вводом любых действий (в сюжете и в бою), Трёхслойной Памятью и Перебросом Судьбы."""
from __future__ import annotations

import json
import logging
import random

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import db
import h_perks
import lotm_data as lotm
import rules
import ui
from ui import btn, esc

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

_rng = random.SystemRandom()

SOLO_ENEMIES: list[dict] = [
    {"name_ru": "Потерявший контроль Потусторонний", "name_en": "Rampager of Iron Cross Street", "atk_stat": "str", "dice": "1d8", "desc_ru": "Его тело мутировало после срыва рассудка — сквозь кожу прорастают глаза и щупальца.", "desc_en": "His body mutated after losing control, eyes and tentacles sprouting through torn skin."},
    {"name_ru": "Проповедник Ордена Авроры", "name_en": "Aurora Order Zealot", "atk_stat": "wis", "dice": "1d10", "desc_ru": "Фанатик Истинного Творца, режущий собственную ладонь ритуальным серпом.", "desc_en": "A fanatic of the True Creator slicing his palm with a ritual sickle."},
    {"name_ru": "Боевая Марионетка Тайного Ордена", "name_en": "Secret Order Marionette", "atk_stat": "dex", "dice": "1d8", "desc_ru": "Движется рывками на незримых духовных нитях, сжимая револьвер с гравировкой.", "desc_en": "Moves in eerie jerks along invisible spiritual threads, clutching an engraved revolver."},
    {"name_ru": "Лазутчица Секты Демонессы", "name_en": "Demoness Sect Infiltrator", "atk_stat": "dex", "dice": "1d10", "desc_ru": "В её глазах пляшет ледяное очарование, а вокруг запястий вьётся чёрное пламя.", "desc_en": "Frosty charm dances in her eyes while black flames coil around her wrists."},
    {"name_ru": "Гончая Фулгрима из Тумана Истории", "name_en": "Hound of Fulgrim Projection", "atk_stat": "int", "dice": "1d8", "desc_ru": "Хищная тварь из серого тумана, охотящаяся на носителей потусторонних черт.", "desc_en": "A predatory hound from the gray fog hunting Beyonders."},
]

STATIC_SYSTEM_PROMPT = """You are the AI Game Master for an open-world tabletop RPG set in the 'Lord of the Mysteries' (LotM) universe (Fifth Epoch, Autumn 1349).
The player is a 'Fragment of the First Creator' (An Anomaly Outside Fate): divine prophecies and 0-08 cannot foresee their path, allowing them to build wealth, create syndicates, or alter canonical events.

CORE GM RULES:
1. YES-AND WITH DICE CONSEQUENCES: The player can choose an option OR type ANY custom action, business deal, or combat stunt. Weave their exact words into the scene based on the D20 Roll Result.
   - If the roll SUCCEEDED (especially on high rolls or Nat 20): let their audacious or bizarre plan actually work in a clever, atmospheric LotM way!
   - If the roll FAILED: let their crazy stunt backfire with tense, funny, or dangerous consequences (-HP or -Sanity), pushing the scene forward.
   - ANTI-GODMODING: If a low-sequence player declares impossible godlike cheats (e.g., "I instantly become Sequence 0"), treat it in-universe as a dangerous hallucination of losing control, a backfiring ritual, or a comical misfire.
2. DISCO ELYSIUM PASSIVE SKILL VOICES: In EVERY `narrative`, include 1 atmospheric inner-voice insight in brackets from the player's Top Passive Skills (e.g., "[Проницательность — Пассивно]: Его пальцы дрожат на трости — он блефует.").
3. WEALTH & SANDBOX FREEDOM: If the player trades, invests, loots, or builds a business/faction, update their gold or assets in `new_clue`.
4. ACTING METHOD (Potion Digestion): Reward `acting_gain` (3 to 9) when their action matches their Sequence Acting Principle.

Respond STRICTLY with a valid JSON object matching this exact schema:
{
  "location": "Specific place name in target language",
  "current_arc": "Updated or current main goal (1 sentence)",
  "summary_append": "1 concise sentence summarizing what just happened",
  "new_clue": "New clue, item, or Gold Pounds update (e.g. '💰 Капитал: 120 фунтов'), or empty string",
  "new_npc": "Name & role of a new/updated NPC, or empty string",
  "narrative": "2-3 vivid paragraphs (120-190 words) including 1 [Passive Skill] insight and reacting to the D20 roll.",
  "acting_gain": 5,
  "acting_feedback": "1 short sentence explaining how their action resonated with their potion (+X%).",
  "hp_delta": 0,
  "sanity_delta": 0,
  "spawn_enemy": false,
  "enemy_name": "",
  "options": [
    {"id": 1, "text": "Action 1 (<=40 chars)", "skill": "investigation", "dc": 12},
    {"id": 2, "text": "Action 2 (<=40 chars)", "skill": "persuasion", "dc": 13},
    {"id": 3, "text": "Risky / Combat action (<=40 chars)", "skill": "arcana", "dc": 14, "combat": true}
  ]
}
Allowed 'skill' keys: athletics, brawling, acrobatics, sleight_of_hand, stealth, firearms, arcana, history, investigation, nature, religion, animal_handling, insight, medicine, perception, survival, deception, intimidation, performance, persuasion."""


def _classify_free_text(text: str, c: dict) -> tuple[str, int, bool]:
    """Умный классификатор: определяет проверяемый навык (из 20), адекватную Сл (DC) и триггер боя по тексту игрока."""
    low = text.lower()
    sk_key = "investigation"

    if any(w in low for w in ("гадан", "маятник", "ритуал", "зель", "оккульт", "молитв", "заклинан", "духовн", "карт")):
        sk_key = "arcana"
    elif any(w in low for w in ("зрени", "аур", "призрак", "дух", "чуть")):
        sk_key = "animal_handling"
    elif any(w in low for w in ("украс", "ворую", "карман", "ловкост", "фокус", "подмен", "жонгл")):
        sk_key = "sleight_of_hand"
    elif any(w in low for w in ("скрыт", "тень", "тихо", "крад", "пряч", "подкрад")):
        sk_key = "stealth"
    elif any(w in low for w in ("сальто", "прыг", "паркур", "уворач", "кувыр", "люстр", "окно")):
        sk_key = "acrobatics"
    elif any(w in low for w in ("выбив", "ломаю", "держ", "тащу", "толка")):
        sk_key = "athletics"
    elif any(w in low for w in ("захват", "душу", "вреза", "кулак", "борьб")):
        sk_key = "brawling"
    elif any(w in low for w in ("стрел", "револьвер", "пистолет", "ружь", "выстрел", "пул")):
        sk_key = "firearms"
    elif any(w in low for w in ("обман", "лгу", "притвор", "блеф", "прикидыва", "актёр", "маск")):
        sk_key = "deception"
    elif any(w in low for w in ("соблазн", "танц", "пою", "этикет", "поклон", "представлен", "анекдот", "шутк")):
        sk_key = "performance"
    elif any(w in low for w in ("убежд", "говор", "договар", "прошу", "дипломат", "торг")):
        sk_key = "persuasion"
    elif any(w in low for w in ("угрож", "пуга", "давл", "крич", "рычу", "допрос")):
        sk_key = "intimidation"
    elif any(w in low for w in ("лечу", "перевяз", "яд", "пульс", "рана", "вскрыт")):
        sk_key = "medicine"
    elif any(w in low for w in ("механиз", "бомб", "взрыв", "шестер", "паров", "замк")):
        sk_key = "nature"
    elif any(w in low for w in ("мотив", "читаю мысл", "микровыраж", "психоанализ", "взгляд")):
        sk_key = "insight"

    # Определяем сложность (DC) в зависимости от дерзости / абсурдности заявки
    dc = 12
    if any(w in low for w in ("вслепую", "одновременно", "сальто", "соблазн", "украсть у", "рикошет", "на лету", "люстр")):
        dc = 14
    if any(w in low for w in ("взрываю весь", "убиваю одним", "бог", "0 последовательн", "уничтожаю город", "бессмерт")):
        dc = 20  # Защита от читерства (Anti-Godmoding)

    # Проверяем, использует ли игрок название своего открытого умения Пути в тексте (+2 к шансу / снижение Сл на 2)
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    my_skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    for s in my_skills:
        if s["name"].lower()[:6] in low:
            dc = max(10, dc - 2)
            break

    is_combat_init = any(w in low for w in ("атакую в лоб", "нападаю на него", "вызываю на бой", "вступаю в бой"))
    return sk_key, dc, is_combat_init


def _default_arc_for(c: dict, lang: str) -> str:
    seq_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(c["sequence"], "Потусторонний")
    if lang == "en":
        return f"Case #1: Investigate the occult anomaly in Tingen while acting as a '{seq_title}'."
    return f"Дело №1: Расследовать мистическое происшествие в Тингене и переварить зелье «{seq_title}»."


def _fallback_scene(c: dict, session: dict, last_action: str, roll_str: str = "") -> dict:
    is_en = session.get("lang") == "en"
    seq = int(c.get("sequence", 9))
    seq_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(seq, f"Посл. {seq}")
    arc = session.get("current_arc") or _default_arc_for(c, session.get("lang", "ru"))
    if is_en:
        return {
            "location": session.get("location") or "Tingen City, Iron Cross Street",
            "current_arc": arc,
            "summary_append": f"Performed action: '{last_action[:55]}'.",
            "new_clue": "Silver-stained tarot card",
            "new_npc": "Inspector Royd (Nighthawks)",
            "narrative": (
                f"Yellowish fog swirls around the gas lamps as you execute your move: «{last_action}» ({roll_str}). "
                f"The world reacts immediately to your '{seq_title}' instincts — from the overturned ritual table rolls a silver-stained tarot card, "
                f"while a figure in a dark double-breasted coat freezes in the doorway, stunned by your audacity."
            ),
            "acting_gain": 5,
            "acting_feedback": f"Your creative action resonates with the '{seq_title}' potion (+5%).",
            "hp_delta": 0,
            "sanity_delta": -1,
            "spawn_enemy": False,
            "enemy_name": "",
            "options": [
                {"id": 1, "text": "Inspect the tarot card with Spirit Vision", "skill": "animal_handling", "dc": 12},
                {"id": 2, "text": "Bluff the stranger in the doorway", "skill": "deception", "dc": 13},
                {"id": 3, "text": "Attack the figure before they draw", "skill": "firearms", "dc": 13, "combat": True},
            ],
        }
    return {
        "location": session.get("location") or "Тинген, улица Железного Креста",
        "current_arc": arc,
        "summary_append": f"Совершил действие: «{last_action[:55]}».",
        "new_clue": "Карта Таро со следами ритуального серебра",
        "new_npc": "Дознаватель Ройд (Ночные Ястребы)",
        "narrative": (
            f"Густой туман Тингена клубится у газовых фонарей, когда ты воплощаешь свой замысел в жизнь: "
            f"«<i>{last_action}</i>» {roll_str}. Окружающий мир мгновенно откликается на твой манёвр — "
            f"с перевёрнутого стола слетает карта Таро, испачканная ритуальным серебром, а фигура в чёрном двубортном сюртуке "
            f"в дверном проёме замирает врасплох от такой дерзости."
        ),
        "acting_gain": 5,
        "acting_feedback": f"Твоя нестандартная выходка отлично резонирует с зельем «{seq_title}» (+5%).",
        "hp_delta": 0,
        "sanity_delta": -1,
        "spawn_enemy": False,
        "enemy_name": "",
        "options": [
            {"id": 1, "text": "Осмотреть карту Духовным зрением", "skill": "animal_handling", "dc": 12},
            {"id": 2, "text": "Заговорить зубы незнакомцу в дверях", "skill": "deception", "dc": 13},
            {"id": 3, "text": "Атаковать фигуру на упреждение", "skill": "firearms", "dc": 13, "combat": True},
        ],
    }


async def _call_ai_gm(c: dict, session: dict, user_action: str, roll_info: str = "") -> dict:
    if not config.LLM_API_KEY:
        return _fallback_scene(c, session, user_action, roll_info)

    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    ab_names = ", ".join(s["name"] for s in skills)
    seq = int(c.get("sequence", 9))
    seq_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(seq, f"Seq {seq}")
    eff_ac = int(c.get("ac", 10)) + h_perks.ac_bonus(c)
    lang = session.get("lang", "ru")

    arc = session.get("current_arc") or _default_arc_for(c, lang)
    summary = session.get("summary") or ("Расследование только началось." if lang == "ru" else "Investigation just started.")
    clues = ", ".join(session.get("clues") or []) or ("Пока нет" if lang == "ru" else "None yet")
    npcs = ", ".join(session.get("npcs") or []) or ("Пока нет" if lang == "ru" else "None yet")
    acting_principle = lotm.ACTING_PRINCIPLES.get(c["pathway"], "Отыгрывай роль своей Последовательности.")

    gm_mode = session.get("gm_mode", "canon")
    if gm_mode == "madness":
        gm_mode_desc = (
            "UNHINGED & CREATIVE D&D SANDBOX MODE: Welcome absurd, hilarious, chaotic, and audacious player stunts! "
            "Blend dark Victorian LotM mysticism with witty, dynamic tabletop D&D energy where crazy plans lead to epic or darkly funny outcomes."
        )
    else:
        gm_mode_desc = (
            "DARK VICTORIAN CANON LOTM MODE: Atmospheric, tense, mysterious steampunk & Lovecraftian investigation, "
            "while still fully supporting creative and unconventional player actions."
        )

    # --- Динамический контекст (идёт в user-сообщении, system остаётся кэшируемым) ---
    lang_name = "English" if lang == "en" else "Russian"
    turn_no = int(session.get("chapter") or 0)
    clock_event = ""
    if turn_no > 0 and turn_no % 5 == 0:
        clock_event = f"WORLD CLOCK EVENT: {lotm.WORLD_CLOCK_EVENTS[min((turn_no // 5) - 1, len(lotm.WORLD_CLOCK_EVENTS) - 1)]}"

    bg = lotm.BACKGROUNDS.get(c.get("faction") or "", {})
    region = lotm.STARTING_REGIONS.get(c.get("faction_perk") or "", {})
    voices = rules.get_passive_voices(c)
    history_txt = " | ".join((session.get("history") or [])[-6:])

    dyn = (
        f"[LANGUAGE: Respond strictly in {lang_name}. TONE MODE: {gm_mode_desc}]\n\n"
        f"=== PLAYER CHARACTER ===\n"
        f"- Name: {c['name']} | Pathway: {lotm.PATHWAYS.get(c['pathway'], {}).get('name', c['pathway'])} | Sequence {seq}: \"{seq_title}\"\n"
        f"- Background: {bg.get('name', 'Unknown')} | Starting Region: {region.get('name', 'Tingen')}\n"
        f"- Region Opening Hook: {region.get('opening_hook', '')}\n"
        f"- Acting Method Principle (Roleplay Law): {acting_principle}\n"
        f"- Top Passive Skill Voices (use 1 as [Passive] insight): {voices}\n"
        f"- HP: {c['hp']}/{c['max_hp']} | AC: {eff_ac} | Sanity: {c.get('sanity', 100)}% | Potion Digestion: {c.get('digestion', 0)}%\n"
        f"- Known Beyonder Abilities: {ab_names}\n\n"
        f"=== PERSISTENT STORY MEMORY (DO NOT CONTRADICT!) ===\n"
        f"- Active Investigation Arc (Main Goal): {arc}\n"
        f"- Story Chronicle So Far: {summary}\n"
        f"- Recent Chronicle: {history_txt or 'None yet'}\n"
        f"- Discovered Clues & Items: {clues}\n"
        f"- Met NPCs & Status: {npcs}\n"
        f"- Current Location: {session.get('location', 'Tingen')}\n"
        f"{('# ' + clock_event) if clock_event else ''}\n"
    )

    hist_window = 10 if session.get("is_vip") else 6
    full_user_msg = f"{dyn}\n=== PLAYER ACTION ===\n{user_action}"
    if roll_info:
        full_user_msg += f"\n[SYSTEM DICE ENGINE: {roll_info}]"
    messages = [{"role": "system", "content": STATIC_SYSTEM_PROMPT}, *(session.get("history") or [])[-hist_window:]]
    messages.append({"role": "user", "content": full_user_msg})

    try:
        timeout = aiohttp.ClientTimeout(total=38)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                config.LLM_API_URL,
                headers={
                    "Authorization": f"Bearer {config.LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.LLM_MODEL,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0.8 if gm_mode == "madness" else 0.72,
                    "max_tokens": 600,
                },
            ) as resp:
                if resp.status != 200:
                    log.warning("LLM API status %s", resp.status)
                    return _fallback_scene(c, session, user_action, roll_info)
                payload = await resp.json()
                raw = payload["choices"][0]["message"]["content"]
                data = json.loads(raw)
                if not isinstance(data.get("options"), list) or not data.get("narrative"):
                    return _fallback_scene(c, session, user_action, roll_info)
                return data
    except Exception as e:
        log.warning("LLM call failed, using fallback: %s", e)
        return _fallback_scene(c, session, user_action, roll_info)


def _scaled_digestion_gain(seq: int, base_gain: int) -> int:
    """На 9–8 Посл. зелье усваивается быстро, на 7–5 средне, на 4–1 (Полубоги и Ангелы) медленнее."""
    seq = int(seq or 9)
    if seq >= 8:
        return max(2, base_gain)
    if seq >= 5:
        return max(1, int(base_gain * 0.7))
    return max(1, int(base_gain * 0.45))


async def _apply_ai_memory_and_state(
    uid: int, c: dict, session: dict, action_prompt: str, data: dict, roll_success: bool
) -> tuple[dict, dict, int]:
    """Обновляет Трёхслойную Память (Арку, Летопись, Улики, NPC) и статы персонажа."""
    raw_gain = int(data.get("acting_gain") or (5 if roll_success else 2))
    unscaled = max(1, min(10, raw_gain if roll_success else max(2, raw_gain // 2)))
    dig_gain = _scaled_digestion_gain(int(c.get("sequence", 9)), unscaled)
    new_dig = min(100, int(c.get("digestion") or 0) + dig_gain)

    hp_delta = int(data.get("hp_delta") or 0)
    san_delta = int(data.get("sanity_delta") or 0)
    new_hp, new_temp = int(c["hp"]), int(c.get("temp_hp") or 0)
    if hp_delta < 0:
        new_hp, new_temp = rules.apply_damage(new_hp, new_temp, -hp_delta)
    elif hp_delta > 0:
        new_hp = rules.apply_heal(new_hp, int(c["max_hp"]), hp_delta)
    new_hp = max(1, new_hp)
    new_san = max(5, min(100, int(c.get("sanity", 100)) + san_delta))

    await db.update_character(uid, hp=new_hp, temp_hp=new_temp, sanity=new_san, digestion=new_dig)
    c = await db.get_character(uid)

    max_sum_len = 1600 if session.get("is_vip") else 800
    old_sum = (session.get("summary") or "").strip()
    append_sum = (data.get("summary_append") or "").strip()
    new_sum = (f"{old_sum} {append_sum}".strip() if append_sum else old_sum)[-max_sum_len:]

    clues = list(session.get("clues") or [])
    new_clue = (data.get("new_clue") or "").strip()
    if new_clue and new_clue not in clues:
        clues = (clues + [new_clue])[-12:]

    npcs = list(session.get("npcs") or [])
    new_npc = (data.get("new_npc") or "").strip()
    if new_npc and new_npc not in npcs:
        npcs = (npcs + [new_npc])[-12:]

    history = (session.get("history") or []) + [
        {"role": "user", "content": action_prompt},
        {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
    ]
    new_turns = max(0, int(session.get("turns_left") or 0) - 1)
    new_arc = (data.get("current_arc") or session.get("current_arc") or _default_arc_for(c, session.get("lang", "ru"))).strip()

    session = await db.update_story_session(
        uid,
        location=data.get("location") or session["location"],
        current_arc=new_arc,
        summary=new_sum,
        clues=clues,
        npcs=npcs,
        history=history[-10:],
        options=data.get("options") or [],
        turns_left=new_turns,
    )
    return c, session, dig_gain


def _spawn_enemy_for(c: dict, lang: str, custom_name: str = "") -> dict:
    seq = int(c.get("sequence", 9))
    tier = lotm.OPEN_TIERS.get(seq, 1)
    tmpl = _rng.choice(SOLO_ENEMIES)
    name = custom_name or (tmpl["name_en"] if lang == "en" else tmpl["name_ru"])
    desc = tmpl["desc_en"] if lang == "en" else tmpl["desc_ru"]
    max_hp = max(18, int(c.get("max_hp", 24) * 0.85))
    return {
        "name": name,
        "seq": seq,
        "hp": max_hp,
        "max_hp": max_hp,
        "ac": 11 + tier,
        "atk_bonus": 2 + tier,
        "dmg_dice": f"{max(1, (tier + 1) // 2)}d{tmpl['dice'].split('d')[1]}",
        "dmg_mod": tier,
        "save_bonus": tier,
        "desc": desc,
        "round": 1,
    }


def _header_bar(c: dict, session: dict) -> str:
    seq = int(c.get("sequence", 9))
    seq_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(seq, f"Посл. {seq}")
    eff_ac = int(c.get("ac", 10)) + h_perks.ac_bonus(c)
    vip_badge = "👑" if session.get("is_vip") else "⚡"
    mode_icon = "🎭" if session.get("gm_mode") == "madness" else "🕯"
    turns_str = f"{vip_badge} {session.get('turns_left', 0)} · 🎲 {session.get('fate_tokens', 0)}"
    temp = f" (+{c['temp_hp']})" if int(c.get("temp_hp") or 0) > 0 else ""
    loc = esc(session.get("location") or "Тинген")
    arc = esc(session.get("current_arc") or _default_arc_for(c, session.get("lang", "ru")))
    return (
        f"📍 <i>{loc}</i> | {turns_str}\n"
        f"📜 <b>Дело:</b> <i>{arc}</i>\n"
        f"{mode_icon} <b>{esc(c['name'])}</b> (Посл. {seq} «{esc(seq_title)}»)\n"
        f"❤️ {c['hp']}/{c['max_hp']}{temp} · 🛡 КД {eff_ac} · 🧠 {c.get('sanity', 100)}% · 🧪 {c.get('digestion', 0)}%\n"
        "────────────────────"
    )


def _story_kb(c: dict, session: dict, allow_reroll: bool = False) -> InlineKeyboardMarkup:
    lang = session.get("lang", "ru")
    is_en = lang == "en"
    rows: list[list[InlineKeyboardButton]] = []
    if allow_reroll and int(session.get("fate_tokens") or 0) > 0 and session.get("last_fail"):
        rows.append([
            btn(
                f"🎲 Reroll Fate with Advantage ({session['fate_tokens']} left)" if is_en else f"🎲 Перебросить Судьбу (2d20, ост. {session['fate_tokens']})",
                "st:reroll",
            )
        ])
    for opt in (session.get("options") or [])[:3]:
        oid = int(opt.get("id", 1))
        sk_key = opt.get("skill", "perception")
        if sk_key not in rules.SKILLS:
            sk_key = "perception"
        dc = int(opt.get("dc", 12))
        bonus = rules.skill_total(c, sk_key)
        sk_name = sk_key.capitalize() if is_en else rules.SKILLS[sk_key][0].split()[0]
        combat_icon = "⚔️" if opt.get("combat") else "🎲"
        rows.append([
            btn(f"{combat_icon} [{sk_name} {bonus:+d} | Сл {dc}] {opt.get('text', '')[:32]}", f"st:opt:{oid}")
        ])
    cur_mode = session.get("gm_mode", "canon")
    mode_btn_label = ("🎭 Режим: Угар" if cur_mode == "madness" else "🕯 Режим: Канон") if not is_en else ("🎭 Mode: Sandbox" if cur_mode == "madness" else "🕯 Mode: Canon")
    rows.append([
        btn("📓 Дневник" if not is_en else "📓 Journal", "st:journal"),
        btn(mode_btn_label, "st:toggle_mode"),
        btn("🧘 Когитация" if not is_en else "🧘 Rest", "st:rest"),
    ])
    if int(c.get("digestion") or 0) >= 100:
        rows.append([btn("🧪 Advance Sequence (/promote)!" if is_en else "🧪 Выпить зелье следующей ступени (/promote)!", "st:promote_hint")])
    rows.append([
        btn("⚔️ Охота (1-на-1)" if not is_en else "⚔️ Hunt (1v1)", "st:hunt"),
        btn("🏛 Святилище / Магазин" if not is_en else "🏛 Sanctuary / Shop", "shop:open"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _combat_kb(c: dict, session: dict) -> InlineKeyboardMarkup:
    is_en = session.get("lang") == "en"
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    rows: list[list[InlineKeyboardButton]] = []

    cantrip = next((s for s in skills if s["seq"] == 9 and s["key"] == "core"), None)
    c_name = cantrip["name"] if cantrip else ("Удар Потустороннего" if not is_en else "Beyonder Strike")
    c_dice = cantrip["dice"] if cantrip and cantrip.get("dice") else "1d8"
    rows.append([btn(f"🗡 {c_name} ({c_dice}, бесплатно)", "st:atk:cantrip")])

    p_mech = lotm.PATHWAY_MECHANICS.get(c["pathway"], {})
    uses = c.get("lair_uses") or {}
    res_left = int(uses.get("path_res", p_mech.get("max_base", 3)))
    if p_mech and res_left > 0:
        rows.append([btn(f"⚡ {p_mech['name'][:30]} ({res_left})", "st:atk:mech")])

    for sk in sorted(skills, key=lambda s: -s["seq"]):
        if sk["action"] == "passive" or (sk["seq"] == 9 and sk["key"] == "core"):
            continue
        cost_str = f"Р{sk['slot_rank']}" if sk["slot_cost"] else "0"
        rows.append([btn(f"✨ {sk['name'][:28]} ({cost_str})", f"st:atk:sk:{sk['seq']}:{sk['key']}")])

    if int(c.get("sequence", 9)) <= 4 and not uses.get("mythical_used"):
        rows.append([btn("👁 Форма Мифического Существа (-20🧠)", "st:atk:myth")])

    rows.append([btn("🏃 Отступить в туман" if not is_en else "🏃 Retreat into the fog", "st:flee")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _combat_screen_text(c: dict, session: dict, log_lines: list[str]) -> str:
    enemy = session.get("enemy") or {}
    head = _header_bar(c, session)
    e_bar = (
        f"👹 <b>{esc(enemy.get('name', 'Враг'))}</b> (Посл. {enemy.get('seq', 9)})\n"
        f"❤️ ОЗ врага: <b>{enemy.get('hp', 0)}/{enemy.get('max_hp', 1)}</b> · 🛡 КД {enemy.get('ac', 12)}\n"
        f"<i>{esc(enemy.get('desc', ''))}</i>\n"
        "────────────────────\n"
    )
    body = "\n".join(log_lines) if log_lines else "⚔️ <b>Противник перед тобой! Жми кнопку умения ИЛИ напиши любой боевой трюк прямо в чат:</b>"
    return f"{head}\n{e_bar}{body}"


@router.message(Command("story", "play", "hunt", "journal", "mode"))
async def cmd_story(message: Message):
    c = await db.get_character(message.from_user.id)
    if not c or not c.get("pathway"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🔮 Создать Потустороннего (/build)", "st:go_build")]])
        await message.answer("Сначала выпей зелье 9-й Последовательности и создай персонажа: /build", reply_markup=kb)
        return
    session = await db.get_story_session(message.from_user.id)
    cmd = (message.text or "").split()[0].lower()
    if cmd == "/hunt":
        enemy = _spawn_enemy_for(c, session.get("lang", "ru"))
        session = await db.update_story_session(c["user_id"], enemy=enemy)
        await message.answer(_combat_screen_text(c, session, []), reply_markup=_combat_kb(c, session))
        return
    if cmd == "/journal":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("🔄 Начать новую сюжетную арку", "st:new_arc")],
            [btn("🌫 К сюжету", "st:resume")],
        ])
        await message.answer(_journal_text(c, session), reply_markup=kb)
        return
    if cmd == "/mode":
        new_m = "madness" if session.get("gm_mode") == "canon" else "canon"
        session = await db.update_story_session(c["user_id"], gm_mode=new_m)
        label = "🎭 <b>Безумный D&D-Песочница (Угар и свобода)</b>" if new_m == "madness" else "🕯 <b>Мрачный Канон Повелителя Тайн</b>"
        await message.answer(f"Стиль ИИ-Мастера переключён на: {label}!")
        await _render_or_start_story(message, c, session)
        return
    await _render_or_start_story(message, c, session)


@router.callback_query(F.data == "st:toggle_mode")
async def cb_toggle_mode(cb: CallbackQuery):
    c = await db.get_character(cb.from_user.id)
    session = await db.get_story_session(cb.from_user.id)
    new_m = "madness" if session.get("gm_mode") == "canon" else "canon"
    session = await db.update_story_session(cb.from_user.id, gm_mode=new_m)
    msg = "🎭 Включён режим Безумной D&D-Песочницы!" if new_m == "madness" else "🕯 Включён режим Мрачного Канона LotM!"
    await cb.answer(msg, show_alert=True)
    await _render_or_start_story(cb.message, c, session, edit=True)


def _journal_text(c: dict, session: dict) -> str:
    arc = session.get("current_arc") or _default_arc_for(c, session.get("lang", "ru"))
    summary = session.get("summary") or "Пока записей нет."
    clues = session.get("clues") or []
    npcs = session.get("npcs") or []
    principle = lotm.ACTING_PRINCIPLES.get(c["pathway"], "")
    return (
        f"📓 <b>Дневник Потустороннего: {esc(c['name'])}</b>\n\n"
        f"🧭 <b>Заповедь Метода Отыгрыша:</b>\n<i>{esc(principle)}</i>\n\n"
        f"📜 <b>Текущее расследование:</b>\n{esc(arc)}\n\n"
        f"📖 <b>Летопись событий:</b>\n{esc(summary)}\n\n"
        f"🔍 <b>Найденные улики и предметы:</b>\n" + ("\n".join(f"• {esc(x)}" for x in clues) if clues else "—") + "\n\n"
        f"👥 <b>Встреченные лица (NPC):</b>\n" + ("\n".join(f"• {esc(x)}" for x in npcs) if npcs else "—")
    )


@router.callback_query(F.data == "st:journal")
async def cb_journal(cb: CallbackQuery):
    c = await db.get_character(cb.from_user.id)
    session = await db.get_story_session(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("🔄 Начать новую сюжетную арку", "st:new_arc")],
        [btn("🌫 Вернуться в сюжет", "st:resume")],
    ])
    await ui.safe_edit(cb.message, _journal_text(c, session), kb)
    await cb.answer()


@router.callback_query(F.data == "st:new_arc")
async def cb_new_arc(cb: CallbackQuery):
    c = await db.get_character(cb.from_user.id)
    session = await db.get_story_session(cb.from_user.id)
    session = await db.update_story_session(
        cb.from_user.id,
        current_arc="",
        options=[],
        chapter=int(session.get("chapter") or 1) + 1,
    )
    await cb.answer("Начинаем новое дело!")
    await _render_or_start_story(cb.message, c, session, edit=True)


@router.callback_query(F.data == "st:go_build")
async def cb_go_build(cb: CallbackQuery):
    await cb.message.answer("Нажми /build, чтобы начать создание своего Потустороннего с 1-го шага!")
    await cb.answer()


@router.callback_query(F.data == "st:promote_hint")
async def cb_promote_hint(cb: CallbackQuery):
    await cb.message.answer("🧪 Твоё зелье переварено на 100%! Отправь команду /promote, чтобы провести ритуал повышения Последовательности.")
    await cb.answer()


@router.callback_query(F.data == "st:resume")
async def cb_story_resume(cb: CallbackQuery):
    c = await db.get_character(cb.from_user.id)
    if not c or not c.get("pathway"):
        await cb.answer("Сначала создай персонажа: /build", show_alert=True)
        return
    session = await db.get_story_session(cb.from_user.id)
    await _render_or_start_story(cb.message, c, session, edit=True)
    await cb.answer()


async def _render_or_start_story(msg: Message, c: dict, session: dict, edit: bool = False):
    if session.get("enemy") and int(session["enemy"].get("hp", 0)) > 0:
        text = _combat_screen_text(c, session, [])
        kb = _combat_kb(c, session)
        if edit:
            await ui.safe_edit(msg, text, kb)
        else:
            await msg.answer(text, reply_markup=kb)
        return

    if not session.get("options"):
        if int(session.get("turns_left") or 0) <= 0:
            import h_shop
            await msg.answer(h_shop.shop_text(session), reply_markup=h_shop.shop_kb(session.get("lang", "ru")))
            return
        wait = await (msg.edit_text("🌫 <i>Серый туман клубится, формируя сцену...</i>") if edit else msg.answer("🌫 <i>Серый туман клубится, формируя сцену...</i>"))
        prompt = "Open an intriguing investigative scene continuing my current arc and tailored to my Pathway's Acting Method."
        data = await _call_ai_gm(c, session, prompt, "Scene Start")
        c, session, _ = await _apply_ai_memory_and_state(c["user_id"], c, session, prompt, data, True)
        footer = "\n\n💬 <i>Выбери кнопку ИЛИ просто напиши любое своё действие прямо в чат!</i>"
        text = f"{_header_bar(c, session)}\n{esc(data.get('narrative', ''))}{footer}"
        await ui.safe_edit(wait, text, _story_kb(c, session))
        return

    last_narr = ""
    for item in reversed(session.get("history") or []):
        if item.get("role") == "assistant":
            try:
                last_narr = json.loads(item["content"]).get("narrative", "")
                break
            except Exception:
                pass
    if not last_narr:
        last_narr = "Ты стоишь посреди туманной улицы Тингена, прислушиваясь к шёпоту Духовного Мира."
    footer = "\n\n💬 <i>Выбери кнопку ИЛИ просто напиши любое своё действие прямо в чат!</i>"
    text = f"{_header_bar(c, session)}\n{esc(last_narr)}{footer}"
    if edit:
        await ui.safe_edit(msg, text, _story_kb(c, session))
    else:
        await msg.answer(text, reply_markup=_story_kb(c, session))


@router.callback_query(F.data.startswith("st:opt:"))
async def cb_story_option(cb: CallbackQuery):
    uid = cb.from_user.id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    if not c or not c.get("pathway"):
        await cb.answer("Сначала создай персонажа: /build", show_alert=True)
        return

    if int(session.get("turns_left") or 0) <= 0:
        import h_shop
        await ui.safe_edit(cb.message, h_shop.shop_text(session), h_shop.shop_kb(session.get("lang", "ru")))
        await cb.answer("Ходы Духовности закончились!", show_alert=True)
        return

    opt_id = int(cb.data.split(":")[2])
    options = session.get("options") or []
    picked = next((o for o in options if int(o.get("id", 0)) == opt_id), options[0] if options else None)
    if not picked:
        session = await db.update_story_session(uid, options=[])
        await _render_or_start_story(cb.message, c, session, edit=True)
        await cb.answer()
        return

    await _execute_narrative_turn(cb.message, c, session, picked, mode="normal", edit=True)
    await cb.answer()


@router.callback_query(F.data == "st:reroll")
async def cb_story_reroll(cb: CallbackQuery):
    uid = cb.from_user.id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    last_fail = session.get("last_fail") or {}
    if not c or not last_fail:
        await cb.answer("Нет проваленной проверки для переброса!", show_alert=True)
        return
    if int(session.get("fate_tokens") or 0) <= 0:
        import h_shop
        await ui.safe_edit(cb.message, h_shop.shop_text(session), h_shop.shop_kb(session.get("lang", "ru")))
        await cb.answer("Не хватает Печатей Судьбы!", show_alert=True)
        return

    await db.add_fate_tokens(uid, -1)
    await db.update_character(uid, sanity=min(100, int(c.get("sanity", 100)) + 15))
    c = await db.get_character(uid)
    session = await db.update_story_session(uid, last_fail={})
    await cb.answer("🎲 Нити Судьбы натянуты! Переброс с Преимуществом (2d20)...")
    await _execute_narrative_turn(cb.message, c, session, last_fail, mode="adv", edit=True)


async def _execute_narrative_turn(msg: Message, c: dict, session: dict, picked: dict, mode: str = "normal", edit: bool = True):
    uid = c["user_id"]
    sk_key = picked.get("skill", "perception")
    if sk_key not in rules.SKILLS:
        sk_key = "perception"
    dc = int(picked.get("dc", 12))
    roll_res = rules.skill_check(c, ("skill", sk_key), mode=mode)
    success = roll_res.natural == 20 or (roll_res.natural != 1 and roll_res.total >= dc)

    if picked.get("combat"):
        enemy = _spawn_enemy_for(c, session.get("lang", "ru"))
        if success:
            ambush = _rng.randint(3, 7)
            enemy["hp"] = max(1, enemy["hp"] - ambush)
            intro = [
                f"🗣 <i>«{esc(picked.get('text', ''))}»</i>",
                f"🎲 Проверка <b>{esc(rules.SKILLS[sk_key][0])}</b> (Сл {dc}): выпало <b>{roll_res.total}</b> (d20={roll_res.natural}) — ✅ <b>УСПЕХ!</b>",
                f"⚡ Ты перехватываешь инициативу и наносишь <b>{ambush}</b> упреждающего урона!",
            ]
        else:
            intro = [
                f"🗣 <i>«{esc(picked.get('text', ''))}»</i>",
                f"🎲 Проверка <b>{esc(rules.SKILLS[sk_key][0])}</b> (Сл {dc}): выпало <b>{roll_res.total}</b> (d20={roll_res.natural}) — ❌ <b>ПРОВАЛ!</b>",
                "⚠️ Противник готов к твоему выпаду — начинается бой!",
            ]
        session = await db.update_story_session(uid, enemy=enemy, last_fail={})
        if edit:
            await ui.safe_edit(msg, _combat_screen_text(c, session, intro), _combat_kb(c, session))
        else:
            await msg.answer(_combat_screen_text(c, session, intro), reply_markup=_combat_kb(c, session))
        return

    crit_tag = " 🌟 НАТУРАЛЬНАЯ 20!" if roll_res.natural == 20 else (" 💀 НАТУРАЛЬНАЯ 1!" if roll_res.natural == 1 else "")
    wait_text = (
        f"{_header_bar(c, session)}\n"
        f"🗣 <b>Твой ход:</b> <i>«{esc(picked.get('text', ''))}»</i>\n"
        f"🎲 <b>{esc(rules.SKILLS[sk_key][0])} (Сл {dc}):</b> выпало <b>{roll_res.total}</b> (d20={roll_res.natural}){crit_tag} — "
        f"{'✅ УСПЕХ!' if success else '❌ ПРОВАЛ!'}\n"
        f"<i>🌫 ИИ-Мастер вплетает твой поступок в реальность...</i>"
    )
    wait_msg = await (msg.edit_text(wait_text) if edit else msg.answer(wait_text))

    roll_info = (
        f"Skill '{sk_key}' ({rules.SKILLS[sk_key][0]}): rolled {roll_res.total} (natural d20={roll_res.natural}) vs DC {dc} "
        f"-> {'CRITICAL SUCCESS (Nat 20)!' if roll_res.natural == 20 else ('CRITICAL FAILURE (Nat 1)!' if roll_res.natural == 1 else ('SUCCESS' if success else 'FAILURE'))}."
    )
    action_prompt = f"Player exact action: \"{picked.get('text', '')}\""
    data = await _call_ai_gm(c, session, action_prompt, roll_info)
    c, session, dig_gain = await _apply_ai_memory_and_state(uid, c, session, f"{action_prompt} [{roll_info}]", data, success)

    if not success:
        session = await db.update_story_session(uid, last_fail=picked)
    else:
        session = await db.update_story_session(uid, last_fail={})

    if data.get("spawn_enemy"):
        enemy = _spawn_enemy_for(c, session.get("lang", "ru"), str(data.get("enemy_name") or ""))
        session = await db.update_story_session(uid, enemy=enemy)
        log_lines = [esc(data.get("narrative", "")), "", "⚔️ <b>Сцена переходит в пошаговую схватку!</b>"]
        await ui.safe_edit(wait_msg, _combat_screen_text(c, session, log_lines), _combat_kb(c, session))
        return

    act_fb = data.get("acting_feedback") or f"Метод отыгрыша: +{dig_gain}% к перевариванию зелья."
    clue_note = f"\n🔍 <b>Новая улика:</b> <i>{esc(data['new_clue'])}</i>" if data.get("new_clue") else ""
    reroll_hint = "\n💡 <i>Не нравится провал кубика? Нажми «🎲 Перебросить Судьбу» ниже!</i>" if not success and int(session.get("fate_tokens") or 0) > 0 else ""
    footer = "\n\n💬 <i>Жми кнопку ИЛИ пиши любую свою фразу/действие прямо в чат!</i>"
    roll_banner = (
        f"🗣 <i>«{esc(picked.get('text', ''))}»</i>\n"
        f"🎲 <b>{esc(rules.SKILLS[sk_key][0])} (Сл {dc}):</b> {roll_res.total} (d20={roll_res.natural}){crit_tag} — "
        f"{'✅ Успех' if success else '❌ Провал'}\n"
        f"🧪 <i>{esc(act_fb)}</i>{clue_note}{reroll_hint}\n\n"
    )
    await ui.safe_edit(
        wait_msg,
        f"{_header_bar(c, session)}\n{roll_banner}{esc(data.get('narrative', ''))}{footer}",
        _story_kb(c, session, allow_reroll=not success),
    )


@router.message(Command("act"))
async def cmd_custom_act(message: Message):
    raw = (message.text or "").partition(" ")[2].strip()
    if not raw:
        await message.answer("Ты можешь даже не писать /act — просто отправь любое своё действие обычным сообщением в чат!")
        return
    await _handle_free_text_action(message, raw)


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_free_text_story(message: Message):
    """Любое текстовое сообщение игрока в личке вне конструктора /build автоматически становится его ходом в игре!"""
    c = await db.get_character(message.from_user.id)
    if not c or not c.get("pathway"):
        kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🔮 Создать Потустороннего (/build)", "st:go_build")]])
        await message.answer("Чтобы начать приключение и писать свои действия ИИ-Мастеру, сначала создай персонажа: /build", reply_markup=kb)
        return
    await _handle_free_text_action(message, message.text.strip())


async def _handle_free_text_action(message: Message, text: str):
    uid = message.from_user.id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)

    # Если игрок сейчас в бою 1-на-1 — обрабатываем его текст как нестандартный боевой манёвр!
    if session.get("enemy") and int(session["enemy"].get("hp", 0)) > 0:
        await _handle_combat_free_text(message, c, session, text)
        return

    if int(session.get("turns_left") or 0) <= 0:
        import h_shop
        await message.answer(h_shop.shop_text(session), reply_markup=h_shop.shop_kb(session.get("lang", "ru")))
        return

    sk_key, dc, is_combat_init = _classify_free_text(text, c)
    picked = {"id": 99, "text": text[:200], "skill": sk_key, "dc": dc, "combat": is_combat_init}
    await _execute_narrative_turn(message, c, session, picked, mode="normal", edit=False)


async def _handle_combat_free_text(message: Message, c: dict, session: dict, text: str):
    """Позволяет писать любую дичь и трюки прямо во время пошагового боя 1-на-1."""
    uid = c["user_id"]
    enemy = dict(session.get("enemy") or {})
    sk_key, dc, _ = _classify_free_text(text, c)
    roll_res = rules.skill_check(c, ("skill", sk_key))
    success = roll_res.natural == 20 or (roll_res.natural != 1 and roll_res.total >= dc)

    _, stat_mod = rules.best_path_stat(c)
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    cantrip = next((s for s in skills if s["seq"] == 9 and s["key"] == "core"), None)
    c_dice = cantrip["dice"] if cantrip and cantrip.get("dice") else "1d8"

    log_lines = [f"🗣 <b>Твой манёвр:</b> <i>«{esc(text[:150])}»</i>"]
    new_hp, new_temp = int(c["hp"]), int(c.get("temp_hp") or 0)
    new_sanity = int(c.get("sanity", 100))

    if success:
        base_roll = rules.roll_expression(c_dice)
        stunt_bonus = _rng.randint(2, 6) + (base_roll.total if roll_res.natural == 20 else 0)
        dmg = max(2, base_roll.total + stat_mod + stunt_bonus)
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        log_lines.append(
            f"🎲 <b>{esc(rules.SKILLS[sk_key][0])} (Сл {dc}):</b> {roll_res.total} (d20={roll_res.natural}) — ✅ <b>Трюк удался!</b>\n"
            f"💥 Твой нестандартный ход застаёт врага врасплох: <b>−{dmg} ОЗ</b> и помеха врагу!"
        )
        enemy_dis = True
    else:
        grazed = max(1, (rules.roll_expression(c_dice).total + stat_mod) // 2)
        enemy["hp"] = max(0, int(enemy["hp"]) - grazed)
        log_lines.append(
            f"🎲 <b>{esc(rules.SKILLS[sk_key][0])} (Сл {dc}):</b> {roll_res.total} (d20={roll_res.natural}) — ❌ <b>Осечка!</b>\n"
            f"🗡 Трюк сорвался, удар прошёл вскользь: всего <b>−{grazed} ОЗ</b> врагу."
        )
        enemy_dis = False

    if int(enemy["hp"]) <= 0:
        dig_reward = _scaled_digestion_gain(int(c.get("sequence", 9)), 18 if success else 15)
        new_dig = min(100, int(c.get("digestion") or 0) + dig_reward)
        await db.update_character(uid, hp=new_hp, temp_hp=new_temp, sanity=new_sanity, digestion=new_dig, lair_uses=h_perks.reset_combat_uses(c))
        session = await db.update_story_session(uid, enemy={})
        c = await db.get_character(uid)
        win_text = (
            f"{_header_bar(c, session)}\n"
            + "\n".join(log_lines)
            + f"\n\n🏆 <b>Красивая победа!</b> {esc(enemy['name'])} повержен твоим приёмом.\n"
            f"🧪 Метод отыгрыша: <b>+{dig_reward}% к перевариванию зелья</b> (итого: <b>{new_dig}%</b>)."
        )
        await message.answer(win_text, reply_markup=_story_kb(c, session))
        return

    e_d20 = min(_rng.randint(1, 20), _rng.randint(1, 20)) if enemy_dis else _rng.randint(1, 20)
    e_atk = e_d20 + int(enemy.get("atk_bonus", 3))
    eff_ac = int(c.get("ac", 10)) + h_perks.ac_bonus(c)
    if e_d20 == 20 or e_atk >= eff_ac:
        e_roll = rules.roll_expression(enemy.get("dmg_dice", "1d8"))
        e_dmg = max(1, e_roll.total + int(enemy.get("dmg_mod", 1)))
        new_hp, new_temp = rules.apply_damage(new_hp, new_temp, e_dmg)
        log_lines.append(f"👹 <b>{esc(enemy['name'])}</b> бьёт в ответ ({e_atk} vs КД {eff_ac}) → <b>−{e_dmg} ОЗ</b> тебе!")
    else:
        log_lines.append(f"🛡 <b>{esc(enemy['name'])}</b> дезориентирован твоим трюком и мажет ({e_atk} vs КД {eff_ac})!")

    if new_hp <= 0:
        new_hp = max(1, int(c["max_hp"]) // 2)
        new_sanity = max(10, new_sanity - 15)
        await db.update_character(uid, hp=new_hp, temp_hp=0, sanity=new_sanity, lair_uses=h_perks.reset_combat_uses(c))
        session = await db.update_story_session(uid, enemy={})
        c = await db.get_character(uid)
        await message.answer(
            f"{_header_bar(c, session)}\n" + "\n".join(log_lines) + "\n\n🕯 <b>Серый Туман спас тебя на грани гибели!</b>",
            reply_markup=_story_kb(c, session),
        )
        return

    enemy["round"] = int(enemy.get("round", 1)) + 1
    await db.update_character(uid, hp=new_hp, temp_hp=new_temp, sanity=new_sanity)
    session = await db.update_story_session(uid, enemy=enemy)
    c = await db.get_character(uid)
    await message.answer(_combat_screen_text(c, session, log_lines), reply_markup=_combat_kb(c, session))


@router.callback_query(F.data == "st:hunt")
async def cb_story_hunt(cb: CallbackQuery):
    uid = cb.from_user.id
    c = await db.get_character(uid)
    if not c or not c.get("pathway"):
        await cb.answer("Сначала создай персонажа: /build", show_alert=True)
        return
    session = await db.get_story_session(uid)
    if int(session.get("turns_left") or 0) <= 0:
        import h_shop
        await ui.safe_edit(cb.message, h_shop.shop_text(session), h_shop.shop_kb(session.get("lang", "ru")))
        await cb.answer("Недостаточно ходов Духовности для выслеживания цели!", show_alert=True)
        return
    enemy = _spawn_enemy_for(c, session.get("lang", "ru"))
    session = await db.update_story_session(uid, enemy=enemy)
    await ui.safe_edit(cb.message, _combat_screen_text(c, session, []), _combat_kb(c, session))
    await cb.answer("Охота началась! (-1⚡)")


@router.callback_query(F.data == "st:rest")
async def cb_story_rest(cb: CallbackQuery):
    uid = cb.from_user.id
    c = await db.get_character(uid)
    if not c:
        await cb.answer()
        return
    session = await db.get_story_session(uid)
    if int(c.get("hp", 0)) >= int(c.get("max_hp", 1)) and int(c.get("sanity", 100)) >= 95:
        await cb.answer("Твои ОЗ и Рассудок уже в полном порядке!", show_alert=True)
        return
    if int(session.get("turns_left") or 0) <= 0:
        import h_shop
        await ui.safe_edit(cb.message, h_shop.shop_text(session), h_shop.shop_kb(session.get("lang", "ru")))
        await cb.answer("Нужен 1 ход Духовности для проведения ритуала Когитации!", show_alert=True)
        return
    restored = rules.long_rest(c)
    uses = h_perks.reset_combat_uses(c)
    await db.update_character(uid, sanity=100, lair_uses=uses, **restored)
    c = await db.get_character(uid)
    await cb.answer("🧘 Когитация (бесплатно): ОЗ, Рассудок и Ячейки духовности полностью восстановлены!", show_alert=True)
    await _render_or_start_story(cb.message, c, session, edit=True)


@router.callback_query(F.data == "st:flee")
async def cb_story_flee(cb: CallbackQuery):
    session = await db.update_story_session(cb.from_user.id, enemy={})
    c = await db.get_character(cb.from_user.id)
    await cb.answer("Ты скрылся в тумане!")
    await _render_or_start_story(cb.message, c, session, edit=True)


@router.callback_query(F.data.startswith("st:atk:"))
async def cb_combat_attack(cb: CallbackQuery):
    uid = cb.from_user.id
    c = await db.get_character(uid)
    session = await db.get_story_session(uid)
    enemy = dict(session.get("enemy") or {})
    if not c or not enemy or int(enemy.get("hp", 0)) <= 0:
        await cb.answer("Бой уже завершён.")
        await _render_or_start_story(cb.message, c, session, edit=True)
        return

    parts = cb.data.split(":")
    kind = parts[2]
    log_lines: list[str] = []
    _, stat_mod = rules.best_path_stat(c)
    prof = rules.effective_prof_bonus(c)
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    uses = dict(c.get("lair_uses") or {})
    new_hp, new_temp = int(c["hp"]), int(c.get("temp_hp") or 0)
    new_sanity = int(c.get("sanity", 100))

    if kind == "cantrip":
        cantrip = next((s for s in skills if s["seq"] == 9 and s["key"] == "core"), None)
        c_name = cantrip["name"] if cantrip else "Удар Потустороннего"
        c_dice = cantrip["dice"] if cantrip and cantrip.get("dice") else "1d8"
        d20 = _rng.randint(1, 20)
        atk_tot = d20 + prof + stat_mod
        if d20 == 20 or atk_tot >= int(enemy["ac"]):
            roll = rules.roll_expression(c_dice)
            dmg = max(1, roll.total + stat_mod + (roll.total if d20 == 20 else 0))
            enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
            crit_str = " (КРИТ!)" if d20 == 20 else ""
            log_lines.append(f"🗡 <b>{esc(c_name)}</b>: попадание {atk_tot} vs КД {enemy['ac']}{crit_str} → <b>−{dmg} ОЗ</b> врагу!")
        else:
            log_lines.append(f"🗡 <b>{esc(c_name)}</b>: бросок {atk_tot} (d20={d20}) vs КД {enemy['ac']} — промах!")

    elif kind == "mech":
        p_mech = lotm.PATHWAY_MECHANICS.get(c["pathway"])
        left = int(uses.get("path_res", p_mech.get("max_base", 3) if p_mech else 0))
        if not p_mech or left <= 0:
            await cb.answer("Заряды механики Пути закончились!", show_alert=True)
            return
        uses["path_res"] = left - 1
        tier_mult = max(1, lotm.OPEN_TIERS.get(int(c["sequence"]), 1) // 2 + 1)
        die_side = p_mech["dice"].split("d")[1]
        roll = rules.roll_expression(f"{tier_mult}d{die_side}")
        dmg = max(2, roll.total + stat_mod)
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        new_temp = max(new_temp, tier_mult * 3)
        log_lines.append(f"⚡ <b>{esc(p_mech['name'])}</b>: наносит <b>{dmg}</b> урона врагу и укрепляет духовный щит!")

    elif kind == "myth":
        if uses.get("mythical_used") or int(c.get("sequence", 9)) > 4:
            await cb.answer("Недоступно!", show_alert=True)
            return
        cost = 10 if "mythical_anchor" in (c.get("feats") or []) else 20
        if new_sanity < cost:
            await cb.answer("Недостаточно Рассудка!", show_alert=True)
            return
        uses["mythical_used"] = 1
        new_sanity -= cost
        seq = int(c["sequence"])
        roll = rules.roll_expression(f"{10 - seq}d10")
        dmg = roll.total + stat_mod
        enemy["hp"] = max(0, int(enemy["hp"]) - dmg)
        new_temp = max(new_temp, (10 - seq) * 10)
        log_lines.append(f"👁 <b>Форма Мифического Существа!</b> Враг получает <b>{dmg}</b> сокрушительного урона, твой щит: +{new_temp} ОЗ!")

    elif kind == "sk" and len(parts) >= 5:
        seq, key = int(parts[3]), parts[4]
        sk = next((s for s in skills if s["seq"] == seq and s["key"] == key), None)
        if not sk:
            await cb.answer("Умение не найдено", show_alert=True)
            return
        cost, rank = int(sk["slot_cost"]), int(sk["slot_rank"])
        if cost > 0:
            slots = {str(k): dict(v) for k, v in (c.get("spell_slots") or {}).items()}
            bucket = slots.get(str(rank)) or {}
            if int(bucket.get("left", 0)) < cost:
                await cb.answer(f"Не хватает ячеек Р{rank}! Используй бесплатный Заговор, напиши трюк в чат или отдохни.", show_alert=True)
                return
            bucket["left"] = int(bucket["left"]) - cost
            slots[str(rank)] = bucket
            await db.update_character(uid, spell_slots=slots)

        val = rules.roll_expression(sk["dice"]).total + stat_mod if sk.get("dice") else (stat_mod + prof * 2)
        low_desc = (sk.get("desc") or "").lower()
        if any(w in low_desc for w in ("лечит", "исцел", "восстанавлив")) and "урон" not in low_desc:
            new_hp = rules.apply_heal(new_hp, int(c["max_hp"]), val)
            log_lines.append(f"✨ <b>{esc(sk['name'])}</b>: восстанавливает тебе <b>+{val} ОЗ</b>!")
        else:
            dc = 8 + prof + stat_mod
            e_save = _rng.randint(1, 20) + int(enemy.get("save_bonus", 2))
            if sk.get("save") and e_save >= dc:
                dmg = max(1, val // 2)
                log_lines.append(f"✨ <b>{esc(sk['name'])}</b>: враг выбросил спасбросок {e_save} (Сл {dc}) → <b>−{dmg} ОЗ</b> (50%)!")
            else:
                dmg = max(1, val)
                log_lines.append(f"✨ <b>{esc(sk['name'])}</b>: прямое поражение цели → <b>−{dmg} ОЗ</b> врагу!")
            enemy["hp"] = max(0, int(enemy["hp"]) - dmg)

    if int(enemy["hp"]) <= 0:
        dig_reward = _scaled_digestion_gain(int(c.get("sequence", 9)), 15)
        new_dig = min(100, int(c.get("digestion") or 0) + dig_reward)
        fresh_uses = h_perks.reset_combat_uses(c)
        await db.update_character(uid, hp=new_hp, temp_hp=new_temp, sanity=new_sanity, digestion=new_dig, lair_uses=fresh_uses)
        old_sum = (session.get("summary") or "").strip()
        new_sum = f"{old_sum} Победил в бою: {enemy['name']}.".strip()[-1200:]
        session = await db.update_story_session(uid, enemy={}, summary=new_sum)
        c = await db.get_character(uid)
        promo_note = "\n🎉 <b>Зелье переварено на 100%! Нажми /promote для перехода на новую Последовательность!</b>" if new_dig >= 100 else ""
        win_text = (
            f"{_header_bar(c, session)}\n"
            + "\n".join(log_lines)
            + f"\n\n🏆 <b>Победа!</b> {esc(enemy['name'])} повержен, а его потусторонняя черта отделяется в тумане.\n"
            f"🧪 Метод отыгрыша в бою: <b>+{dig_reward}% к перевариванию зелья</b> (итого: <b>{new_dig}%</b>).{promo_note}"
        )
        await ui.safe_edit(cb.message, win_text, _story_kb(c, session))
        await cb.answer("Враг повержен! 🏆")
        return

    e_d20 = _rng.randint(1, 20)
    e_atk = e_d20 + int(enemy.get("atk_bonus", 3))
    eff_ac = int(c.get("ac", 10)) + h_perks.ac_bonus(c)
    if e_d20 == 20 or e_atk >= eff_ac:
        e_roll = rules.roll_expression(enemy.get("dmg_dice", "1d8"))
        e_dmg = max(1, e_roll.total + int(enemy.get("dmg_mod", 1)))
        new_hp, new_temp = rules.apply_damage(new_hp, new_temp, e_dmg)
        log_lines.append(f"👹 <b>{esc(enemy['name'])}</b> контратакует ({e_atk} vs КД {eff_ac}) и наносит тебе <b>{e_dmg} урона</b>!")
    else:
        log_lines.append(f"🛡 <b>{esc(enemy['name'])}</b> атакует ({e_atk} vs КД {eff_ac}), но ты уходишь от удара!")

    if new_hp <= 0:
        new_hp = max(1, int(c["max_hp"]) // 2)
        new_sanity = max(10, new_sanity - 15)
        await db.update_character(uid, hp=new_hp, temp_hp=0, sanity=new_sanity, lair_uses=h_perks.reset_combat_uses(c))
        session = await db.update_story_session(uid, enemy={})
        c = await db.get_character(uid)
        lose_text = (
            f"{_header_bar(c, session)}\n"
            + "\n".join(log_lines)
            + "\n\n🕯 <b>Серый Туман спас тебя на грани гибели!</b> Ты отступаешь в укрытие, потеряв 15% Рассудка, но сохранив жизнь."
        )
        await ui.safe_edit(cb.message, lose_text, _story_kb(c, session))
        await cb.answer("Ты едва выжил...")
        return

    enemy["round"] = int(enemy.get("round", 1)) + 1
    await db.update_character(uid, hp=new_hp, temp_hp=new_temp, sanity=new_sanity, lair_uses=uses)
    session = await db.update_story_session(uid, enemy=enemy)
    c = await db.get_character(uid)
    await ui.safe_edit(cb.message, _combat_screen_text(c, session, log_lines), _combat_kb(c, session))
    await cb.answer()
