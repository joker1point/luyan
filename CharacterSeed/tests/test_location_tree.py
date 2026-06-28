"""
Location 树形查询工具测试（ADR-009 / Phase 1）

覆盖：
  - path_to_root / root_of / depth_of
  - children_of / siblings_of
  - format_path（可读化）
  - is_descendant_of
  - 边界：不存在节点 / 循环引用防御（应用层 MAX_TREE_DEPTH=10）
"""
from __future__ import annotations

import pytest

from backend.models import Location, World
from backend.world.location_tree import (
    MAX_TREE_DEPTH,
    children_of,
    depth_of,
    format_path,
    is_descendant_of,
    path_to_root,
    root_of,
    siblings_of,
)


@pytest.fixture
def world(db_session):
    w = World(name="tree-world", season="spring", day_of_year=1, year=1)
    db_session.add(w)
    db_session.commit()
    db_session.refresh(w)
    return w


@pytest.fixture
def location_factory(db_session, world):
    """创建一个 location 并返回"""
    def _make(name, parent_id=None, kind="generic"):
        loc = Location(
            world_id=world.id,
            parent_id=parent_id,
            name=name,
            kind=kind,
            climate="temperate",
        )
        db_session.add(loc)
        db_session.commit()
        db_session.refresh(loc)
        return loc
    return _make


# ============================================================
# path_to_root
# ============================================================
class TestPathToRoot:
    def test_root_has_path_of_one(self, db_session, location_factory):
        tokyo = location_factory("东京")
        path = path_to_root(db_session, tokyo.id)
        assert len(path) == 1
        assert path[0].id == tokyo.id

    def test_three_level_path(self, db_session, location_factory):
        tokyo = location_factory("东京")
        shibuya = location_factory("涩谷", parent_id=tokyo.id)
        cafe = location_factory("猫头鹰咖啡馆", parent_id=shibuya.id)
        path = path_to_root(db_session, cafe.id)
        names = [loc.name for loc in path]
        # path_to_root 返回 [leaf, ..., root]
        assert names == ["猫头鹰咖啡馆", "涩谷", "东京"]

    def test_nonexistent_returns_empty(self, db_session):
        path = path_to_root(db_session, 99999)
        assert path == []

    def test_max_depth_raises(self, db_session, location_factory):
        """构造 > MAX_TREE_DEPTH 层的链，path_to_root 应抛 RuntimeError"""
        # 11 层（> 10）
        prev = location_factory("L0")
        for i in range(1, 12):
            prev = location_factory(f"L{i}", parent_id=prev.id)
        # L11 深度 = 11 > 10
        with pytest.raises(RuntimeError, match="too deep|cycle"):
            path_to_root(db_session, prev.id)


# ============================================================
# root_of / depth_of
# ============================================================
class TestRootAndDepth:
    def test_root_of(self, db_session, location_factory):
        tokyo = location_factory("东京")
        shibuya = location_factory("涩谷", parent_id=tokyo.id)
        assert root_of(db_session, tokyo.id).id == tokyo.id
        assert root_of(db_session, shibuya.id).id == tokyo.id

    def test_root_of_nonexistent(self, db_session):
        assert root_of(db_session, 99999) is None

    def test_depth_of(self, db_session, location_factory):
        tokyo = location_factory("东京")  # depth=0
        shibuya = location_factory("涩谷", parent_id=tokyo.id)  # depth=1
        cafe = location_factory("cafe", parent_id=shibuya.id)  # depth=2
        assert depth_of(db_session, tokyo.id) == 0
        assert depth_of(db_session, shibuya.id) == 1
        assert depth_of(db_session, cafe.id) == 2

    def test_depth_of_nonexistent(self, db_session):
        # depth_of 不存在节点 → path_to_root 返回空 list → depth = -1
        assert depth_of(db_session, 99999) == -1


# ============================================================
# children_of
# ============================================================
class TestChildrenOf:
    def test_root_has_no_children(self, db_session, location_factory):
        tokyo = location_factory("东京")
        assert children_of(db_session, tokyo.id) == []

    def test_one_child(self, db_session, location_factory):
        tokyo = location_factory("东京")
        shibuya = location_factory("涩谷", parent_id=tokyo.id)
        kids = children_of(db_session, tokyo.id)
        assert len(kids) == 1
        assert kids[0].id == shibuya.id

    def test_multiple_children_ordered_by_id(self, db_session, location_factory):
        tokyo = location_factory("东京")
        c1 = location_factory("涩谷", parent_id=tokyo.id)
        c2 = location_factory("新宿", parent_id=tokyo.id)
        kids = children_of(db_session, tokyo.id)
        assert [k.id for k in kids] == sorted([c1.id, c2.id])

    def test_grandchildren_not_included(self, db_session, location_factory):
        tokyo = location_factory("东京")
        shibuya = location_factory("涩谷", parent_id=tokyo.id)
        location_factory("cafe", parent_id=shibuya.id)
        # children_of(tokyo) 只列直接子
        assert len(children_of(db_session, tokyo.id)) == 1


