"""h_story.py — ИИ-Мастер со свободным вводом любых действий (в сюжете и в бою), Трёхслойной Памятью и Перебросом Судьбы."""
from __future__ import annotations

import json
import logging
import random
import re

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import combat_engine as combat
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

STATIC_SYSTEM_PROMPT = """You are the AI Game Master for an open-world tabletop RPG set in the 'Lord of the Mysteries' (LotM) universe (Fifth Epoch, Autumn 1349). Always respond with a valid JSON object.
The player is an Anomaly Outside Fate. You run the world with tabletop rigor, cosmic horror, and atmospheric depth.

CORE GM RULES:
1. PLAYER AGENCY, CHECKLIST RESOLUTION & STRICT TARGET FIDELITY:
   - SHORT TACTICAL BURSTS (1-3 actions, e.g., 'slip into crowd -> snatch clerk\'s dark cloak and hat -> disguise -> turn into alley'):
     The dice roll arbitrates the CHIEF RISK of the sequence. On SUCCESS (and especially when beating DC by 5+), the protagonist GUARANTEED executes ALL declared steps in chronological order and CHANGES THEIR POSITION IN SPACE.
     STRICTLY FORBIDDEN to paralyze the protagonist at the starting spot with phrases like "не сдвинулся с места", "не стал двигаться", "остался стоять", "прижался к стене/колонне", "замер у колонны"!
     NEVER force or trap the character into an unwanted conversation with nearby NPCs if the player declared evasion, stealth, flight, or mingling into a crowd!
   - STRICT TARGET FIDELITY (NEVER SWAP TARGETS OR OBJECTS):
     You MUST NEVER substitute, change, or invent different targets or objects than those specified by the player.
     If the player declares interacting with a specific target/item (e.g., 'take dark cloak and hat from a clerk in the crowd and duck into an alley'), upon success you MUST narrate interaction with THAT EXACT CLERK, CLOAK, HAT AND ALLEY. NEVER default to picking the pocket of a previous conversation partner or inventing a copper token in someone else's coat!
   - SOFT CAP FOR MULTI-STAGE PLANS (4+ actions across different rooms/zones):
     Execute the early 2-3 actions sequentially, then transition to Phase 3 at the new threshold or obstacle. Never stop the sequence at step zero.
   - RESOLUTION ON FAILURE (or Nat 1): Protagonist attempts the maneuver, but stumbles, is spotted, or blocked at the chief risk choke point. Phase 1 describes only the attempt and the friction point.
   - NO SKILL NAME TUNNEL VISION: The skill key arbitrates the risk mechanism, but does NOT override or narrow the player's full physical maneuver.

2. DELTA PRINCIPLE & END-OF-TURN STATE CONTINUITY (ZERO CONTEXT ECHO):
   - MANDATORY STATE CONTINUITY: The new turn MUST continue directly from the FINAL WORLD STATE established in the last 2 sentences of the previous Master response (Phase 3). If time resumed, crowds unfroze, clocks ticked, or a door clicked open in the previous turn's conclusion, you MUST NOT revert to the initial frozen scene or previous static state!
   - ZERO REPEATS: Describe ONLY new developments, physical consequences, and shifts in the scene. STRICTLY FORBIDDEN to echo static descriptions from earlier turns (fog, lamps, coats, rain, room decor, or already found clues) unless physically altered. Every turn MUST propel the narrative forward in time and physical space!

3. CANONICAL CONSISTENCY & FACT INTEGRITY:
   Never negate, overwrite, or contradict facts, motives, and conclusions already established in previous turns (including passive skill insights). New revelations may deepen the mystery, but must NEVER invalidate established canon.

4. RELEVANCE & STRICT DOMAINS OF PASSIVE VOICES ([Навык — Пассивно]):
   Include a standalone paragraph with an inner-voice insight in brackets from Top Passive Skills (e.g., "[Проницательность и Психоанализ — Пассивно]: ...") in Phase 2 ONLY when there is a genuine hidden clue, tactical detail, psychological subtext, or lore hook relevant to that skill's domain.
   DO NOT generate empty, forced, or trivial passive voice insights during routine actions merely to satisfy a template. If no genuine clue or subtext exists, omit the passive bracket tag.
   STRICT DOMAIN BOUNDARIES (NO HALLUCINATIONS / NO DOMAIN BLENDING):
   - Lore & Occult (История Эпох, Оккультизм и Зелья, Ритуалистика и Астрология, Духовное зрение): ONLY ancient lore, mysticism, tarot, deities, ritual laws, auras, secret ciphers.
   - Deduction & Perception (Криминалистика и Анализ, Восприятие, Проницательность и Психоанализ): ONLY physical evidence, timeline, contradictions, logic, body language, micro-expressions, sounds/smells.
   - Physical & Motor Skills (Ловкость рук, Скрытность, Атлетика, Акробатика, Стрельба, Силовой напор): ONLY muscle memory, finger dexterity, balance, stealth in shadows, footstep sounds, theft timing, distances.
     CRITICAL FOR SLEIGHT OF HAND ([Ловкость рук — Пассивно]):
     Tactile perception ONLY on physical touch/grip (weight, center of mass, roughness, texture of cloth, seams, hidden pockets, balance of a blade). STRICTLY FORBIDDEN to deduce occult lore, read ciphers, analyze heraldry, or connect clues to murder mysteries through Sleight of Hand (that is strictly Investigation or History)!
   - Medicine & Science (Медицина и Токсикология, Паровая Инженерия): wounds, poisons, anatomy, mechanical gears, alchemy.
   - Social (Обман, Убеждение, Запугивание, Светский Этикет): social leverage, bluff, voice tone, aristocratic etiquette.

5. THREE-PHASE NARRATIVE STRUCTURE (HARD REQUIREMENT & OUTCOME BRANCHING):
   The `narrative` text (strict 2-3 vivid, concise paragraphs, 450-800 chars) MUST follow this 3-phase progression:
   - Phase 1 (Action Execution vs Attempt / Interruption):
     * On SUCCESS (Short Chain): Detailed, sequential physical execution of ALL stages in the player's declared action checklist in logical order.
     * On LONG CHAIN (4+ actions / new rooms): Sequential execution of early actions UP TO the first friction or resistance point where the world responds.
     * On FAILURE: Only the ATTEMPT and the specific moment of breakdown or interruption at the risk point. NEVER describe a successful completion of an action that was negated by a failed roll.
   - Phase 2 (Perception & Insight): What the protagonist notices, deduces, or retrieves during/after the action. If a real clue, lore nuance, or tactical subtext exists matching a passive skill domain, insert `[Навык — Пассивно]: ...` as a standalone paragraph (\\n\\n). If no relevant subtext exists, provide natural observational detail without the bracketed tag.
   - Phase 3 (Scene Shift & World Response): The resulting new disposition, world reaction, interruption point, consequences, or emerging threat requiring the player's next choice.

6. WEALTH & SANDBOX: If player trades, loots, or invests, update gold/assets in `new_clue`.
7. STRICT ACTING METHOD: Award `acting_gain` (1-3) ONLY when action directly embodies Sequence Acting Principle AND D20 succeeded! Otherwise 0.
8. ЗАПРЕТ НА МЕТА-СПОЙЛЕРЫ: Никогда не пиши в `current_arc`, `clues` и `options` истинные имена NPC или тайных культов, пока персонаж не узнал их прямо в тексте сцены. Используй внешние маркеры ("человек в котелке", "незнакомец с моноклем").
9. DC SCALE (5-25): Easy 8-10, Medium 12-14, Risky 16-20. Every turn options array MUST offer 3 actions matching this spread.
10. ПРОСТРАНСТВЕННАЯ И ФИЗИЧЕСКАЯ ЛОГИКА: Сверяйся с реальной позицией героя и преградами (запертые двери, окна, дистанция) перед предложением options.
11. STRICT INVENTORY DELTA MANAGEMENT:
    - Any item physically obtained, picked up, stolen, bought, crafted, or retrieved in Phase 1-3 MUST be explicitly recorded in `inventory_add`:
      [{"name": "Exact item name", "qty": 1, "category": "gear|clue|consumable|currency", "description": "1 brief sentence description and properties"}].
    - Any item spent, consumed, lost, broken, surrendered, sold, or destroyed MUST be recorded in `inventory_remove`:
      [{"name": "Exact item name", "qty": 1}].
    - NEVER add items that the protagonist only saw from afar or heard about, but did not physically take.
    - If no items were gained or lost this turn, return empty arrays `[]` for both fields.

ARTIFACT REWARD: Use `reward_artifact` schema field ONLY upon major quest milestones/vault loot, else empty string.

OUTPUT FORMAT:
Respond strictly with a valid JSON object matching the following schema:
{
  "gm_thought": "1 brief sentence (max 25 words)",
  "location": "Specific place name in target language",
  "current_arc": "Current main goal without meta-spoilers (1 sentence)",
  "summary_append": "1 concise sentence summarizing what just happened",
  "new_clue": "New clue or Gold Pounds update, or empty string",
  "new_npc": "Name or visual descriptor & role of NPC, or empty string",
  "consequence_add": {
    "target": "Affected NPC/Faction or empty string",
    "event": "What player did that left a trace",
    "threat": 1,
    "trigger": "Condition when this triggers"
  },
  "narrative": "2-3 vivid, concise paragraphs (450-800 chars) following the 3-Phase structure (include [Skill — Пассивно] insight only when relevant clue/lore/tactical nuance exists).",
  "acting_gain": 0,
  "acting_feedback": "Why action advanced/did not advance digestion",
  "hp_delta": 0,
  "sanity_delta": 0,
  "spawn_enemy": false,
  "enemy_name": "",
  "inventory_add": [
    {"name": "Item name", "qty": 1, "category": "gear|clue|consumable|currency", "description": "1 brief sentence description"}
  ],
  "inventory_remove": [
    {"name": "Item name", "qty": 1}
  ],
  "reward_artifact": {
    "name": "Unique LotM artifact name (or empty string)",
    "icon": "watch|ring|mask|dagger|monocle|mirror|vial|tome|lantern|cane|revolver|tarot",
    "lore": "1 sentence eerie appearance"
  },
  "options": [
    {"id": 1, "text": "Action 1 (<=40 chars, no meta-spoilers)", "skill": "investigation", "dc": 10},
    {"id": 2, "text": "Action 2 (<=40 chars, no meta-spoilers)", "skill": "persuasion", "dc": 13},
    {"id": 3, "text": "Risky action (<=40 chars)", "skill": "arcana", "dc": 16, "combat": true}
  ]
}
Allowed 'skill' keys: athletics, brawling, acrobatics, sleight_of_hand, stealth, firearms, arcana, history, investigation, nature, religion, animal_handling, insight, medicine, perception, survival, deception, intimidation, performance, persuasion."""


