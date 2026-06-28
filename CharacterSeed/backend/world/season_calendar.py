"""
季节日历工具（ADR-009 / 2026-06-27-world-pillar-design.md §3.2-3.3）

设计要点：
  1) 季节由 day_of_year 计算（北半球默认，World.season_offset 支持南半球）
  2) 天气用 random.Random((location.id, day)) 种子化 → 同一天同地点天气固定
  3) 季节概率表：春雨/夏晴/秋凉/冬雪（基础模板，可按 climate 调整）
  4) 纯函数，零 DB 依赖，易测试

边界约定：
  - day_of_year 范围 1-365
  - 季节 4 选 1：spring/summer/fall/winter
  - 天气 8 选 1：sunny/cloudy/rainy/snowy/stormy/windy/foggy/clear
"""
from __future__ import annotations

import random
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# 季节常量
# ---------------------------------------------------------------------------
SEASONS = ("spring", "summer", "fall", "winter")

# 季节对应 day_of_year 范围（北半球）
# 春 60-150 / 夏 151-240 / 秋 241-330 / 冬 331-365 或 1-59
SEASON_DAY_RANGES: Dict[str, List[Tuple[int, int]]] = {
    "spring": [(60, 150)],
    "summer": [(151, 240)],
    "fall":   [(241, 330)],
    "winter": [(1, 59), (331, 365)],
}


def compute_season(day_of_year: int, season_offset: int = 0) -> str:
    """
    根据 day_of_year 计算季节（北半球默认）。

    Args:
        day_of_year: 1-365
        season_offset: 南半球 +180（春/秋对调），让季节反向

    Returns:
        季节字符串（spring/summer/fall/winter）

    边界：
        day=1   → winter
        day=60  → spring
        day=151 → summer
        day=241 → fall
        day=331 → winter
    """
    if not 1 <= day_of_year <= 365:
        raise ValueError(f"day_of_year must be 1-365, got {day_of_year}")

    # 南半球偏移：把"夏"调成"冬"，但简化做法是直接对 day_of_year 偏移
    # season_offset 是经验值（0=北半球，180=南半球）
    # 简化：offset 决定是否翻转
    if season_offset >= 180:
        # 翻转：spring<->fall, summer<->winter
        day_of_year = (day_of_year + 182) % 365 or 365  # 翻转
        # 重新校验（180+365=545 % 365 = 180；如果原 365 -> 180)
        if day_of_year == 0:
            day_of_year = 365

    for season, ranges in SEASON_DAY_RANGES.items():
        for lo, hi in ranges:
            if lo <= day_of_year <= hi:
                return season
    # 兜底（不应到达）
    return "spring"


# ---------------------------------------------------------------------------
# 天气常量
# ---------------------------------------------------------------------------
WEATHERS = ("sunny", "cloudy", "rainy", "snowy", "stormy", "windy", "foggy", "clear")

# 季节 → 天气概率表（基线，climate=temperate）
SEASON_WEATHER_TABLE: Dict[str, List[Tuple[str, float]]] = {
    "spring": [
        ("rainy", 0.35),
        ("sunny", 0.35),
        ("cloudy", 0.20),
        ("windy", 0.10),
    ],
    "summer": [
        ("sunny", 0.55),
        ("rainy", 0.15),
        ("stormy", 0.10),
        ("cloudy", 0.10),
        ("clear", 0.10),
    ],
    "fall": [
        ("cloudy", 0.35),
        ("sunny", 0.25),
        ("rainy", 0.20),
        ("windy", 0.15),
        ("foggy", 0.05),
    ],
    "winter": [
        ("snowy", 0.40),
        ("cloudy", 0.30),
        ("sunny", 0.15),
        ("stormy", 0.10),
        ("foggy", 0.05),
    ],
}


def _weighted_choice(weights: List[Tuple[str, float]], rng: random.Random) -> str:
    """按权重选一个（确定性：相同 rng 必返回相同结果）"""
    r = rng.random()
    acc = 0.0
    for label, w in weights:
        acc += w
        if r < acc:
            return label
    return weights[-1][0]  # 浮点兜底


def generate_weather(
    location_id: int,
    day_of_year: int,
    season: str,
    climate: str = "temperate",
) -> str:
    """
    根据 location + day + season + climate 生成天气。

    关键：种子化 → 确定性
    同一天同一地点多次调用结果一致（debug 友好 + 时间倒流回放可能）。

    Args:
        location_id: 地点 ID
        day_of_year: 1-365
        season: spring/summer/fall/winter
        climate: 暂仅记录，不影响概率（未来扩展）

    Returns:
        天气字符串
    """
    if season not in SEASON_WEATHER_TABLE:
        season = "spring"  # 兜底
    # random.Random 仅支持 scalar seed，把 (loc, day, season) 哈希成一个 int
    seed = hash((location_id, day_of_year, season)) & 0x7FFFFFFF
    rng = random.Random(seed)
    return _weighted_choice(SEASON_WEATHER_TABLE[season], rng)


def day_to_season_change(world, new_day: int) -> bool:
    """
    判定 day 推进后季节是否变化。

    用法：
        old_season = world.season
        world.day_of_year = new_day
        new_season = compute_season(new_day, world.season_offset)
        if old_season != new_season:
            # 触发季节切换事件
    """
    new_season = compute_season(new_day, world.season_offset)
    return world.season != new_season, new_season
