"""
炉石传说 Bot 主程序入口
将日志解析、决策引擎、屏幕操作、悬浮窗UI串联成完整系统

运行方式：
  python main.py              # 完整Bot（自动操作 + 悬浮窗）
  python main.py --suggest    # 仅建议模式（不自动操作）
  python main.py --headless   # 无UI模式（命令行输出）

快捷键：
  F9  暂停/继续Bot
  F10 退出
"""

import sys
import time
import argparse
import threading
from pathlib import Path
from loguru import logger

from core.log_parser import LogParser
from core.game_state import GameState
from core.decision_engine import DecisionEngine, Action
from core.screen_controller import ScreenController, ScreenConfig
from data.cards_db import get_card_db


# ─────────────────────────────────────────────────
#  日志配置（输出到控制台 + 文件）
# ─────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    level="INFO",
    colorize=True
)
logger.add(
    "logs/bot_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{line} - {message}",
    level="DEBUG",
    rotation="1 day",
    retention="7 days",
    encoding="utf-8"
)


class HearthstoneBot:
    """
    炉石传说 Bot 核心控制器
    协调所有模块工作
    """

    def __init__(self, suggest_only: bool = False, headless: bool = False):
        """
        :param suggest_only: True=仅显示建议，不实际操作
        :param headless:     True=无UI，仅命令行输出
        """
        self.suggest_only = suggest_only
        self.headless = headless
        self.paused = False

        logger.info("=" * 50)
        logger.info("  炉石传说 Bot 启动中...")
        logger.info(f"  模式: {'仅建议' if suggest_only else '全自动'} | UI: {'无' if headless else '悬浮窗'}")
        logger.info("=" * 50)

        # 初始化各模块
        self.card_db = get_card_db()
        self.parser = LogParser()
        self.engine = DecisionEngine()
        self.controller = ScreenController()
        self.overlay = None

        # 状态
        self._is_running = False
        self._action_thread: threading.Thread = None

    def start(self):
        """启动Bot"""
        self._is_running = True

        # 注册日志解析回调
        self.parser.on_state_change = self._on_state_change
        self.parser.on_my_turn = self._on_my_turn

        # 启动日志监控（后台线程）
        self.parser.start()

        # 启动UI（如果需要）
        if not self.headless:
            self._start_ui()
        else:
            # 无UI模式：阻塞等待
            logger.info("无UI模式运行，按 Ctrl+C 退出")
            try:
                while self._is_running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()

    def stop(self):
        """停止Bot"""
        logger.info("Bot 正在停止...")
        self._is_running = False
        self.parser.stop()

    # ─────────────────────────────────────────────────
    #  事件回调
    # ─────────────────────────────────────────────────

    def _on_state_change(self, state: GameState):
        """游戏状态更新时调用（高频触发）"""
        # 补全卡牌名称
        for card in state.cards.values():
            if card.card_id and card.name == "Unknown":
                self.card_db.enrich_card(card)

        # 更新UI
        if self.overlay:
            # 通过信号更新UI（线程安全）
            self.overlay.update_state_info(
                f"回合: {state.turn} | 费用: {state.my_mana}/{state.my_max_mana}\n"
                f"我方手牌: {len(state.my_hand)}张 | 战场: {len(state.my_board)}个\n"
                f"对方战场: {len(state.enemy_board)}个 随从"
            )

    def _on_my_turn(self, state: GameState):
        """轮到我方行动时调用"""
        if self.paused:
            logger.info("Bot 已暂停，跳过本回合自动操作")
            return

        logger.info(f"\n{'='*40}")
        logger.info(f"🃏 我的第 {state.turn} 回合！")
        logger.info(state.summary())

        # 更新UI回合状态
        if self.overlay:
            self.overlay.set_my_turn(True)

        # 计算最优行动
        actions = self.engine.compute_actions(state)
        summary = self.engine.get_action_summary(actions)

        logger.info(f"\n{summary}")

        if self.overlay:
            self.overlay.update_suggestions(summary)

        # 执行行动（在独立线程中，避免阻塞UI）
        if not self.suggest_only and actions:
            self._action_thread = threading.Thread(
                target=self._execute_actions,
                args=(actions, state),
                daemon=True,
                name="ActionExecutor"
            )
            self._action_thread.start()

    def _execute_actions(self, actions: list[Action], state: GameState):
        """
        按顺序执行决策引擎给出的行动序列
        在独立线程中运行，避免阻塞主线程
        """
        logger.info(f"开始执行 {len(actions)} 个行动...")

        # 构建快速查找映射（entity_id -> 位置索引）
        my_board_ids = [m.entity_id for m in state.my_board]
        enemy_board_ids = [m.entity_id for m in state.enemy_board]
        my_hand_ids = [c.entity_id for c in state.my_hand]

        for i, action in enumerate(actions):
            if self.paused or not self._is_running:
                logger.info("行动序列中断（Bot暂停或退出）")
                break

            log_msg = f"[{i+1}/{len(actions)}] 执行: {action.description}"
            logger.info(log_msg)
            if self.overlay:
                self.overlay.append_log(log_msg)

            try:
                if action.action_type == "PLAY_CARD":
                    # 找到手牌中的位置
                    if action.source_id in my_hand_ids:
                        hand_idx = my_hand_ids.index(action.source_id)
                        target_coord = None
                        if action.target_id in enemy_board_ids:
                            target_idx = enemy_board_ids.index(action.target_id)
                            target_coord = self.controller._get_board_minion_coord(
                                target_idx, len(enemy_board_ids), is_my_board=False
                            )
                        elif action.target_id == (state.enemy_hero.entity_id if state.enemy_hero else -1):
                            target_coord = (self.controller.cfg.enemy_hero_x, self.controller.cfg.enemy_hero_y)

                        self.controller.play_card(hand_idx, len(my_hand_ids), target_coord)

                elif action.action_type == "ATTACK":
                    if action.source_id in my_board_ids:
                        attacker_idx = my_board_ids.index(action.source_id)
                        is_hero = action.target_id == (state.enemy_hero.entity_id if state.enemy_hero else -1)
                        target_idx = enemy_board_ids.index(action.target_id) if action.target_id in enemy_board_ids else 0

                        self.controller.attack(
                            attacker_idx, len(my_board_ids),
                            target_idx if not is_hero else -1,
                            is_hero,
                            len(enemy_board_ids)
                        )

                elif action.action_type == "END_TURN":
                    self.controller.click_end_turn()
                    if self.overlay:
                        self.overlay.set_my_turn(False)
                    logger.info("✅ 回合结束")

            except Exception as e:
                logger.error(f"执行行动出错: {e}")
                if self.overlay:
                    self.overlay.append_log(f"❌ 执行出错: {e}")
                break

    # ─────────────────────────────────────────────────
    #  UI 管理
    # ─────────────────────────────────────────────────

    def _start_ui(self):
        """启动悬浮窗UI（在主线程中运行Qt应用）"""
        try:
            from PyQt6.QtWidgets import QApplication
            from ui.overlay import OverlayWindow

            app = QApplication(sys.argv)
            self.overlay = OverlayWindow()

            # 连接暂停信号
            self.overlay.pause_toggled.connect(lambda paused: setattr(self, 'paused', paused))

            self.overlay.show()
            self.overlay.append_log("🚀 Bot 已启动，等待游戏...")
            self.overlay.append_log(f"模式: {'仅建议' if self.suggest_only else '全自动操作'}")
            self.overlay.append_log("F9=暂停 | F10=退出")

            # Qt事件循环（阻塞，直到窗口关闭）
            app.exec()
            self.stop()

        except ImportError:
            logger.warning("PyQt6 不可用，切换到命令行模式")
            self.headless = True
            try:
                while self._is_running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()


# ─────────────────────────────────────────────────
#  程序入口
# ─────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="炉石传说 Bot - 智能自动化对战助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                  全自动模式（操作+悬浮窗）
  python main.py --suggest        仅显示建议，不自动点击
  python main.py --headless       无界面命令行模式
  python main.py --log <路径>     指定自定义 Power.log 路径
        """
    )
    parser.add_argument("--suggest",  action="store_true", help="仅显示操作建议，不自动执行")
    parser.add_argument("--headless", action="store_true", help="无UI命令行模式")
    parser.add_argument("--log", type=str, default=None, help="自定义 Power.log 路径")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # 确保日志目录存在
    Path("logs").mkdir(exist_ok=True)

    bot = HearthstoneBot(
        suggest_only=args.suggest,
        headless=args.headless
    )

    if args.log:
        bot.parser.log_path = Path(args.log)
        logger.info(f"使用自定义日志路径: {args.log}")

    bot.start()