def _last_narrative_from_session(session: dict) -> str:
    """Извлекает нарратив последнего ответа Мастера из сессии для контекста опасности цели."""
    for item in reversed((session or {}).get("history") or []):
        if item.get("role") == "assistant":
            try:
                last_d = json.loads(item.get("content") or "{}")
                narr = (last_d.get("narrative") or "").strip()
                if narr:
                    return narr
            except Exception:
                pass
            raw = str(item.get("content") or "").strip()
            if raw:
                return raw[:500]
    return ""


def _generate_action_summary(text: str, sk_key: str, intent: str = "act") -> str:
    """Мгновенно формирует лаконичный игровой ярлык действия (2-5 слов, до 45 символов)
    в стиле BG3 / Disco Elysium без вызова LLM."""
    clean = re.sub(r"^\[[^\]]+\]\s*", "", text or "").strip()
    low = clean.lower()

    # 1. Специфические игровые ярлыки по распознанным ключевым словам и намерениям
    # Карманная кража, подмена, замки
    if any(w in low for w in ("карман", "кошел", "среза", "спереть", "стянуть")):
        return "Карманная кража"
    if any(w in low for w in ("подмен", "фокус", "рукав")):
        return "Ловкая подмена"
    if any(w in low for w in ("отмычк", "взлом", "вскры", "сейф")):
        return "Взлом замка"
    if any(w in low for w in ("переоде", "маскир", "образ", "ожед", "одежд")):
        return "Маскировка и смена образа"

    # Скрытность и побег
    if any(w in low for w in ("толп", "столп", "смешат", "раствор")):
        return "Растворение в толпе"
    if any(w in low for w in ("тень", "темнот", "затаит")):
        return "Уход в тень"
    if any(w in low for w in ("подкрас", "крад", "бесшум", "тихо")):
        return "Бесшумное сближение"

    # Акробатика и атлетика
    if any(w in low for w in ("окно", "подоконник")):
        return "Прыжок в окно"
    if any(w in low for w in ("крыш", "карниз", "балансир")):
        return "Манёвр по крышам"
    if any(w in low for w in ("уворач", "уклон", "кувыр")):
        return "Ловкое уклонение"
    if any(w in low for w in ("выбив", "выбит", "ломаю двер", "выломать")):
        return "Силовой прорыв двери"
    if any(w in low for w in ("убеж", "убег", "беж", "сбеж", "мчать", "нестись", "удира")):
        return "Стремительный побег"
    if any(w in low for w in ("карабк", "вскарабк", "перелез")):
        return "Преодоление преграды"
    if any(w in low for w in ("таран", "прорыв", "волоком", "толка")):
        return "Силовой таран"

    # Боевые действия и стрельба
    if any(w in low for w in ("курок", "пистолет", "револьвер", "выстрел", "стрел")):
        return "Прицельный выстрел"
    if any(w in low for w in ("душ", "захват", "горл", "зажат")):
        return "Удушающий захват"
    if any(w in low for w in ("сбить", "подножк")):
        return "Бросок на землю"
    if any(w in low for w in ("нокаут", "апперкот", "кулак", "врез", "удар")):
        return "Оглушающий удар"

    # Социальное взаимодействие и обман
    if any(w in low for w in ("притвор", "прикидыва", "актёр", "актер")):
        return "Притворство и маска"
    if any(w in low for w in ("блеф", "фальш")):
        return "Дерзкий блеф"
    if any(w in low for w in ("обман", "лгу", "врать", "солгат")):
        return "Блеф и отвлечение"
    if any(w in low for w in ("шантаж", "давл", "рычу")):
        return "Шантаж и давление"
    if any(w in low for w in ("допрос", "пыта")):
        return "Жёсткий допрос"
    if any(w in low for w in ("угрож", "пуга")):
        return "Угроза и давление"
    if any(w in low for w in ("торг", "скидк")):
        return "Торг и выгода"
    if any(w in low for w in ("договор", "соглашен", "дипломат", "уговор")):
        return "Дипломатический уговор"

    # Внимание, анализ и мистика
    if any(w in low for w in ("аур", "дух", "призрак", "эфир", "чуть")):
        return "Оккультное видение"
    if any(w in low for w in ("улик", "след", "обыск")):
        return "Поиск улик и следов"
    if any(w in low for w in ("дедукц", "анализ")):
        return "Осмотр и дедукция"
    if any(w in low for w in ("прислуш", "вслуш", "шорох", "звук")):
        return "Чуткий слух"
    if any(w in low for w in ("осмотр", "оглядет", "посмотр", "взглян", "разглядет")):
        return "Осмотр окружения"
    if any(w in low for w in ("таро", "гадан", "маятник")):
        return "Гадание и спиритизм"
    if any(w in low for w in ("ритуал", "зель", "оккульт")):
        return "Тайный ритуал"
    if any(w in low for w in ("леч", "бинт", "перевяз", "пульс", "яд")):
        return "Полевая медицина"
    if any(w in low for w in ("этикет", "светск", "салон", "танц")):
        return "Светский манёвр"

    # 2. Если действие не попало в шаблоны — формируем из первой смысловой фразы заявки
    if clean:
        first_clause = re.split(r"[.;\n!?]", clean)[0].strip()
        first_clause = re.sub(
            r"^(я|хочу|пытаюсь|попытаться|пробую|аккуратно|быстро|тихо|осторожно|медленно)\s+",
            "",
            first_clause,
            flags=re.IGNORECASE,
        ).strip()
        if len(first_clause) >= 5:
            if len(first_clause) > 45:
                clipped = first_clause[:45]
                first_clause = clipped.rsplit(" ", 1)[0] if " " in clipped else clipped
            first_clause = first_clause.rstrip(" ,:;-")
            words = first_clause.split()
            if len(words) > 5:
                first_clause = " ".join(words[:5])
            if len(first_clause) >= 4 and len(words) >= 2:
                return first_clause[0].upper() + first_clause[1:]

    # 3. Дефолтные стильные ярлыки по типу навыка
    skill_labels = {
        "sleight_of_hand": "Кража на ходу",
        "stealth": "Скрытный манёвр",
        "acrobatics": "Рывок и уклонение",
        "athletics": "Силовой манёвр",
        "firearms": "Стрельба навскидку",
        "brawling": "Рукопашная схватка",
        "deception": "Блеф и отвлечение",
        "intimidation": "Угроза и давление",
        "persuasion": "Убеждение и диалог",
        "performance": "Актёрская игра",
        "investigation": "Поиск и анализ улик",
        "perception": "Осмотр и внимание",
        "insight": "Чтение мотивов",
        "arcana": "Оккультный анализ",
        "history": "Знание эпох",
        "religion": "Сакральное знание",
        "nature": "Анализ механизмов",
        "medicine": "Оказание помощи",
        "animal_handling": "Чутьё потустороннего",
    }
    return skill_labels.get(sk_key, "Действие персонажа")


