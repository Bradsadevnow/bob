# mtg_core/cards.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import json
import re


# ============================
# Enums
# ============================

class Color(str, Enum):
    WHITE = "WHITE"
    BLUE = "BLUE"
    BLACK = "BLACK"
    RED = "RED"
    GREEN = "GREEN"


class CardType(str, Enum):
    CREATURE = "CREATURE"
    INSTANT = "INSTANT"
    SORCERY = "SORCERY"
    LAND = "LAND"
    ARTIFACT = "ARTIFACT"
    ENCHANTMENT = "ENCHANTMENT"
    PLANESWALKER = "PLANESWALKER"


class Keyword(str, Enum):
    HASTE = "HASTE"
    LIFELINK = "LIFELINK"
    FLYING = "FLYING"
    VIGILANCE = "VIGILANCE"
    DOUBLE_STRIKE = "DOUBLE_STRIKE"
    FIRST_STRIKE = "FIRST_STRIKE"
    DEATHTOUCH = "DEATHTOUCH"
    TRAMPLE = "TRAMPLE"
    REACH = "REACH"
    FLASH = "FLASH"
    MENACE = "MENACE"
    HEXPROOF = "HEXPROOF"
    DEFENDER = "DEFENDER"
    INDESTRUCTIBLE = "INDESTRUCTIBLE"
    UNDEAD_RETURN = "UNDEAD_RETURN"


class EffectType(str, Enum):
    # One-shot effects
    DEAL_DAMAGE = "DEAL_DAMAGE"
    DESTROY_PERMANENT = "DESTROY_PERMANENT"
    DESTROY_CREATURE = "DESTROY_CREATURE"
    DESTROY_ARTIFACT = "DESTROY_ARTIFACT"
    DESTROY_ENCHANTMENT = "DESTROY_ENCHANTMENT"
    DESTROY_FLYING_CREATURE = "DESTROY_FLYING_CREATURE"
    EXILE_CREATURE = "EXILE_CREATURE"
    EXILE_TARGET_UNTIL = "EXILE_TARGET_UNTIL"
    COUNTER_SPELL = "COUNTER_SPELL"
    COPY_SPELL = "COPY_SPELL"
    DRAW_CARDS = "DRAW_CARDS"
    DRAW_THEN_DISCARD = "DRAW_THEN_DISCARD"
    DRAW_X_THEN_DISCARD = "DRAW_X_THEN_DISCARD"
    DISCARD_CARDS = "DISCARD_CARDS"
    DISCARD_HAND_DRAW_SEVEN = "DISCARD_HAND_DRAW_SEVEN"
    GAIN_LIFE = "GAIN_LIFE"
    LOSE_LIFE = "LOSE_LIFE"
    ADD_MANA = "ADD_MANA"
    ADD_MANA_PER_TAPPED_LANDS = "ADD_MANA_PER_TAPPED_LANDS"
    ADD_MANA_PER_ELF = "ADD_MANA_PER_ELF"
    CREATE_TOKEN = "CREATE_TOKEN"
    RETURN_TO_HAND = "RETURN_TO_HAND"
    RETURN_FROM_GRAVEYARD_TO_HAND = "RETURN_FROM_GRAVEYARD_TO_HAND"
    RETURN_FROM_GRAVEYARD_TO_BATTLEFIELD_TAPPED = "RETURN_FROM_GRAVEYARD_TO_BATTLEFIELD_TAPPED"
    SEARCH_BASIC_LAND_TO_BATTLEFIELD_TAPPED = "SEARCH_BASIC_LAND_TO_BATTLEFIELD_TAPPED"
    SEARCH_BASIC_PLAINS_TO_HAND = "SEARCH_BASIC_PLAINS_TO_HAND"
    LOOK_AT_TOP_N_PUT_ONE_IN_HAND_REST_BOTTOM = "LOOK_AT_TOP_N_PUT_ONE_IN_HAND_REST_BOTTOM"
    LOOK_AT_TOP_N_PUT_LAND_TO_BATTLEFIELD_REST_BOTTOM_RANDOM = "LOOK_AT_TOP_N_PUT_LAND_TO_BATTLEFIELD_REST_BOTTOM_RANDOM"
    REVEAL_TOP_N_PUT_ALL_TYPE_TO_HAND_REST_BOTTOM = "REVEAL_TOP_N_PUT_ALL_TYPE_TO_HAND_REST_BOTTOM"
    FACT_OR_FICTION = "FACT_OR_FICTION"
    PUT_COUNTERS = "PUT_COUNTERS"
    SACRIFICE_TARGET = "SACRIFICE_TARGET"
    EACH_PLAYER_SACRIFICE = "EACH_PLAYER_SACRIFICE"
    EACH_PLAYER_DRAWS = "EACH_PLAYER_DRAWS"
    TAKE_EXTRA_TURN = "TAKE_EXTRA_TURN"
    SCRY = "SCRY"
    GOAD = "GOAD"
    RAM_THROUGH = "RAM_THROUGH"
    CREATURE_DEALS_DAMAGE_TO_CREATURE = "CREATURE_DEALS_DAMAGE_TO_CREATURE"
    DISCARD_HAND_DRAW_EQUAL_DAMAGE = "DISCARD_HAND_DRAW_EQUAL_DAMAGE"
    CAST_FROM_OPPONENT_GRAVEYARD = "CAST_FROM_OPPONENT_GRAVEYARD"
    ATTACH_EQUIPMENT = "ATTACH_EQUIPMENT"
    ATTACH_ALL_EQUIPMENT = "ATTACH_ALL_EQUIPMENT"
    ATTACH_AURA = "ATTACH_AURA"
    RETURN_TWO_DIFFERENT_CONTROLLERS = "RETURN_TWO_DIFFERENT_CONTROLLERS"
    ADDENDUM_SCRY_DRAW = "ADDENDUM_SCRY_DRAW"
    SET_BASE_P_T = "SET_BASE_P_T"
    ADD_SUBTYPE = "ADD_SUBTYPE"

    # Continuous/static effects
    MODIFY_P_T = "MODIFY_P_T"
    ADD_KEYWORD = "ADD_KEYWORD"
    REMOVE_KEYWORD = "REMOVE_KEYWORD"
    CANT_ATTACK_PLAYER = "CANT_ATTACK_PLAYER"
    ATTACK_TAX = "ATTACK_TAX"
    REQUIRE_ATTACK = "REQUIRE_ATTACK"
    REQUIRE_BLOCK = "REQUIRE_BLOCK"
    PREVENT_COMBAT_DAMAGE = "PREVENT_COMBAT_DAMAGE"
    COST_REDUCTION = "COST_REDUCTION"
    EQUIPPED_ONLY = "EQUIPPED_ONLY"
    CONTROLLED_TYPE_LORD = "CONTROLLED_TYPE_LORD"
    OTHER_CONTROLLED_TYPE_LORD = "OTHER_CONTROLLED_TYPE_LORD"
    OTHER_CONTROLLED_BUFF_PER_ATTACHMENT = "OTHER_CONTROLLED_BUFF_PER_ATTACHMENT"
    ASSIGN_DAMAGE_AS_UNBLOCKED = "ASSIGN_DAMAGE_AS_UNBLOCKED"
    TEAM_BUFF = "TEAM_BUFF"

    # Modal or choice
    MODAL = "MODAL"
    VOTE = "VOTE"


class TriggerType(str, Enum):
    ETB = "ETB"
    DIES = "DIES"
    ATTACKS = "ATTACKS"
    ATTACKS_OR_BLOCKS = "ATTACKS_OR_BLOCKS"
    EQUIPPED_CREATURE_ATTACKS = "EQUIPPED_CREATURE_ATTACKS"
    COMBAT_DAMAGE_TO_PLAYER = "COMBAT_DAMAGE_TO_PLAYER"
    DEALT_DAMAGE = "DEALT_DAMAGE"
    BECOMES_TARGET = "BECOMES_TARGET"
    UPKEEP = "UPKEEP"
    END_STEP = "END_STEP"
    YOU_LOSE_LIFE = "YOU_LOSE_LIFE"
    CAST_SPELL = "CAST_SPELL"
    CREATURE_ENTERS = "CREATURE_ENTERS"
    OTHER_FRIENDLY_DIES = "OTHER_FRIENDLY_DIES"
    OTHER_DIES_DURING_YOUR_TURN = "OTHER_DIES_DURING_YOUR_TURN"


class CostType(str, Enum):
    MANA = "MANA"
    TAP = "TAP"
    SACRIFICE_SELF = "SACRIFICE_SELF"
    SACRIFICE_CREATURE = "SACRIFICE_CREATURE"
    SACRIFICE_OTHER_CREATURE = "SACRIFICE_OTHER_CREATURE"
    DISCARD_CARD = "DISCARD_CARD"
    PAY_LIFE = "PAY_LIFE"


