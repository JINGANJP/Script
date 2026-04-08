"""
单元测试 - 决策引擎
不需要运行游戏，纯逻辑测试
"""

import sys
sys.path.insert(0, "..")

from core.game_state import GameState, Card, Hero, Zone, CardType
from core.decision_engine import DecisionEngine


def make_minion(eid, name, cost, atk, hp, controller=0, has_taunt=False,
                has_charge=False, has_divine_shield=False, exhausted=False) -> Card:
    """快速创建测试用随从"""
    c = Card(
        entity_id=eid,
        card_id=f"TEST_{eid}",
        name=name,
        cost=cost,
        card_type=CardType.MINION,
        attack=atk,
        health=hp,
        max_health=hp,
        zone=Zone.PLAY,
        controller=controller,
        has_taunt=has_taunt,
        has_charge=has_charge,
        has_divine_shield=has_divine_shield,
        exhausted=exhausted
    )
    return c


def make_hand_card(eid, name, cost, card_type=CardType.MINION, atk=0, hp=0) -> Card:
    """快速创建测试用手牌"""
    return Card(
        entity_id=eid,
        card_id=f"TEST_{eid}",
        name=name,
        cost=cost,
        card_type=card_type,
        attack=atk,
        health=hp,
        max_health=hp,
        zone=Zone.HAND,
        controller=0
    )


def test_scenario_1_lethal():
    """场景1：必杀检测 - 3个随从合计伤害能斩杀对面英雄"""
    print("\n[测试1] 必杀检测...")

    state = GameState(my_mana=5, my_max_mana=5, is_my_turn=True, turn=8)
    state.my_hero  = Hero(entity_id=1, player_id=1, health=20, armor=0)
    state.enemy_hero = Hero(entity_id=2, player_id=2, health=8, armor=0)

    # 3个随从，合计攻击力 3+3+3=9 > 8
    for i, (name, atk) in enumerate([("火焰精灵", 3), ("土地元素", 3), ("炉火之心", 3)]):
        m = make_minion(10+i, name, 3, atk, 3, controller=0, exhausted=False)
        state.cards[m.entity_id] = m

    engine = DecisionEngine()
    actions = engine.compute_actions(state)

    print(engine.get_action_summary(actions))

    lethal_actions = [a for a in actions if a.action_type == "ATTACK"]
    assert len(lethal_actions) >= 3, "应该有3次攻击行动！"
    assert any(a.action_type == "END_TURN" for a in actions), "应该有结束回合行动！"
    print("✅ 必杀检测测试通过")


def test_scenario_2_clear_taunt():
    """场景2：优先攻击嘲讽随从"""
    print("\n[测试2] 嘲讽优先攻击...")

    state = GameState(my_mana=4, my_max_mana=4, is_my_turn=True, turn=5)
    state.my_hero    = Hero(entity_id=1, player_id=1, health=20)
    state.enemy_hero = Hero(entity_id=2, player_id=2, health=20)

    # 我方2个随从
    my1 = make_minion(10, "战士", 3, 3, 4, controller=0)
    my2 = make_minion(11, "弓箭手", 2, 2, 2, controller=0)
    # 对方1嘲讽 + 1普通
    en_taunt  = make_minion(20, "石皮草甲虫（嘲讽）", 2, 2, 3, controller=1, has_taunt=True)
    en_normal = make_minion(21, "普通随从", 3, 3, 3, controller=1)

    for c in [my1, my2, en_taunt, en_normal]:
        state.cards[c.entity_id] = c

    engine = DecisionEngine()
    actions = engine.compute_actions(state)
    print(engine.get_action_summary(actions))

    # 验证：所有攻击都打嘲讽随从
    attack_actions = [a for a in actions if a.action_type == "ATTACK"]
    for a in attack_actions:
        assert a.target_id == en_taunt.entity_id, f"应该攻击嘲讽随从，但攻击了 entity:{a.target_id}"
    print("✅ 嘲讽优先测试通过")


def test_scenario_3_play_cards():
    """场景3：出牌决策 - 最大化费用利用率"""
    print("\n[测试3] 出牌决策...")

    state = GameState(my_mana=6, my_max_mana=6, is_my_turn=True, turn=6)
    state.my_hero    = Hero(entity_id=1, player_id=1, health=20)
    state.enemy_hero = Hero(entity_id=2, player_id=2, health=20)

    # 手牌：1费 + 2费 + 3费（总共6费，刚好全出）
    h1 = make_hand_card(30, "1费随从", 1, atk=1, hp=2)
    h2 = make_hand_card(31, "2费随从", 2, atk=2, hp=3)
    h3 = make_hand_card(32, "3费随从", 3, atk=3, hp=4)
    h7 = make_hand_card(33, "7费随从", 7, atk=7, hp=7)  # 太贵，应该不出

    for c in [h1, h2, h3, h7]:
        state.cards[c.entity_id] = c

    engine = DecisionEngine()
    actions = engine.compute_actions(state)
    print(engine.get_action_summary(actions))

    play_actions = [a for a in actions if a.action_type == "PLAY_CARD"]
    played_ids = [a.source_id for a in play_actions]
    assert h7.entity_id not in played_ids, "7费随从不应该被出（费用不足）"
    print(f"✅ 出牌决策测试通过，计划出 {len(play_actions)} 张牌")


def test_scenario_4_clear_then_face():
    """场景4：先清场再攻脸"""
    print("\n[测试4] 清场优先后攻脸...")

    state = GameState(my_mana=3, my_max_mana=3, is_my_turn=True, turn=7)
    state.my_hero    = Hero(entity_id=1, player_id=1, health=20)
    state.enemy_hero = Hero(entity_id=2, player_id=2, health=20)

    # 我方3个随从
    my1 = make_minion(10, "强力随从", 4, 5, 5, controller=0)
    my2 = make_minion(11, "速攻者",   3, 4, 3, controller=0)
    my3 = make_minion(12, "小随从",   1, 1, 2, controller=0)

    # 对方1个2/2随从（弱小，应该被击杀）
    en1 = make_minion(20, "敌方弱随从", 2, 2, 2, controller=1)

    for c in [my1, my2, my3, en1]:
        state.cards[c.entity_id] = c

    engine = DecisionEngine()
    actions = engine.compute_actions(state)
    print(engine.get_action_summary(actions))

    attack_targets = [a.target_id for a in actions if a.action_type == "ATTACK"]
    # 应该有人去清掉 en1，其他人攻脸
    assert en1.entity_id in attack_targets, "应该有随从去清理敌方随从"
    print("✅ 清场优先测试通过")


if __name__ == "__main__":
    print("🃏 炉石Bot决策引擎单元测试")
    print("=" * 40)
    test_scenario_1_lethal()
    test_scenario_2_clear_taunt()
    test_scenario_3_play_cards()
    test_scenario_4_clear_then_face()
    print("\n" + "=" * 40)
    print("✅ 所有测试通过！")
