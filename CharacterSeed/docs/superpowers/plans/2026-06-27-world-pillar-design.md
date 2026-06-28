# 2026-06-27 — 世界四要素（World Pillar）实施设计

> 配套：[architecture.md §0.5.4 / §0.6 / §10 P0 / ADR-009](../architecture.md)
> 状态：proposed → 用户确认后转 accepted
> 目标：让"NPC 在世界里"愿景真正落地。补齐 4 要素中**最薄弱**的世界维度（当前 2⭐）。

---

## 0. 设计哲学

**世界观选择**：**默认单共享世界 + 支持多世界隔离**。

- 单共享世界：所有 NPC 在同一个物理空间，能互相看见/相遇/对话
- 多世界隔离：每个 `world` 是独立空间（如"现代都市" vs "魔法大陆"），角色不跨世界
- 决策：MVP 只实现一个默认世界（`world_id=1`），但 schema 留 `world_id` 外键以备扩展

**核心理念**：
1. **结构化优先**：把 `world_setting`（纯文本）拆为 `Location` + `WorldRule` 结构化表
2. **外键替代字符串**：`current_state.location` 字符串 → `Character.current_location_id` 外键
3. **无向关系图**：`Relationship` 表用 `char_a_id < char_b_id` 约束去重
4. **可降级**：WorldEngine 失败不阻塞 chat/growth/jiwen 三大主流程
5. **渐进迁移**：老数据兼容 + 双写期 + 单写期，零停机迁移

**不做的事**（v0.4 scope 限定）：
- ❌ 完整 3D 地图（仅结构化数据，不做渲染）
- ❌ 物理仿真（重力 / 碰撞）
- ❌ 跨世界传送（schema 留口子，不实现）
- ❌ 物品经济系统（Item 表存在但不实现交易/铸造）
- ❌ 群像 AI 对话（多 NPC 互相对话，依赖未来 LLM 能力）

---

## 1. 架构总览

```
              ┌──────────────────────────────────────────┐
              │           FastAPI (Python)               │
              │                                          │
   User Chat ─┤  InteractionPipeline                    │
              │       │                                 │
              │       ├──> Director (LLM)               │
              │       │       ↓  ← 注入 location_aware │
              │       ├──> Actor (LLM, stream)            │
              │       │       ↓                         │
              │       ├──> Conversation CRUD            │
              │       │                                 │
              │       └──> World ◀─────────┐            │
              │              │             │            │
              │              ↓             │            │
              │      WorldEngine.tick_world() (daily)   │
              │              │                          │
              │              ├─→ Location (季节/天气)   │
              │              ├─→ WorldEvent (广播)       │
              │              └─→ Character (感知)       │
              │                                          │
              └──────────────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────────┐
        │            持久化层（SQLite）                │
        │  worlds / locations / items /               │
        │  relationships / world_events                │
        │  + 既有 10 张表（character / memory / jiwen）│
        └────────────────────────────────────────────┘
```

**新模块清单**：
- `backend/world/__init__.py`
- `backend/world/world_models.py` — 4 张新表 ORM（World / Location / Item / Relationship）
- `backend/world/world_engine.py` — `WorldEngine` 单例 + `tick_world()` 入口
- `backend/world/world_router.py` — REST API（CRUD + tick + 路径规划）
- `backend/world/season_calendar.py` — 季节 / 天气 / 日历工具
- `backend/world/location_aware.py` — 把 location 上下文注入 Director/Actor prompt

**改动模块**：
- `backend/models.py` — 新增 4 张表
- `backend/modules/interaction.py` — `Director.analyze_with_fallback()` 入参加 `world_context`
- `backend/api/main.py` — `app.include_router(world_router)`
- `web/react-vite/src/pages/` — 新增 `/world` 路由（地图 / 关系网 / 物品清单）

---

## 2. 数据模型

