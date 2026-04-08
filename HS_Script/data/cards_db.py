"""
卡牌数据库模块
从 HearthstoneJSON (https://hearthstonejson.com) 下载并缓存卡牌数据
提供快速的卡牌属性查询接口

卡牌数据示例：
{
  "id": "CS2_005",
  "name": "恶魔之爪",
  "cost": 4,
  "attack": 4,
  "health": 3,
  "type": "MINION",
  "mechanics": ["TAUNT"],
  "text": "嘲讽"
}
"""

import json
import time
from pathlib import Path
from typing import Optional
from loguru import logger

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests 未安装，将使用内置卡牌数据库")


# HearthstoneJSON API（提供所有炉石卡牌的完整数据）
CARDS_JSON_URL = "https://api.hearthstonejson.com/v1/latest/zhCN/cards.json"
CACHE_FILE = Path(__file__).parent.parent / "data" / "cards_cache.json"

# ========= 内置精简卡牌库（离线备用，覆盖常见中立基础随从）=========
BUILTIN_CARDS = {
    # 格式: card_id -> {name, cost, attack, health, mechanics}
    "CS2_118":  {"name": "炉石传说玩家", "cost": 0, "attack": 0, "health": 1, "mechanics": []},
    "CS2_189":  {"name": "精英督军", "cost": 6, "attack": 4, "health": 5, "mechanics": ["TAUNT"]},
    "CS2_182":  {"name": "石皮草甲虫", "cost": 2, "attack": 2, "health": 3, "mechanics": ["TAUNT"]},
    "EX1_011":  {"name": "古老的守望者", "cost": 7, "attack": 5, "health": 5, "mechanics": ["TAUNT"]},
    "CS2_172":  {"name": "暴怒的鸡", "cost": 1, "attack": 1, "health": 1, "mechanics": ["CHARGE"]},
    "CS2_231":  {"name": "弑神者", "cost": 3, "attack": 2, "health": 3, "mechanics": ["DIVINE_SHIELD"]},
    "CS2_124":  {"name": "冰霜冰元素", "cost": 5, "attack": 3, "health": 6, "mechanics": []},
    "CS2_222":  {"name": "火球术", "cost": 4, "attack": 0, "health": 0, "type": "SPELL", "mechanics": []},
    "CS2_029":  {"name": "火焰冲击", "cost": 1, "attack": 0, "health": 0, "type": "SPELL", "mechanics": []},
    "CS2_023":  {"name": "灵魂冻结", "cost": 0, "attack": 0, "health": 0, "type": "SPELL", "mechanics": []},
}


class CardDatabase:
    """
    卡牌属性数据库
    优先使用本地缓存，缓存过期（7天）后自动更新
    """

    CACHE_TTL_DAYS = 7  # 缓存有效期（天）

    def __init__(self):
        self._db: dict[str, dict] = {}
        self._loaded = False
        self._load()

    def _load(self):
        """加载卡牌数据（优先本地缓存）"""
        # 1. 尝试加载本地缓存
        if CACHE_FILE.exists():
            cache_age_days = (time.time() - CACHE_FILE.stat().st_mtime) / 86400
            if cache_age_days < self.CACHE_TTL_DAYS:
                try:
                    with open(CACHE_FILE, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    self._index_cards(raw)
                    logger.info(f"从缓存加载 {len(self._db)} 张卡牌数据")
                    self._loaded = True
                    return
                except Exception as e:
                    logger.warning(f"缓存读取失败: {e}，尝试重新下载")

        # 2. 从网络下载
        if REQUESTS_AVAILABLE:
            self._download()
        else:
            # 3. 降级到内置数据
            self._db = BUILTIN_CARDS.copy()
            logger.info(f"使用内置卡牌库（{len(self._db)} 张）")
            self._loaded = True

    def _download(self):
        """从 HearthstoneJSON 下载最新卡牌数据"""
        logger.info(f"下载卡牌数据库中... ({CARDS_JSON_URL})")
        try:
            resp = requests.get(CARDS_JSON_URL, timeout=30)
            resp.raise_for_status()
            raw = resp.json()

            # 保存缓存
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)

            self._index_cards(raw)
            logger.info(f"卡牌数据库下载完成，共 {len(self._db)} 张")
            self._loaded = True

        except Exception as e:
            logger.error(f"下载失败: {e}，降级使用内置数据库")
            self._db = BUILTIN_CARDS.copy()
            self._loaded = True

    def _index_cards(self, raw_list: list):
        """将原始列表转为 card_id -> 属性 的字典"""
        self._db = {}
        for card in raw_list:
            card_id = card.get("id")
            if not card_id:
                continue
            # 提取关键属性，统一字段名
            self._db[card_id] = {
                "name":      card.get("name", "未知卡牌"),
                "cost":      card.get("cost", 0),
                "attack":    card.get("attack", 0),
                "health":    card.get("health", 0),
                "type":      card.get("type", "MINION"),
                "text":      card.get("text", ""),
                "mechanics": [m.get("name", "") for m in card.get("mechanics", [])],
                "rarity":    card.get("rarity", ""),
                "set":       card.get("set", ""),
            }

    # ─────────────────────────────────────────────────
    #  公开查询接口
    # ─────────────────────────────────────────────────

    def get(self, card_id: str) -> Optional[dict]:
        """获取卡牌完整属性"""
        return self._db.get(card_id)

    def get_name(self, card_id: str) -> str:
        card = self._db.get(card_id)
        return card["name"] if card else card_id

    def get_cost(self, card_id: str) -> int:
        card = self._db.get(card_id)
        return card["cost"] if card else 0

    def has_mechanic(self, card_id: str, mechanic: str) -> bool:
        """判断卡牌是否有某个特殊效果"""
        card = self._db.get(card_id)
        if not card:
            return False
        return mechanic.upper() in [m.upper() for m in card.get("mechanics", [])]

    def enrich_card(self, card) -> None:
        """
        用数据库信息补全 Card 对象的名称等字段
        （Log解析时只有entity_id和card_id，名称需从数据库补全）
        """
        data = self._db.get(card.card_id)
        if not data:
            return
        card.name = data["name"]
        # 补充力学特性（日志里的tag更准确，这里作为兜底）
        if not card.has_taunt:
            card.has_taunt = self.has_mechanic(card.card_id, "TAUNT")
        if not card.has_charge:
            card.has_charge = self.has_mechanic(card.card_id, "CHARGE")
        if not card.has_divine_shield:
            card.has_divine_shield = self.has_mechanic(card.card_id, "DIVINE_SHIELD")


# 全局单例
_card_db: Optional[CardDatabase] = None

def get_card_db() -> CardDatabase:
    """获取全局卡牌数据库单例"""
    global _card_db
    if _card_db is None:
        _card_db = CardDatabase()
    return _card_db