class TimingRestriction(str, Enum):
    ANYTIME = "ANYTIME"
    SORCERY_SPEED = "SORCERY_SPEED"
    ONLY_WHEN_ATTACKING = "ONLY_WHEN_ATTACKING"


class Zone(str, Enum):
    BATTLEFIELD = "BATTLEFIELD"
    PLAYER = "PLAYER"
    STACK = "STACK"
    GRAVEYARD = "GRAVEYARD"
    ANY = "ANY"


class Selector(str, Enum):
    ANY_CREATURE = "ANY_CREATURE"
    ANY_PLAYER = "ANY_PLAYER"
    ANY_TARGET = "ANY_TARGET"
    ANY_PERMANENT = "ANY_PERMANENT"
    TARGET_CREATURE = "TARGET_CREATURE"
    TARGET_FRIENDLY_CREATURE = "TARGET_FRIENDLY_CREATURE"
    TARGET_OPPONENT_CREATURE = "TARGET_OPPONENT_CREATURE"
    TARGET_NON_BLACK_CREATURE = "TARGET_NON_BLACK_CREATURE"
    TARGET_FLYING_CREATURE = "TARGET_FLYING_CREATURE"
    TARGET_ARTIFACT = "TARGET_ARTIFACT"
    TARGET_ENCHANTMENT = "TARGET_ENCHANTMENT"
    TARGET_PERMANENT = "TARGET_PERMANENT"
    TARGET_SPELL = "TARGET_SPELL"
    TARGET_PLAYER = "TARGET_PLAYER"
    TARGET_OPPONENT_PLAYER = "TARGET_OPPONENT_PLAYER"
    TARGET_CARD_GRAVEYARD = "TARGET_CARD_GRAVEYARD"
    TARGET_CREATURE_YOU_CONTROL = "TARGET_CREATURE_YOU_CONTROL"
    TARGET_CREATURE_OPPONENT_CONTROLS = "TARGET_CREATURE_OPPONENT_CONTROLS"
    TARGET_ATTACKING_CREATURE = "TARGET_ATTACKING_CREATURE"
    TARGET_EQUIPPED_CREATURE = "TARGET_EQUIPPED_CREATURE"
    TARGET_ENCHANTED_CREATURE = "TARGET_ENCHANTED_CREATURE"


class LandType(str, Enum):
    PLAINS = "PLAINS"
    ISLAND = "ISLAND"
    SWAMP = "SWAMP"
    MOUNTAIN = "MOUNTAIN"
    FOREST = "FOREST"


# ============================
# Core Types
# ============================

@dataclass(frozen=True)
class ManaCost:
    generic: int
    colored: Dict[Color, int]
    x: int = 0

    def validate(self) -> None:
        if self.generic < 0:
            raise ValueError("ManaCost.generic must be >= 0")
        if self.x < 0:
            raise ValueError("ManaCost.x must be >= 0")
        for c, n in self.colored.items():
            if not isinstance(c, Color):
                raise ValueError(f"ManaCost.colored key must be Color, got {c!r}")
            if n <= 0:
                raise ValueError(f"ManaCost.colored[{c}] must be > 0")


@dataclass(frozen=True)
class AbilityCost:
    type: CostType
    amount: Optional[Any] = None


@dataclass
class CreatureStats:
    base_power: int
    base_toughness: int
    counters: Dict[str, int]  # keys: "+1/+1", "-1/-1"

    def validate(self) -> None:
        if self.base_power < 0 or self.base_toughness < 0:
            raise ValueError("CreatureStats base stats must be >= 0")
        for k in ("+1/+1", "-1/-1"):
            if k not in self.counters:
                raise ValueError(f"CreatureStats.counters missing key {k!r}")
            if self.counters[k] < 0:
                raise ValueError(f"CreatureStats.counters[{k!r}] must be >= 0")

    def effective_pt(self) -> Tuple[int, int]:
        plus = self.counters.get("+1/+1", 0)
        minus = self.counters.get("-1/-1", 0)
        p = self.base_power + plus - minus
        t = self.base_toughness + plus - minus
        return p, t


@dataclass(frozen=True)
class TargetSpec:
    zone: Zone
    selector: Selector

    def validate(self) -> None:
        if not isinstance(self.zone, Zone):
            raise ValueError("TargetSpec.zone must be Zone")
        if not isinstance(self.selector, Selector):
            raise ValueError("TargetSpec.selector must be Selector")


@dataclass(frozen=True)
class Effect:
    type: EffectType
    params: Dict[str, Any]


@dataclass(frozen=True)
class StaticAbility:
    effects: List[Effect]
    condition: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class TriggeredAbility:
    trigger: TriggerType
    effects: List[Effect]
    condition: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ActivatedAbility:
    costs: List[AbilityCost]
    effects: List[Effect]
    timing: TimingRestriction = TimingRestriction.ANYTIME
    zone: Zone = Zone.BATTLEFIELD


@dataclass
class RulesBlock:
    keywords: Set[Keyword] = field(default_factory=set)
    effects: List[Effect] = field(default_factory=list)  # spell effects
    static_abilities: List[StaticAbility] = field(default_factory=list)
    triggered_abilities: List[TriggeredAbility] = field(default_factory=list)
    activated_abilities: List[ActivatedAbility] = field(default_factory=list)
    additional_costs: List[AbilityCost] = field(default_factory=list)
    alternate_costs: List[Dict[str, Any]] = field(default_factory=list)
    flashback_cost: Optional[ManaCost] = None
    cast_from_graveyard_costs: List[AbilityCost] = field(default_factory=list)


@dataclass(frozen=True)
class LandStats:
    produces: Dict[Color, int]
    land_types: Set[LandType]

    def validate(self) -> None:
        for c, n in self.produces.items():
            if not isinstance(c, Color):
                raise ValueError("LandStats.produces keys must be Color")
            if not isinstance(n, int) or n <= 0:
                raise ValueError("LandStats.produces values must be int > 0")
        for lt in self.land_types:
            if not isinstance(lt, LandType):
                raise ValueError("LandStats.land_types must contain LandType values")


@dataclass(frozen=True)
class EquipmentStats:
    equip_cost: ManaCost


@dataclass(frozen=True)
class AuraStats:
    enchant_target: TargetSpec