### 2.1 `World` 表（多世界隔离）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK | |
| `name` | String(100) | NOT NULL | 世界名（如"现代东京"） |
| `description` | Text | NULL | LLM 生成的背景设定 |
| `rules_json` | Text | NULL | 物理 / 魔法规则（JSON 字符串） |
| `season` | String(20) | DEFAULT 'spring' | 当前季节（spring/summer/fall/winter） |
| `day_of_year` | Integer | DEFAULT 1 | 1-365 |
| `year` | Integer | DEFAULT 1 | 世界内的年 |
| `created_at` | DateTime | server_default=now | |

**索引**：`ix_worlds_name (name)`

**默认值**：系统启动时自动 `INSERT OR IGNORE` 一行 `id=1, name="默认世界"`，所有角色默认属于这个世界。

### 2.2 `Location` 表（嵌套树形地点）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK | |
| `world_id` | Integer | FK→worlds.id, ON DELETE CASCADE, NOT NULL | 所属世界 |
| `parent_id` | Integer | FK→locations.id, NULL | 父地点（酒馆→城市） |
| `name` | String(100) | NOT NULL | 地点名 |
| `kind` | String(50) | DEFAULT 'generic' | 类别（city/building/room/landscape/dungeon） |
| `description` | Text | NULL | LLM 生成的描述 |
| `climate` | String(20) | DEFAULT 'temperate' | 气候（tropical/temperate/arctic/...） |
| `biome_json` | Text | NULL | 生态/地形细节（JSON） |
| `capacity` | Integer | NULL | 容量上限（NULL=无限制） |
| `is_public` | Boolean | DEFAULT 1 | 是否公共场所 |
| `owner_id` | Integer | FK→characters.id, NULL | 拥有者（私宅） |
| `created_at` | DateTime | server_default=now | |

**索引**：
- `ix_locations_world (world_id)`
- `ix_locations_parent (parent_id)` — 树形查询
- `ix_locations_name (name)` — 名字搜索

**约束**：
- `CHECK (parent_id != id)` — 禁止自引用
- `FOREIGN KEY (parent_id) REFERENCES locations(id) ON DELETE SET NULL` — 父级删除时降级

**关键 API**：
- `Location.children_of(parent_id)` — 子节点
- `Location.path_to_root(id)` — 路径（如"东京 / 涩谷 / 猫头鹰咖啡馆"）
- `Location.siblings(id)` — 兄弟节点

### 2.3 `Item` 表（物品 / 道具）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK | |
| `world_id` | Integer | FK→worlds.id, NOT NULL | 所属世界 |
| `name` | String(100) | NOT NULL | 物品名 |
| `description` | Text | NULL | 描述 |
| `owner_kind` | String(20) | NOT NULL | 拥有者类型（character/location/container） |
| `owner_id` | Integer | NOT NULL | 拥有者 ID（多态） |
| `properties_json` | Text | NULL | 弹性属性（JSON） |
| `rarity` | String(20) | DEFAULT 'common' | 稀有度（common/rare/epic/legendary） |
| `value` | Integer | DEFAULT 0 | 价值（经济系统 PoC） |
| `created_at` | DateTime | server_default=now | |

**索引**：
- `ix_items_owner (owner_kind, owner_id)` — 多态查询
- `ix_items_world (world_id)`
- `ix_items_name (name)`

**注意**：`owner_kind` + `owner_id` 是经典"多态外键"反模式，但 CharacterSeed 规模小（< 10 万物品），**不引入额外 `character_id/location_id` 列**，避免双写一致性问题。

### 2.4 `Relationship` 表（NPC 关系网）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK | |
| `world_id` | Integer | FK→worlds.id, NOT NULL | |
| `char_a_id` | Integer | FK→characters.id, NOT NULL | |
| `char_b_id` | Integer | FK→characters.id, NOT NULL | |
| `type` | String(30) | NOT NULL | family/friend/lover/rival/mentor/acquaintance |
| `strength` | Integer | DEFAULT 0 | 亲密度 -100 ~ +100 |
| `history_json` | Text | NULL | 关系演变事件（JSON 数组） |
| `last_interaction_at` | DateTime | NULL | |
| `created_at` | DateTime | server_default=now | |
| `updated_at` | DateTime | onupdate=now | |

