"""
游戏状态模型
负责表示炉石传说的完整对局状态：手牌、随从、费用、生命值等
"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Zone(Enum):
    """卡牌所在区域"""
    HAND = "HAND"           # 手牌
    PLAY = "PLAY"           # 战场
    DECK = "DECK"           # 牌库
    GRAVEYARD = "GRAVEYARD" # 墓地


class CardType(Enum):
    """卡牌类型"""
    MINION = "MINION"   # 随从
    SPELL = "SPELL"     # 法术
    WEAPON = "WEAPON"   # 武器
    HERO = "HERO"       # 英雄牌
    HERO_POWER = "HERO_POWER"  # 英雄技能


@dataclass
class Card:
    """单张卡牌"""
    entity_id: int          # 游戏内唯一实体ID
    card_id: str            # 卡牌代码，如 "CS2_005"
    name: str = "Unknown"   # 卡牌名称
    cost: int = 0           # 法力值消耗
    card_type: CardType = CardType.MINION

    # 随从属性
    attack: int = 0
    health: int = 0
    max_health: int = 0

    # 状态标记
    exhausted: bool = False     # 已攻击/使用（竖着的状态）
    has_taunt: bool = False     # 嘲讽
    has_divine_shield: bool = False  # 圣盾
    has_charge: bool = False    # 冲锋
    has_stealth: bool = False   # 潜行
    has_windfury: bool = False  # 圣风
    has_poisonous: bool = False # 剧毒
    has_lifesteal: bool = False # 吸血
    frozen: bool = False        # 冻结
    silenced: bool = False      # 沉默

    # 所在区域
    zone: Zone = Zone.HAND
    controller: int = 0         # 0=我方, 1=对手

    def can_attack(self) -> bool:
        """判断随从是否可以攻击"""
        return (
            self.zone == Zone.PLAY
            and not self.exhausted
            and not self.frozen
            and self.attack > 0
            and self.card_type == CardType.MINION
        )

    def __repr__(self) -> str:
        if self.card_type == CardType.MINION:
            return f"[{self.name} {self.cost}费 {self.attack}/{self.health}]"
        return f"[{self.name} {self.cost}费 {self.card_type.value}]"


@dataclass
class Hero:
    """英雄（玩家）"""
    entity_id: int
    player_id: int          # 1=玩家1, 2=玩家2
    health: int = 30
    max_health: int = 30
    armor: int = 0
    attack: int = 0         # 武器攻击力
    exhausted: bool = False # 英雄本回合是否已攻击
    frozen: bool = False

    @property
    def total_health(self) -> int:
        return self.health + self.armor

    def can_attack(self) -> bool:
        return (
            not self.exhausted
            and not self.frozen
            and self.attack > 0
        )

    def __repr__(self) -> str:
        return f"[英雄 {self.health}+{self.armor}血 {self.attack}攻]"


@dataclass
class GameState:
    """
    完整的对局状态快照
    由 log_parser.py 持续更新
    """
    # 玩家标识（从日志解析得到）
    my_player_id: int = 1

    # 英雄
    my_hero: Optional[Hero] = None
    enemy_hero: Optional[Hero] = None

    # 费用
    my_mana: int = 0
    my_max_mana: int = 0
    enemy_mana: int = 0     # 通常估算，不准确

    # 卡牌集合（entity_id -> Card）
    cards: dict = field(default_factory=dict)

    # 回合信息
    turn: int = 0
    is_my_turn: bool = False

    # ===== 快速访问属性 =====

    @property
    def my_hand(self) -> list[Card]:
        """我方手牌"""
        return [
            c for c in self.cards.values()
            if c.zone == Zone.HAND and c.controller == 0
        ]

    @property
    def my_board(self) -> list[Card]:
        """我方战场随从"""
        return [
            c for c in self.cards.values()
            if c.zone == Zone.PLAY
            and c.controller == 0
            and c.card_type == CardType.MINION
        ]

    @property
    def enemy_board(self) -> list[Card]:
        """对方战场随从"""
        return [
            c for c in self.cards.values()
            if c.zone == Zone.PLAY
            and c.controller == 1
            and c.card_type == CardType.MINION
        ]

    @property
    def playable_cards(self) -> list[Card]:
        """当前费用可打出的手牌"""
        return [c for c in self.my_hand if c.cost <= self.my_mana]

    @property
    def taunt_minions(self) -> list[Card]:
        """对方有嘲讽的随从（必须优先攻击）"""
        return [c for c in self.enemy_board if c.has_taunt]

    def get_card(self, entity_id: int) -> Optional[Card]:
        return self.cards.get(entity_id)

    def summary(self) -> str:
        """打印当前局面摘要"""
        lines = [
            f"=== 第{self.turn}回合 {'【我的回合】' if self.is_my_turn else '【对手回合】'} ===",
            f"我方: {self.my_hero}  费用: {self.my_mana}/{self.my_max_mana}",
            f"对方: {self.enemy_hero}",
            f"我方手牌({len(self.my_hand)}张): {self.my_hand}",
            f"我方战场({len(self.my_board)}个): {self.my_board}",
            f"对方战场({len(self.enemy_board)}个): {self.enemy_board}",
        ]
        return "\n".join(lines)
