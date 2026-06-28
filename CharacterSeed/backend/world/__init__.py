"""
world 四要素 — 包入口（ADR-009 / 2026-06-27-world-pillar-design.md）

用法：
    from backend.world import get_world_engine, build_world_subfield

    engine = get_world_engine()
    state = engine.tick_world(1, n=1)
    ctx = engine.get_context_for_character(character_id)
"""
from backend.world.season_calendar import (
    SEASONS,
    WEATHERS,
    SEASON_DAY_RANGES,
    SEASON_WEATHER_TABLE,
    compute_season,
    generate_weather,
    day_to_season_change,
)
from backend.world.location_tree import (
    MAX_TREE_DEPTH,
    path_to_root,
    root_of,
    depth_of,
    children_of,
    siblings_of,
    format_path,
    is_descendant_of,
)
from backend.world.world_engine import (
    WorldEngine,
    get_world_engine,
    reset_world_engine,
)
from backend.world.location_aware import (
    build_world_subfield,
    get_style_guidance,
)
from backend.world.location_dual_write import (
    set_character_location,
    get_character_location_label,
    get_character_location_row,
    backfill_location_strings,
    backfill_location_strings_sqlite,
)
from backend.world.relationship_network import (
    get_relationships_of,
    detect_relationship_changes,
    build_relationship_subfield,
    broadcast_world_event,
)

__all__ = [
    # season_calendar
    "SEASONS",
    "WEATHERS",
    "SEASON_DAY_RANGES",
    "SEASON_WEATHER_TABLE",
    "compute_season",
    "generate_weather",
    "day_to_season_change",
    # location_tree
    "MAX_TREE_DEPTH",
    "path_to_root",
    "root_of",
    "depth_of",
    "children_of",
    "siblings_of",
    "format_path",
    "is_descendant_of",
    # world_engine
    "WorldEngine",
    "get_world_engine",
    "reset_world_engine",
    # location_aware
    "build_world_subfield",
    "get_style_guidance",
    # location_dual_write (Phase 3)
    "set_character_location",
    "get_character_location_label",
    "get_character_location_row",
    "backfill_location_strings",
    "backfill_location_strings_sqlite",
    # relationship_network (Phase 4)
    "get_relationships_of",
    "detect_relationship_changes",
    "build_relationship_subfield",
    "broadcast_world_event",
]