**约束**：
- `CHECK (char_a_id < char_b_id)` — 强制排序，避免双向重复（`A→B` vs `B→A`）
- `UNIQUE (char_a_id, char_b_id)` — 一对角色最多一个关系
- `CHECK (char_a_id != char_b_id)` — 禁止自关系

**查询 API**：
- `Relationship.between(a, b)` — `WHERE (a,b) = (char_a, char_b) OR (a,b) = (char_b, char_a)` 需展开
- `Relationship.of(char_id)` — `WHERE char_a_id = char OR char_b_id = char`

### 2.5 `WorldEvent` 表（世界级事件）

> 复用 `Event` 表 + 加 `world_id` / `location_id` / `broadcast_scope` 字段更轻，但为清晰起见**新建表**。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | Integer | PK | |
| `world_id` | Integer | FK→worlds.id, NOT NULL | |
| `location_id` | Integer | FK→locations.id, NULL | 事件发生地（NULL=全局） |
| `title` | String(200) | NOT NULL | 事件标题 |
| `description` | Text | NULL | |
| `kind` | String(30) | DEFAULT 'global' | global/local/seasonal/weather |
| `scope` | String(20) | DEFAULT 'public' | public/private/system |
| `day` | Integer | NOT NULL | 发生日（day_of_year） |
| `year` | Integer | NOT NULL | 发生年 |
| `created_at` | DateTime | server_default=now | |

**索引**：
- `ix_world_events_world_day (world_id, day)`
- `ix_world_events_location (location_id)`

---

## 3. WorldEngine 设计

### 3.1 核心 API

```python
# backend/world/world_engine.py
class WorldEngine:
    """世界引擎单例：每日 tick 一次，驱动季节/天气/全局事件"""

    def __init__(self, session_factory: Optional[Callable[[], Session]] = None):
        self._session_factory = session_factory or SessionLocal

    def tick_world(self, world_id: int = 1) -> Dict[str, Any]:
        """
        推进世界一天。
        返回: {
            "world_id": 1,
            "new_day": 32,
            "new_season": "spring",
            "weather_changes": [...],   # 哪些 location 天气变了
            "events_broadcast": [...],  # 全局事件
        }
        """
        with self._db() as db:
            world = db.get(World, world_id)
            world.day_of_year += 1
            new_season = self._compute_season(world.day_of_year)
            if new_season != world.season:
                world.season = new_season
                # 触发季节切换事件
            # ... weather, events ...
            db.commit()
            return {...}
```

### 3.2 季节算法

```python
def _compute_season(self, day_of_year: int) -> str:
    """
    北半球默认：春 60-150 / 夏 151-240 / 秋 241-330 / 冬 331-365 或 1-59
    可由 World.season_offset 字段调整（南半球）
    """
    if 60 <= day_of_year <= 150:  return "spring"
    if 151 <= day_of_year <= 240: return "summer"
    if 241 <= day_of_year <= 330: return "fall"
    return "winter"
```

### 3.3 天气生成

```python
# 每个 location 每天的天气 = f(季节, 气候, 随机种子)
def _generate_weather(self, location: Location, season: str, day: int) -> str:
    # 种子 = hash((location.id, day)) → 确定性（同一天同一地点天气固定）
    rng = random.Random((location.id, day))
    # 季节概率表：春雨、夏晴、秋凉、冬雪
    table = {
        "spring": [("rainy", 0.4), ("sunny", 0.4), ("cloudy", 0.2)],
        "summer": [("sunny", 0.6), ("rainy", 0.2), ("stormy", 0.1), ("cloudy", 0.1)],
        "fall":   [("cloudy", 0.4), ("sunny", 0.3), ("rainy", 0.2), ("windy", 0.1)],
        "winter": [("snowy", 0.4), ("cloudy", 0.3), ("sunny", 0.2), ("stormy", 0.1)],
    }
    return weighted_choice(table[season], rng)
```

### 3.4 后台 tick 调度

**复用 jiwen 的 asyncio 模式**：

