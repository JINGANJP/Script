# 炉石传说 Bot - 项目说明

## 项目架构

```
HS_Script/
├── main.py                     # 主程序入口
├── requirements.txt            # 依赖列表
├── core/
│   ├── game_state.py           # 游戏状态数据模型（Card / Hero / GameState）
│   ├── log_parser.py           # Power.log 实时解析引擎
│   ├── decision_engine.py      # AI决策引擎（最优出牌/攻击）
│   └── screen_controller.py   # 屏幕识别 + 鼠标自动操作
├── data/
│   ├── cards_db.py             # 卡牌数据库（HearthstoneJSON）
│   └── cards_cache.json        # 本地缓存（自动生成）
├── ui/
│   └── overlay.py              # PyQt6 半透明悬浮窗
├── tests/
│   └── test_decision_engine.py # 单元测试
└── logs/                       # 运行日志（自动生成）
```

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

> 如果 PyQt6 安装失败，可以先用 `--headless` 模式运行

### 2. 启动游戏
打开炉石传说，进入**单人模式/冒险模式**

### 3. 运行 Bot
```bash
# 全自动模式（推荐）
python main.py

# 仅显示建议，不自动操作（学习模式）
python main.py --suggest

# 无UI命令行模式
python main.py --headless
```

### 4. 控制
| 热键 | 功能 |
|------|------|
| `F9` | 暂停/继续 Bot |
| `F10` | 退出 |
| 拖拽悬浮窗 | 移动窗口位置 |

---

## 模块详解

### `core/game_state.py` - 状态模型
定义了游戏中所有实体的数据结构：
- `Card`：单张卡牌，包含攻击力、生命值、关键词（嘲讽/冲锋/圣盾等）
- `Hero`：英雄，包含血量、护甲、武器攻击力
- `GameState`：完整对局快照，提供 `my_hand`/`my_board`/`enemy_board` 等便捷属性

### `core/log_parser.py` - 日志解析
炉石传说运行时会将所有游戏事件写入 `Power.log`。本模块：
- 使用后台线程以 100ms 间隔 **tail** 日志文件
- 解析 `TAG_CHANGE`、`FULL_ENTITY`、`SHOW_ENTITY` 等关键事件
- 持续维护 `GameState` 对象

**Power.log 位置：**
```
C:\Users\<用户名>\AppData\Local\Blizzard\Hearthstone\Logs\Power.log
```

### `core/decision_engine.py` - 决策引擎
基于贪心算法的分层决策：

```
优先级：
1. 必杀局检测（能赢就赢）
2. 出牌（价值最大化，背包算法）
3. 攻击（清场优先：击杀最高威胁；无威胁则攻脸）
4. 结束回合
```

卡牌价值评分公式：
```
价值 = (攻击力 + 生命值) × (1 + 1/费用)
     + 嘲讽加成(+2) + 冲锋加成(+3) + 圣盾加成(+2) + ...
```

### `core/screen_controller.py` - 屏幕操作
- 根据 `GameState` 中的手牌/随从数量，计算对应屏幕坐标
- 使用 `pyautogui` 模拟拖拽出牌、点击攻击
- 内置随机延迟和轻微鼠标抖动，模拟真人操作节奏

**注意：** 如果分辨率不是 1920×1080，需修改 `ScreenConfig` 中的坐标参数

### `data/cards_db.py` - 卡牌数据库
- 首次运行自动从 [HearthstoneJSON](https://hearthstonejson.com) 下载卡牌数据（中文）
- 缓存到本地 `data/cards_cache.json`，7天更新一次
- 无网络时降级使用内置精简卡牌库

---

## 分辨率适配

如果你的游戏分辨率不是 1920×1080，修改 `main.py` 中的 `ScreenConfig`：

```python
from core.screen_controller import ScreenConfig

config = ScreenConfig(
    resolution_w=2560,
    resolution_h=1440,
    end_turn_x=2240,   # 按比例调整
    end_turn_y=720,
    # ... 其他坐标
)
bot = HearthstoneBot(...)
bot.controller = ScreenController(config)
```

---

## 运行测试

不需要游戏，纯逻辑测试：
```bash
cd tests
python test_decision_engine.py
```

测试覆盖：
- ✅ 必杀检测
- ✅ 嘲讽优先攻击  
- ✅ 出牌费用管理
- ✅ 清场后攻脸

---

## 扩展方向

| 方向 | 难度 | 说明 |
|------|------|------|
| 更智能的决策 | ⭐⭐⭐ | 引入 MCTS（蒙特卡洛树搜索），考虑对手可能的反应 |
| 法术目标选择 | ⭐⭐ | 根据卡牌数据库自动选择法术目标 |
| 模板匹配定位 | ⭐⭐ | 用 OpenCV 精确识别卡牌位置，不依赖固定坐标 |
| 特殊卡牌处理 | ⭐⭐⭐ | 处理发现、选择一张等特殊交互 |
| 对战策略学习 | ⭐⭐⭐⭐ | 接入强化学习，从对局数据中持续优化策略 |
