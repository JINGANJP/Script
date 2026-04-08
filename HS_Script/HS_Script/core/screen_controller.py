"""
屏幕操作模块
负责：
1. 截图并识别炉石传说UI元素（手牌位置、随从位置、结束回合按钮等）
2. 将决策引擎的 Action 指令转为实际鼠标操作

UI坐标说明（基于1920x1080分辨率，可在 config.py 中调整）：
  - 手牌区：底部中央，均匀分布
  - 我方战场：中间偏下
  - 对方战场：中间偏上
  - 结束回合按钮：右侧中央
"""

import time
import random
from dataclasses import dataclass
from typing import Optional
from loguru import logger

try:
    import pyautogui
    import mss
    import mss.tools
    from PIL import Image
    import numpy as np
    AUTOMATION_AVAILABLE = True
    pyautogui.FAILSAFE = True   # 移动到左上角紧急停止
    pyautogui.PAUSE = 0.1       # 每次操作后的最短间隔（秒）
except ImportError:
    AUTOMATION_AVAILABLE = False
    logger.warning("pyautogui/mss/PIL 未安装，自动操作功能不可用（仅显示建议）")


@dataclass
class ScreenConfig:
    """
    屏幕布局配置（根据实际校准填写）
    不同分辨率需相应调整坐标
    """
    # 游戏窗口分辨率（可设置为0让脚本自动检测）
    resolution_w: int = 1920
    resolution_h: int = 1080

    # 结束回合按钮（右侧中央）
    end_turn_x: int = 1608
    end_turn_y: int = 489

    # 我方手牌区（底部）
    hand_y: int = 1035          # 手牌区Y坐标
    hand_x_start: int = 550     # 第1张牌的X坐标
    hand_x_end: int = 1350      # 最后一张牌的X坐标

    # 我方战场（中间偏下）
    my_board_y: int = 589
    my_board_x_start: int = 450
    my_board_x_end: int = 1470

    # 对方战场（中间偏上）
    enemy_board_y: int = 401
    enemy_board_x_start: int = 450
    enemy_board_x_end: int = 1470

    # 对方英雄（中间偏左上）
    enemy_hero_x: int = 960
    enemy_hero_y: int = 200

    # 操作延迟（秒）
    action_delay_min: float = 0.5  # 模拟人工操作，随机延迟
    action_delay_max: float = 1.2