```python
# backend/world/world_scheduler.py
class WorldScheduler:
    """与 jiwen_scheduler 并行的世界级 tick"""

    async def run(self):
        while True:
            try:
                with SessionLocal() as db:
                    worlds = db.query(World).all()
                    for w in worlds:
                        self.engine.tick_world(w.id)
            except Exception as e:
                logger.exception("world tick failed: %s", e)
            await asyncio.sleep(86400)  # 24h 一次
```

**触发方式**：
- 真实时间：每 24h 一次（午夜）
- 手动：`POST /api/world/{id}/tick` 立即推进
- 加速测试：`/api/world/{id}/tick?n=30` 推进 30 天

### 3.5 NPC 感知世界

`WorldEngine` tick 后，**不**主动 push 到 NPC，而是**被动查询**：
- 当 NPC 聊天/事件推进时，调 `WorldEngine.get_context_for_character(cid)` 返回该角色所在 location 的天气/季节/最近事件
- 注入 Director 的 `world_context` 字段（类似 jiwen 的 `_jiwen` 子字段注入）
- 注入 Actor 的 `style` 字符串（"外面下着雨，你听起来有点忧郁"）

```python
def get_context_for_character(self, character_id: int) -> Dict[str, Any]:
    """角色能感知的世界上下文"""
    with self._db() as db:
        char = db.get(Character, character_id)
        loc = db.get(Location, char.current_location_id) if char.current_location_id else None
        world = db.get(World, char.world_id) if char.world_id else None
        # 找该 location 今天的天气
        weather = self._get_today_weather(loc.id) if loc else None
        # 找最近 7 天的 local events
        events = self._get_recent_events(loc.id, days=7) if loc else []
        return {
            "location": {"id": loc.id, "name": loc.name, "kind": loc.kind} if loc else None,
            "world": {"season": world.season, "day": world.day_of_year} if world else None,
            "weather": weather,
            "recent_events": [{"title": e.title, "day": e.day} for e in events[:3]],
        }
```

---

## 4. 迁移路径（兼容 + 渐进）

### 4.1 阶段 0：schema 扩展（向后兼容）

```sql
-- Alembic migration: 2026-07-01-world-pillar
-- 1. 新建 5 张表
CREATE TABLE worlds (id INTEGER PRIMARY KEY, ...);
CREATE TABLE locations (...);
CREATE TABLE items (...);
CREATE TABLE relationships (...);
CREATE TABLE world_events (...);

-- 2. 给 characters 加列（不删老字段！）
ALTER TABLE characters ADD COLUMN world_id INTEGER DEFAULT 1 REFERENCES worlds(id);
ALTER TABLE characters ADD COLUMN current_location_id INTEGER REFERENCES locations(id);

-- 3. 创建默认 world
INSERT OR IGNORE INTO worlds (id, name) VALUES (1, '默认世界');
```

**双写策略**：
- 写入 `current_state` 时**同时**更新 `current_location_id`（前向）
- 读取时优先 `current_location_id`，NULL 时回退到 `current_state.location`（反向）

### 4.2 阶段 1：数据回填（一次性脚本）

```python
# scripts/migrate_locations.py
"""
读取所有 character.current_state JSON，提取 location 字符串：
  - 在 locations 表中查同名的（同 world_id 下）
  - 找不到就创建（kind=generic, is_public=1）
  - 把 character.current_location_id 设为新 ID
"""
```

### 4.3 阶段 2：Director 注入 world_context

`Director.analyze_with_fallback()` 入参加 `world_context: Optional[Dict] = None`，从 `WorldEngine.get_context_for_character(cid)` 取值。

**不修改 prompt 模板**：把 `world_context` 塞进 `current_state._world` 子字段（与 jiwen 同样的"零模板侵入"策略）。

### 4.4 阶段 3：交互迁移

- 前端 `realApi.js` 增加 `worldApi.*` 方法
- 新增 `/world` 页面（地图列表 / 关系网图 / 物品清单）
- 删除 `current_state.location` 字符串（**v0.5 之后**）

---

## 5. 实施阶段（5 个 phase）

### Phase 1 — Schema + WorldEngine PoC（1 周，2026-W27）

