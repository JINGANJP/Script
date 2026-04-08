"""
Power.log 实时解析引擎
炉石传说在运行时会将所有游戏事件写入 Power.log
本模块持续监控该文件，将日志转化为 GameState 对象

Power.log 默认路径：
  Windows: C:/Users/<用户名>/AppData/Local/Blizzard/Hearthstone/Logs/Power.log
  macOS:   ~/Library/Logs/Unity/Hearthstone_Player.log（日志合并）
"""

import re
import time
import threading
from pathlib import Path
from typing import Optional, Callable
from loguru import logger

from core.game_state import GameState, Card, Hero, Zone, CardType


# =========================================================
#  Power.log 常用正则表达式
#  炉石日志格式示例：
#  D 16:22:01.1234567 GameState.DebugPrintPower() -    TAG_CHANGE Entity=GameEntity tag=STEP value=MAIN_ACTION
#  D 16:22:01.1234567 GameState.DebugPrintEntityChoices() ...
# =========================================================

# 匹配 TAG_CHANGE 行（属性变化事件）
RE_TAG_CHANGE = re.compile(
    r"TAG_CHANGE Entity=(?P<entity>.+?) tag=(?P<tag>\w+) value=(?P<value>\w+)"
)

# 匹配 FULL_ENTITY 块（新实体创建）
RE_FULL_ENTITY = re.compile(
    r"FULL_ENTITY - Updating.+?id=(?P<entity_id>\d+).+?cardId=(?P<card_id>\S+)?"
)

# 匹配 SHOW_ENTITY（翻面揭示）
RE_SHOW_ENTITY = re.compile(
    r"SHOW_ENTITY - Updating Entity=(?P<entity_id>\d+).+?cardId=(?P<card_id>\S+)"
)

# 匹配标签行（在FULL_ENTITY / SHOW_ENTITY block内）
RE_TAG_LINE = re.compile(
    r"tag=(?P<tag>\w+) value=(?P<value>\w+)"
)

# 匹配 BLOCK_START（技能、攻击触发）
RE_BLOCK_START = re.compile(
    r"BLOCK_START BlockType=(?P<block_type>\w+).+?Entity=(?P<entity>\S+)"
)

# 匹配回合变化
RE_TURN = re.compile(
    r"TAG_CHANGE Entity=GameEntity tag=TURN value=(?P<turn>\d+)"
)

# 匹配主动回合步骤
RE_STEP = re.compile(
    r"TAG_CHANGE Entity=GameEntity tag=STEP value=(?P<step>\w+)"
)

# 匹配玩家ID
RE_PLAYER_ID = re.compile(
    r"TAG_CHANGE Entity=(?P<entity_id>\d+) tag=CURRENT_PLAYER value=(?P<value>[01])"
)


# ========= 标签常量（对应游戏内部属性名）=========
TAG_ZONE        = "ZONE"
TAG_CONTROLLER  = "CONTROLLER"
TAG_CARDTYPE    = "CARDTYPE"
TAG_COST        = "COST"
TAG_ATK         = "ATK"
TAG_HEALTH      = "HEALTH"
TAG_DAMAGE      = "DAMAGE"
TAG_EXHAUSTED   = "EXHAUSTED"
TAG_TAUNT       = "TAUNT"
TAG_DIVINE_SHIELD = "DIVINE_SHIELD"
TAG_CHARGE      = "CHARGE"
TAG_STEALTH     = "STEALTH"
TAG_FROZEN      = "FROZEN"
TAG_SILENCED    = "SILENCED"
TAG_WINDFURY    = "WINDFURY"
TAG_POISONOUS   = "POISONOUS"
TAG_LIFESTEAL   = "LIFESTEAL"
TAG_ARMOR       = "ARMOR"
TAG_RESOURCES   = "RESOURCES"          # 最大费用
TAG_RESOURCES_USED = "RESOURCES_USED"  # 已用费用
TAG_CURRENT_PLAYER = "CURRENT_PLAYER"
TAG_TURN        = "TURN"
TAG_CARDTYPE_MINION  = "MINION"
TAG_CARDTYPE_SPELL   = "SPELL"
TAG_CARDTYPE_WEAPON  = "WEAPON"
TAG_CARDTYPE_HERO    = "HERO"
TAG_CARDTYPE_HEROPOWER = "HERO_POWER"
TAG_ZONE_HAND   = "HAND"
TAG_ZONE_PLAY   = "PLAY"
TAG_ZONE_DECK   = "DECK"
TAG_ZONE_GRAVEYARD = "GRAVEYARD"