def _classify_free_text(text: str, c: dict, intent: str = "act", context: str = "") -> tuple[str, int, bool, str]:
    """Умный арбитр заявок: определяет проверку главного риска (Chief Risk) при составных действиях,
    адекватную сложность (DC 10..24), триггеры боевого столкновения и краткий ярлык действия."""
    low = text.lower()

    # 1. Специфические кнопки намерений интерфейса
    if intent == "talk":
        sk_key = "persuasion"
        if any(w in low for w in ("обман", "лгу", "блеф", "притвор", "прикидыва", "маск", "фальш")):
            sk_key = "deception"
        elif any(w in low for w in ("угрож", "пуга", "давл", "крич", "рычу", "шантаж", "допрос")):
            sk_key = "intimidation"
        elif any(w in low for w in ("этикет", "светск", "салон", "танц", "шутк", "анекдот", "представлен")):
            sk_key = "performance"
    elif intent == "inspect":
        sk_key = "perception"
        if any(w in low for w in ("аур", "дух", "призрак", "мистик", "потусторонн", "духовн", "гадан", "эфир")):
            sk_key = "animal_handling"
        elif any(w in low for w in ("улик", "тайник", "обыск", "след", "осматрив", "расспраш", "внимател", "поиск", "дедукц", "анализ")):
            sk_key = "investigation"
        elif any(w in low for w in ("механиз", "замок", "бомб", "алхим", "порох")):
            sk_key = "nature"
    elif intent == "skill":
        # Способность Пути
        sk_key = "arcana"
        choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
        my_skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
        for s in my_skills:
            if s["name"].lower()[:6] in low:
                sk_key = _pick_path_skill_key(s, rules.SKILLS)
                break
    else:
        # 2. ИЕРАРХИЯ ГЛАВНОГО РИСКА (CHIEF RISK ARBITRATION) ДЛЯ СВОБОДНЫХ И СОСТАВНЫХ ЗАЯВОК:
        # В цепочке действий (напр. "смешаться с толпой -> украсть одежду -> сменить образ -> уйти")
        # арбитр выбирает проверку наиболее критического звена (кража/ловкость или скрытность),
        # опираясь на основы слов (стемминг), устойчивые к опечаткам и слияниям.

        # Ранг 1: Огнестрельное оружие и прямая стрельба
        if any(w in low for w in ("стрел", "револьвер", "пистолет", "ружь", "выстрел", "пул", "взвести курок", "курок", "патрон", "спуск")):
            sk_key = "firearms"
        # Ранг 2: Силовой рукопашный контакт, захваты и удушения
        elif any(w in low for w in ("захват", "душ", "схват", "горл", "зажат", "врез", "кулак", "борьб", "сбить с ног", "удар", "рукопаш", "ломаю ше", "сломать ше", "нокаут", "апперкот", "пинок", "пнуть")):
            sk_key = "brawling"
        # Ранг 3: Экстремальный побег, прыжки, акробатика и уход в окно/по крышам
        elif any(w in low for w in ("окно", "подоконник", "сальто", "прыг", "выпрыг", "паркур", "уворач", "уклонен", "кувыр", "люстр", "перемах", "крыш", "карниз", "балансир", "перепрыг")):
            sk_key = "acrobatics"
        # Ранг 4: Силовой таран, выбивание преград, бегство напролом
        elif any(w in low for w in ("выбив", "ломаю двер", "выломать", "протаран", "тащит", "волоком", "толка", "убеж", "убег", "сбеж", "беж", "рвану", "перелез", "карабк", "вскарабк", "удира", "прорыв", "нестись", "мчать", "силой")) and not any(k in low for k in ("карман", "кошел", "краж", "вор", "подмен")):
            sk_key = "athletics"
        # Ранг 5: Кража, подмена, карманное изъятие, переодевание и маскировка (с учётом опечаток типа "ожед", "столп")
        elif any(w in low for w in ("вор", "краж", "укра", "свис", "подмен", "стащ", "карман", "одежд", "ожед", "переоде", "маскир", "сменить образ", "ловкост", "фокус", "забрать", "взять со стол", "спрятать в рукав", "выхват", "сгрест", "спереть", "стянуть", "срезать кошель", "отмычк", "взлом", "вскрыт", "присвоит", "залезть в карман", "вытащит")):
            sk_key = "sleight_of_hand"
        # Ранг 6: Скрытное перемещение, тени, растворение в толпе, уход незамеченным
        elif any(w in low for w in ("скрыт", "незамет", "прят", "спрят", "толп", "столп", "смешат", "раствор", "уйти", "уход", "ускольз", "тихо", "бесшум", "тень", "крад", "подкрад", "подкрас", "бесшумн", "пригнувшись", "затаит", "смытьс", "шмыгн", "нырну")):
            sk_key = "stealth"
        # Ранг 7: Замки, сейфы и механизмы
        elif any(w in low for w in ("механиз", "бомб", "взрыв", "шестер", "паров", "часов", "замок", "сейф")):
            sk_key = "nature"
        # Ранг 8: Оккультные ритуалы, зелья, карты Таро, астрология
        elif any(w in low for w in ("гадан", "маятник", "ритуал", "зель", "оккульт", "заклинан", "таро", "символ культ", "мистик", "проклят")):
            sk_key = "arcana"
        elif any(w in low for w in ("аур", "призрак", "духовн", "дух", "эфирн", "чуть", "астрал", "звер", "животн")):
            sk_key = "animal_handling"
        elif any(w in low for w in ("бог", "церков", "догмат", "молитв", "священн", "астролог")):
            sk_key = "religion"
        # Ранг 9: Медицина, вскрытие, яды
        elif any(w in low for w in ("леч", "перевяз", "бинт", "яд", "пульс", "ран", "вскрыт", "токсин", "противояд")):
            sk_key = "medicine"
        # Ранг 10: Социальные манипуляции
        elif any(w in low for w in ("обман", "лгу", "притвор", "блеф", "прикидыва", "актёр", "актер", "маск", "фальш", "врать", "солгат")):
            sk_key = "deception"
        elif any(w in low for w in ("угрож", "пуга", "давл", "крич", "рычу", "допрос", "шантаж", "припугн")):
            sk_key = "intimidation"
        elif any(w in low for w in ("этикет", "светск", "поклон", "представлен", "анекдот", "танц", "роль")):
            sk_key = "performance"
        elif any(w in low for w in ("убежд", "договар", "прош", "дипломат", "торг", "уговор", "предложит", "соглашен")):
            sk_key = "persuasion"
        # Ранг 11: Анализ улик, дедукция и история
        elif any(w in low for w in ("хроник", "эпох", "династи", "древн", "истори")):
            sk_key = "history"
        elif any(w in low for w in ("мотив", "читаю мысл", "микровыраж", "психоанализ", "взгляд", "врет", "лжет", "подозрен")):
            sk_key = "insight"
        elif any(w in low for w in ("улик", "тайник", "обыск", "след", "расспраш", "внимател", "дедукц", "анализ", "переписа")):
            sk_key = "investigation"
        else:
            # ДЕФОЛТНЫЙ ФОЛЛБЭК: perception назначается ТОЛЬКО когда игрок явно осматривается, прислушивается, ищет или изучает обстановку.
            # Если в тексте есть глаголы физического действия, перемещения или взаимодействия — perception ЗАПРЕЩЁН!
            is_explicit_perception = any(w in low for w in (
                "осмотр", "осматрив", "посмотр", "взглян", "прислуш", "вслуш", "приглядет",
                "заметить", "оглядет", "оглядыва", "разглядет", "поискать", "изучит",
                "нюха", "принюха", "учуят", "взор", "глаз", "уши", "слух"
            ))
            # Глаголы физического действия, движения или перемещения
            has_movement_or_action = any(w in low for w in (
                "идти", "пойти", "пошел", "пошёл", "шагаю", "шагает", "шагать", "шагну", "сделать шаг",
                "бег", "двиг", "направ", "пройти", "выход", "вход", "добр", "дойти", "пробра", "просоч",
                "вста", "сесть", "взят", "взял", "полож", "откры", "закры", "трону", "косну", "толк",
                "тян", "сдела", "начат", "продолж", "кину", "брос", "удерж", "схват", "смешат", "раствор",
                "уход", "уйти", "переоде", "сменит", "краст", "стащит", "взять", "потащить", "полез",
                "беж", "лезть", "двинут", "ускольз"
            ))

            if is_explicit_perception and not has_movement_or_action:
                sk_key = "perception"
            elif any(w in low for w in ("рука", "руки", "пальц", "карман", "вещ", "предмет", "одежд", "ожед", "хвата", "подмен")):
                sk_key = "sleight_of_hand"
            elif any(w in low for w in ("бег", "рывок", "сильн", "скорост", "взбира", "лез", "прорыв")):
                sk_key = "athletics"
            else:
                sk_key = "stealth"

    # 3. КАЛИБРОВКА СЛОЖНОСТИ (DC) ДЛЯ СОХРАНЕНИЯ РИСКА И ИГРОВОГО НАПРЯЖЕНИЯ:
    # Шкала: 10-11 (простые/пассивные), 13-14 (стандартный риск), 15-16 (высокий риск/настороженная цель), 17-18 (экстремальный риск)
    risky_skills = {
        "sleight_of_hand", "stealth", "deception",
        "persuasion", "intimidation", "acrobatics", "athletics"
    }

    # Объединяем текст заявки и контекст окружения/последнего сообщения мастера
    ctx_low = f"{low} {(context or '').lower()}".strip()

    # Маркеры настороженной или опасной цели
    danger_target_markers = (
        "странн", "подозрительн", "смотрит", "незнаком", "ищейк", "страж",
        "преследовател", "вплотну", "под наблюден", "на глазах", "патрул",
        "констебл", "полицейск", "жандарм", "дознавател", "ночн ястреб", "ястреб",
        "насторож", "следит", "охран", "сторож"
    )
    has_danger_target = any(m in ctx_low for m in danger_target_markers)

    # Маркеры экстремального риска (Потусторонние, мистические ловушки, высшие саны)
    extreme_markers = (
        "потусторонн", "высокопосл", "маг", "колдун", "архибишоп", "епископ",
        "капитан", "гримуар", "ловушк", "под прицел", "заложник", "святилищ",
        "алтар", "артефакт", "дознавател ястреб", "телепорт", "читаю мысли", "церковная печать"
    )
    is_extreme = any(m in ctx_low for m in extreme_markers)

    # Маркеры мифического размаха (Anti-Godmoding)
    godmode_markers = (
        "взрываю весь", "убиваю одним", "бог", "0 последовательн",
        "уничтожаю город", "бессмерт", "ангел", "божеств", "невозможн", "судьб"
    )
    is_godmode = any(m in low for m in godmode_markers)

    # Простые действия без прямого противодействия
    easy_markers = (
        "пьян", "спящ", "рассеян", "зевак", "пуст", "безлюдн", "беспрепятств", "в одиночеств"
    )
    is_easy_target = any(m in ctx_low for m in easy_markers)

    # Базовые пороги сложности по шкале риска
    if is_godmode:
        dc = 24  # Мифический максимум
    elif is_extreme:
        dc = 18  # Экстремальный риск (Сл 17–18)
    elif sk_key in risky_skills:
        if has_danger_target:
            # Сл 15–16: высокий риск или прямой контакт с опасной/настороженной целью
            # (кража вплотную у подозрительного/странного незнакомца, обман ищейки, трюк под наблюдением)
            if any(m in low for m in ("вслепую", "одновремен", "сальто", "люстр", "взлом", "сейф", "на глазах", "вплотну")):
                dc = 16
            else:
                dc = 15
        elif is_easy_target:
            # Сл 10–11: простые действия без прямого противодействия
            # (тихий шаг в пустом переулке, кража у пьяного/рассеянного зеваки)
            dc = 11
        else:
            # Сл 13–14: стандартный риск в людной обстановке или на скорости
            # (нырнуть в толпу, незаметно подменить предмет, залезть в карман прохожему)
            if any(m in low for m in ("толп", "карман", "подмен", "стащ", "стяну", "выхват", "спереть", "срезать", "отмычк", "взлом", "бег", "прохож", "скорост", "прыж", "рывок", "удира", "прорыв")):
                dc = 14
            else:
                dc = 13
    else:
        # Пассивные, аналитические и информационные навыки
        if has_danger_target:
            dc = 13  # Наблюдение / анализ опасного субъекта или под давлением
        elif is_easy_target or any(m in low for m in ("осматрив", "расспраш", "спрашив", "внимател", "обыск", "посмотр", "прислуш")):
            dc = 10  # Рутинный осмотр или расспрос (нижний порог)
        else:
            dc = 11  # Стандартное восприятие

    # Учёт контекста опасности цели:
    # Если в тексте заявки (или последнем сообщении Мастера) фигурируют маркеры настороженной/опасной цели,
    # минимальная сложность для sleight_of_hand, stealth и deception должна быть не ниже Сл 14–15.
    if has_danger_target and sk_key in ("sleight_of_hand", "stealth", "deception"):
        dc = max(15, dc)

    # Применение классовой способности Пути:
    # снижает сложность (-2 к DC), но не пробивает минимальный порог опасности цели (не ниже Сл 14)
    # и общий нижний порог активных проверок (dc >= 10).
    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    my_skills = rules.path_skills(c.get("pathway", ""), choices, c.get("sequence", 9), c.get("start_sequence", 9))
    used_path_skill = False
    for s in my_skills:
        if s.get("name") and s["name"].lower()[:6] in low:
            used_path_skill = True
            break
    if intent == "skill":
        used_path_skill = True

    if used_path_skill:
        if has_danger_target and sk_key in ("sleight_of_hand", "stealth", "deception"):
            dc = max(14, dc - 2)
        else:
            dc = max(10, dc - 2)

    # Нижний порог для активных проверок: dc >= 10, верхний предел 24
    dc = max(10, min(24, dc))

    is_combat_init = any(w in low for w in ("атакую в лоб", "нападаю на него", "вызываю на бой", "вступаю в бой"))
    action_summary = _generate_action_summary(text, sk_key, intent)
    return sk_key, dc, is_combat_init, action_summary


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
            "acting_gain": 1,
            "acting_feedback": f"Your action resonates slightly with the '{seq_title}' potion (+1%).",
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
        "acting_gain": 1,
        "acting_feedback": f"Твоя выходка слегка резонирует с зельем «{seq_title}» (+1%).",
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