**目标**：建表 + 最小可演示

| 任务 | 验收 | 文件 |
|------|------|------|
| ORM 模型 5 张表 | `python -c "from backend.world.world_models import *"` 不报错 | `backend/world/world_models.py` |
| Alembic 迁移 | `alembic upgrade head` 成功 | `alembic/versions/2026_07_01_world_pillar.py` |
| 默认世界种子 | `python -c "from backend.world.world_engine import *; e=WorldEngine(); print(e.tick_world(1))"` 成功 | `world_engine.py` |
| `tick_world(1)` 单元测试 | 季节、day_of_year、weather 正确变化 | `tests/test_world_engine.py` |
| Location 树形查询测试 | `path_to_root`、`children_of` 正确 | `tests/test_location_tree.py` |
| **PoC 验收**：2 个角色在不同 location，能被 director 注入世界上下文 | E2E 跑通 | manual |

### Phase 2 — REST API + 前端世界页（1 周，2026-W28）

| 任务 | 验收 |
|------|------|
| `world_router.py` — 10+ 个端点 | `/api/world/{id}/state` `/api/world/{id}/tick` `/api/locations` `POST /api/locations` `/api/relationships` `POST /api/relationships` `/api/items` |
| 前端 `/world` 页面 | 树形地点列表 / 季节天气 / 关系网图 / 物品清单 |
| 前端 `realApi.js` 加 `worldApi` | 12 个方法 |
| API 集成测试 | 20 用例 |

### Phase 3 — 数据迁移（0.5 周，2026-W29）

| 任务 | 验收 |
|------|------|
| `scripts/migrate_locations.py` | 100% 老数据回填 |
| 双写期验证 | 写 current_state 同时写 current_location_id |
| `current_state.location` 仍可读 | 兼容老查询 |

### Phase 4 — Relationship + 跨角色事件（1 周，2026-W30）

| 任务 | 验收 |
|------|------|
| `Relationship` 表 ORM + CRUD | 单元测试 |
| `Relationship.of(char_id)` 索引 | 查询 < 1ms（< 1000 关系） |
| Director 注入关系网 | "你最近和 ta 关系变差了" |
| 跨角色事件 broadcast | WorldScheduler tick 后给所有受影响角色发 system event |

### Phase 5 — Item 系统（1 周，2026-W31，可选）

> 视 Phase 1-4 完成情况决定是否启动

| 任务 | 验收 |
|------|------|
| Item CRUD | 单元测试 |
| 物品 → 记忆关联 | 角色回忆"我在酒馆捡到一个杯子" |
| 简单经济系统 | PoC（不实现交易） |

**总工期估算**：4 周（Phase 1-4 必做）+ 1 周（Phase 5 可选）

---

## 6. 测试策略

| 测试类型 | 文件 | 数量 | 覆盖 |
|---------|------|------|------|
| 单元（Location 树形） | `test_location_tree.py` | 8 | parent/child/sibling/path/cycle 防御 |
| 单元（WorldEngine） | `test_world_engine.py` | 10 | 季节 / 天气 / tick / 多世界隔离 |
| 单元（Relationship） | `test_relationship.py` | 12 | 双向查询 / strength / type 枚举 |
| 单元（Item） | `test_item.py` | 6 | 多态 owner / 稀有度 |
| 集成（REST） | `test_world_router.py` | 20 | 10+ 端点 |
| 集成（Director 注入） | `test_world_injection.py` | 8 | world_context 注入 `current_state._world` |
| 集成（跨角色事件） | `test_cross_character_event.py` | 6 | 生日 / 节日 / 灾难广播 |
| E2E | manual | — | 2 角色同 location，weather 变化被感知 |

**总测试数**：**70+ 用例**

**测试隔离要求**（沿用 jiwen 经验）：
1. `app.dependency_overrides[get_db]` — 路由层
2. `monkeypatch.setattr(WorldEngine, "_session_factory", TestingSessionLocal)` — 单例层
3. `monkeypatch.setattr(world_engine_module, "SessionLocal", TestingSessionLocal)` — 模块层
4. autouse fixture 在每个测试前重置 `_default_world` 单例