@dataclass
class Card:
    id: str
    name: str
    mana_cost: ManaCost
    card_types: Set[CardType]
    subtypes: Set[str]
    colors: Set[Color]
    rules: RulesBlock

    creature_stats: Optional[CreatureStats] = None
    land_stats: Optional[LandStats] = None
    equipment_stats: Optional[EquipmentStats] = None
    aura_stats: Optional[AuraStats] = None
    raw_oracle_text: Optional[str] = None

    def has_type(self, t: CardType) -> bool:
        return t in self.card_types

    @property
    def card_type(self) -> CardType:
        # Back-compat shim for older engine code.
        if CardType.INSTANT in self.card_types:
            return CardType.INSTANT
        if CardType.SORCERY in self.card_types:
            return CardType.SORCERY
        if CardType.CREATURE in self.card_types:
            return CardType.CREATURE
        if CardType.ARTIFACT in self.card_types:
            return CardType.ARTIFACT
        if CardType.ENCHANTMENT in self.card_types:
            return CardType.ENCHANTMENT
        if CardType.LAND in self.card_types:
            return CardType.LAND
        return next(iter(self.card_types))

    def validate(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Card.id must be a non-empty string")
        if not self.name or not isinstance(self.name, str):
            raise ValueError("Card.name must be a non-empty string")
        if not isinstance(self.mana_cost, ManaCost):
            raise ValueError("Card.mana_cost must be ManaCost")
        self.mana_cost.validate()
        if not self.card_types:
            raise ValueError("Card.card_types must be non-empty")
        for ct in self.card_types:
            if not isinstance(ct, CardType):
                raise ValueError("Card.card_types must contain CardType")
        for c in self.colors:
            if not isinstance(c, Color):
                raise ValueError("Card.colors must contain Color")
        if not isinstance(self.rules, RulesBlock):
            raise ValueError("Card.rules must be RulesBlock")

        # Type checks
        if CardType.CREATURE in self.card_types:
            if self.creature_stats is None:
                raise ValueError("Creature card must have creature_stats")
            self.creature_stats.validate()
        if CardType.LAND in self.card_types:
            if self.land_stats is None:
                raise ValueError("Land card must have land_stats")
            self.land_stats.validate()


# ============================
# Parsing helpers
# ============================

_COLOR_MAP = {
    "W": Color.WHITE,
    "U": Color.BLUE,
    "B": Color.BLACK,
    "R": Color.RED,
    "G": Color.GREEN,
}

_KEYWORD_MAP = {
    "flying": Keyword.FLYING,
    "vigilance": Keyword.VIGILANCE,
    "double strike": Keyword.DOUBLE_STRIKE,
    "first strike": Keyword.FIRST_STRIKE,
    "haste": Keyword.HASTE,
    "lifelink": Keyword.LIFELINK,
    "deathtouch": Keyword.DEATHTOUCH,
    "trample": Keyword.TRAMPLE,
    "reach": Keyword.REACH,
    "flash": Keyword.FLASH,
    "menace": Keyword.MENACE,
    "hexproof": Keyword.HEXPROOF,
    "defender": Keyword.DEFENDER,
    "indestructible": Keyword.INDESTRUCTIBLE,
}

_ALIAS_CARD_IDS = {
    "plains": "basic_plains",
    "island": "basic_island",
    "swamp": "basic_swamp",
    "mountain": "basic_mountain",
    "forest": "basic_forest",
}


def _strip_reminder_text(line: str) -> str:
    if "(" in line and line.endswith(")"):
        return line[: line.rfind("(")].strip()
    return line


def _parse_mana_cost_str(text: Optional[str]) -> ManaCost:
    if not text:
        return ManaCost(generic=0, colored={})

    raw = text.strip()
    if raw.startswith("{"):
        tokens = re.findall(r"\{([^}]+)\}", raw)
    else:
        # compact form like 1WW
        tokens = []
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch.isdigit():
                j = i
                while j < len(raw) and raw[j].isdigit():
                    j += 1
                tokens.append(raw[i:j])
                i = j
                continue
            tokens.append(ch)
            i += 1

    generic = 0
    colored: Dict[Color, int] = {}
    x_count = 0
    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        if t.isdigit():
            generic += int(t)
            continue
        if t.upper() == "X":
            x_count += 1
            continue
        if t.upper() in _COLOR_MAP:
            c = _COLOR_MAP[t.upper()]
            colored[c] = colored.get(c, 0) + 1
            continue
        # Ignore unsupported symbols (hybrid, phyrexian) for phase-1
        raise ValueError(f"Unsupported mana symbol: {t}")

    mc = ManaCost(generic=generic, colored=colored, x=x_count)
    mc.validate()
    return mc


def _parse_type_line(type_line: str) -> Tuple[Set[CardType], Set[str]]:
    if not type_line:
        return set(), set()
    parts = type_line.split("—")
    left = parts[0].strip()
    right = parts[1].strip() if len(parts) > 1 else ""

    types: Set[CardType] = set()
    subtypes: Set[str] = set()

    for token in left.replace("—", " ").split():
        t = token.strip()
        if not t or t.lower() in {"basic", "legendary", "snow"}:
            continue
        upper = t.upper()
        if upper in CardType.__members__:
            types.add(CardType[upper])

    for st in right.split():
        if st:
            subtypes.add(st.strip())

    return types, subtypes


def _parse_color_list(values: List[str]) -> Set[Color]:
    out: Set[Color] = set()
    for v in values or []:
        c = _COLOR_MAP.get(v.upper())
        if c:
            out.add(c)
    return out


# ----------------------------
# Oracle parsing
# ----------------------------

@dataclass
class ParsedRules:
    rules: RulesBlock
    equipment_stats: Optional[EquipmentStats]
    aura_stats: Optional[AuraStats]
    unparsed_lines: List[str]


def _parse_keywords_line(line: str) -> Optional[Set[Keyword]]:
    cleaned = _strip_reminder_text(line).strip()
    if not cleaned:
        return None
    parts = [p.strip().lower() for p in cleaned.split(",")]
    if not parts:
        return None
    keywords: Set[Keyword] = set()
    for part in parts:
        if part in _KEYWORD_MAP:
            keywords.add(_KEYWORD_MAP[part])
        else:
            return None
    return keywords if keywords else None


def _parse_cost_chunk(chunk: str) -> AbilityCost:
    c = chunk.strip()
    if c == "{T}":
        return AbilityCost(CostType.TAP)
    if c.lower().startswith("pay ") and c.lower().endswith(" life"):
        amount = int(c.split()[1])
        return AbilityCost(CostType.PAY_LIFE, amount)
    if c.lower() == "discard a card":
        return AbilityCost(CostType.DISCARD_CARD, 1)
    if c.lower() == "sacrifice a creature":
        return AbilityCost(CostType.SACRIFICE_CREATURE, 1)
    if c.lower() == "sacrifice another creature":
        return AbilityCost(CostType.SACRIFICE_OTHER_CREATURE, 1)
    if c.lower() == "sacrifice this token":
        return AbilityCost(CostType.SACRIFICE_SELF, 1)
    if c.startswith("{"):
        return AbilityCost(CostType.MANA, _parse_mana_cost_str(c))
    raise ValueError(f"Unsupported cost chunk: {chunk}")


def _parse_costs(cost_text: str) -> List[AbilityCost]:
    chunks = [c.strip() for c in cost_text.split(",") if c.strip()]
    return [_parse_cost_chunk(c) for c in chunks]


def _target_spec_from_phrase(phrase: str) -> TargetSpec:
    p = phrase.strip().lower()
    if p == "any target":
        return TargetSpec(Zone.ANY, Selector.ANY_TARGET)
    if p == "target creature":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_CREATURE)
    if p == "target creature an opponent controls":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_OPPONENT_CREATURE)
    if p == "target creature you control":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_CREATURE_YOU_CONTROL)
    if p == "target creature you don't control":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_CREATURE_OPPONENT_CONTROLS)
    if p == "target nonblack creature":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_NON_BLACK_CREATURE)
    if p == "target creature with flying":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_FLYING_CREATURE)
    if p == "target permanent":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_PERMANENT)
    if p == "target artifact":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ARTIFACT)
    if p == "target enchantment":
        return TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTMENT)
    if p == "target spell":
        return TargetSpec(Zone.STACK, Selector.TARGET_SPELL)
    if p == "target player":
        return TargetSpec(Zone.PLAYER, Selector.TARGET_PLAYER)
    if p == "target opponent":
        return TargetSpec(Zone.PLAYER, Selector.TARGET_OPPONENT_PLAYER)
    raise ValueError(f"Unsupported target phrase: {phrase}")