def _fmt_consequence(cc: Any, lang: str = "ru") -> str:
    """Один элемент цепочки последствий -> плоская строка.

    Понимает и новую dict-структуру {target, event, threat, trigger},
    и старый строковый формат (обратная совместимость с сессиями в БД).
    """
    if isinstance(cc, dict):
        target = str(cc.get("target") or "").strip()
        event = str(cc.get("event") or "").strip()
        trigger = str(cc.get("trigger") or "").strip()
        try:
            threat = max(1, min(3, int(cc.get("threat") or 1)))
        except (TypeError, ValueError):
            threat = 1
        head = f"{target}: {event}" if (target and event) else (event or target)
        if not any(k in cc for k in ("threat", "trigger", "target")):
            # это не последствие, а просто объект-улика/предмет -> без префикса угрозы
            return head
        out = f"[{threat}/3] {head}".strip()
        if trigger:
            out += f" (триггер: {trigger})" if lang == "ru" else f" (trigger: {trigger})"
        return out
    return str(cc).strip()


def _as_text_list(items: Any) -> list[str]:
    """Приводит список улик/NPC/последствий к чистым строкам (dict -> читаемый текст)."""
    out: list[str] = []
    for x in (items or []):
        if isinstance(x, dict):
            # NPC-структура {name, role/status/...} — берём имя (+роль)
            if "name" in x:
                name = str(x.get("name") or "").strip()
                role = str(x.get("role") or x.get("status") or "").strip()
                txt = f"{name} ({role})" if (name and role) else (name or role)
            else:
                txt = _fmt_consequence(x)
        else:
            txt = _fmt_consequence(x)
        if txt:
            out.append(txt)
    return out


def _norm_hist(entry: Any) -> dict:
    """Нормализует запись истории в формат OpenAI-сообщения."""
    if isinstance(entry, dict):
        role = entry.get("role")
        if role in ("user", "assistant", "system"):
            return {"role": role, "content": str(entry.get("content") or "")}
        return {"role": "assistant", "content": json.dumps(entry, ensure_ascii=False)}
    return {"role": "assistant", "content": str(entry)}