---

## 7. 风险与权衡

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 多态外键（Item.owner_kind/owner_id）破坏外键约束 | 中 | 数据脏 | 触发器 + 应用层校验（owner_kind 合法值） |
| `current_state.location` 字符串→外键迁移漏数据 | 中 | 老角色无 location | 回填脚本 + 双写期 + 灰度切换 |
| Location 树形 parent_id 循环引用 | 低 | 死循环 | `CHECK (parent_id != id)` + 应用层深度限制 ≤ 10 |
| WorldEngine tick 阻塞主流程 | 中 | 聊天卡顿 | `run_in_executor` 异步；失败回退到上次状态 |
| `Relationship` char_a < char_b 约束漏检查 | 中 | 数据脏 | 应用层 `min(a,b), max(a,b)` 标准化 + DB CHECK |
| 多 World 让 LLM 上下文更复杂 | 低 | prompt 膨胀 | 默认 1 个 world，不在 LLM prompt 暴露 `world_id` |
| 前端 `/world` 页面复杂度（地图/关系网/物品三视图） | 中 | 开发延期 | 简化版：表格 + 简单 SVG，不做 3D |

---

## 8. 验收标准

### Phase 1 完成后（必过）
- [ ] `alembic upgrade head` 成功，5 张表创建
- [ ] `WorldEngine().tick_world(1)` 返回正确字段
- [ ] 季节算法 4 个边界（day=1/60/151/241/331）单测过
- [ ] 天气生成确定性（同一 location + 同一 day 多次调结果一致）
- [ ] `Location.path_to_root()` 树形深度 ≤ 10
- [ ] `pytest tests/test_world_engine.py tests/test_location_tree.py` 全绿
- [ ] 2 角色同 location 聊天，Director prompt 注入 `current_state._world.season="spring"`

### Phase 2 完成后
- [ ] 前端 `/world` 页可显示季节 / 天气 / 地点列表
- [ ] `POST /api/world/1/tick` 可手动推进
- [ ] 20 个 REST API 单测全绿

### Phase 3 完成后
- [ ] 老数据 100% 回填（`SELECT COUNT(*) FROM characters WHERE current_location_id IS NULL` = 0）
- [ ] 双写期：写 current_state 同时写 current_location_id
- [ ] 兼容老查询（API 接受 current_state.location 字符串）

### Phase 4 完成后
- [ ] 关系网图能展示
- [ ] 跨角色事件 broadcast 端到端跑通
- [ ] 70+ 用例全绿

### 全部完成后
- [ ] 4 要素评分：**人格 5⭐ / 时间 4⭐ / 记忆 4⭐ / 世界 4⭐**（从 2⭐ 升到 4⭐）
- [ ] ADR-009 状态：proposed → **accepted**
- [ ] architecture.md §0.5.4 评分从 ⭐⭐ 更新为 ⭐⭐⭐⭐

---

## 9. 待办与下一步

### 立即（本设计文档 review 后）
- [ ] 发起人确认"单共享世界 + 多世界隔离"语义
- [ ] 确认 Phase 5 (Item 系统) 是否在 v0.4 scope
- [ ] 确认前端 `/world` 页面优先级 vs `/status` `/growth` 现有页面

### Phase 1 启动前
- [ ] 建 `backend/world/__init__.py` 空包
- [ ] 配 `alembic` 迁移工具（如未配）
- [ ] 写 `world_models.py` ORM 5 张表

### Phase 1 启动后
- [ ] 同步 project_memory.md 沉淀本次设计
- [ ] 在 architecture.md §0.5.4 写实施进度

---

## 10. 关联文档

- [architecture.md §0.5.4 世界四要素现状](../architecture.md) — 详细分析
- [architecture.md §10 P0 待办](../architecture.md) — ADR-009 任务清单
- [ADR-009](../architecture.md) — 设计决策记录
- [2026-06-27-jiwen-integration-design.md](./2026-06-27-jiwen-integration-design.md) — 借鉴 jiwen 的零模板侵入策略