def _parse_effect_line(line: str) -> List[Effect]:
    # Returns a list of effects (some lines expand to multiple)
    text = line.strip()
    lower = text.lower()

    # Card draw / discard / life
    if lower == 'draw a card.':
        return [Effect(EffectType.DRAW_CARDS, {'amount': 1})]
    if lower == 'draw two cards.':
        return [Effect(EffectType.DRAW_CARDS, {'amount': 2})]
    if lower == 'draw three cards.':
        return [Effect(EffectType.DRAW_CARDS, {'amount': 3})]
    if lower == 'draw two cards and create a treasure token. (it\'s an artifact with "{t}, sacrifice this token: add one mana of any color.")':
        return [Effect(EffectType.DRAW_CARDS, {'amount': 2}), Effect(EffectType.CREATE_TOKEN, {'token': 'TREASURE', 'count': 1})]
    if lower == 'draw x cards, then discard a card.':
        return [Effect(EffectType.DRAW_X_THEN_DISCARD, {})]
    if lower == 'you draw a card and you lose 1 life.':
        return [Effect(EffectType.DRAW_CARDS, {'amount': 1}), Effect(EffectType.LOSE_LIFE, {'amount': 1})]
    if lower == 'you lose 2 life.':
        return [Effect(EffectType.LOSE_LIFE, {'amount': 2})]
    if lower == 'target player draws two cards and loses 2 life.':
        return [
            Effect(EffectType.DRAW_CARDS, {'amount': 2, 'target': _target_spec_from_phrase('target player')}),
            Effect(EffectType.LOSE_LIFE, {'amount': 2, 'target': _target_spec_from_phrase('target player')}),
        ]
    if lower == 'each player draws a card.':
        return [Effect(EffectType.EACH_PLAYER_DRAWS, {'amount': 1})]
    if lower == 'each player sacrifices a creature of their choice.':
        return [Effect(EffectType.EACH_PLAYER_SACRIFICE, {})]
    if lower == "you gain life equal to the sacrificed creature's toughness. draw a card.":
        return [Effect(EffectType.GAIN_LIFE, {'amount': 'SACRIFICED_TOUGHNESS'}), Effect(EffectType.DRAW_CARDS, {'amount': 1})]

    # Mana
    if lower == 'add {r} for each tapped land your opponents control.':
        return [Effect(EffectType.ADD_MANA_PER_TAPPED_LANDS, {'color': 'R'})]
    if lower == 'add {g} for each elf you control.':
        return [Effect(EffectType.ADD_MANA_PER_ELF, {'color': 'G'})]
    if lower == 'add {g}{g}{g}.':
        return [Effect(EffectType.ADD_MANA, {'mana': 'GGG'})]
    if lower == 'add {g}.':
        return [Effect(EffectType.ADD_MANA, {'mana': 'G'})]
    if lower == 'add {u}.':
        return [Effect(EffectType.ADD_MANA, {'mana': 'U'})]
    if lower == 'add {w}.':
        return [Effect(EffectType.ADD_MANA, {'mana': 'W'})]
    if lower == 'add {r}.':
        return [Effect(EffectType.ADD_MANA, {'mana': 'R'})]
    if lower == 'add {b}.':
        return [Effect(EffectType.ADD_MANA, {'mana': 'B'})]

    # Damage / removal
    if lower == 'lightning bolt deals 3 damage to any target.':
        return [Effect(EffectType.DEAL_DAMAGE, {'amount': 3, 'target': _target_spec_from_phrase('any target')})]
    if lower == 'abrade deals 3 damage to target creature.':
        return [Effect(EffectType.DEAL_DAMAGE, {'amount': 3, 'target': _target_spec_from_phrase('target creature')})]
    if lower == 'blaze deals x damage to any target.':
        return [Effect(EffectType.DEAL_DAMAGE, {'amount': 'X', 'target': _target_spec_from_phrase('any target')})]
    if lower == 'destroy target nonblack creature.':
        return [Effect(EffectType.DESTROY_CREATURE, {'target': _target_spec_from_phrase('target nonblack creature')})]
    if lower == 'destroy target creature with flying.':
        return [Effect(EffectType.DESTROY_FLYING_CREATURE, {'target': _target_spec_from_phrase('target creature with flying')})]
    if lower == 'destroy target artifact, enchantment, or creature with flying.':
        return [Effect(EffectType.DESTROY_PERMANENT, {'targets_any_of': [
            _target_spec_from_phrase('target artifact'),
            _target_spec_from_phrase('target enchantment'),
            _target_spec_from_phrase('target creature with flying'),
        ]})]
    if lower == 'destroy target artifact.':
        return [Effect(EffectType.DESTROY_ARTIFACT, {'target': _target_spec_from_phrase('target artifact')})]
    if lower == 'destroy target creature with toughness 4 or greater.':
        return [Effect(EffectType.DESTROY_CREATURE, {'target': _target_spec_from_phrase('target creature'), 'min_toughness': 4})]
    if lower == 'destroy target creature an opponent controls.':
        return [Effect(EffectType.DESTROY_CREATURE, {'target': _target_spec_from_phrase('target creature an opponent controls')})]
    if lower == 'destroy target permanent.':
        return [Effect(EffectType.DESTROY_PERMANENT, {'target': _target_spec_from_phrase('target permanent')})]
    if lower == 'exile target creature. its controller gains life equal to its power.':
        return [Effect(EffectType.EXILE_CREATURE, {'target': _target_spec_from_phrase('target creature'), 'gain_life_equal_power': True})]
    if lower == 'exile target creature. its controller may search their library for a basic land card, put that card onto the battlefield tapped, then shuffle.':
        return [Effect(EffectType.SEARCH_BASIC_LAND_TO_BATTLEFIELD_TAPPED, {'target': _target_spec_from_phrase('target creature')})]
    if lower == 'exile target creature an opponent controls until this creature leaves the battlefield.':
        return [Effect(EffectType.EXILE_TARGET_UNTIL, {'target': _target_spec_from_phrase('target creature an opponent controls'), 'until': 'SOURCE_LEAVES'})]

    # Counter / copy / vote
    if lower == 'counter target spell.':
        return [Effect(EffectType.COUNTER_SPELL, {'target': _target_spec_from_phrase('target spell')})]
    if lower == 'counter target spell unless its controller pays {3}.':
        return [Effect(EffectType.COUNTER_SPELL, {'target': _target_spec_from_phrase('target spell'), 'unless_pay': _parse_mana_cost_str('{3}')})]
    if lower.startswith('will of the council —'):
        if 'denial' in lower and 'duplication' in lower:
            return [Effect(EffectType.VOTE, {'type': 'SPLIT_DECISION', 'target': _target_spec_from_phrase('target spell')})]
        if 'time' in lower and 'knowledge' in lower:
            return [Effect(EffectType.VOTE, {'type': 'PLEA_FOR_POWER'})]
        raise ValueError(f'Unsupported vote effect: {line}')

    # Bounce / return
    if lower == "return target creature to its owner's hand.":
        return [Effect(EffectType.RETURN_TO_HAND, {'target': _target_spec_from_phrase('target creature')})]
    if lower == 'return target card from your graveyard to your hand.':
        return [Effect(EffectType.RETURN_FROM_GRAVEYARD_TO_HAND, {'target': TargetSpec(Zone.GRAVEYARD, Selector.TARGET_CARD_GRAVEYARD)})]
    if lower == 'return target creature card from your graveyard to the battlefield tapped.':
        return [Effect(EffectType.RETURN_FROM_GRAVEYARD_TO_BATTLEFIELD_TAPPED, {'target': TargetSpec(Zone.GRAVEYARD, Selector.TARGET_CARD_GRAVEYARD)})]
    if lower == 'return this card from your graveyard to the battlefield tapped.':
        return [Effect(EffectType.RETURN_FROM_GRAVEYARD_TO_BATTLEFIELD_TAPPED, {'target': 'SELF'})]
    if lower == 'return this card from your graveyard to your hand.':
        return [Effect(EffectType.RETURN_FROM_GRAVEYARD_TO_HAND, {'target': 'SELF'})]
    if lower == "choose two target creatures controlled by different players. return those creatures to their owners' hands.":
        return [Effect(EffectType.RETURN_TWO_DIFFERENT_CONTROLLERS, {'targets': 2})]

    # Tokens
    if lower == 'create a 1/1 blue bird illusion creature token with flying.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'BIRD_ILLUSION_1_1_FLYING', 'count': 1})]
    if lower == 'create a 1/1 green elf warrior creature token for each elf you control.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'ELF_WARRIOR_1_1', 'count': 'PER_ELF_YOU_CONTROL'})]
    if lower == 'create a 1/1 green elf warrior creature token.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'ELF_WARRIOR_1_1', 'count': 1})]
    if lower == 'create a 1/1 white soldier creature token, then attach this equipment to it.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'SOLDIER_1_1', 'count': 1, 'attach_equipment': True})]
    if lower == 'create a 2/2 black zombie creature token.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'ZOMBIE_2_2', 'count': 1})]
    if lower == 'create two 2/2 blue drake creature tokens with flying.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'DRAKE_2_2_FLYING', 'count': 2})]
    if lower == 'create two 2/2 black zombie creature tokens.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'ZOMBIE_2_2', 'count': 2})]
    if lower == 'create three 1/1 white soldier creature tokens.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'SOLDIER_1_1', 'count': 3})]
    if lower == 'create a 4/4 white angel creature token with flying.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'ANGEL_4_4_FLYING', 'count': 1})]
    if lower == 'create a 5/5 black demon creature token with flying.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'DEMON_5_5_FLYING', 'count': 1})]
    if lower == 'create a 2/2 red dragon creature token with flying and "{r}: this token gets +1/+0 until end of turn."':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'DRAGON_2_2_FLYING_FIREBREATH', 'count': 1})]
    if lower == "create two treasure tokens. (they're artifacts with \"{t}, sacrifice this token: add one mana of any color.\")":
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'TREASURE', 'count': 2})]

    # Searching / reveal / look
    if lower == 'look at the top four cards of your library. put one of them into your hand and the rest on the bottom of your library in any order.':
        return [Effect(EffectType.LOOK_AT_TOP_N_PUT_ONE_IN_HAND_REST_BOTTOM, {'n': 4})]
    if lower == 'look at the top two cards of your library. put one of them into your hand and the other on the bottom of your library.':
        return [Effect(EffectType.LOOK_AT_TOP_N_PUT_ONE_IN_HAND_REST_BOTTOM, {'n': 2})]
    if lower == 'look at the top five cards of your library. you may put a land card from among them onto the battlefield tapped. put the rest on the bottom of your library in a random order.':
        return [Effect(EffectType.LOOK_AT_TOP_N_PUT_LAND_TO_BATTLEFIELD_REST_BOTTOM_RANDOM, {'n': 5})]
    if lower == 'reveal the top four cards of your library. put all elf cards revealed this way into your hand and the rest on the bottom of your library in any order.':
        return [Effect(EffectType.REVEAL_TOP_N_PUT_ALL_TYPE_TO_HAND_REST_BOTTOM, {'n': 4, 'subtype': 'Elf'})]
    if lower == 'reveal the top five cards of your library. an opponent separates those cards into two piles. put one pile into your hand and the other into your graveyard.':
        return [Effect(EffectType.FACT_OR_FICTION, {'n': 5})]
    if lower == 'you may search your library for a basic plains card, reveal it, put it into your hand, then shuffle.':
        return [Effect(EffectType.SEARCH_BASIC_PLAINS_TO_HAND, {})]

    # Pumps / keywords
    if lower == 'target creature gets +2/+2 until end of turn.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 2}, 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')})]
    if lower == 'target creature gets +2/+2 until end of turn. if you control an equipment, create a 1/1 white human soldier creature token.':
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 2}, 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')}),
            Effect(EffectType.CREATE_TOKEN, {'token': 'HUMAN_SOLDIER_1_1', 'count': 1, 'condition': 'CONTROL_EQUIPMENT'}),
        ]
    if lower == 'target creature gets +4/+4 until end of turn.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 4, 'toughness': 4}, 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')})]
    if lower == 'target creature gets -1/-1 until end of turn.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': -1, 'toughness': -1}, 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')})]
    if lower == 'target creature gains haste until end of turn. (it can attack and {t} this turn.)':
        return [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.HASTE.value, 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')})]
    if lower == 'it gains haste until end of turn.':
        return [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.HASTE.value, 'duration': 'EOT', 'target': 'TRIGGER_SOURCE'})]
    if lower == "target creature gains indestructible until end of turn. (damage and effects that say \"destroy\" don't destroy it.)":
        return [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.INDESTRUCTIBLE.value, 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')})]
    if lower == 'target creature gets +x/+x until end of turn, where x is the number of elves on the battlefield.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': 'COUNT_ELVES', 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')})]
    if lower == "target creature you control deals damage equal to its power to target creature you don't control.":
        return [Effect(EffectType.CREATURE_DEALS_DAMAGE_TO_CREATURE, {'source': _target_spec_from_phrase('target creature you control'), 'target': _target_spec_from_phrase("target creature you don't control")})]
    if lower == "target creature you control deals damage equal to its power to target creature you don't control. if the creature you control has trample, excess damage is dealt to that creature's controller instead.":
        return [Effect(EffectType.RAM_THROUGH, {'source': _target_spec_from_phrase('target creature you control'), 'target': _target_spec_from_phrase("target creature you don't control")})]
    if lower == "until end of turn, target creature gets +2/+0 and gains \"when this creature dies, return it to the battlefield tapped under its owner's control.\"":
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 0}, 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': 'UNDEAD_RETURN', 'duration': 'EOT', 'target': _target_spec_from_phrase('target creature')}),
        ]
    if lower == 'this creature gets +1/+0 until end of turn.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 1, 'toughness': 0}, 'duration': 'EOT', 'target': 'SELF'})]
    if lower == 'this creature gets +4/+4 until end of turn.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 4, 'toughness': 4}, 'duration': 'EOT', 'target': 'SELF'})]

    # Team and global effects
    if lower == "creatures you control get +3/+3 and gain trample until end of turn. (each of those creatures can deal excess combat damage to the player or planeswalker it's attacking.)":
        return [Effect(EffectType.TEAM_BUFF, {'amount': {'power': 3, 'toughness': 3}, 'keywords': [Keyword.TRAMPLE.value], 'duration': 'EOT', 'controller': 'YOU'})]
    if lower == 'other creatures you control get +2/+2 and gain vigilance and trample until end of turn.':
        return [Effect(EffectType.TEAM_BUFF, {'amount': {'power': 2, 'toughness': 2}, 'keywords': [Keyword.VIGILANCE.value, Keyword.TRAMPLE.value], 'duration': 'EOT', 'controller': 'YOU', 'exclude_source': True})]
    if lower == 'all zombies gain menace until end of turn.':
        return [Effect(EffectType.TEAM_BUFF, {'keywords': [Keyword.MENACE.value], 'duration': 'EOT', 'subtype': 'Zombie'})]
    if lower == 'creatures your opponents control attack each combat if able.':
        return [Effect(EffectType.REQUIRE_ATTACK, {'controller': 'OPPONENTS'})]

    # Static continuous text (equipment/auras/lords)
    if lower == 'other elf creatures you control get +1/+1.':
        return [Effect(EffectType.OTHER_CONTROLLED_TYPE_LORD, {'subtype': 'Elf', 'amount': {'power': 1, 'toughness': 1}})]
    if lower == 'other soldier creatures you control get +1/+1 and have vigilance.':
        return [Effect(EffectType.OTHER_CONTROLLED_TYPE_LORD, {'subtype': 'Soldier', 'amount': {'power': 1, 'toughness': 1}, 'keywords': [Keyword.VIGILANCE.value]})]
    if lower == 'other zombies you control get +1/+1.':
        return [Effect(EffectType.OTHER_CONTROLLED_TYPE_LORD, {'subtype': 'Zombie', 'amount': {'power': 1, 'toughness': 1}})]
    if lower == 'zombies you control get +1/+1.':
        return [Effect(EffectType.CONTROLLED_TYPE_LORD, {'subtype': 'Zombie', 'amount': {'power': 1, 'toughness': 1}})]
    if lower == 'other creatures you control get +1/+1 for each aura and equipment attached to this creature.':
        return [Effect(EffectType.OTHER_CONTROLLED_BUFF_PER_ATTACHMENT, {'amount_per_attachment': {'power': 1, 'toughness': 1}})]
    if lower == 'dragon creatures you control get +3/+3.':
        return [Effect(EffectType.CONTROLLED_TYPE_LORD, {'subtype': 'Dragon', 'amount': {'power': 3, 'toughness': 3}})]
    if lower == 'dragon spells you cast cost {2} less to cast.':
        return [Effect(EffectType.COST_REDUCTION, {'amount': 2, 'spell_subtype': 'Dragon'})]
    if lower == 'dragon spells you cast cost {1} less to cast.':
        return [Effect(EffectType.COST_REDUCTION, {'amount': 1, 'spell_subtype': 'Dragon'})]
    if lower == 'aura and equipment spells you cast cost {1} less to cast.':
        return [Effect(EffectType.COST_REDUCTION, {'amount': 1, 'spell_tags': ['AURA', 'EQUIPMENT']})]
    if lower == 'equipped creature gets +2/+1.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 1}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)})]
    if lower == 'equipped creature gets +3/+0.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 3, 'toughness': 0}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)})]
    if lower == 'equipped creature gets +6/+6.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 6, 'toughness': 6}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)})]
    if lower == 'equipped creature gets +10/+10 and loses flying.':
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 10, 'toughness': 10}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
            Effect(EffectType.REMOVE_KEYWORD, {'keyword': Keyword.FLYING.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
        ]
    if lower == 'equipped creature gets +2/+0 and has first strike, vigilance, trample, and haste.':
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 0}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.FIRST_STRIKE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.VIGILANCE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.TRAMPLE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.HASTE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
        ]
    if lower == 'equipped creature gets +2/+0 and is goaded. (it attacks each combat if able and attacks a player other than you if able.)':
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 0}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
            Effect(EffectType.GOAD, {'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)}),
        ]
    if lower == "enchanted creature gets +2/+2, has first strike, and can't attack you or planeswalkers you control.":
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 2}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.FIRST_STRIKE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.CANT_ATTACK_PLAYER, {'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
        ]
    if lower == "enchanted creature gets +2/+2, has flying, and can't attack you or planeswalkers you control.":
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 2}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.FLYING.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.CANT_ATTACK_PLAYER, {'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
        ]
    if lower == "enchanted creature gets +2/+2, has menace, and can't attack you or planeswalkers you control. (it can't be blocked except by two or more creatures.)":
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 2}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.MENACE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.CANT_ATTACK_PLAYER, {'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
        ]
    if lower == "enchanted creature gets +2/+2, has vigilance, and can't attack you or planeswalkers you control.":
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 2, 'toughness': 2}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.VIGILANCE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.CANT_ATTACK_PLAYER, {'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
        ]
    if lower == 'enchanted creature gets +3/+1, has flying, and is a demon in addition to its other types.':
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 3, 'toughness': 1}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.FLYING.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.ADD_SUBTYPE, {'subtype': 'Demon', 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
        ]
    if lower == "enchanted creature gets +3/+3, has trample, and can't attack you or planeswalkers you control.":
        return [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 3, 'toughness': 3}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.TRAMPLE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
            Effect(EffectType.CANT_ATTACK_PLAYER, {'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_ENCHANTED_CREATURE)}),
        ]
    if lower == 'equipped creature gets +1/+1.':
        return [Effect(EffectType.MODIFY_P_T, {'amount': {'power': 1, 'toughness': 1}, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)})]
    if lower == 'equipped creature has first strike.':
        return [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.FIRST_STRIKE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)})]
    if lower == "equipped creature has vigilance. (attacking doesn't cause it to tap.)":
        return [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.VIGILANCE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE)})]
    if lower == 'as long as this creature is equipped, it gets +1/+1 and has flying.':
        return [Effect(EffectType.EQUIPPED_ONLY, {'effects': [
            Effect(EffectType.MODIFY_P_T, {'amount': {'power': 1, 'toughness': 1}, 'target': 'SELF'}),
            Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.FLYING.value, 'target': 'SELF'}),
        ]})]
    if lower == 'as long as this creature is equipped, it has double strike. (it deals both first-strike and regular combat damage.)':
        return [Effect(EffectType.EQUIPPED_ONLY, {'effects': [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.DOUBLE_STRIKE.value, 'target': 'SELF'})]})]
    if lower == 'as long as you control a dragon, this creature has flying.':
        return [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.FLYING.value, 'target': 'SELF', 'condition': {'control_subtype': 'Dragon'}})]
    if lower == 'during your turn, equipped creatures you control have indestructible.':
        return [Effect(EffectType.ADD_KEYWORD, {'keyword': Keyword.INDESTRUCTIBLE.value, 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE), 'condition': {'during_your_turn': True}})]

    # Misc combat / rules text
    if lower == 'all creatures able to block this creature do so.':
        return [Effect(EffectType.REQUIRE_BLOCK, {'target': 'SELF'})]
    if lower == 'prevent all combat damage that would be dealt to and dealt by this creature.':
        return [Effect(EffectType.PREVENT_COMBAT_DAMAGE, {'target': 'SELF'})]
    if lower == "you may have imaryll assign its combat damage as though it weren't blocked.":
        return [Effect(EffectType.ASSIGN_DAMAGE_AS_UNBLOCKED, {'target': 'SELF'})]
    if lower == "until your next turn, creatures can't attack you or planeswalkers you control unless their controller pays {2} for each of those creatures.":
        return [Effect(EffectType.ATTACK_TAX, {'amount': 2, 'duration': 'UNTIL_YOUR_NEXT_TURN'})]

    # Counters
    if lower == 'put a +1/+1 counter on this creature.':
        return [Effect(EffectType.PUT_COUNTERS, {'amount': 1, 'counter': '+1/+1', 'target': 'SELF'})]
    if lower == 'put a +1/+1 counter on vogar.':
        return [Effect(EffectType.PUT_COUNTERS, {'amount': 1, 'counter': '+1/+1', 'target': 'SELF'})]
    if lower == "put a +1/+1 counter on equipped creature if it's white.":
        return [Effect(EffectType.PUT_COUNTERS, {'amount': 1, 'counter': '+1/+1', 'target': TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_EQUIPPED_CREATURE), 'condition': {'color': 'WHITE'}})]
    if lower == 'put a +1/+1 counter on target creature for each elf you control.':
        return [Effect(EffectType.PUT_COUNTERS, {'amount': 'COUNT_ELVES', 'counter': '+1/+1', 'target': _target_spec_from_phrase('target creature')})]

    # Attach / goad / misc
    if lower == 'attach this equipment to target creature an opponent controls.':
        return [Effect(EffectType.ATTACH_EQUIPMENT, {'target': _target_spec_from_phrase('target creature an opponent controls')})]
    if lower == 'you may attach any number of auras and equipment you control to it.':
        return [Effect(EffectType.ATTACH_ALL_EQUIPMENT, {'target': 'SELF'})]
    if lower == 'you may attach target equipment you control to target creature you control.':
        return [Effect(EffectType.ATTACH_EQUIPMENT, {'target': _target_spec_from_phrase('target creature you control'), 'equipment_target': 'CONTROLLED_EQUIPMENT'})]
    if lower == "you may return target creature to its owner's hand.":
        return [Effect(EffectType.RETURN_TO_HAND, {'target': _target_spec_from_phrase('target creature')})]
    if lower == 'you may goad target creature. (until your next turn, that creature attacks each combat if able and attacks a player other than you if able.)':
        return [Effect(EffectType.GOAD, {'target': _target_spec_from_phrase('target creature')})]
    if lower.startswith('goad target creature. whenever that creature attacks'):
        return [Effect(EffectType.GOAD, {'target': _target_spec_from_phrase('target creature'), 'draw_on_attack': True})]
    if lower == 'this creature deals 1 damage to target creature defending player controls.':
        return [Effect(EffectType.DEAL_DAMAGE, {'amount': 1, 'target': _target_spec_from_phrase('target creature'), 'defending_player_only': True})]
    if lower == 'it deals 4 damage to target creature.':
        return [Effect(EffectType.DEAL_DAMAGE, {'amount': 4, 'target': _target_spec_from_phrase('target creature')})]
    if lower == 'it deals x damage to any target, where x is the number of dragons you control.':
        return [Effect(EffectType.DEAL_DAMAGE, {'amount': 'COUNT_DRAGONS', 'target': _target_spec_from_phrase('any target'), 'source': 'TRIGGER_SOURCE'})]
    if lower == 'if you control another elf, create a 1/1 green elf warrior creature token.':
        return [Effect(EffectType.CREATE_TOKEN, {'token': 'ELF_WARRIOR_1_1', 'count': 1, 'condition': 'CONTROL_ANOTHER_ELF'})]
    if lower == "for each opponent, you may cast up to one target instant or sorcery card from that player's graveyard without paying its mana cost. if a spell cast this way would be put into a graveyard, exile it instead.":
        return [Effect(EffectType.CAST_FROM_OPPONENT_GRAVEYARD, {})]
    if lower == 'you may discard your hand and draw cards equal to the damage dealt to target opponent this turn.':
        return [Effect(EffectType.DISCARD_HAND_DRAW_EQUAL_DAMAGE, {'target': _target_spec_from_phrase('target opponent')})]

    # Addendum
    if lower.startswith('addendum —'):
        return [Effect(EffectType.ADDENDUM_SCRY_DRAW, {'scry': 3, 'draw': 3})]

    raise ValueError(f'Unsupported effect line: {line}')