# ============================================================
# siblings_of
# ============================================================
class TestSiblingsOf:
    def test_root_siblings(self, db_session, location_factory):
        r1 = location_factory("root1")
        r2 = location_factory("root2")
        # r2 的兄弟 = 所有 root（不含自己）
        sibs = siblings_of(db_session, r2.id)
        ids = [s.id for s in sibs]
        assert r1.id in ids
        assert r2.id not in ids

    def test_child_siblings(self, db_session, location_factory):
        tokyo = location_factory("东京")
        c1 = location_factory("c1", parent_id=tokyo.id)
        c2 = location_factory("c2", parent_id=tokyo.id)
        c3 = location_factory("c3", parent_id=tokyo.id)
        # c2 的兄弟 = c1, c3
        sibs = siblings_of(db_session, c2.id)
        ids = [s.id for s in sibs]
        assert set(ids) == {c1.id, c3.id}

    def test_only_child_has_no_siblings(self, db_session, location_factory):
        tokyo = location_factory("东京")
        c1 = location_factory("c1", parent_id=tokyo.id)
        assert siblings_of(db_session, c1.id) == []

    def test_nonexistent(self, db_session):
        assert siblings_of(db_session, 99999) == []


# ============================================================
# format_path
# ============================================================
class TestFormatPath:
    def test_root_format(self, db_session, location_factory):
        tokyo = location_factory("东京")
        s = format_path(db_session, tokyo.id)
        assert s == "东京"

    def test_nested_format(self, db_session, location_factory):
        tokyo = location_factory("东京")
        shibuya = location_factory("涩谷", parent_id=tokyo.id)
        cafe = location_factory("猫头鹰咖啡馆", parent_id=shibuya.id)
        s = format_path(db_session, cafe.id)
        assert s == "东京 / 涩谷 / 猫头鹰咖啡馆"

    def test_custom_separator(self, db_session, location_factory):
        tokyo = location_factory("A")
        b = location_factory("B", parent_id=tokyo.id)
        s = format_path(db_session, b.id, separator=" > ")
        assert s == "A > B"

    def test_nonexistent_returns_unknown(self, db_session):
        assert format_path(db_session, 99999) == "<unknown>"


# ============================================================
# is_descendant_of
# ============================================================
class TestIsDescendantOf:
    def test_self_is_not_descendant(self, db_session, location_factory):
        tokyo = location_factory("东京")
        assert is_descendant_of(db_session, tokyo.id, tokyo.id) is False

    def test_direct_child(self, db_session, location_factory):
        tokyo = location_factory("东京")
        shibuya = location_factory("涩谷", parent_id=tokyo.id)
        assert is_descendant_of(db_session, shibuya.id, tokyo.id) is True

    def test_grandchild(self, db_session, location_factory):
        tokyo = location_factory("东京")
        shibuya = location_factory("涩谷", parent_id=tokyo.id)
        cafe = location_factory("cafe", parent_id=shibuya.id)
        assert is_descendant_of(db_session, cafe.id, tokyo.id) is True
        assert is_descendant_of(db_session, cafe.id, shibuya.id) is True

    def test_unrelated(self, db_session, location_factory):
        a = location_factory("A")
        b = location_factory("B")
        assert is_descendant_of(db_session, b.id, a.id) is False
        assert is_descendant_of(db_session, a.id, b.id) is False

    def test_nonexistent_target(self, db_session, location_factory):
        tokyo = location_factory("东京")
        # descendant 不存在 → path_to_root 返回 [] → False
        assert is_descendant_of(db_session, 99999, tokyo.id) is False


# ============================================================
# MAX_TREE_DEPTH 常量
# ============================================================
def test_max_tree_depth_value():
    """防止未来误改 MAX_TREE_DEPTH（影响循环检测深度阈值）"""
    assert MAX_TREE_DEPTH == 10
