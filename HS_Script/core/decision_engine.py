"""
决策引擎
基于当前 GameState 计算最优出牌和攻击顺序

策略层次（优先级从高到低）：
1. 必杀局（能直接斩杀对面英雄就赢）
2. 清场（清理对方嘲讽或威胁随从）
3. 出牌（最大化费用利用率，优先出高价值牌）
4. 攻脸（剩余随从攻击对方英雄）
"""

from dataclasses import dataclass
from typing import Optional
from loguru import logger

from core.game_state import GameState, Card, CardType, Zone


@dataclass
class Action:
    """一个具体的行动指令"""
    action_type: str        # "PLAY_CARD" | "ATTACK" | "END_TURN"
    source_id: int = -1     # 攻击者/出牌的 entity_id
    target_id: int = -1     # 目标的 entity_id（-1=无目标/直接出牌）
    description: str = ""   # 人类可读描述
    priority: int = 0       # 优先级（越高越先执行）

    def __repr__(self) -> str:
        return f"[{self.action_type}] {self.description}"


class DecisionEngine:
    """
    炉石传说决策引擎

    每次轮到我方时，调用 compute_actions(state) 获得有序行动列表
    Bot 按顺序执行即可完成最优回合
    """

    def compute_actions(self, state: GameState) -> list[Action]:
        """
        计算当前局面的最优行动序列
        返回有序的 Action 列表，按优先级从高到低排列
        """
        if not state.is_my_turn:
            return []

        actions: list[Action] = []

        # ─── 第一步：检查必杀局 ───
        lethal = self._check_lethal(state)
        if lethal:
            logger.info("🎯 检测到必杀！直接结束对手")
            actions.extend(lethal)
            return actions  # 必杀不需要再做其他事

        # ─── 第二步：出牌（法术/随从）───
        play_actions = self._decide_plays(state)
        actions.extend(play_actions)

        # ─── 第三步：攻击（清场优先，然后攻脸）───
        attack_actions = self._decide_attacks(state)
        actions.extend(attack_actions)

        # ─── 最后：结束回合 ───
        actions.append(Action(
            action_type="END_TURN",
            description="结束回合",
            priority=0
        ))

        return actions

    # ═══════════════════════════════════════════════
    #  必杀检测
    # ═══════════════════════════════════════════════

    def _check_lethal(self, state: GameState) -> list[Action]:
        """
        计算是否能本回合斩杀对手英雄
        返回导致必杀的行动序列（为空则无必杀）
        """
        if not state.enemy_hero:
            return []

        enemy_hp = state.enemy_hero.total_health
        available_damage = 0

        # 统计我方所有可攻击随从的攻击力
        for minion in state.my_board:
            if minion.can_attack():
                available_damage += minion.attack

        # 统计英雄本身攻击力
        if state.my_hero and state.my_hero.can_attack():
            available_damage += state.my_hero.attack

        # 统计可打出的直伤法术（简化：检测cost<=当前费用的法术）
        for card in state.playable_cards:
            if card.card_type == CardType.SPELL:
                spell_dmg = self._estimate_spell_damage(card.card_id)
                available_damage += spell_dmg

        if available_damage >= enemy_hp:
            logger.info(f"💀 必杀可行！可打出 {available_damage} 伤害，对手 {enemy_hp} 血")
            lethal_seq = self._build_lethal_sequence(state)
            lethal_seq.append(Action(
                action_type="END_TURN",
                description="结束回合（必杀后）",
                priority=0
            ))
            return lethal_seq

        return []

    def _build_lethal_sequence(self, state: GameState) -> list[Action]:
        """构建导致必杀的行动序列"""
        actions = []
        enemy_hero_id = state.enemy_hero.entity_id if state.enemy_hero else -1

        # 先打直伤法术
        for card in sorted(state.playable_cards, key=lambda c: c.cost, reverse=True):
            if card.card_type == CardType.SPELL and self._estimate_spell_damage(card.card_id) > 0:
                actions.append(Action(
                    action_type="PLAY_CARD",
                    source_id=card.entity_id,
                    target_id=enemy_hero_id,
                    description=f"打出 {card.name}（直伤法术）→ 对方英雄",
                    priority=100
                ))

        # 随从攻击英雄（无嘲讽时才直接攻脸）
        if not state.taunt_minions:
            for minion in state.my_board:
                if minion.can_attack():
                    actions.append(Action(
                        action_type="ATTACK",
                        source_id=minion.entity_id,
                        target_id=enemy_hero_id,
                        description=f"{minion.name} 攻击对方英雄 ({minion.attack}伤)",
                        priority=90
                    ))

        return actions

    # ═══════════════════════════════════════════════
    #  出牌决策
    # ═══════════════════════════════════════════════

    def _decide_plays(self, state: GameState) -> list[Action]:
        """
        决定本回合出哪些牌
        策略：背包算法——在费用限制内，最大化场面价值
        """
        actions = []
        remaining_mana = state.my_mana

        # 将可打出的牌按价值评分排序（价值高的先出）
        playable = [(c, self._card_value(c)) for c in state.my_hand if c.cost <= remaining_mana]
        playable.sort(key=lambda x: x[1], reverse=True)

        for card, value in playable:
            if card.cost > remaining_mana:
                continue

            target_id = self._pick_target_for_card(card, state)

            actions.append(Action(
                action_type="PLAY_CARD",
                source_id=card.entity_id,
                target_id=target_id,
                description=f"出牌 {card.name}（{card.cost}费，价值评分{value:.1f}）"
                            + (f" → 目标 entity:{target_id}" if target_id != -1 else ""),
                priority=70
            ))

            remaining_mana -= card.cost
            logger.debug(f"计划出牌: {card.name}, 剩余费用: {remaining_mana}")

        return actions

    def _card_value(self, card: Card) -> float:
        """
        评估一张牌的场面价值（越高越优先出）
        简化评分：综合随从战斗力和关键词加成
        """
        if card.card_type == CardType.SPELL:
            # 法术价值：固定分值（后续可细化）
            return 5.0 + self._estimate_spell_damage(card.card_id) * 0.5

        if card.card_type != CardType.MINION:
            return 3.0

        # 随从价值 = 攻击力 + 生命值 + 关键词加成
        value = float(card.attack + card.health)

        # 关键词加成
        if card.has_taunt:         value += 2.0  # 嘲讽保护友方
        if card.has_charge:        value += 3.0  # 冲锋可立即攻击
        if card.has_divine_shield: value += 2.0  # 圣盾额外抗打击
        if card.has_windfury:      value += 1.5  # 圣风双次攻击
        if card.has_poisonous:     value += 3.0  # 剧毒秒杀
        if card.has_lifesteal:     value += 1.5  # 吸血续航

        # 效率比（cost越低相对价值越高）
        if card.cost > 0:
            value *= (1 + 1.0 / card.cost)

        return value

    def _pick_target_for_card(self, card: Card, state: GameState) -> int:
        """为出牌选择最合适的目标（-1=无需目标）"""
        # 随从通常无目标，法术可能需要目标
        # 简化：优先攻击最危险的随从
        if card.card_type == CardType.SPELL:
            # 找攻击力最高的敌方随从作为目标
            if state.enemy_board:
                target = max(state.enemy_board, key=lambda m: m.attack)
                return target.entity_id
        return -1

    # ═══════════════════════════════════════════════
    #  攻击决策
    # ═══════════════════════════════════════════════

    def _decide_attacks(self, state: GameState) -> list[Action]:
        """
        决定随从攻击顺序
        策略：
        1. 如果有嘲讽，必须先打嘲讽
        2. 优先击杀对方高攻/低血随从（清场）
        3. 无高威胁则直接攻脸
        """
        actions = []
        attackers = [m for m in state.my_board if m.can_attack()]

        if not attackers:
            return actions

        # 有嘲讽随从时，所有攻击都打嘲讽
        if state.taunt_minions:
            for attacker in attackers:
                target = self._pick_best_taunt_target(attacker, state.taunt_minions)
                actions.append(Action(
                    action_type="ATTACK",
                    source_id=attacker.entity_id,
                    target_id=target.entity_id,
                    description=f"{attacker.name}({attacker.attack}/{attacker.health}) "
                                f"攻击嘲讽随从 {target.name}({target.attack}/{target.health})",
                    priority=60
                ))
            return actions

        # 无嘲讽：对每个攻击者找最优目标
        enemy_threats = self._rank_threats(state.enemy_board)

        for attacker in attackers:
            if enemy_threats:
                # 找能一击击杀的最高威胁目标
                kill_target = self._find_kill_target(attacker, enemy_threats)
                if kill_target:
                    actions.append(Action(
                        action_type="ATTACK",
                        source_id=attacker.entity_id,
                        target_id=kill_target.entity_id,
                        description=f"{attacker.name} 击杀 {kill_target.name}（清场）",
                        priority=60
                    ))
                    enemy_threats = [t for t in enemy_threats if t.entity_id != kill_target.entity_id]
                    continue

            # 无法清场则攻脸
            if state.enemy_hero:
                actions.append(Action(
                    action_type="ATTACK",
                    source_id=attacker.entity_id,
                    target_id=state.enemy_hero.entity_id,
                    description=f"{attacker.name}({attacker.attack}伤) 攻击对方英雄",
                    priority=50
                ))

        return actions

    def _pick_best_taunt_target(self, attacker: Card, taunts: list[Card]) -> Card:
        """从嘲讽随从中选最优攻击目标（优先击杀）"""
        # 优先攻击攻击者能击杀的（health <= 攻击者attack）
        killable = [t for t in taunts if t.health <= attacker.attack]
        if killable:
            return min(killable, key=lambda t: t.health)  # 血最少的（更安全击杀）
        # 不能击杀就打血最少的（减少反伤）
        return min(taunts, key=lambda t: t.health)

    def _rank_threats(self, enemies: list[Card]) -> list[Card]:
        """
        对敌方随从按威胁程度排序（高威胁优先清理）
        威胁 = 攻击力 * 存活加成
        """
        def threat_score(m: Card) -> float:
            score = float(m.attack)
            if m.has_divine_shield: score += 2
            if m.has_windfury:      score *= 1.5
            if m.has_poisonous:     score += 5  # 剧毒极危险
            return score

        return sorted(enemies, key=threat_score, reverse=True)

    def _find_kill_target(self, attacker: Card, enemies: list[Card]) -> Optional[Card]:
        """
        在敌方随从中找能被当前攻击者一击击杀的目标
        优先选择威胁最高的（清场效率最大化）
        """
        killable = [
            e for e in enemies
            if e.health <= attacker.attack and not e.has_divine_shield
        ]
        if not killable:
            return None
        # 击杀攻击力最高的（清理最大威胁）
        return max(killable, key=lambda m: m.attack)

    # ═══════════════════════════════════════════════
    #  工具函数
    # ═══════════════════════════════════════════════

    @staticmethod
    def _estimate_spell_damage(card_id: str) -> int:
        """
        简化版法术伤害估算（基于卡牌ID硬编码常见法术）
        完整版应从卡牌数据库读取
        """
        SPELL_DAMAGE = {
            "CS2_222": 6,   # 火球术 6伤
            "CS2_029": 1,   # 火焰冲击 1伤
            "EX1_173": 3,   # 闪电波 3伤（萨满）
            "CS2_041": 4,   # 火焰之舌图腾 4/1
            "CS2_234": 5,   # 暗影词：死亡 摧毁5+攻击的随从
            "CS2_007": 3,   # 施法者的仆从（未必是直伤）
        }
        return SPELL_DAMAGE.get(card_id, 0)

    def get_action_summary(self, actions: list[Action]) -> str:
        """生成决策摘要文本，用于UI展示"""
        if not actions:
            return "当前无可用行动"

        lines = ["=== 本回合最优行动计划 ==="]
        for i, action in enumerate(actions, 1):
            icon = {"PLAY_CARD": "🃏", "ATTACK": "⚔️", "END_TURN": "⏭️"}.get(action.action_type, "•")
            lines.append(f"{i}. {icon} {action.description}")

        return "\n".join(lines)
