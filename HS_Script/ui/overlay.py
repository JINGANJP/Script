"""
悬浮窗 UI（PyQt6 实现）
在炉石传说游戏窗口上方显示半透明叠加层
实时展示：当前局面分析、决策建议、操作日志

特性：
- 始终置顶、点击穿透（不影响游戏操作）
- 半透明背景（不遮挡游戏画面）
- 支持拖拽移动
- 热键 F9：暂停/继续Bot
- 热键 F10：退出
"""

import sys
from typing import Optional
from loguru import logger

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout,
        QLabel, QTextEdit, QPushButton, QHBoxLayout, QFrame
    )
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, pyqtSlot
    from PyQt6.QtGui import QFont, QColor, QPalette, QKeySequence, QShortcut
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False
    logger.warning("PyQt6 未安装，悬浮窗功能不可用")

from core.game_state import GameState
from core.decision_engine import Action


# ─────────────────────────────────────────────────
#  颜色主题（游戏风格：深色金边）
# ─────────────────────────────────────────────────
STYLE_MAIN = """
QWidget#MainPanel {
    background-color: rgba(15, 10, 5, 200);
    border: 2px solid rgba(180, 140, 60, 180);
    border-radius: 12px;
}
QLabel#Title {
    color: #FFD700;
    font-size: 14px;
    font-weight: bold;
    font-family: "Microsoft YaHei";
}
QLabel#Status {
    color: #C0C0C0;
    font-size: 11px;
    font-family: "Microsoft YaHei";
}
QTextEdit#LogArea {
    background-color: rgba(0, 0, 0, 150);
    color: #A0E0A0;
    font-size: 11px;
    font-family: "Consolas", "Microsoft YaHei";
    border: 1px solid rgba(100, 100, 100, 100);
    border-radius: 6px;
}
QPushButton {
    background-color: rgba(180, 140, 60, 180);
    color: #1a0a00;
    border: none;
    border-radius: 6px;
    padding: 4px 12px;
    font-weight: bold;
    font-family: "Microsoft YaHei";
}
QPushButton:hover {
    background-color: rgba(220, 180, 80, 220);
}
QPushButton#PauseBtn[paused="true"] {
    background-color: rgba(180, 60, 60, 180);
    color: #FFD0D0;
}
"""