async def _call_ai_gm(c: dict, session: dict, user_action: str, roll_info: str = "", fate_seal_used: bool = False) -> dict:
    if not config.LLM_API_KEY:
        return _fallback_scene(c, session, user_action, roll_info)

    choices = {int(k): v for k, v in (c.get("pathway_choices") or {}).items()}
    skills = rules.path_skills(c["pathway"], choices, c["sequence"], c["start_sequence"])
    ab_names = ", ".join(f"{s['name']} ({s['dice']})" if s.get("dice") else s["name"] for s in skills)
    seq = int(c.get("sequence", 9))
    seq_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(seq, f"Seq {seq}")
    eff_ac = int(c.get("ac", 10)) + h_perks.ac_bonus(c)
    lang = session.get("lang", "ru")

    turn_no = int(session.get("chapter") or 0)
    is_opening = (turn_no == 0) or ("GENERATE OPENING SCENE" in user_action)

    arc = session.get("current_arc") or _default_arc_for(c, lang)
    summary = session.get("summary") or ("Расследование только началось." if lang == "ru" else "Investigation just started.")
    if not is_opening and len(summary) > 800:
        summary = summary[-800:]

    clues = ", ".join(_as_text_list(session.get("clues"))) or ("Пока нет" if lang == "ru" else "None yet")
    npcs = ", ".join(_as_text_list(session.get("npcs"))) or ("Пока нет" if lang == "ru" else "None yet")
    acting_principle = lotm.ACTING_PRINCIPLES.get(c["pathway"], "Отыгрывай роль своей Последовательности.")

    gm_mode = session.get("gm_mode", "canon")
    if gm_mode == "madness":
        gm_mode_desc = (
            "UNHINGED & CREATIVE D&D SANDBOX MODE: Welcome absurd, hilarious, chaotic stunts! "
            "Blend LotM mysticism with dynamic tabletop D&D energy."
        )
    else:
        gm_mode_desc = (
            "DARK VICTORIAN CANON LOTM MODE: Atmospheric, tense, mysterious steampunk & Lovecraftian investigation."
        )

    # --- Динамический контекст (идёт в user-сообщении, system остаётся кэшируемым) ---
    lang_name = "English" if lang == "en" else "Russian"
    clock_event = ""
    if turn_no > 0 and turn_no % 5 == 0:
        clock_event = f"WORLD CLOCK EVENT: {lotm.WORLD_CLOCK_EVENTS[min((turn_no // 5) - 1, len(lotm.WORLD_CLOCK_EVENTS) - 1)]}"

    bg = lotm.BACKGROUNDS.get(c.get("faction") or "detective", {})
    region = lotm.STARTING_REGIONS.get(c.get("faction_perk") or "tingen", {})
    raw_bio = (c.get("bio") or "").strip()
    if is_opening:
        bio_entry = f"- Player Custom Backstory (Bio): '{raw_bio or 'Не указана — опирайся на Происхождение'}'\n- Background Hook: {bg.get('story_hook', '')}\n- Background Lore: {bg.get('lore', '')}\n- Region Opening: {region.get('opening_hook', '')}\n"
    else:
        short_bio = (raw_bio[:350] + "…") if len(raw_bio) > 350 else (raw_bio or "Опирайся на Происхождение")
        bio_entry = f"- Player Backstory (Bio brief): '{short_bio}'\n"

    consequences = ", ".join(_as_text_list(session.get("consequences"))) or ("Пока нет" if lang == "ru" else "None yet")
    voices = rules.get_passive_voices(c)

    inv_items = c.get("inventory") or []
    if inv_items:
        inv_str = ", ".join(f"{it.get('name', 'Предмет')} (×{it.get('qty', 1)})" for it in inv_items if isinstance(it, dict) and it.get("name"))
    else:
        inv_str = "Пусто"

    dyn = (
        f"[LANGUAGE: Respond strictly in {lang_name}. TONE: {gm_mode_desc}]\n\n"
        f"=== PLAYER CHARACTER ===\n"
        f"- Name: {c['name']} | Pathway: {lotm.PATHWAYS.get(c['pathway'], {}).get('name', c['pathway'])} | Sequence {seq}: \"{seq_title}\"\n"
        f"- Background: {bg.get('name', 'Unknown')} | Region: {region.get('name', 'Tingen')}\n"
        f"{bio_entry}"
        f"- Acting Method Principle: {acting_principle}\n"
        f"- Top Passive Skill Voices (use [Навык — Пассивно] ONLY if scene has relevant clue/tactics/lore): {voices}\n"
        f"- HP: {c['hp']}/{c['max_hp']} | AC: {eff_ac} | Sanity: {c.get('sanity', 100)}% | Digestion: {c.get('digestion', 0)}%\n"
        f"- Carried Inventory: {inv_str}\n"
        f"- Abilities (Name & Dice): {ab_names}\n\n"
        f"=== PERSISTENT STORY MEMORY ===\n"
        f"- Active Arc (Main Goal): {arc}\n"
        f"- Story Chronicle: {summary}\n"
        f"- Discovered Clues: {clues}\n"
        f"- Met NPCs: {npcs}\n"
        f"- Consequences: {consequences}\n"
        f"- Location: {session.get('location', 'Tingen')}\n"
        f"{('# ' + clock_event) if clock_event else ''}\n"
    )

    # Предупреждение о низком Рассудке
    try:
        _sanity_val = int(c.get("sanity", 100))
    except (TypeError, ValueError):
        _sanity_val = 100
    if _sanity_val <= 45:
        dyn += (
            f"\n[WARNING — LOW SANITY ({_sanity_val}%): Hear ravings from Gray Fog; weave eerie hallucinations.]"
        )

    # Очищаем обёртку при необходимости для красивого выделения полной заявки
    clean_action = user_action.strip()
    if clean_action.startswith('Player exact action: "') and clean_action.endswith('"'):
        clean_action = clean_action[len('Player exact action: "'):-1].strip()

    # Извлечение актуального финального состояния мира из предыдущего хода (End-of-Turn State Continuity)
    last_assistant_narr = ""
    for item in reversed(session.get("history") or []):
        if item.get("role") == "assistant":
            try:
                last_d = json.loads(item.get("content") or "{}")
                last_assistant_narr = (last_d.get("narrative") or "").strip()
                if last_assistant_narr:
                    break
            except Exception:
                pass
    last_state_anchor = ""
    if last_assistant_narr:
        end_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', last_assistant_narr) if s.strip()]
        last_state_anchor = " ".join(end_sentences[-2:]) if end_sentences else last_assistant_narr[-220:].strip()

    # Диагностическое логирование для отслеживания входящих данных хода
    b1_preview = clean_action[:300]
    b2_preview = (roll_info or "None")[:300]
    log.info("[AI GM INPUT] [БЛОК 1]: %s", b1_preview)
    log.info("[AI GM INPUT] [БЛОК 2]: %s", b2_preview)
    print(f"\n[AI GM INPUT] [БЛОК 1 / ДЕЙСТВИЕ ИГРОКА]:\n{b1_preview}", flush=True)
    print(f"[AI GM INPUT] [БЛОК 2 / КУБИК]:\n{b2_preview}\n", flush=True)

    # --- Чёткая многоблочная структура входного контекста текущего хода ---
    turn_blocks = [
        dyn,
        "=== ДАННЫЕ ТЕКУЩЕГО ХОДА (CURRENT TURN INPUT) ===",
        (
            f"[БЛОК 1: ДЕЙСТВИЕ ИГРОКА / ПОЛНАЯ ЗАЯВКА]:\n«{clean_action}»\n"
            "• НЕПРИКОСНОВЕННОСТЬ ЦЕЛЕЙ И ОБЪЕКТОВ ИГРОКА (STRICT TARGET FIDELITY):\n"
            "  Мастеру КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО подменять объекты, цели и направление движения, указанные игроком!\n"
            "  Если игрок указал конкретную цель и предмет («снять плащ и шляпу с приказчика в толпе и уйти в переулок») — "
            "  при успехе проверки отыгрывай взаимодействие ИМЕННО с этим приказчиком, плащом, шляпой и переулком! "
            "  Запрещено вместо этого обкрадывать другого NPC, шарить по карманам собеседника или подменять цель из-за стереотипа навыка.\n"
            "• ЖЁСТКИЙ ЗАПРЕТ НА «ПАРАЛИЧ У СТЕНЫ/КОЛОННЫ» И ПРИНУДИТЕЛЬНЫЕ ДИАЛОГИ:\n"
            "  Если игрок заявил уход, побег, смену одежды или смешивание с толпой — запрещено тормозить его ради разговоров со старыми NPC! "
            "  Персонаж ОБЯЗАН совершить заявленные движения и физически изменить свою позицию.\n"
            "• МЯГКИЙ ЛИМИТ СВЕРХДЛИННЫХ ЦЕПОЧЕК:\n"
            "  Только если заявка содержит 4–5+ действий со сменой нескольких локаций, выполни первые 2–3 шага и остановись на новом препятствии. "
            "  Никогда не останавливай игрока на нулевом шаге до начала движения!"
        ),
    ]
    if roll_info:
        seal_note = ""
        if fate_seal_used or session.get("fate_seal_used"):
            seal_note = "• Примечание: игрок потратил Печать Судьбы, чтобы переломить исход проверки (вплети ощущение треснувшей печати / вмешательства судьбы в сцену).\n"
        turn_blocks.append(
            f"[БЛОК 2: РЕЗУЛЬТАТ ПРОВЕРКИ ГЛАВНОГО РИСКА]:\n{roll_info}\n"
            f"{seal_note}"
            "• ИНСТРУКЦИЯ КУБИКА И ВЕТВЛЕНИЕ ФАЗЫ 1:\n"
            "- Название навыка проверяло главный узел риска и НЕ ДОЛЖНО сужать фокус или менять цель "
            "(при успехе выполняются заявленные взаимодействия именно с указанными игроком объектами и целями).\n"
            "- ПРИ УСПЕХЕ (или Nat 20): В Фазе 1 физически и последовательно завершаются действия заявки, персонаж РЕАЛЬНО меняет положение в пространстве.\n"
            "- ПРИ ПРОВАЛЕ (или Nat 1): В Фазе 1 описывается ПОПЫТКА и конкретная точка срыва именно заявленного действия. "
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать об успешном завершении действия при провале!"
        )
    if last_state_anchor:
        turn_blocks.append(
            f"[БЛОК 3: НЕПРЕРЫВНОСТЬ СОСТОЯНИЯ МИРА (END-OF-TURN STATE CONTINUITY)]:\n"
            f"• ФИНАЛЬНЫЙ СТАТУС ПРЕДЫДУЩЕГО ХОДА: «{last_state_anchor}»\n"
            "• ДИРЕКТИВА НЕПРЕРЫВНОСТИ (TEMPORAL STATE CONTINUITY): "
            "Текущий ход ОБЯЗАН развиваться строго из этого обновлённого состояния! "
            "Если в конце предыдущего хода замершее время возобновилось, капли упали, толпа ожила или дверь открылась — "
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО откатывать сцену назад и снова описывать застывший мир, висящие капли или замерших людей!"
        )
    turn_blocks.append(
        "[БЛОК 4: ДИРЕКТИВА ДЕЛЬТЫ (DELTA ONLY / ZERO CONTEXT ECHO)]:\n"
        "• Сообщения истории диалога и летопись служат ИСКЛЮЧИТЕЛЬНО для сохранения фактов и канона. "
        "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО заново описывать статичные детали окружения, одежду, погоду, свет газовых фонарей "
        "или уже найденные улики. Описывай ТОЛЬКО дельту: физическое движение героя, результат заявки и НОВУЮ реакцию мира!"
    )
    full_user_msg = "\n\n".join(turn_blocks)

    def _compact_assistant(entry: dict) -> dict:
        try:
            d = json.loads(entry.get("content") or "")
        except Exception:
            d = {}
        narr = (d.get("narrative") or "").strip().replace("\ufffd", "").replace("\uFFFD", "")
        # Сохраняем начало И финал сцены (где происходит сдвиг мира / Phase 3), чтобы не терять конечное состояние
        if len(narr) > 380:
            brief = f"{narr[:180]} [...] [ИТОГ СЦЕНЫ]: {narr[-190:]} (Локация: {d.get('location', '')})"
        else:
            brief = f"{narr} (Локация: {d.get('location', '')})"
        return {"role": "assistant", "content": brief}

    hist_msgs = list(session.get("history") or [])[-6:]
    compact_hist = [
        _compact_assistant(m) if m.get("role") == "assistant" else m
        for m in hist_msgs if isinstance(m, dict)
    ]
    messages = [{"role": "system", "content": STATIC_SYSTEM_PROMPT}, *compact_hist]
    messages.append({"role": "user", "content": full_user_msg})

    timeout = aiohttp.ClientTimeout(total=85, connect=15, sock_read=75)
    last_err_msg = ""
    last_status = 502

    for attempt in range(2):
        cur_messages = list(messages)
        if attempt > 0:
            cur_messages.append({
                "role": "user",
                "content": "IMPORTANT: Keep narrative concise (450-700 characters, 2-3 paragraphs), complete Phase 1-3, and ensure full JSON object is closed with exactly 3 options.",
            })

        try:
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post(
                    config.LLM_API_URL,
                    headers={
                        "Authorization": f"Bearer {config.LLM_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.LLM_MODEL or "deepseek/deepseek-v4.1-flash-1017",
                        "messages": cur_messages,
                        "response_format": {"type": "json_object"},
                        "temperature": 0.8 if gm_mode == "madness" else 0.72,
                        "max_tokens": 3500,
                        "reasoning_effort": "low",
                    },
                ) as resp:
                    if resp.status != 200:
                        err_body = await resp.text()
                        last_status = resp.status
                        last_err_msg = f"LLM API status {resp.status}: {err_body[:200]}"
                        log.warning("LLM attempt %d status %s: %s", attempt + 1, resp.status, err_body[:200])
                        if attempt == 0:
                            continue
                        break

                    payload = await resp.json()
                    choice = (payload.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason")
                    raw = (choice.get("message") or {}).get("content") or ""
                    # Очистка от битых символов кодировки \ufffd
                    raw = raw.replace("\ufffd", "").replace("\uFFFD", "")

                    if finish_reason == "length":
                        log.warning("LLM attempt %d truncated by max_tokens (finish_reason=length, len=%d)", attempt + 1, len(raw))
                        last_err_msg = "LLM response truncated by length"
                        if attempt == 0:
                            continue
                        break

                    data = _parse_llm_json(raw)
                    if not isinstance(data, dict) or not data.get("narrative"):
                        log.warning(
                            "LLM attempt %d JSON unusable (finish_reason=%s, len=%d): %s",
                            attempt + 1,
                            finish_reason,
                            len(raw),
                            raw[:200],
                        )
                        last_err_msg = "LLM returned empty or invalid narrative"
                        if attempt == 0:
                            continue
                        break

                    opts = data.get("options")
                    is_combat = bool(data.get("spawn_enemy"))
                    if not is_combat and (not isinstance(opts, list) or len(opts) == 0):
                        log.warning("LLM attempt %d returned narrative without options (len=%d), retrying...", attempt + 1, len(raw))
                        last_err_msg = "LLM returned narrative without options"
                        if attempt == 0:
                            continue
                        break

                    if not isinstance(opts, list):
                        data["options"] = []
                    if isinstance(data.get("narrative"), str):
                        data["narrative"] = re.sub(
                            r'([^\n])\s*(\[[^\]\n]+?(?:—|-)\s*(?:Пассивно|Успех|Провал|Passive|Success|Failure)[^\]\n]*\]:?)',
                            r'\1\n\n\2',
                            data["narrative"]
                        )
                    return _clean_utf8_artifacts(data)
        except Exception as e:
            last_err_msg = f"LLM call exception: {e}"
            log.warning("LLM attempt %d failed with exception: %s", attempt + 1, e)
            if attempt == 0:
                continue
            break

    log.error("All LLM attempts failed: %s", last_err_msg)
    return {
        "error": True,
        "status": last_status,
        "message": f"Связь с Мастером прервалась ({last_err_msg[:120]}). Попробуйте повторить запрос.",
        "narrative": "",
        "options": [],
    }


def _clean_utf8_artifacts(v):
    """Рекурсивно удаляет битые символы кодировки \\ufffd из структур данных."""
    if isinstance(v, str):
        return v.replace("\ufffd", "").replace("\uFFFD", "")
    elif isinstance(v, list):
        return [_clean_utf8_artifacts(x) for x in v]
    elif isinstance(v, dict):
        return {k: _clean_utf8_artifacts(val) for k, val in v.items()}
    return v


def _parse_llm_json(raw: str) -> dict:
    """Терпимый парсер JSON от LLM: снимает ```json-обёртки, обрезанные хвосты и артефакты кодировки."""
    if not raw:
        return {}
    txt = raw.strip().replace("\ufffd", "").replace("\uFFFD", "")
    if txt.startswith("```"):
        parts = txt.split("```")
        txt = parts[1] if len(parts) > 1 else txt
        if txt.lstrip().lower().startswith("json"):
            txt = txt.lstrip()[4:]
        txt = txt.strip().rstrip("`").strip()

    try:
        parsed = json.loads(txt)
        if isinstance(parsed, dict):
            return _clean_utf8_artifacts(parsed)
    except Exception:
        pass

    start = txt.find("{")
    if start < 0:
        return {}
    candidate = txt[start:]

    try:
        repaired = _close_truncated_json(candidate)
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            return _clean_utf8_artifacts(parsed)
    except Exception:
        pass

    # Последний шанс: вытащить хотя бы narrative регуляркой.
    out: dict = {}
    m = re.search(r'"narrative"\s*:\s*"((?:[^"\\]|\\.)*)"', candidate, re.S)
    if m:
        try:
            out["narrative"] = json.loads('"' + m.group(1) + '"')
        except Exception:
            out["narrative"] = m.group(1)
    return _clean_utf8_artifacts(out)


def _close_truncated_json(txt: str) -> str:
    """Достраивает закрывающие кавычки/скобки для обрезанного JSON-объекта."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in txt:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()

    out = txt
    if in_str:
        if esc:
            out = out[:-1]
        out += '"'
    out = out.rstrip().rstrip(",").rstrip()
    for opener in reversed(stack):
        out += "}" if opener == "{" else "]"
    return out


def _scaled_digestion_gain(seq: int, raw_gain: int, roll_success: bool = True) -> int:
    """Строгий расчёт усвоения зелья: 0% при провале или рутине, 1–3% только за истинный Метод Отыгрыша."""
    if not roll_success or raw_gain <= 0:
        return 0
    seq = int(seq or 9)
    capped = min(3, int(raw_gain))
    if seq >= 8:
        return capped          # Посл. 9-8: +1..+3% за настоящий отыгрыш (~35-45 ходов)
    if seq >= 5:
        return max(1, capped - 1)  # Посл. 7-5: +1..+2%
    return 1                   # Полубоги и Ангелы (Посл. 4-0): +1% за идеальный отыгрыш


async def _apply_ai_memory_and_state(
    uid: int, c: dict, session: dict, action_prompt: str, data: dict, roll_success: bool
) -> tuple[dict, dict, int]:
    """Обновляет Трёхслойную Память (Арку, Летопись, Улики, NPC) и статы персонажа."""
    if isinstance(data.get("narrative"), str):
        data["narrative"] = re.sub(
            r'([^\n])\s*(\[[^\]\n]+?(?:—|-)\s*(?:Пассивно|Успех|Провал|Passive|Success|Failure)[^\]\n]*\]:?)',
            r'\1\n\n\2',
            data["narrative"],
        )
    raw_gain = int(data.get("acting_gain") or 0)
    dig_gain = _scaled_digestion_gain(int(c.get("sequence", 9)), raw_gain, roll_success)
    new_dig = min(100, int(c.get("digestion") or 0) + dig_gain)

    # --- Запечатанные Артефакты: ИИ выдал reward_artifact при крупном успехе ---
    raw_art = data.get("reward_artifact")
    if not isinstance(raw_art, dict):
        raw_art = {}
    art_name = str(raw_art.get("name") or "").strip()
    if art_name:
        chassis = lotm.roll_artifact_chassis(c)
        new_art = {
            "id": f"art_{int(c.get('user_id', 0))}_{int(session.get('chapter') or 0)}_{len(c.get('artifacts') or [])}",
            "name": art_name[:60],
            "icon": str(raw_art.get("icon") or chassis.get("icon") or "ring"),
            "lore": str(raw_art.get("lore") or "").strip()[:160],
            "skill": chassis.get("skill", "all"),
            "stat": chassis.get("stat", ""),
            "bonus": int(chassis.get("bonus", 1)),
            "ac_bonus": int(chassis.get("ac_bonus", 0)),
            "class": int(chassis.get("class", 3)),
            "curse": str(chassis.get("curse", "")),
            "equipped": False,
        }
        arts = [a for a in (c.get("artifacts") or []) if isinstance(a, dict)]
        if len(arts) >= 6:
            arts = arts[-5:]
        # Максимум 2 экипированных одновременно
        arts.append(new_art)
        c["artifacts"] = arts

    # --- Реальный инвентарь: применение JSON-дельты от ИИ-Мастера ---
    raw_add = data.get("inventory_add") or []
    raw_rem = data.get("inventory_remove") or []
    new_inv, inv_delta = rules.apply_inventory_delta(c, raw_add, raw_rem)
    data["inventory_delta"] = inv_delta
    c["inventory"] = new_inv

    hp_delta = int(data.get("hp_delta") or 0)
    san_delta = int(data.get("sanity_delta") or 0)
    new_hp, new_temp = int(c["hp"]), int(c.get("temp_hp") or 0)
    if hp_delta < 0:
        new_hp, new_temp = rules.apply_damage(new_hp, new_temp, -hp_delta)
    elif hp_delta > 0:
        new_hp = rules.apply_heal(new_hp, int(c["max_hp"]), hp_delta)
    new_hp = max(1, new_hp)
    new_san = max(5, min(100, int(c.get("sanity", 100)) + san_delta))

    await db.update_character(
        uid,
        hp=new_hp,
        temp_hp=new_temp,
        sanity=new_san,
        digestion=new_dig,
        artifacts=c.get("artifacts") or [],
        inventory=new_inv,
    )
    c = await db.get_character(uid)

    max_sum_len = 1600 if session.get("is_vip") else 800
    old_sum = (session.get("summary") or "").strip()
    append_sum = (data.get("summary_append") or "").strip()
    merged = f"{old_sum} {append_sum}".strip() if append_sum else old_sum
    if len(merged) > max_sum_len:
        # Умная обрезка: сохраняем первое предложение (завязку героя) + хвост по границе ". "
        first_sent = merged.split(". ", 1)[0].strip()
        if len(first_sent) < 200:
            tail = merged[len(first_sent) + 1:]
            while len(first_sent) + len(tail) > max_sum_len and ". " in tail:
                tail = tail.split(". ", 1)[1]
            new_sum = (first_sent + ". " + tail)[:max_sum_len].strip()
        else:
            new_sum = merged[-max_sum_len:]
    else:
        new_sum = merged

    clues = list(session.get("clues") or [])
    new_clue = (data.get("new_clue") or "").strip()
    if new_clue:
        clue_low = new_clue.lower()
        is_capital = ("💰" in new_clue) or ("капитал" in clue_low)
        if is_capital:
            # Только одна актуальная запись капитала в дневнике
            clues = [cl for cl in clues if ("💰" not in cl) and ("капитал" not in cl.lower())]
            clues.insert(0, new_clue)
        elif new_clue not in clues:
            clues = (clues + [new_clue])[-12:]

    npcs = list(session.get("npcs") or [])
    new_npc = (data.get("new_npc") or "").strip()
    if new_npc and new_npc not in npcs:
        npcs = (npcs + [new_npc])[-12:]

    # Долгосрочные Последствия (Цепочка Последствий и Угроз, до 8 активных структур)
    consequences = list(session.get("consequences") or [])
    raw_conseq = data.get("consequence_add")
    new_conseq: dict | None = None
    if isinstance(raw_conseq, dict):
        target = str(raw_conseq.get("target") or "").strip()
        event = str(raw_conseq.get("event") or "").strip()
        if target and event:
            new_conseq = {
                "target": target,
                "event": event,
                "threat": max(1, min(3, int(raw_conseq.get("threat") or 1))),
                "trigger": str(raw_conseq.get("trigger") or "").strip(),
            }
    elif isinstance(raw_conseq, str) and raw_conseq.strip():
        # Обратная совместимость: старый строковый формат превращаем в структуру
        new_conseq = {"target": "", "event": raw_conseq.strip(), "threat": 1, "trigger": ""}
    if new_conseq and all(cc.get("event") != new_conseq["event"] for cc in consequences if isinstance(cc, dict)):
        consequences = (consequences + [new_conseq])[-8:]
    history = (session.get("history") or []) + [
        {"role": "user", "content": action_prompt},
        {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
    ]
    # Списание хода теперь атомарно через db.try_consume_turn() перед вызовом ИИ — здесь не списываем
    new_turns = int(session.get("turns_left") or 0)
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
        consequences=consequences,
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
        f"🔍 <b>Найденные улики и предметы:</b>\n" + ("\n".join(f"• {esc(t)}" for t in _as_text_list(clues)) if clues else "—") + "\n\n"
        f"👥 <b>Встреченные лица (NPC):</b>\n" + ("\n".join(f"• {esc(t)}" for t in _as_text_list(npcs)) if npcs else "—") + "\n\n"
        f"⚠️ <b>Цепочка последствий и угроз:</b>\n"
        + (
            "\n".join(f"• {esc(t)}" for t in _as_text_list(session.get("consequences"))) or "—"
        )
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


@router.callback_query(F.data == "st:menu_hint")
async def cb_menu_hint(cb: CallbackQuery):
    c = await db.get_character(cb.from_user.id)
    if not c or not c.get("pathway"):
        await cb.answer("Сначала создай персонажа: /build", show_alert=True)
        return
    await cb.message.answer(
        "📓 <b>/journal</b> — дневник улик, NPC и Метод Отыгрыша\n"
        "⚔️ <b>/hunt</b> — соло-бой 1-на-1 (бесплатно, двигает переваривание зелья)\n"
        "🧪 <b>/promote</b> — ритуал повышения Последовательности (когда зелье усвоено на 100%)\n"
        "🧘 <b>Когитация</b> — кнопка отдыха в сюжете\n"
        "🏛 <b>/shop</b> — Святилище Серого Тумана: ходы, Печати Судьбы, Артефакты, VIP\n"
        "🎲 <b>Печати Судьбы</b> — переброс проваленной проверки d20 прямо во время сцены\n\n"
        "💡 В сюжетных сценах можно просто <b>писать любые действия текстом</b> — Мастер сам подберёт навык и бросит куб.",
    )
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


# --- Процедурный Генератор Завязки (Глава 1): погода/время + катализатор + анкета ---
_OPENING_TIMES = [
    "Глубокая полночь под светом Багровой Луны, густой жёлтый туман",
    "Дождливый рассвет, запах угольного дыма и сырой брусчатки",
    "Сумерки во время перебоев с газовым освещением квартала",
    "Полдень в разгар уличной суеты, внезапно прерванный аномалией",
]
_OPENING_TRIGGERS = [
    "За дверью раздаются торопливые шаги тех, кто шёл по твоему следу",
    "В твоём кармане начинает раскаляться предмет из твоего прошлого",
    "Незнакомец напротив внезапно теряет самообладание, проявляя признаки потери контроля",
    "Ты находишь зашифрованное послание, адресованное лично тебе",
]


def _build_procedural_opening_seed(c: dict) -> str:
    bg = lotm.BACKGROUNDS.get(c.get("faction") or "detective", {})
    reg = lotm.STARTING_REGIONS.get(c.get("faction_perk") or "tingen", {})
    seq9_title = lotm.SEQUENCE_TITLES.get(c["pathway"], {}).get(9, "Посл. 9")
    bio_txt = (c.get("bio") or "").strip()
    path_name = lotm.PATHWAYS.get(c["pathway"], {}).get("name", c["pathway"])
    pathway_name = path_name
    t_atm = _rng.choice(_OPENING_TIMES)
    trig = _rng.choice(_OPENING_TRIGGERS)

    # Приоритет личной анкеты: bio непуста и не является дефолтной заглушкой — 100% приоритет игроку.
    _stubs = ("не указана", "не указано", "нет", "-", "—", "bio", "история не", "default", "none", "n/a")
    bio_is_real = bool(bio_txt) and not any(bio_txt.lower().startswith(st) for st in _stubs) and len(bio_txt) >= 12

    if bio_is_real:
        return (
            f"OPENING SCENE SEED (Chapter 1) in {reg.get('location', 'Tingen')}.\n"
            f"- Atmosphere: {t_atm}.\n"
            f"- Player Status: Sequence 9 Beyonder of Pathway '{pathway_name}'. Strictly follow the player's Bio below to determine when the potion was consumed and whether it is already digested — NEVER assume the potion was just taken unless the Bio explicitly says so.\n"
            f"- Player Custom Backstory (Bio) — ABSOLUTE PRIORITY, binding blueprint:\n"
            f"  >>> {bio_txt} <<<\n"
            f"- Starting Region (binding): {reg.get('name', 'Tingen')} | {reg.get('opening_hook', '')}\n"
            f"- Background profession: {bg.get('name', '')} (use ONLY as flavor for skills/contacts).\n"
            "STRICT ORDER: The player's Bio above is 100% canon. Do NOT impose any template quest, "
            "generic order or pre-written hook on the player. Build the opening scene organically FROM the Bio "
            "and the chosen Region, set `current_arc` around their personal story."
        )
    # Анкета пуста — ИИ придумывает органичную бытовую/детективную сцену под регион, профессию и Путь.
    return (
        f"OPENING SCENE SEED (Chapter 1) in {reg.get('location', 'Tingen')}.\n"
        f"- Atmosphere: {t_atm}.\n"
        f"- Immediate Catalyst: {trig}.\n"
        f"- Player Status: Sequence 9 Beyonder of Pathway '{pathway_name}' ('{seq9_title}').\n"
        f"- Background: {bg.get('name', '')} | Gear: {bg.get('gear', '')} | Inspiration trigger: {bg.get('inspiration_trigger', '')}.\n"
        f"- Starting Region hook: {reg.get('opening_hook', '')}\n"
        "- Player Bio is EMPTY. Invent an organic, mundane or detective everyday scene that fits the Region location, "
        "the Background profession and the player's Pathway. NO template quests, NO imposed bosses: start small, "
        "personal and atmospheric; set `current_arc` around a plausible everyday problem of this profession."
    )


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
        # Анти-эксплойт: списываем ход даже при генерации первой сцены (нельзя фармить бесплатные сцены через /journal)
        ok, reason = await db.try_consume_turn(c["user_id"], config.is_dev(c["user_id"]))
        if not ok:
            import h_shop
            await msg.answer(h_shop.shop_text(session), reply_markup=h_shop.shop_kb(session.get("lang", "ru")))
            return
        wait = await (msg.edit_text("🌫 <i>Серый туман клубится, формируя сцену...</i>") if edit else msg.answer("🌫 <i>Серый туман клубится, формируя сцену...</i>"))
        # Уникальный старт: процедурный сид (атмосфера + катализатор) + анкета + Регион — без шаблонов!
        prompt = "GENERATE OPENING SCENE (Chapter 1). Use the OPENING SCENE SEED below as binding blueprint.\n" + _build_procedural_opening_seed(c)
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
    await _execute_narrative_turn(cb.message, c, session, last_fail, mode="adv", edit=True, fate_seal_used=True)


async def _execute_narrative_turn(msg: Message, c: dict, session: dict, picked: dict, mode: str = "normal", edit: bool = True, fate_seal_used: bool = False):
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
    ok, reason = await db.try_consume_turn(uid, config.is_dev(uid))
    if not ok:
        import h_shop
        no_txt = (
            "🚫 Суточный лимит бесплатных ходов ИИ-Мастера исчерпан сервером. Загляни в Магазин Духовности!"
            if reason == "global_cap_reached"
            else "🌫 Ходы Духовности закончились! Загляни в Магазин Духовности."
        )
        await (msg.edit_text(no_txt) if edit else msg.answer(no_txt))
        return
    session["turns_left"] = max(0, int(session.get("turns_left") or 0) - 1)
    action_prompt = f"Player exact action: \"{picked.get('text', '')}\""
    data = await _call_ai_gm(c, session, action_prompt, roll_info, fate_seal_used=fate_seal_used)
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
    inv_d = data.get("inventory_delta") or {}
    inv_notes = []
    if inv_d.get("added"):
        inv_notes.append("🎒 <b>Получено:</b> " + ", ".join(f"<i>{esc(it['name'])}</i>" + (f" (×{it['qty']})" if it.get("qty", 1) > 1 else "") for it in inv_d["added"]))
    if inv_d.get("removed"):
        inv_notes.append("🎒 <b>Потрачено:</b> " + ", ".join(f"<i>{esc(it['name'])}</i>" + (f" (×{it['qty']})" if it.get("qty", 1) > 1 else "") for it in inv_d["removed"]))
    inv_note = ("\n" + "\n".join(inv_notes)) if inv_notes else ""
    reroll_hint = "\n💡 <i>Не нравится провал кубика? Нажми «🎲 Перебросить Судьбу» ниже!</i>" if not success and int(session.get("fate_tokens") or 0) > 0 else ""
    footer = "\n\n💬 <i>Жми кнопку ИЛИ пиши любую свою фразу/действие прямо в чат!</i>"
    roll_banner = (
        f"🗣 <i>«{esc(picked.get('text', ''))}»</i>\n"
        f"🎲 <b>{esc(rules.SKILLS[sk_key][0])} (Сл {dc}):</b> {roll_res.total} (d20={roll_res.natural}){crit_tag} — "
        f"{'✅ Успех' if success else '❌ Провал'}\n"
        f"🧪 <i>{esc(act_fb)}</i>{clue_note}{inv_note}{reroll_hint}\n\n"
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

    last_ctx = _last_narrative_from_session(session)
    sk_key, dc, is_combat_init, action_summary = _classify_free_text(text, c, context=last_ctx)
    picked = {"id": 99, "text": text[:1000], "action_summary": action_summary, "skill": sk_key, "dc": dc, "combat": is_combat_init}
    await _execute_narrative_turn(message, c, session, picked, mode="normal", edit=False)


async def _persist_combat_outcome(uid: int, c: dict, session: dict, enemy: dict,
                                  out: combat.CombatOutcome) -> tuple[dict, dict]:
    """Применяет итог боевого раунда из combat_engine к БД. Возвращает (c, session)."""
    fields: dict = {}
    for f in ("hp", "temp_hp", "sanity", "digestion"):
        if f in out.delta:
            fields[f] = out.delta[f]

    if out.victory or out.gray_fog_save:
        fields["lair_uses"] = h_perks.reset_combat_uses(c)
        if out.spell_slots:
            fields["spell_slots"] = out.spell_slots
        extra: dict = {"enemy": {}}
        if out.victory:
            old_sum = (session.get("summary") or "").strip()
            extra["summary"] = f"{old_sum} Победил в бою: {enemy['name']}.".strip()[-1200:]
        session = await db.update_story_session(uid, **extra)
        await db.update_character(uid, **fields)
        return await db.get_character(uid), await db.get_story_session(uid)

    if out.spell_slots:
        fields["spell_slots"] = out.spell_slots
    fields["lair_uses"] = out.uses
    session = await db.update_story_session(uid, enemy=enemy)
    if fields:
        await db.update_character(uid, **fields)
    return await db.get_character(uid), session


async def _log_combat_rolls(uid: int, c: dict, out: combat.CombatOutcome) -> None:
    """Боевые броски раунда — в roll_log (Хроника подвигов во вкладке «Дело»)."""
    for ev in out.events:
        rl = ev.get("roll")
        if not rl:
            continue
        try:
            await db.log_roll(
                user_id=uid,
                chat_id=uid,
                chat_type="combat",
                username=c.get("username") or "",
                char_name=c.get("name") or "",
                command="check",
                label=str(rl.get("label") or "")[:120],
                detail=str(rl.get("detail") or "")[:120],
                total=int(rl.get("total") or 0),
                natural=int(rl["natural"]) if rl.get("natural") is not None else None,
            )
        except Exception:
            pass


async def _handle_combat_free_text(message: Message, c: dict, session: dict, text: str):
    """Позволяет писать любую дичь и трюки прямо во время пошагового боя 1-на-1."""
    uid = c["user_id"]
    enemy = dict(session.get("enemy") or {})
    sk_key, dc, _, action_summary = _classify_free_text(text, c, context=str(enemy.get("name") or ""))
    try:
        out = combat.run_round(
            c, enemy, "stunt", ac_bonus=h_perks.ac_bonus(c),
            stunt_text=text, classify=(sk_key, dc, action_summary),
        )
    except combat.CombatError as e:
        await message.answer(str(e))
        return

    c, session = await _persist_combat_outcome(uid, c, session, enemy, out)
    lines = combat.render_html(out)
    await _log_combat_rolls(uid, c, out)

    if out.victory or out.gray_fog_save:
        await message.answer(f"{_header_bar(c, session)}\n" + "\n".join(lines),
                             reply_markup=_story_kb(c, session))
        return
    await message.answer(_combat_screen_text(c, session, lines), reply_markup=_combat_kb(c, session))


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
    seq = int(parts[3]) if len(parts) >= 5 else None
    key = parts[4] if len(parts) >= 5 else None

    try:
        out = combat.run_round(
            c, enemy, kind, ac_bonus=h_perks.ac_bonus(c), seq=seq, key=key,
        )
    except combat.CombatError as e:
        await cb.answer(str(e), show_alert=True)
        return

    c, session = await _persist_combat_outcome(uid, c, session, enemy, out)
    lines = combat.render_html(out)
    await _log_combat_rolls(uid, c, out)

    if out.victory:
        promo = ""
        if int(c.get("digestion") or 0) >= 100:
            promo = "\n🎉 <b>Зелье переварено на 100%! Нажми /promote для перехода на новую Последовательность!</b>"
        await ui.safe_edit(cb.message, f"{_header_bar(c, session)}\n" + "\n".join(lines) + promo,
                           _story_kb(c, session))
        await cb.answer("Враг повержен! 🏆")
        return

    if out.gray_fog_save:
        await ui.safe_edit(cb.message, f"{_header_bar(c, session)}\n" + "\n".join(lines),
                           _story_kb(c, session))
        await cb.answer("Ты едва выжил...")
        return

    await ui.safe_edit(cb.message, _combat_screen_text(c, session, lines), _combat_kb(c, session))
    await cb.answer()