class ScreenController:
    """
    屏幕识别与自动操作控制器

    【工作原理】
    炉石传说的UI布局相对固定。我们使用以下方法确定元素位置：
    1. 根据 Power.log 解析的卡牌数量，计算手牌/随从的屏幕坐标
    2. 使用 mss 快速截图 + OpenCV 模板匹配进行精确定位（可选）
    3. 使用 pyautogui 模拟鼠标点击/拖拽
    """

    def __init__(self, config: Optional[ScreenConfig] = None):
        self.cfg = config or ScreenConfig()
        self._dry_run = not AUTOMATION_AVAILABLE  # 无依赖时只打印不执行

        if self._dry_run:
            logger.info("自动操作处于[演示模式]（未安装自动化依赖，仅输出操作日志）")
        else:
            logger.info("屏幕操作控制器已初始化，FAILSAFE已启用（移动到左上角可紧急停止）")

    # ─────────────────────────────────────────────────
    #  核心操作接口
    # ─────────────────────────────────────────────────

    def play_card(self, hand_index: int, hand_size: int, target_coord: Optional[tuple] = None):
        """
        出牌操作
        :param hand_index: 该牌在手牌中的位置（0开始）
        :param hand_size: 当前手牌总张数
        :param target_coord: 目标坐标（法术/需目标的牌），None则直接拖到战场中央
        """
        card_x, card_y = self._get_hand_card_coord(hand_index, hand_size)
        
        if target_coord:
            # 拖拽到目标
            self._drag(card_x, card_y, target_coord[0], target_coord[1])
            logger.info(f"出牌 手牌[{hand_index}] → 目标{target_coord}")
        else:
            # 拖拽到战场中央（出随从）
            board_center_x = (self.cfg.my_board_x_start + self.cfg.my_board_x_end) // 2
            self._drag(card_x, card_y, board_center_x, self.cfg.my_board_y)
            logger.info(f"出牌 手牌[{hand_index}] → 战场中央")

        self._random_delay()

    def attack(self, attacker_board_index: int, board_size: int,
               target_board_index: int = -1, target_is_hero: bool = False,
               target_board_size: int = 0):
        """
        随从攻击操作
        :param attacker_board_index: 攻击者在我方战场的位置
        :param board_size: 我方战场随从数
        :param target_board_index: 目标在对方战场的位置（-1则攻击英雄）
        :param target_is_hero: 目标是否是对方英雄
        :param target_board_size: 对方战场随从数
        """
        # 攻击者坐标
        att_x, att_y = self._get_board_minion_coord(
            attacker_board_index, board_size, is_my_board=True
        )

        # 目标坐标
        if target_is_hero or target_board_index < 0:
            tgt_x, tgt_y = self.cfg.enemy_hero_x, self.cfg.enemy_hero_y
            logger.info(f"随从[{attacker_board_index}] 攻击对方英雄")
        else:
            tgt_x, tgt_y = self._get_board_minion_coord(
                target_board_index, target_board_size, is_my_board=False
            )
            logger.info(f"随从[{attacker_board_index}] 攻击对方随从[{target_board_index}]")

        # 先点击攻击者（选中），再点击目标
        self._click(att_x, att_y)
        time.sleep(0.3)
        self._click(tgt_x, tgt_y)
        self._random_delay()

    def click_end_turn(self):
        """点击结束回合按钮"""
        logger.info("点击结束回合")
        self._click(self.cfg.end_turn_x, self.cfg.end_turn_y)
        self._random_delay()

    def take_screenshot(self) -> Optional["Image.Image"]:
        """截取当前屏幕（调试用）"""
        if not AUTOMATION_AVAILABLE:
            return None
        with mss.mss() as sct:
            screenshot = sct.grab(sct.monitors[1])  # 主显示器
            return Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

    # ─────────────────────────────────────────────────
    #  坐标计算
    # ─────────────────────────────────────────────────

    def _get_hand_card_coord(self, index: int, total: int) -> tuple[int, int]:
        """计算手牌中第 index 张牌的屏幕坐标"""
        if total <= 1:
            x = (self.cfg.hand_x_start + self.cfg.hand_x_end) // 2
        else:
            span = self.cfg.hand_x_end - self.cfg.hand_x_start
            step = span / (total - 1)
            x = int(self.cfg.hand_x_start + index * step)
        return x, self.cfg.hand_y

    def _get_board_minion_coord(self, index: int, total: int, is_my_board: bool) -> tuple[int, int]:
        """计算战场上第 index 个随从的屏幕坐标"""
        if is_my_board:
            y = self.cfg.my_board_y
            x_start = self.cfg.my_board_x_start
            x_end = self.cfg.my_board_x_end
        else:
            y = self.cfg.enemy_board_y
            x_start = self.cfg.enemy_board_x_start
            x_end = self.cfg.enemy_board_x_end

        if total <= 1:
            x = (x_start + x_end) // 2
        else:
            span = x_end - x_start
            step = span / (total - 1)
            x = int(x_start + index * step)

        return x, y

    # ─────────────────────────────────────────────────
    #  底层鼠标操作
    # ─────────────────────────────────────────────────

    def _click(self, x: int, y: int):
        """单击指定坐标"""
        # 加入少量随机偏移，模拟真人操作
        jitter_x = x + random.randint(-3, 3)
        jitter_y = y + random.randint(-3, 3)

        if self._dry_run:
            logger.debug(f"[演示] 点击 ({jitter_x}, {jitter_y})")
        else:
            pyautogui.click(jitter_x, jitter_y)

    def _drag(self, from_x: int, from_y: int, to_x: int, to_y: int):
        """拖拽操作（出牌/攻击）"""
        if self._dry_run:
            logger.debug(f"[演示] 拖拽 ({from_x},{from_y}) → ({to_x},{to_y})")
        else:
            # 使用缓动动画模拟真人拖拽
            pyautogui.moveTo(from_x, from_y, duration=0.3)
            pyautogui.mouseDown()
            time.sleep(0.1)
            pyautogui.moveTo(to_x, to_y, duration=0.4)
            time.sleep(0.1)
            pyautogui.mouseUp()

    def _random_delay(self):
        """随机延迟，模拟真人节奏"""
        delay = random.uniform(self.cfg.action_delay_min, self.cfg.action_delay_max)
        time.sleep(delay)