def _parse_triggered_line(line: str, card_name: str) -> TriggeredAbility:
    text = line.strip()

    if text.startswith("When this creature enters,") or text.startswith("When this enchantment enters,") or text.startswith("When this Equipment enters,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.ETB, effects)

    if text.startswith("When Vogar dies,"):
        rest = text.split(",", 1)[1].strip()
        if rest == "draw a card for each +1/+1 counter on it.":
            return TriggeredAbility(TriggerType.DIES, [Effect(EffectType.DRAW_CARDS, {"amount": "COUNTERS_ON_SELF"})])
        raise ValueError(f"Unsupported Vogar trigger: {line}")

    if text.startswith("When this creature dies,"):
        rest = text.split(",", 1)[1].strip()
        if rest == "draw a card for each +1/+1 counter on it.":
            effects = [Effect(EffectType.DRAW_CARDS, {"amount": "COUNTERS_ON_SELF"})]
        else:
            effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.DIES, effects)

    if text.startswith("Whenever this creature attacks, you may pay"):
        # e.g., pay {X}{R}. If you do, it deals X damage to any target.
        return TriggeredAbility(
            TriggerType.ATTACKS,
            [Effect(EffectType.DEAL_DAMAGE, {"amount": "X", "target": _target_spec_from_phrase("any target"), "requires_optional_cost": "{X}{R}"})],
        )

    if text.startswith("Whenever this creature attacks or blocks,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.ATTACKS_OR_BLOCKS, effects)

    if text.startswith("Whenever equipped creature attacks,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.EQUIPPED_CREATURE_ATTACKS, effects)

    if text.startswith("Whenever Drakuseth attacks,"):
        # 4 damage to any target and 3 damage to up to two other targets.
        return TriggeredAbility(
            TriggerType.ATTACKS,
            [
                Effect(EffectType.DEAL_DAMAGE, {"amount": 4, "target": _target_spec_from_phrase("any target")}),
                Effect(EffectType.DEAL_DAMAGE, {"amount": 3, "target": _target_spec_from_phrase("any target"), "count": 2, "up_to": True, "exclude_primary": True}),
            ],
        )

    if text.startswith("Whenever Imaryll attacks,"):
        return TriggeredAbility(
            TriggerType.ATTACKS,
            [Effect(EffectType.MODIFY_P_T, {"amount": "COUNT_OTHER_ELVES", "duration": "EOT", "target": "SELF"})],
        )

    if text.startswith("Whenever Nogi attacks,"):
        return TriggeredAbility(
            TriggerType.ATTACKS,
            [
                Effect(EffectType.SET_BASE_P_T, {"power": 5, "toughness": 5, "duration": "EOT", "target": "SELF"}),
                Effect(EffectType.ADD_SUBTYPE, {"subtype": "Dragon", "duration": "EOT", "target": "SELF"}),
                Effect(EffectType.ADD_KEYWORD, {"keyword": Keyword.FLYING.value, "duration": "EOT", "target": "SELF"}),
            ],
            condition={"control_subtype_count": {"subtype": "Dragon", "min": 3}},
        )

    if text.startswith("Whenever this creature deals combat damage to a player,"):
        rest = text.split(",", 1)[1].strip()
        if rest == "each player discards their hand, then draws seven cards.":
            return TriggeredAbility(TriggerType.COMBAT_DAMAGE_TO_PLAYER, [Effect(EffectType.DISCARD_HAND_DRAW_SEVEN, {})])
        if rest == "that player sacrifices a creature of their choice.":
            return TriggeredAbility(TriggerType.COMBAT_DAMAGE_TO_PLAYER, [Effect(EffectType.SACRIFICE_TARGET, {"target": _target_spec_from_phrase("target creature"), "chooser": "DAMAGED_PLAYER"})])
        raise ValueError(f"Unsupported combat-damage trigger: {line}")

    if text.startswith("Whenever this creature is dealt damage,"):
        return TriggeredAbility(TriggerType.DEALT_DAMAGE, [Effect(EffectType.DRAW_CARDS, {"amount": "DAMAGE"})])

    if text.startswith("Whenever you lose life,"):
        return TriggeredAbility(TriggerType.YOU_LOSE_LIFE, [Effect(EffectType.DRAW_CARDS, {"amount": "LIFE_LOST"})])

    if text.startswith("Whenever you cast a creature spell,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.CAST_SPELL, effects, condition={"spell_type": "CREATURE"})

    if text.startswith("Whenever you cast an instant or sorcery spell,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.CAST_SPELL, effects, condition={"spell_type": "INSTANT_OR_SORCERY"})

    if text.startswith("Whenever you cast a spell during an opponent's turn,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.CAST_SPELL, effects, condition={"during_opponent_turn": True})

    if text.startswith("Whenever another creature you control dies,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.OTHER_FRIENDLY_DIES, effects)

    if text.startswith("Whenever another creature dies during your turn,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.OTHER_DIES_DURING_YOUR_TURN, effects)

    if text.startswith("Whenever a creature you control with flying enters,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.CREATURE_ENTERS, effects, condition={"controller": "YOU", "has_keyword": "FLYING"})

    if text.startswith("Whenever a Dragon you control enters,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.CREATURE_ENTERS, effects, condition={"controller": "YOU", "subtype": "Dragon"})

    if text.startswith("Whenever this creature becomes the target of a spell or ability an opponent controls,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.BECOMES_TARGET, effects, condition={"controller": "OPPONENT"})

    if text.startswith("At the beginning of your upkeep,"):
        rest = text.split(",", 1)[1].strip()
        effects = _parse_effect_line(rest)
        return TriggeredAbility(TriggerType.UPKEEP, effects)

    raise ValueError(f"Unsupported triggered line: {line}")


def _parse_activated_line(line: str) -> ActivatedAbility:
    text = line.strip()
    if ":" not in text:
        raise ValueError(f"Invalid activated ability line: {line}")
    cost_text, effect_text = text.split(":", 1)
    costs = _parse_costs(cost_text.strip())

    timing = TimingRestriction.ANYTIME
    if "Activate only as a sorcery" in effect_text:
        timing = TimingRestriction.SORCERY_SPEED
        effect_text = effect_text.replace("Activate only as a sorcery.", "").strip()
    if "Activate only if this creature is attacking." in effect_text:
        timing = TimingRestriction.ONLY_WHEN_ATTACKING
        effect_text = effect_text.replace("Activate only if this creature is attacking.", "").strip()

    effects = _parse_effect_line(effect_text)
    return ActivatedAbility(costs=costs, effects=effects, timing=timing)


def _parse_oracle_text(oracle_text: Optional[str], card_types: Set[CardType], card_name: str) -> ParsedRules:
    rules = RulesBlock()
    equipment_stats: Optional[EquipmentStats] = None
    aura_stats: Optional[AuraStats] = None
    unparsed: List[str] = []

    if not oracle_text:
        return ParsedRules(rules, equipment_stats, aura_stats, unparsed)

    lines = [l.strip() for l in oracle_text.split("\n") if l.strip()]
    idx = 0
    while idx < len(lines):
        line = lines[idx]

        # Modal spells
        if line in ("Choose one —", "Choose two —"):
            choose = 1 if "one" in line else 2
            modes: List[List[Effect]] = []
            idx += 1
            while idx < len(lines) and lines[idx].startswith("•"):
                bullet = lines[idx][1:].strip()
                modes.append(_parse_effect_line(bullet))
                idx += 1
            rules.effects.append(Effect(EffectType.MODAL, {"choose": choose, "modes": modes}))
            continue

        # Keywords line
        kws = _parse_keywords_line(line)
        if kws:
            rules.keywords.update(kws)
            idx += 1
            continue

        # Enchant
        if line.lower() == "enchant creature":
            aura_stats = AuraStats(TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_CREATURE))
            idx += 1
            continue

        # Equip
        if line.startswith("Equip "):
            cost_str = line.replace("Equip", "", 1).strip()
            # may include reminder text
            cost_str = cost_str.split("(", 1)[0].strip()
            equipment_stats = EquipmentStats(_parse_mana_cost_str(cost_str))
            idx += 1
            continue

        # Additional costs
        if line.startswith("As an additional cost to cast this spell,"):
            rest = line.split(",", 1)[1].strip().rstrip(".")
            if rest == "discard a card":
                rules.additional_costs.append(AbilityCost(CostType.DISCARD_CARD, 1))
                idx += 1
                continue
            if rest == "sacrifice a creature":
                rules.additional_costs.append(AbilityCost(CostType.SACRIFICE_CREATURE, 1))
                idx += 1
                continue

        # Alternate cost (Invigorate)
        if line.startswith("If you control a Forest, rather than pay this spell's mana cost"):
            rules.alternate_costs.append({"type": "CONTROL_FOREST_GAIN_LIFE", "amount": 3})
            idx += 1
            continue

        # Flashback
        if line.startswith("Flashback "):
            cost_part = line.replace("Flashback", "", 1).strip()
            cost_part = cost_part.split("(", 1)[0].strip()
            rules.flashback_cost = _parse_mana_cost_str(cost_part)
            idx += 1
            continue

        # Cast from graveyard with extra costs
        if line.startswith("You may cast this card from your graveyard by paying"):
            # "... paying 3 life and discarding a card in addition to paying its other costs."
            rules.cast_from_graveyard_costs = [AbilityCost(CostType.PAY_LIFE, 3), AbilityCost(CostType.DISCARD_CARD, 1)]
            idx += 1
            continue

        # Activated abilities (cost: effect)
        if line.startswith("{") and ":" in line:
            rules.activated_abilities.append(_parse_activated_line(line))
            idx += 1
            continue

        # Triggered abilities
        if line.startswith("When ") or line.startswith("Whenever ") or line.startswith("At "):
            rules.triggered_abilities.append(_parse_triggered_line(line, card_name))
            idx += 1
            continue

        # Static or spell effects
        try:
            effects = _parse_effect_line(line)
            if CardType.INSTANT in card_types or CardType.SORCERY in card_types:
                rules.effects.extend(effects)
            else:
                rules.static_abilities.append(StaticAbility(effects))
            idx += 1
            continue
        except Exception:
            unparsed.append(line)
            idx += 1
            continue

    return ParsedRules(rules, equipment_stats, aura_stats, unparsed)


# ============================
# JSON (de)serialization
# ============================


def card_from_dict(obj: Dict[str, Any]) -> Card:
    # Legacy schema
    if "id" in obj:
        card_type = CardType(obj["card_type"])
        mana_cost = _parse_mana_cost_str(obj.get("mana_cost"))
        colors = _parse_color_list(obj.get("colors", []))
        rules = RulesBlock(
            keywords=set(_KEYWORD_MAP[k.lower()] for k in obj.get("rules", {}).get("keywords", []) if k.lower() in _KEYWORD_MAP),
            effects=[],
        )
        creature_stats = None
        land_stats = None
        if card_type == CardType.CREATURE:
            cs = obj.get("creature_stats", {})
            creature_stats = CreatureStats(
                base_power=int(cs.get("base_power", 0)),
                base_toughness=int(cs.get("base_toughness", 0)),
                counters={
                    "+1/+1": int(cs.get("counters", {}).get("+1/+1", 0)),
                    "-1/-1": int(cs.get("counters", {}).get("-1/-1", 0)),
                },
            )
        if card_type == CardType.LAND:
            produces = {Color(k): int(v) for k, v in obj.get("land_stats", {}).get("produces", {}).items()}
            land_types = {LandType(v) for v in obj.get("land_stats", {}).get("land_types", [])}
            land_stats = LandStats(produces=produces, land_types=land_types)

        card = Card(
            id=obj["id"],
            name=obj["name"],
            mana_cost=mana_cost,
            card_types={card_type},
            subtypes=set(),
            colors=colors,
            rules=rules,
            creature_stats=creature_stats,
            land_stats=land_stats,
        )
        card.validate()
        return card

    # Scryfall-based schema
    card_id = obj["card_id"]
    canonical_id = _ALIAS_CARD_IDS.get(card_id.lower(), card_id)
    type_line = obj.get("type_line", "")
    card_types, subtypes = _parse_type_line(type_line)
    if not card_types:
        raise ValueError(f"Unable to parse types for card {card_id}: {type_line}")

    mana_cost = _parse_mana_cost_str(obj.get("mana_cost"))
    colors = _parse_color_list(obj.get("colors", []))

    power = obj.get("power")
    toughness = obj.get("toughness")
    creature_stats = None
    if CardType.CREATURE in card_types:
        creature_stats = CreatureStats(
            base_power=int(power) if power is not None else 0,
            base_toughness=int(toughness) if toughness is not None else 0,
            counters={"+1/+1": 0, "-1/-1": 0},
        )

    land_stats = None
    if CardType.LAND in card_types:
        # Basic lands only for phase-1
        produces = {}
        if "W" in (obj.get("color_identity") or []):
            produces[Color.WHITE] = 1
        if "U" in (obj.get("color_identity") or []):
            produces[Color.BLUE] = 1
        if "B" in (obj.get("color_identity") or []):
            produces[Color.BLACK] = 1
        if "R" in (obj.get("color_identity") or []):
            produces[Color.RED] = 1
        if "G" in (obj.get("color_identity") or []):
            produces[Color.GREEN] = 1

        land_types = set()
        for lt in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
            if lt in type_line:
                land_types.add(LandType[lt.upper()])
        land_stats = LandStats(produces=produces, land_types=land_types)

    parsed = _parse_oracle_text(obj.get("oracle_text"), card_types, obj.get("name", ""))
    if parsed.unparsed_lines:
        raise ValueError(f"Unparsed oracle text for {card_id}: {parsed.unparsed_lines}")

    card = Card(
        id=canonical_id,
        name=obj.get("name", card_id),
        mana_cost=mana_cost,
        card_types=card_types,
        subtypes=subtypes,
        colors=colors,
        rules=parsed.rules,
        creature_stats=creature_stats,
        land_stats=land_stats,
        equipment_stats=parsed.equipment_stats,
        aura_stats=parsed.aura_stats,
        raw_oracle_text=obj.get("oracle_text"),
    )

    # Equip is a built-in activated ability on Equipment.
    if card.equipment_stats is not None:
        card.rules.activated_abilities.append(
            ActivatedAbility(
                costs=[AbilityCost(type=CostType.MANA, amount=card.equipment_stats.equip_cost)],
                effects=[
                    Effect(
                        type=EffectType.ATTACH_EQUIPMENT,
                        params={"target": TargetSpec(Zone.BATTLEFIELD, Selector.TARGET_CREATURE_YOU_CONTROL)},
                    )
                ],
                timing=TimingRestriction.SORCERY_SPEED,
            )
        )

    card.validate()
    return card


_TOKEN_DEFS: Dict[str, Dict[str, Any]] = {
    "BIRD_ILLUSION_1_1_FLYING": {
        "name": "Bird Illusion",
        "types": ["CREATURE"],
        "subtypes": ["Bird", "Illusion"],
        "colors": ["BLUE"],
        "power": 1,
        "toughness": 1,
        "keywords": ["FLYING"],
    },
    "ELF_WARRIOR_1_1": {
        "name": "Elf Warrior",
        "types": ["CREATURE"],
        "subtypes": ["Elf", "Warrior"],
        "colors": ["GREEN"],
        "power": 1,
        "toughness": 1,
        "keywords": [],
    },
    "SOLDIER_1_1": {
        "name": "Soldier",
        "types": ["CREATURE"],
        "subtypes": ["Soldier"],
        "colors": ["WHITE"],
        "power": 1,
        "toughness": 1,
        "keywords": [],
    },
    "HUMAN_SOLDIER_1_1": {
        "name": "Human Soldier",
        "types": ["CREATURE"],
        "subtypes": ["Human", "Soldier"],
        "colors": ["WHITE"],
        "power": 1,
        "toughness": 1,
        "keywords": [],
    },
    "ZOMBIE_2_2": {
        "name": "Zombie",
        "types": ["CREATURE"],
        "subtypes": ["Zombie"],
        "colors": ["BLACK"],
        "power": 2,
        "toughness": 2,
        "keywords": [],
    },
    "DRAKE_2_2_FLYING": {
        "name": "Drake",
        "types": ["CREATURE"],
        "subtypes": ["Drake"],
        "colors": ["BLUE"],
        "power": 2,
        "toughness": 2,
        "keywords": ["FLYING"],
    },
    "ANGEL_4_4_FLYING": {
        "name": "Angel",
        "types": ["CREATURE"],
        "subtypes": ["Angel"],
        "colors": ["WHITE"],
        "power": 4,
        "toughness": 4,
        "keywords": ["FLYING"],
    },
    "DEMON_5_5_FLYING": {
        "name": "Demon",
        "types": ["CREATURE"],
        "subtypes": ["Demon"],
        "colors": ["BLACK"],
        "power": 5,
        "toughness": 5,
        "keywords": ["FLYING"],
    },
    "DRAGON_2_2_FLYING_FIREBREATH": {
        "name": "Dragon",
        "types": ["CREATURE"],
        "subtypes": ["Dragon"],
        "colors": ["RED"],
        "power": 2,
        "toughness": 2,
        "keywords": ["FLYING"],
        "ability": "FIREBREATH",
    },
    "TREASURE": {
        "name": "Treasure",
        "types": ["ARTIFACT"],
        "subtypes": ["Treasure"],
        "colors": [],
        "keywords": [],
        "ability": "TREASURE_MANA",
    },
}


def _build_token_cards() -> Dict[str, Card]:
    tokens: Dict[str, Card] = {}
    for token_id, spec in _TOKEN_DEFS.items():
        rules = RulesBlock(
            keywords={Keyword[k] for k in spec.get("keywords", [])},
            effects=[],
            static_abilities=[],
            triggered_abilities=[],
            activated_abilities=[],
        )

        if spec.get("ability") == "FIREBREATH":
            rules.activated_abilities.append(
                ActivatedAbility(
                    costs=[AbilityCost(type=CostType.MANA, amount=ManaCost(generic=0, colored={Color.RED: 1}, x=0))],
                    effects=[
                        Effect(
                            type=EffectType.MODIFY_P_T,
                            params={"amount": {"power": 1, "toughness": 0}, "duration": "EOT", "target": "SELF"},
                        )
                    ],
                )
            )
        elif spec.get("ability") == "TREASURE_MANA":
            rules.activated_abilities.append(
                ActivatedAbility(
                    costs=[AbilityCost(type=CostType.TAP), AbilityCost(type=CostType.SACRIFICE_SELF)],
                    effects=[Effect(type=EffectType.ADD_MANA, params={"mana": "ANY"})],
                )
            )

        card_types = {CardType(t) for t in spec.get("types", [])}
        subtypes = set(spec.get("subtypes", []))
        colors = {Color(c) for c in spec.get("colors", [])}
        creature_stats = None
        if CardType.CREATURE in card_types:
            creature_stats = CreatureStats(
                base_power=int(spec.get("power", 0)),
                base_toughness=int(spec.get("toughness", 0)),
                counters={"+1/+1": 0, "-1/-1": 0},
            )

        tokens[token_id] = Card(
            id=token_id,
            name=spec["name"],
            mana_cost=ManaCost(generic=0, colored={}, x=0),
            card_types=card_types,
            subtypes=subtypes,
            colors=colors,
            rules=rules,
            creature_stats=creature_stats,
            land_stats=None,
            equipment_stats=None,
            aura_stats=None,
            raw_oracle_text=None,
        )

    return tokens


def load_card_db(path: str) -> Dict[str, Card]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cards_raw = raw.get("cards")
    if not isinstance(cards_raw, list):
        raise ValueError("Card DB JSON must contain a list at key 'cards'")

    db: Dict[str, Card] = {}
    for obj in cards_raw:
        c = card_from_dict(obj)
        if c.id in db:
            raise ValueError(f"Duplicate card id in DB: {c.id}")
        db[c.id] = c

    for token_id, token_card in _build_token_cards().items():
        if token_id in db:
            raise ValueError(f"Token id collides with card id: {token_id}")
        db[token_id] = token_card

    return db


def save_card_db(path: str, db: Dict[str, Card]) -> None:
    cards = []
    for c in db.values():
        cards.append({
            "id": c.id,
            "name": c.name,
        })
    payload = {"cards": cards}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ============================
# Decklists
# ============================

@dataclass(frozen=True)
class DeckList:
    name: str
    cards: List[Tuple[str, int]]  # (card_id, count)

    def validate(self, card_db: Dict[str, Card]) -> None:
        if not self.name:
            raise ValueError("DeckList.name must be non-empty")
        total = 0
        for cid, n in self.cards:
            if cid not in card_db:
                raise ValueError(f"Deck references unknown card id: {cid}")
            if n <= 0:
                raise ValueError(f"Deck count for {cid} must be > 0")
            total += n
        if total <= 0:
            raise ValueError("Deck must have at least 1 card")

    def total_cards(self) -> int:
        return sum(n for _, n in self.cards)


def load_decks(path: str) -> Dict[str, DeckList]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    decks_raw = raw.get("decks")
    if not isinstance(decks_raw, list):
        raise ValueError("Deck JSON must contain a list at key 'decks'")

    out: Dict[str, DeckList] = {}
    for d in decks_raw:
        name = d.get("name") or d.get("deck_id") or ""
        cards_raw = d.get("cards", [])
        cards: List[Tuple[str, int]] = []
        for c in cards_raw:
            cid = c.get("card_id") or c.get("id")
            if cid is None:
                raise ValueError("Deck card entry missing card_id/id")
            cards.append((cid, int(c.get("count", 0))))
        dl = DeckList(name=name, cards=cards)
        out[name] = dl

    return out