class OverlayWindow(QMainWindow if PYQT6_AVAILABLE else object):
    """
    半透明悬浮窗主窗口
    """

    # 信号：Bot暂停状态改变
    pause_toggled = pyqtSignal(bool) if PYQT6_AVAILABLE else None

    def __init__(self):
        if not PYQT6_AVAILABLE:
            return
        super().__init__()
        self._paused = False
        self._drag_pos = None

        self._setup_window()
        self._setup_ui()
        self._setup_shortcuts()

        logger.info("悬浮窗已创建")

    def _setup_window(self):
        """配置窗口属性"""
        self.setWindowTitle("HS Bot 悬浮层")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint      # 无标题栏
            | Qt.WindowType.WindowStaysOnTopHint   # 始终置顶
            | Qt.WindowType.Tool                   # 不在任务栏显示
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # 透明背景
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # 不抢焦点
        
        # 默认位置：右上角
        self.setGeometry(1550, 20, 360, 620)

    def _setup_ui(self):
        """构建UI布局"""
        central = QWidget()
        central.setObjectName("MainPanel")
        central.setStyleSheet(STYLE_MAIN)
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── 标题栏 ──
        title_bar = QHBoxLayout()
        title = QLabel("🃏 HS Bot")
        title.setObjectName("Title")
        title_bar.addWidget(title)
        title_bar.addStretch()

        self.status_label = QLabel("● 监听中")
        self.status_label.setObjectName("Status")
        self.status_label.setStyleSheet("color: #60E060;")
        title_bar.addWidget(self.status_label)
        layout.addLayout(title_bar)

        # ── 分隔线 ──
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(180, 140, 60, 100);")
        layout.addWidget(line)

        # ── 局面信息 ──
        self.state_label = QLabel("等待游戏开始...")
        self.state_label.setObjectName("Status")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        # ── 决策建议区 ──
        suggestion_title = QLabel("📋 最优行动计划")
        suggestion_title.setObjectName("Title")
        suggestion_title.setStyleSheet("color: #FFD700; font-size: 12px; margin-top: 4px;")
        layout.addWidget(suggestion_title)

        self.suggestion_area = QTextEdit()
        self.suggestion_area.setObjectName("LogArea")
        self.suggestion_area.setReadOnly(True)
        self.suggestion_area.setMaximumHeight(180)
        self.suggestion_area.setPlaceholderText("等待轮到我方行动...")
        layout.addWidget(self.suggestion_area)

        # ── 操作日志区 ──
        log_title = QLabel("📜 操作日志")
        log_title.setObjectName("Title")
        log_title.setStyleSheet("color: #FFD700; font-size: 12px; margin-top: 4px;")
        layout.addWidget(log_title)

        self.log_area = QTextEdit()
        self.log_area.setObjectName("LogArea")
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(200)
        layout.addWidget(self.log_area)

        # ── 按钮栏 ──
        btn_layout = QHBoxLayout()

        self.pause_btn = QPushButton("⏸ 暂停 (F9)")
        self.pause_btn.setObjectName("PauseBtn")
        self.pause_btn.clicked.connect(self.toggle_pause)
        btn_layout.addWidget(self.pause_btn)

        exit_btn = QPushButton("✕ 退出 (F10)")
        exit_btn.clicked.connect(QApplication.quit)
        btn_layout.addWidget(exit_btn)

        layout.addLayout(btn_layout)

    def _setup_shortcuts(self):
        """全局热键"""
        QShortcut(QKeySequence("F9"), self).activated.connect(self.toggle_pause)
        QShortcut(QKeySequence("F10"), self).activated.connect(QApplication.quit)

    # ─────────────────────────────────────────────────
    #  公开更新接口（由主线程或信号调用）
    # ─────────────────────────────────────────────────

    @pyqtSlot(str)
    def update_state_info(self, text: str):
        """更新局面信息区"""
        self.state_label.setText(text)

    @pyqtSlot(str)
    def update_suggestions(self, text: str):
        """更新决策建议区"""
        self.suggestion_area.setPlainText(text)

    @pyqtSlot(str)
    def append_log(self, text: str):
        """追加操作日志"""
        self.log_area.append(text)
        # 自动滚动到底部
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @pyqtSlot(bool)
    def set_my_turn(self, is_my_turn: bool):
        """更新回合状态指示"""
        if is_my_turn:
            self.status_label.setText("● 我的回合")
            self.status_label.setStyleSheet("color: #FFD700; font-weight: bold;")
        else:
            self.status_label.setText("● 对手回合")
            self.status_label.setStyleSheet("color: #808080;")

    # ─────────────────────────────────────────────────
    #  交互事件
    # ─────────────────────────────────────────────────

    def toggle_pause(self):
        """切换暂停/继续状态"""
        self._paused = not self._paused
        if self._paused:
            self.pause_btn.setText("▶ 继续 (F9)")
            self.pause_btn.setProperty("paused", True)
            self.status_label.setText("● 已暂停")
            self.status_label.setStyleSheet("color: #FF8040;")
            self.append_log("⏸ Bot 已暂停，恢复请按 F9")
        else:
            self.pause_btn.setText("⏸ 暂停 (F9)")
            self.pause_btn.setProperty("paused", False)
            self.status_label.setText("● 恢复运行")
            self.status_label.setStyleSheet("color: #60E060;")
            self.append_log("▶ Bot 已恢复")

        # 刷新按钮样式
        self.pause_btn.style().polish(self.pause_btn)
        self.pause_toggled.emit(self._paused)

    # 支持拖拽移动悬浮窗
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


def create_overlay() -> Optional["OverlayWindow"]:
    """创建并返回悬浮窗实例"""
    if not PYQT6_AVAILABLE:
        logger.warning("PyQt6 未安装，以文本模式运行")
        return None

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = OverlayWindow()
    window.show()
    return window