class LogParser:
    """
    实时解析 Power.log，维护并持续更新 GameState

    使用方法：
        parser = LogParser()
        parser.on_state_change = lambda state: print(state.summary())
        parser.start()   # 后台线程开始监听
        ...
        parser.stop()
    """

    # 炉石日志路径（游戏安装目录）
    DEFAULT_LOG_PATH = Path("C:/Program Files (x86)/Hearthstone/Logs/Power.log")

    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = Path(log_path) if log_path else self.DEFAULT_LOG_PATH
        self.state = GameState()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._file_pos = 0  # 上次读取到的文件位置

        # 回调函数：每次状态更新时调用
        self.on_state_change: Optional[Callable[[GameState], None]] = None
        # 回调函数：轮到我方行动时
        self.on_my_turn: Optional[Callable[[GameState], None]] = None

        # 实体解析暂存（用于多行FULL_ENTITY块）
        self._pending_entity: Optional[dict] = None

        logger.info(f"LogParser 初始化，监控路径: {self.log_path}")

    # ───────────────────────────── 生命周期 ─────────────────────────────

    def start(self):
        """启动后台日志监控线程"""
        if not self.log_path.exists():
            logger.warning(f"日志文件不存在：{self.log_path}，等待游戏启动...")
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True, name="LogWatcher")
        self._thread.start()
        logger.info("日志监控线程已启动")

    def stop(self):
        """停止监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("日志监控已停止")

    # ───────────────────────────── 监控循环 ─────────────────────────────

    def _watch_loop(self):
        """主循环：持续tail日志文件"""
        while self._running:
            if not self.log_path.exists():
                time.sleep(2)
                continue

            try:
                with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                    # 首次运行跳到文件末尾，避免解析历史对局
                    if self._file_pos == 0:
                        f.seek(0, 2)  # 跳到末尾
                        self._file_pos = f.tell()
                        logger.info(f"日志文件已连接，从末尾开始监控 (位置: {self._file_pos})")

                    f.seek(self._file_pos)
                    new_lines = f.readlines()
                    self._file_pos = f.tell()

                    if new_lines:
                        for line in new_lines:
                            self._parse_line(line.rstrip("\n"))

            except Exception as e:
                logger.error(f"读取日志出错: {e}")

            time.sleep(0.1)  # 100ms 轮询间隔，足够实时

    # ───────────────────────────── 行解析 ─────────────────────────────

    def _parse_line(self, line: str):
        """解析单行日志"""
        if not line.strip():
            return

        state_changed = False

        # --- 检测回合变化 ---
        m = RE_TURN.search(line)
        if m:
            new_turn = int(m.group("turn"))
            if new_turn != self.state.turn:
                self.state.turn = new_turn
                logger.debug(f"回合更新: {new_turn}")
                state_changed = True

        # --- 检测当前行动玩家 ---
        if "tag=CURRENT_PLAYER" in line:
            m = RE_PLAYER_ID.search(line)
            if m:
                entity_id = int(m.group("entity_id"))
                is_current = m.group("value") == "1"
                # 假设我方是玩家1（entity_id=2通常是玩家1，需根据实际调整）
                # 这里简化处理：通过玩家是否是my_player_id来判断
                if is_current and self.state.my_hero and entity_id == self.state.my_hero.entity_id:
                    if not self.state.is_my_turn:
                        self.state.is_my_turn = True
                        logger.info(f">>> 轮到我方行动！第{self.state.turn}回合 <<<")
                        if self.on_my_turn:
                            self.on_my_turn(self.state)
                elif is_current:
                    self.state.is_my_turn = False
                state_changed = True

        # --- 检测STEP=MAIN_ACTION（我方可操作阶段）---
        m = RE_STEP.search(line)
        if m and m.group("step") == "MAIN_ACTION":
            logger.debug("进入 MAIN_ACTION 阶段")

        # --- 检测 TAG_CHANGE（实体属性变化）---
        if "TAG_CHANGE" in line:
            self._handle_tag_change(line)
            state_changed = True

        # --- 检测 FULL_ENTITY（新卡牌出现）---
        if "FULL_ENTITY" in line:
            m = RE_FULL_ENTITY.search(line)
            if m:
                entity_id = int(m.group("entity_id"))
                card_id = m.group("card_id") or ""
                if entity_id not in self.state.cards:
                    self.state.cards[entity_id] = Card(
                        entity_id=entity_id,
                        card_id=card_id
                    )
                    logger.debug(f"新实体: id={entity_id} cardId={card_id}")
                self._pending_entity = {"entity_id": entity_id}
                state_changed = True

        # --- 检测 SHOW_ENTITY（卡牌翻面/揭示）---
        if "SHOW_ENTITY" in line:
            m = RE_SHOW_ENTITY.search(line)
            if m:
                entity_id = int(m.group("entity_id"))
                card_id = m.group("card_id")
                if entity_id in self.state.cards:
                    self.state.cards[entity_id].card_id = card_id
                self._pending_entity = {"entity_id": entity_id}
                state_changed = True

        # --- 处理 pending entity 的 tag 行（缩进的属性行）---
        if self._pending_entity and "tag=" in line and "value=" in line:
            m = RE_TAG_LINE.search(line)
            if m:
                self._apply_tag_to_entity(
                    self._pending_entity["entity_id"],
                    m.group("tag"),
                    m.group("value")
                )
                state_changed = True
        # 如果遇到非tag行，结束pending解析块
        elif self._pending_entity and line.strip() and "tag=" not in line:
            self._pending_entity = None

        # 触发回调
        if state_changed and self.on_state_change:
            self.on_state_change(self.state)

    def _handle_tag_change(self, line: str):
        """处理 TAG_CHANGE 事件（最核心的状态更新）"""
        m = RE_TAG_CHANGE.search(line)
        if not m:
            return

        entity_str = m.group("entity")
        tag = m.group("tag")
        value = m.group("value")

        # 尝试把 entity 转为 int（entity_id）
        try:
            entity_id = int(entity_str)
        except ValueError:
            # 非数字的entity（如"GameEntity"、"Player"）暂时跳过
            return

        # 如果实体不存在，先创建
        if entity_id not in self.state.cards:
            self.state.cards[entity_id] = Card(entity_id=entity_id, card_id="")

        self._apply_tag_to_entity(entity_id, tag, value)

        # 特殊处理：费用变化（实体是玩家英雄时）
        if tag == TAG_RESOURCES:
            if self.state.my_hero and entity_id == self.state.my_hero.entity_id:
                self.state.my_max_mana = int(value)
        elif tag == TAG_RESOURCES_USED:
            if self.state.my_hero and entity_id == self.state.my_hero.entity_id:
                self.state.my_mana = self.state.my_max_mana - int(value)

    def _apply_tag_to_entity(self, entity_id: int, tag: str, value: str):
        """将单个 tag/value 应用到实体"""
        if entity_id not in self.state.cards:
            return

        card = self.state.cards[entity_id]

        def bool_val(v: str) -> bool:
            return v == "1"

        def int_val(v: str) -> int:
            try:
                return int(v)
            except ValueError:
                return 0

        mapping = {
            TAG_COST:          lambda: setattr(card, "cost", int_val(value)),
            TAG_ATK:           lambda: setattr(card, "attack", int_val(value)),
            TAG_HEALTH:        lambda: (setattr(card, "max_health", int_val(value)) if card.max_health == 0 else None) or setattr(card, "health", int_val(value)),
            TAG_DAMAGE:        lambda: setattr(card, "health", card.max_health - int_val(value)),
            TAG_EXHAUSTED:     lambda: setattr(card, "exhausted", bool_val(value)),
            TAG_TAUNT:         lambda: setattr(card, "has_taunt", bool_val(value)),
            TAG_DIVINE_SHIELD: lambda: setattr(card, "has_divine_shield", bool_val(value)),
            TAG_CHARGE:        lambda: setattr(card, "has_charge", bool_val(value)),
            TAG_STEALTH:       lambda: setattr(card, "has_stealth", bool_val(value)),
            TAG_FROZEN:        lambda: setattr(card, "frozen", bool_val(value)),
            TAG_SILENCED:      lambda: setattr(card, "silenced", bool_val(value)),
            TAG_WINDFURY:      lambda: setattr(card, "has_windfury", bool_val(value)),
            TAG_POISONOUS:     lambda: setattr(card, "has_poisonous", bool_val(value)),
            TAG_LIFESTEAL:     lambda: setattr(card, "has_lifesteal", bool_val(value)),
            TAG_CONTROLLER:    lambda: setattr(card, "controller", int_val(value) - 1),  # 转0/1
            TAG_ZONE:          lambda: setattr(card, "zone", Zone(value) if value in Zone._value2member_map_ else card.zone),
            TAG_CARDTYPE:      lambda: setattr(card, "card_type", self._parse_card_type(value)),
        }

        action = mapping.get(tag)
        if action:
            try:
                action()
            except Exception as e:
                logger.debug(f"应用tag失败: entity={entity_id} tag={tag} value={value} err={e}")

    @staticmethod
    def _parse_card_type(value: str) -> CardType:
        type_map = {
            "MINION": CardType.MINION,
            "SPELL": CardType.SPELL,
            "WEAPON": CardType.WEAPON,
            "HERO": CardType.HERO,
            "HERO_POWER": CardType.HERO_POWER,
        }
        return type_map.get(value, CardType.MINION)
