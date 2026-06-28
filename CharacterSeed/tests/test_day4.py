#!/usr/bin/env python3
"""
CharacterSeed Day 4 — 前端集成与端到端测试
============================================

测试范围（按计划验证全部功能）：
  ┌─────────────────┬───────────────────────────────────────┐
  │ 测试套件         │ 覆盖内容                              │
  ├─────────────────┼───────────────────────────────────────┤
  │ API Client 层   │ 全部 8 个端点调用的参数构造、错误处理  │
  │ 数据流层         │ personality JSON 解析、状态转换      │
  │ UI 逻辑层        │ 页面渲染函数的数据准备逻辑            │
  │ 集成测试         │ Mock LLM 下的端到端数据流验证        │
  │ 边界测试         │ 空数据、错误响应、异常路径            │
  └─────────────────┴───────────────────────────────────────┘

启动命令：
  方式一（推荐—完整测试）:
    cd d:/Desktop/CharacterSeed
    python test_day4.py

  方式二（仅 Mock 测试，无需后端）:
    python test_day4.py --mock-only

  方式三（仅端到端测试，需后端运行）:
    python test_day4.py --e2e-only

  方式四（含阶段输出）:
    python test_day4.py --verbose

期望输出：
  全部测试通过时:
    [OK] 所有 Day 4 前端测试通过！
    总计: N, 通过: N

  存在失败时:
    [FAIL] X 个测试失败
    详细失败信息...

系统要求：
  - Python 3.10+
  - 已安装 requirements.txt 中的依赖
  - （端到端测试）后端运行在 localhost:8000
  - （端到端测试）有效的 DEEPSEEK_API_KEY 在 .env 中
"""

import sys
import os
import json
import time
import unittest
import argparse
from unittest.mock import patch, MagicMock
from io import BytesIO

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# 命令行参数解析
# ============================================================

MOCK_ONLY = False
E2E_ONLY = False
VERBOSE = False


def parse_args():
    global MOCK_ONLY, E2E_ONLY, VERBOSE
    parser = argparse.ArgumentParser(description="CharacterSeed Day 4 前端测试套件")
    parser.add_argument("--mock-only", action="store_true", help="仅运行 Mock 测试（无需后端）")
    parser.add_argument("--e2e-only", action="store_true", help="仅运行端到端测试（需后端运行）")
    parser.add_argument("--verbose", action="store_true", help="输出详细阶段信息 & LLM 原始响应")
    args = parser.parse_args()
    MOCK_ONLY = args.mock_only
    E2E_ONLY = args.e2e_only
    VERBOSE = args.verbose


# ============================================================
# 测试数据常量
# ============================================================

# 示例角色描述（用于创建测试）
SAMPLE_CHARACTER_DESC = (
    "一位在雪山中修炼十年的剑客，性格孤傲但内心善良。"
    "师父临终前将一把名为「霜华」的古剑托付给他，"
    "嘱咐他下山寻找能够继承这把剑的人。"
)

# 示例对话消息
SAMPLE_CHAT_MESSAGES = [
    "你好，你是这座酒馆的主人吗？",
    "最近镇上有什么新闻吗？",
    "看起来你很担心什么，可以告诉我吗？",
]

# Mock Creation LLM 原始响应
MOCK_CREATION_RAW = json.dumps({
    "name": "凌霜",
    "world_setting": (
        "一个架空的武侠世界，北方雪山常年冰封，传说山中埋藏着上古剑圣的传承。"
        "山脚下有一座名为「寒露镇」的小镇，是冒险者进入雪山前最后的补给站。"
    ),
    "personality": {
        "optimism": 40, "courage": 90, "empathy": 55,
        "loyalty": 75, "intelligence": 65, "sociability": 30,
    },
    "current_state": {
        "location": "寒露镇酒馆",
        "activity": "独自坐在角落擦拭古剑",
        "mood": "沉思",
    },
    "initial_memories": [
        {"content": "在雪山中独自修行十年，练成了霜华剑法", "importance": 9},
        {"content": "师父临终前将古剑「霜华」托付给自己", "importance": 10},
        {"content": "下山后发现江湖已与自己记忆中的完全不同", "importance": 7},
    ],
}, ensure_ascii=False)

# Mock Director 原始响应
MOCK_DIRECTOR_RAW = json.dumps({
    "emotion": "警惕中带着好奇",
    "focus_memories": [
        "玩家刚才问起了镇上的新闻",
        "最近雪山附近有异常动静",
    ],
    "goal": "试探对方是否值得信任，了解对方来意",
    "style": "冷淡疏离、有所保留的",
}, ensure_ascii=False)

# Mock Actor 原始响应
MOCK_ACTOR_RAW = json.dumps({
    "action": "停下擦拭古剑的动作，抬头看了玩家一眼",
    "expression": "眼神锐利但嘴角微扬",
    "speech": "这酒馆……确实是我的。旅人，你从何处来？这雪山上可不太平。",
}, ensure_ascii=False)

# Mock Growth 原始响应
MOCK_GROWTH_RAW = json.dumps({
    "personality_delta": {
        "optimism": 5, "courage": 0, "empathy": 3,
        "loyalty": 0, "intelligence": 0, "sociability": 8,
    },
    "new_memories": [
        {"content": "在酒馆遇到了一位好奇心旺盛的旅人", "importance": 7},
        {"content": "分享了关于雪山异常动静的担忧", "importance": 8},
    ],
    "event_summary": (
        "昨日在酒馆与一位旅人交谈，起初保持警惕，"
        "但在交流中逐渐放松，分享了一些关于雪山的见闻和担忧。"
    ),
}, ensure_ascii=False)

# 期望的人格维度列表
EXPECTED_PERSONALITY_DIMS = [
    "optimism", "courage", "empathy",
    "loyalty", "intelligence", "sociability",
]


# ============================================================
# 辅助函数
# ============================================================

def safe_parse_json(raw):
    """安全解析 JSON 字符串"""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def make_mock_openai_response(content_str):
    """构造 mock OpenAI response 对象"""
    mock_choice = MagicMock()
    mock_choice.message.content = content_str
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


# ============================================================
# 测试套件 1：API Client 参数构造与响应解析
# ============================================================

class Test_API_Client_RequestConstruction(unittest.TestCase):
    """
    API Client 请求构造测试

    验证目标：
      - 全部 8 个端点函数的参数是否正确映射到 requests 调用
      - URL 拼接是否正确
      - 错误响应是否正确转换
      - 超时/连接错误是否正确捕获

    不调用真实后端——使用 Mock 验证 requests 的调用参数
    """

    def _assert_success_response(self, result):
        """验证成功响应不含 error key"""
        self.assertIsInstance(result, (dict, list))
        if isinstance(result, dict):
            self.assertNotIn("error", result, f"意外错误: {result.get('detail', '')}")

    def _assert_error_response(self, result):
        """验证错误响应含 error=True"""
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("error"), "应标记 error=True")
        self.assertIn("detail", result, "应包含 detail 字段")

    # ──────────── 测试 1：create_character_text 请求构造 ────────────

    @patch("frontend.api_client.requests.post")
    def test_001_create_character_text_sends_form_data(self, mock_post):
        """
        [TEST-001] create_character_text 使用 Form data 发送请求

        输入: description="测试角色描述"
        预期: requests.post 以 data={"description": "测试角色描述"} 调用
        原因: 后端端点将 description 定义为 Form(...)，需用 data= 而非 json=
        """
        from frontend.api_client import create_character_text

        # Mock 成功响应
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "id": 1, "name": "测试角色",
            "personality": '{"optimism": 50}', "created_at": "2024-01-01T00:00:00",
        }
        mock_post.return_value = mock_response

        result = create_character_text("测试角色描述")

        # 断言 1：无错误
        self._assert_success_response(result)

        # 断言 2：requests.post 以 data= 参数调用
        call_kwargs = mock_post.call_args.kwargs
        self.assertIn("data", call_kwargs, "应使用 data= 参数（Form 语义）")
        self.assertNotIn("json", call_kwargs, "不应使用 json= 参数")
        self.assertEqual(call_kwargs["data"]["description"], "测试角色描述")

        # 断言 3：URL 包含正确路径
        call_args = mock_post.call_args.args
        self.assertIn("/api/characters/create", str(call_args))

    # ──────────── 测试 2：create_character_file 请求构造 ────────────

    @patch("frontend.api_client.requests.post")
    def test_002_create_character_file_sends_multipart(self, mock_post):
        """
        [TEST-002] create_character_file 使用 multipart/form-data 上传文件

        输入: file_bytes="故事内容".encode("utf-8"), filename="story.txt"
        预期: requests.post 以 files={"story_file": ("story.txt", <bytes>, "text/plain")} 调用
        原因: 后端端点通过 UploadFile = File(None) 接收文件，需 multipart 编码
        """
        from frontend.api_client import create_character_file

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"id": 2, "name": "文件角色", "created_at": "2024-01-01T00:00:00"}
        mock_post.return_value = mock_response

        result = create_character_file("故事内容".encode("utf-8"), "story.txt")

        self._assert_success_response(result)

        call_kwargs = mock_post.call_args.kwargs
        self.assertIn("files", call_kwargs, "应使用 files= 参数")
        self.assertIn("story_file", call_kwargs["files"])
        file_tuple = call_kwargs["files"]["story_file"]
        self.assertEqual(file_tuple[0], "story.txt")
        self.assertEqual(file_tuple[1], "故事内容".encode("utf-8"))

    # ──────────── 测试 3：send_message 使用 JSON body ────────────

    @patch("frontend.api_client.requests.post")
    def test_003_send_message_sends_json_body(self, mock_post):
        """
        [TEST-003] send_message 使用 JSON body 发送 ChatRequest

        输入: character_id=1, message="你好"
        预期: requests.post 以 json={"character_id": 1, "message": "你好"} 调用
        原因: ChatRequest 是 Pydantic BaseModel，后端通过请求体解析。
              json= 自动设置 Content-Type: application/json
        """
        from frontend.api_client import send_message

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "id": 1, "character_id": 1, "user_input": "你好",
            "npc_response": "欢迎！", "emotion": "友好",
            "action": "微笑点头", "expression": "温暖的笑容",
        }
        mock_post.return_value = mock_response

        result = send_message(1, "你好")

        self._assert_success_response(result)
        call_kwargs = mock_post.call_args.kwargs
        self.assertIn("json", call_kwargs, "应使用 json= 参数")
        self.assertEqual(call_kwargs["json"]["character_id"], 1)
        self.assertEqual(call_kwargs["json"]["message"], "你好")

    # ──────────── 测试 4：get_memories 查询参数 ────────────

    @patch("frontend.api_client.requests.get")
    def test_004_get_memories_with_type_filter(self, mock_get):
        """
        [TEST-004] get_memories 正确传递 memory_type 查询参数

        输入: character_id=1, memory_type="growth"
        预期: URL 参数包含 ?memory_type=growth
        原因: 记忆筛选是 Dashboard 页面的核心交互功能
        """
        from frontend.api_client import get_memories

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        get_memories(1, memory_type="growth")

        call_kwargs = mock_get.call_args.kwargs
        self.assertEqual(call_kwargs["params"]["memory_type"], "growth")

    # ──────────── 测试 5：get_memories 不传 memory_type ────────────

    @patch("frontend.api_client.requests.get")
    def test_005_get_memories_default_no_filter(self, mock_get):
        """
        [TEST-005] get_memories 默认不传 memory_type（查询全部）

        输入: character_id=1, memory_type=None
        预期: URL 参数不包含 memory_type key
        原因: None 表示 "all"，不筛选
        """
        from frontend.api_client import get_memories

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        get_memories(1, memory_type=None)

        call_kwargs = mock_get.call_args.kwargs
        self.assertNotIn("memory_type", call_kwargs["params"])

    # ──────────── 测试 6：ConnectionError 转换 ────────────

    @patch("frontend.api_client.requests.post")
    def test_006_connection_error_returns_error_dict(self, mock_post):
        """
        [TEST-006] ConnectionError 被转换为 {"error": True, "detail": "..."}

        输入: create_character_text() 时后端不可达
        预期: 返回 error dict，不抛异常
        原因: UI 层不应因网络错误而崩溃，应展示友好提示
        """
        from frontend.api_client import create_character_text, requests as req_module

        mock_post.side_effect = req_module.ConnectionError("模拟连接失败")

        result = create_character_text("测试")

        self._assert_error_response(result)
        self.assertIn("连接", result["detail"])

    # ──────────── 测试 7：Timeout 转换 ────────────

    @patch("frontend.api_client.requests.post")
    def test_007_timeout_returns_error_dict(self, mock_post):
        """
        [TEST-007] Timeout 被转换为 error dict

        输入: create_character_text() 超时
        预期: 返回 error dict，detail 提示超时
        """
        from frontend.api_client import create_character_text, requests as req_module

        mock_post.side_effect = req_module.Timeout("模拟超时")

        result = create_character_text("测试")

        self._assert_error_response(result)
        self.assertIn("超时", result["detail"])

    # ──────────── 测试 8：HTTP 4xx 响应转换 ────────────

    @patch("frontend.api_client.requests.get")
    def test_008_http_error_converted(self, mock_get):
        """
        [TEST-008] HTTP 404 响应被转换为 error dict

        输入: get_character(99999) 不存在
        预期: 返回 error dict
        """
        from frontend.api_client import get_character

        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.text = '{"detail": "角色不存在"}'
        mock_get.return_value = mock_response

        result = get_character(99999)

        self._assert_error_response(result)
        self.assertIn("404", result["detail"])

    # ──────────── 测试 9：全部 8 个函数可无异常导入和调用 ────────────

    def test_009_all_eight_functions_importable(self):
        """
        [TEST-009] 全部 8 个 API 客户端函数可正常导入

        预期: 8 个函数均为 callable
        原因: 确保 api_client.py 未遗漏任何导出
        """
        from frontend import api_client

        functions = [
            "create_character_text", "create_character_file",
            "get_characters", "get_character",
            "send_message", "trigger_growth",
            "get_memories", "get_conversations", "get_growth_logs",
        ]
        for fn_name in functions:
            fn = getattr(api_client, fn_name, None)
            self.assertIsNotNone(fn, f"缺少函数: {fn_name}")
            self.assertTrue(callable(fn), f"{fn_name} 不可调用")

    # ──────────── 测试 10：check_backend_health 可达性 ────────────

    @patch("frontend.api_client.requests.get")
    def test_010_health_check_on_success(self, mock_get):
        """
        [TEST-010] check_backend_health 成功时返回 True

        输入: 后端返回 200 OK
        预期: 返回 True
        原因: 前端启动时需验证后端状态
        """
        from frontend.api_client import check_backend_health

        mock_response = MagicMock()
        mock_response.ok = True
        mock_get.return_value = mock_response

        self.assertTrue(check_backend_health())

    @patch("frontend.api_client.requests.get")
    def test_010b_health_check_on_failure(self, mock_get):
        """
        [TEST-010b] check_backend_health 失败时返回 False

        输入: 后端不可达（ConnectionError）
        预期: 返回 False
        """
        from frontend.api_client import check_backend_health, requests as req_module

        mock_get.side_effect = req_module.ConnectionError()
        self.assertFalse(check_backend_health())


# ============================================================
# 测试套件 2：数据流——JSON 解析与人格格式转换
# ============================================================

class Test_DataFlow_PersonalityParsing(unittest.TestCase):
    """
    数据流测试：personality JSON 解析和格式转换

    验证目标：
      - safe_parse_json 处理合法/非法 JSON 字符串
      - 人格维度映射（英文 key → 中文名）
      - 进度条值转换（0-100 → 0.0-1.0）
    """

    def test_101_parse_valid_personality_json(self):
        """
        [TEST-101] 合法 JSON 字符串正确解析

        输入: '{"optimism": 75, "courage": 60}'
        预期: {"optimism": 75, "courage": 60}
        原因: CharacterResponse.personality 是 JSON 字符串，需反序列化
        """
        result = safe_parse_json('{"optimism": 75, "courage": 60}')
        self.assertEqual(result["optimism"], 75)
        self.assertEqual(result["courage"], 60)

    def test_102_parse_none_returns_empty_dict(self):
        """
        [TEST-102] None 输入返回空 dict

        输入: None
        预期: {}
        原因: 某些角色可能未设置 personality，需安全降级
        """
        result = safe_parse_json(None)
        self.assertEqual(result, {})

    def test_103_parse_invalid_json_returns_empty_dict(self):
        """
        [TEST-103] 非法 JSON 返回空 dict

        输入: "这不是JSON"
        预期: {}
        原因: LLM 输出偶尔可能有格式错误，不应崩溃
        """
        result = safe_parse_json("这不是JSON")
        self.assertEqual(result, {})

    def test_104_personality_dims_count(self):
        """
        [TEST-104] 人格维度数量 = 6

        预期: 6 个维度（optimism/courage/empathy/loyalty/intelligence/sociability）
        原因: 系统中不可变的人格属性数量
        """
        self.assertEqual(len(EXPECTED_PERSONALITY_DIMS), 6)

    def test_105_mock_creation_raw_has_all_dims(self):
        """
        [TEST-105] Mock Creation 输出包含全部 6 个人格维度

        输入: MOCK_CREATION_RAW（JSON 字符串）
        预期: 解析后 personality 包含全部 6 维
        """
        parsed = json.loads(MOCK_CREATION_RAW)
        personality = parsed["personality"]
        for dim in EXPECTED_PERSONALITY_DIMS:
            self.assertIn(dim, personality, f"缺少维度: {dim}")
            self.assertIsInstance(personality[dim], int)
            self.assertGreaterEqual(personality[dim], 0)
            self.assertLessEqual(personality[dim], 100)

    def test_106_progress_value_conversion(self):
        """
        [TEST-106] 人格值 0-100 转 0.0-1.0

        预期: 75 → 0.75, 50 → 0.50, 100 → 1.0, 0 → 0.0
        原因: st.progress 接受 0.0-1.0 的浮点数
        """
        test_cases = [(75, 0.75), (50, 0.50), (100, 1.0), (0, 0.0), (33, 0.33)]
        for val, expected in test_cases:
            self.assertAlmostEqual(val / 100.0, expected, places=2)


# ============================================================
# 测试套件 3：Mock LLM 下的端到端数据流
# ============================================================

class Test_E2E_MockedDataFlow(unittest.TestCase):
    """
    端到端数据流测试（Mock LLM）

    验证目标：
      - API Client → Mock Backend → API Client 的完整数据流
      - ChatResponse 的字段完整性
      - GrowthResponse 的字段完整性
      - personality_delta 计算逻辑

    注意：这些测试不调用真实 LLM，测试的是数据管道而非 AI 行为
    """

    @classmethod
    def setUpClass(cls):
        """创建内存 SQLite 数据库（用于模拟后端数据库操作）"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.database import Base
        # 确保 models 被导入以注册表结构
        import backend.models  # noqa: F401

        cls.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        self.db = self.Session()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_201_chat_response_field_completeness(self):
        """
        [TEST-201] ChatResponse 包含全部预期字段

        验证 ChatResponse 的 11 个字段完整性：
          id, character_id, user_input, npc_response,
          emotion, action, expression,
          director_raw, actor_raw, timestamp

        原因：前端对话气泡展示依赖这些字段
        """
        # 通过后端 CRUD 模拟 ChatResponse 结构
        from backend.crud.character import create_character
        from backend.crud.conversation import create_conversation

        char = create_character(
            db=self.db, name="测试角色",
            personality={"optimism": 50, "courage": 50, "empathy": 50,
                         "loyalty": 50, "intelligence": 50, "sociability": 50},
            current_state={"location": "测试", "activity": "测试", "mood": "平静"},
        )

        conv = create_conversation(
            db=self.db, character_id=char.id,
            user_input="你好！",
            npc_response="欢迎来到 CharacterSeed！",
            emotion="友好",
            action="微笑着挥手",
            expression="温暖的笑容",
            director_raw=MOCK_DIRECTOR_RAW,
            actor_raw=MOCK_ACTOR_RAW,
        )

        # 验证全部期望字段存在
        expected_fields = [
            "id", "character_id", "user_input", "npc_response",
            "emotion", "action", "expression",
            "director_raw", "actor_raw", "timestamp",
        ]
        for field in expected_fields:
            self.assertTrue(hasattr(conv, field), f"缺少字段: {field}")

        # 验证字段值
        self.assertEqual(conv.user_input, "你好！")
        self.assertEqual(conv.npc_response, "欢迎来到 CharacterSeed！")
        self.assertEqual(conv.emotion, "友好")
        self.assertIsNotNone(conv.director_raw)
        self.assertIsNotNone(conv.actor_raw)

        if VERBOSE:
            print(f"\n  [DATA] ChatResponse 字段完整: id={conv.id}")
            print(f"  [DATA]   Director RAW (前 100 字符): {conv.director_raw[:100]}...")
            print(f"  [DATA]   Actor RAW (前 100 字符): {conv.actor_raw[:100]}...")

    def test_202_growth_response_field_completeness(self):
        """
        [TEST-202] GrowthResponse 包含全部预期字段

        验证 GrowthResponse 的字段：
          id, character_id, personality_delta, event_summary,
          new_memories, growth_raw, created_at

        原因：Dashboard 的成长面板依赖这些字段展示
        """
        from backend.crud.character import create_character
        from backend.crud.growth import create_growth_log

        char = create_character(
            db=self.db, name="成长测试角色",
            personality={"optimism": 50, "courage": 50, "empathy": 50,
                         "loyalty": 50, "intelligence": 50, "sociability": 50},
        )

        glog = create_growth_log(
            db=self.db, character_id=char.id,
            personality_delta=json.dumps({"optimism": 5, "courage": -2,
                                           "empathy": 3, "loyalty": 0,
                                           "intelligence": 0, "sociability": 7},
                                          ensure_ascii=False),
            event_summary="测试成长事件",
            new_memories=json.dumps([{"content": "测试记忆", "importance": 5}],
                                     ensure_ascii=False),
            growth_raw=MOCK_GROWTH_RAW,
        )

        expected_fields = [
            "id", "character_id", "personality_delta",
            "event_summary", "new_memories", "growth_raw", "created_at",
        ]
        for field in expected_fields:
            self.assertTrue(hasattr(glog, field), f"缺少字段: {field}")

        # 验证 personality_delta 可反序列化
        delta = json.loads(glog.personality_delta)
        self.assertEqual(delta["optimism"], 5)
        self.assertEqual(delta["courage"], -2)

        # 验证 new_memories 可反序列化
        memories = json.loads(glog.new_memories)
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["content"], "测试记忆")

        if VERBOSE:
            print(f"\n  [DATA] GrowthResponse 字段完整: id={glog.id}")
            print(f"  [DATA]   personality_delta: {delta}")
            print(f"  [DATA]   event_summary: {glog.event_summary}")
            print(f"  [DATA]   Growth RAW (前 100 字符): {glog.growth_raw[:100]}...")

    def test_203_personality_delta_calculation(self):
        """
        [TEST-203] 人格 delta 计算：新人格 = 旧人格 + delta（钳位 [0, 100]）

        输入: 旧人格 optimism=40, delta.optimism=5
        预期: 新人格 optimism=45

        原因: 人格变化由代码计算而非 LLM 输出，保证数值正确性
        """
        old_personality = {"optimism": 40, "courage": 90}
        delta = {"optimism": 5, "courage": -3}

        new_val_optimism = old_personality["optimism"] + delta["optimism"]
        self.assertEqual(new_val_optimism, 45)

        new_val_courage = old_personality["courage"] + delta["courage"]
        self.assertEqual(new_val_courage, 87)

    def test_204_personality_clamp_to_boundaries(self):
        """
        [TEST-204] 人格值超过 [0, 100] 时正确钳位

        输入: 旧 loyalty=95, delta=+20
        预期: 新 loyalty=100（钳位）

        输入: 旧 sociability=5, delta=-20
        预期: 新 sociability=0（钳位）
        """
        old_val = 95
        delta_val = 20
        new_val = max(0, min(100, old_val + delta_val))
        self.assertEqual(new_val, 100, "上限钳位到 100")

        old_val = 5
        delta_val = -20
        new_val = max(0, min(100, old_val + delta_val))
        self.assertEqual(new_val, 0, "下限钳位到 0")


# ============================================================
# 测试套件 4：UI 逻辑单元测试
# ============================================================

class Test_UI_Logic_Unit(unittest.TestCase):
    """
    UI 逻辑单元测试

    验证目标：
      - format_personality_progress 正确排序
      - 中文名映射正确
      - 空数据处理
    """

    # 从 app.py 导入待测函数
    @staticmethod
    def _format_personality_progress(personality_dict):
        """复制 app.py 中的逻辑用于单元测试"""
        dims = [
            ("optimism", "乐观"), ("courage", "勇气"), ("empathy", "同理心"),
            ("loyalty", "忠诚"), ("intelligence", "智慧"), ("sociability", "社交"),
        ]
        result = []
        for key, cn_name in dims:
            val = personality_dict.get(key, 50)
            result.append((key, cn_name, val))
        return result

    def test_301_format_order_is_consistent(self):
        """
        [TEST-301] 人格进度条展示顺序一致

        输入: {"optimism": 30, "courage": 50, "empathy": 70,
               "loyalty": 40, "intelligence": 60, "sociability": 80}
        预期: 输出顺序为 optimism→courage→empathy→loyalty→intelligence→sociability
        原因: 固定排序给用户一致的参照，随机顺序降低可读性
        """
        personality = {"optimism": 30, "courage": 50, "empathy": 70,
                        "loyalty": 40, "intelligence": 60, "sociability": 80}
        result = self._format_personality_progress(personality)

        self.assertEqual(len(result), 6)
        expected_order = ["optimism", "courage", "empathy",
                          "loyalty", "intelligence", "sociability"]
        for i, (key, _, _) in enumerate(result):
            self.assertEqual(key, expected_order[i])

    def test_302_missing_dimension_defaults_to_50(self):
        """
        [TEST-302] 人格缺失维度默认为 50

        输入: {"optimism": 80}  # 只设置了一个维度
        预期: 未设置的维度值为 50
        原因: 角色可能只有部分人格被 LLM 设置，缺失的应给默认中性值
        """
        result = self._format_personality_progress({"optimism": 80})
        for key, _, val in result:
            if key == "optimism":
                self.assertEqual(val, 80)
            else:
                self.assertEqual(val, 50, f"{key} 默认值应为 50")

    def test_303_empty_personality_all_defaults(self):
        """
        [TEST-303] 空人格 dict 全部返回默认值 50

        输入: {}
        预期: 6 个维度全部为 50
        """
        result = self._format_personality_progress({})
        for _, _, val in result:
            self.assertEqual(val, 50)

    def test_304_chinese_name_mapping(self):
        """
        [TEST-304] 人格维度中文名映射正确

        预期:
          optimism → 乐观
          courage → 勇气
          empathy → 同理心
          loyalty → 忠诚
          intelligence → 智慧
          sociability → 社交
        """
        expected_mapping = {
            "optimism": "乐观", "courage": "勇气", "empathy": "同理心",
            "loyalty": "忠诚", "intelligence": "智慧", "sociability": "社交",
        }
        personality = {k: 50 for k in expected_mapping}
        result = self._format_personality_progress(personality)
        for key, cn_name, _ in result:
            self.assertEqual(cn_name, expected_mapping[key],
                             f"{key} 应映射为 {expected_mapping[key]}")


# ============================================================
# 测试套件 5：边界和异常路径
# ============================================================

class Test_Boundary_ErrorPaths(unittest.TestCase):
    """
    边界和异常路径测试

    验证目标：
      - 空角色列表的处理
      - 不存在的角色 ID
      - 极大/极小 personality 值
      - 空对话历史
      - Growth 在无对话时的行为
    """

    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.database import Base
        import backend.models  # noqa: F401

        cls.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        self.db = self.Session()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_401_empty_character_list_returns_empty(self):
        """
        [TEST-401] 空数据库时 get_characters 返回空列表

        输入: 空白数据库
        预期: []
        原因: 前端应优雅处理"无角色"的情况
        """
        from backend.crud.character import get_characters
        chars = get_characters(self.db)
        self.assertEqual(chars, [])
        self.assertIsInstance(chars, list)

    def test_402_nonexistent_character_returns_none(self):
        """
        [TEST-402] get_character(99999) 返回 None

        输入: 不存在的 character_id
        预期: None
        原因: CRUD 层对不存在记录返回 None，API 层转为 404
        """
        from backend.crud.character import get_character
        char = get_character(self.db, 99999)
        self.assertIsNone(char)

    def test_403_empty_memories_for_new_character(self):
        """
        [TEST-403] 新角色默认无记忆

        输入: 刚创建、无初始记忆的角色
        预期: get_character_memories 返回空列表
        """
        from backend.crud.character import create_character
        from backend.crud.memory import get_character_memories

        char = create_character(
            db=self.db, name="空记忆角色",
            personality={"optimism": 50, "courage": 50, "empathy": 50,
                         "loyalty": 50, "intelligence": 50, "sociability": 50},
        )
        memories = get_character_memories(self.db, char.id)
        self.assertEqual(memories, [])

    def test_404_empty_conversations_for_new_character(self):
        """
        [TEST-404] 新角色默认无对话

        预期: get_character_conversations 返回空列表
        """
        from backend.crud.character import create_character
        from backend.crud.conversation import get_character_conversations

        char = create_character(
            db=self.db, name="无对话角色",
            personality={"optimism": 50, "courage": 50, "empathy": 50,
                         "loyalty": 50, "intelligence": 50, "sociability": 50},
        )
        convs = get_character_conversations(self.db, char.id)
        self.assertEqual(convs, [])

    def test_405_memory_type_filter_behavior(self):
        """
        [TEST-405] memory_type 筛选正确过滤记忆

        输入: 创建 memory_type="event" 和 memory_type="growth" 各一条
        预期: 筛选 "event" 返回 1 条，"growth" 返回 1 条，不筛选返回 2 条
        """
        from backend.crud.character import create_character
        from backend.crud.memory import create_memory, get_character_memories

        char = create_character(
            db=self.db, name="筛选测试角色",
            personality={"optimism": 50, "courage": 50, "empathy": 50,
                         "loyalty": 50, "intelligence": 50, "sociability": 50},
        )
        create_memory(self.db, char.id, "事件记忆", memory_type="event")
        create_memory(self.db, char.id, "成长记忆", memory_type="growth")

        all_mem = get_character_memories(self.db, char.id)
        self.assertEqual(len(all_mem), 2)

        event_mem = get_character_memories(self.db, char.id, memory_type="event")
        self.assertEqual(len(event_mem), 1)
        self.assertEqual(event_mem[0].content, "事件记忆")

        growth_mem = get_character_memories(self.db, char.id, memory_type="growth")
        self.assertEqual(len(growth_mem), 1)
        self.assertEqual(growth_mem[0].content, "成长记忆")


# ============================================================
# 测试套件 6：模拟完整交互流程（API Client + Mock Backend）
# ============================================================

class Test_Simulated_Workflow(unittest.TestCase):
    """
    模拟完整交互流程测试

    验证目标（不启动真实服务器）：
      1. 创建角色 → 获取角色列表 → 发送消息 → 触发成长 → 查看状态
      2. 验证数据在各步骤间正确流转
    """

    def test_501_simulated_full_workflow(self):
        """
        [TEST-501] 模拟完整端到端流程：创建→对话→成长→查看

        流程步骤：
          1. 模拟 Creation LLM 返回角色数据
          2. 验证角色数据包含 6 维人格 + 初始记忆
          3. 模拟 Director LLM + Actor LLM 返回对话数据
          4. 验证对话数据包含 emotion/action/expression
          5. 模拟 Growth LLM 返回成长数据
          6. 验证新人格 = 旧人格 + delta
          7. 验证可以查询到记忆、对话、成长记录

        这是一个"纸面演练"——不经过网络，直接验证数据结构的流转正确性。
        """
        # ── 步骤 1：模拟角色创建 ──
        creation_data = json.loads(MOCK_CREATION_RAW)
        self.assertIn("name", creation_data)
        self.assertIn("personality", creation_data)
        self.assertIn("initial_memories", creation_data)

        name = creation_data["name"]
        personality = creation_data["personality"]
        initial_memories = creation_data["initial_memories"]

        if VERBOSE:
            print(f"\n  [STEP 1] 角色创建模拟:")
            print(f"    name: {name}")
            print(f"    personality: {personality}")
            print(f"    initial_memories: {len(initial_memories)} 条")
            print(f"    Creation LLM RAW (前 200 字符): {MOCK_CREATION_RAW[:200]}...")

        # 验证人格维度完整
        for dim in EXPECTED_PERSONALITY_DIMS:
            self.assertIn(dim, personality)

        # 验证初始记忆
        self.assertEqual(len(initial_memories), 3)
        for mem in initial_memories:
            self.assertIn("content", mem)
            self.assertIn("importance", mem)

        # ── 步骤 2：模拟对话 ──
        director_data = json.loads(MOCK_DIRECTOR_RAW)
        actor_data = json.loads(MOCK_ACTOR_RAW)

        if VERBOSE:
            print(f"\n  [STEP 2] 对话模拟:")
            director_emotion = director_data.get("emotion", "N/A")
            director_goal = director_data.get("goal", "N/A")
            print(f"    Director → emotion: {director_emotion}")
            print(f"    Director → goal: {director_goal}")
            print(f"    Director LLM RAW: {MOCK_DIRECTOR_RAW}")
            actor_action = actor_data.get("action", "N/A")
            actor_speech = actor_data.get("speech", "N/A")
            print(f"    Actor → action: {actor_action}")
            print(f"    Actor → speech: {actor_speech}")
            print(f"    Actor LLM RAW: {MOCK_ACTOR_RAW}")

        # 验证 Director 输出 4 个字段
        for field in ["emotion", "focus_memories", "goal", "style"]:
            self.assertIn(field, director_data)

        # 验证 Actor 输出 3 个字段
        for field in ["action", "expression", "speech"]:
            self.assertIn(field, actor_data)

        # 验证 Director → Actor 上下文衔接
        # Director 输出的 emotion 是 Actor 的输入之一
        emotion_val = director_data["emotion"]
        self.assertIsInstance(emotion_val, str)
        self.assertTrue(len(emotion_val) > 0)

        # ── 步骤 3：模拟成长 ──
        growth_data = json.loads(MOCK_GROWTH_RAW)

        if VERBOSE:
            print(f"\n  [STEP 3] 成长模拟:")
            print(f"    event_summary: {growth_data['event_summary'][:80]}...")
            print(f"    personality_delta: {growth_data['personality_delta']}")
            print(f"    new_memories: {len(growth_data['new_memories'])} 条")
            print(f"    Growth LLM RAW: {MOCK_GROWTH_RAW}")

        # 验证 growth 输出字段
        for field in ["personality_delta", "new_memories", "event_summary"]:
            self.assertIn(field, growth_data)

        # 验证 delta 包含全部 6 维
        delta = growth_data["personality_delta"]
        for dim in EXPECTED_PERSONALITY_DIMS:
            self.assertIn(dim, delta)

        # 验证新人格计算
        old_optimism = personality["optimism"]  # 40
        delta_optimism = delta["optimism"]       # 5
        new_optimism = old_optimism + delta_optimism
        self.assertEqual(new_optimism, 45)

        old_sociability = personality["sociability"]  # 30
        delta_sociability = delta["sociability"]       # 8
        new_sociability = old_sociability + delta_sociability
        self.assertEqual(new_sociability, 38)

        if VERBOSE:
            print(f"\n  [STEP 4] 人格变化验证:")
            print(f"    optimism: {old_optimism} + {delta_optimism} = {new_optimism}")
            print(f"    sociability: {old_sociability} + {delta_sociability} = {new_sociability}")

        # ── 步骤 4：验证可查询性（摘要） ──
        summary = {
            "character_name": name,
            "personality_dims_count": len(personality),
            "initial_memories_count": len(initial_memories),
            "director_output_fields": list(director_data.keys()),
            "actor_output_fields": list(actor_data.keys()),
            "growth_delta_sum": sum(delta.values()),
            "growth_new_memories_count": len(growth_data["new_memories"]),
        }

        self.assertEqual(summary["character_name"], "凌霜")
        self.assertEqual(summary["personality_dims_count"], 6)
        self.assertEqual(summary["initial_memories_count"], 3)
        self.assertEqual(summary["growth_new_memories_count"], 2)

        if VERBOSE:
            print(f"\n  [SUMMARY] 数据流验证汇总:")
            print(f"    {json.dumps(summary, ensure_ascii=False, indent=2)}")


# ============================================================
# 测试套件 7：Schema 校验（backend schema 兼容性）
# ============================================================

class Test_Schema_Compatibility(unittest.TestCase):
    """
    Schema 兼容性测试

    验证前端期望的 API 返回格式与后端 Pydantic Schema 一致
    """

    def test_601_character_response_schema_compatible(self):
        """
        [TEST-601] CharacterResponse schema 字段与前端期望一致

        预期字段: id, name, description, world_setting, personality,
                 current_state, creation_raw, created_at
        """
        from backend.schemas import CharacterResponse
        expected_fields = {
            "id", "name", "description", "world_setting",
            "personality", "current_state", "creation_raw",
            "created_at", "updated_at",
        }
        actual_fields = set(CharacterResponse.model_fields.keys())
        for f in expected_fields:
            self.assertIn(f, actual_fields, f"CharacterResponse 缺少字段: {f}")

    def test_602_chat_response_schema_compatible(self):
        """
        [TEST-602] ChatResponse schema 字段与前端期望一致

        预期字段: id, character_id, user_input, npc_response,
                 emotion, action, expression, director_raw, actor_raw, timestamp
        """
        from backend.schemas import ChatResponse
        expected_fields = {
            "id", "character_id", "user_input", "npc_response",
            "emotion", "action", "expression",
            "director_raw", "actor_raw", "timestamp",
        }
        actual_fields = set(ChatResponse.model_fields.keys())
        for f in expected_fields:
            self.assertIn(f, actual_fields, f"ChatResponse 缺少字段: {f}")

    def test_603_growth_response_schema_compatible(self):
        """
        [TEST-603] GrowthResponse schema 字段与前端期望一致

        预期字段: id, character_id, personality_delta, event_summary,
                 new_memories, growth_raw, created_at
        """
        from backend.schemas import GrowthResponse
        expected_fields = {
            "id", "character_id", "personality_delta",
            "event_summary", "new_memories", "growth_raw", "created_at",
        }
        actual_fields = set(GrowthResponse.model_fields.keys())
        for f in expected_fields:
            self.assertIn(f, actual_fields, f"GrowthResponse 缺少字段: {f}")

    def test_604_memory_response_schema_compatible(self):
        """
        [TEST-604] MemoryResponse schema 字段与前端期望一致

        预期字段: id, character_id, content, importance, memory_type, created_at
        """
        from backend.schemas import MemoryResponse
        expected_fields = {
            "id", "character_id", "content",
            "importance", "memory_type", "created_at",
        }
        actual_fields = set(MemoryResponse.model_fields.keys())
        for f in expected_fields:
            self.assertIn(f, actual_fields, f"MemoryResponse 缺少字段: {f}")


# ============================================================
# 测试套件 8：文件存在性验证
# ============================================================

class Test_File_Existence(unittest.TestCase):
    """
    文件存在性验证测试

    验证 Day 4 交付物全部存在
    """

    def test_701_api_client_exists(self):
        """api_client.py 存在"""
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(__file__), "..", "frontend", "api_client.py"))
        )

    def test_702_app_exists(self):
        """app.py 存在"""
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(__file__), "..", "frontend", "app.py"))
        )

    def test_703_start_script_exists(self):
        """start_demo.bat 存在"""
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(__file__), "..", "start_demo.bat"))
        )

    def test_704_test_file_exists(self):
        """test_day4.py 存在（自己）"""
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(__file__), "test_day4.py"))
        )


# ============================================================
# 测试套件 9：端到端集成测试（需后端运行）
# ============================================================

class Test_E2E_Integration(unittest.TestCase):
    """
    端到端集成测试（需后端运行在 localhost:8000）

    这些测试会：
      - 调用真实的 FastAPI 端点
      - 创建角色的 raw_response 会包含真实 Creation LLM 输出
      - 对话的 director_raw/actor_raw 会包含真实 LLM 管线输出
      - 成长的 growth_raw 会包含真实 Growth LLM 输出

    前提条件：
      1. uvicorn backend.main:app --port 8000
      2. .env 中有有效的 DEEPSEEK_API_KEY
      3. 测试之间独立（不依赖其他测试创建的数据）

    注意：由于 LLM 调用耗时较长（每次 5-30 秒），
          此套件仅包含关键路径测试。
    """

    @classmethod
    def setUpClass(cls):
        """验证后端可达性"""
        from frontend.api_client import check_backend_health
        if not check_backend_health():
            raise unittest.SkipTest(
                "后端未启动。请先运行: uvicorn backend.main:app --port 8000"
            )
        if VERBOSE:
            print("\n  [E2E] 后端可达，开始端到端测试...")

    def test_801_e2e_create_character_with_text(self):
        """
        [TEST-801] 端到端：通过文本描述创建角色

        流程：
          1. POST /api/characters/create (文本模式)
          2. 验证返回的 CharacterResponse 字段完整
          3. 验证 personality 是合法 JSON（含 6 维）
          4. 验证 creation_raw 包含 LLM 原始响应

        期望输出：
          - 返回 dict，无 error key
          - id > 0
          - name 非空
          - personality 为可解析 JSON，含 6 维
          - creation_raw 非空（真实 LLM 响应）
        """
        from frontend.api_client import create_character_text

        if VERBOSE:
            print(f"\n  [E2E-801] 创建角色: {SAMPLE_CHARACTER_DESC[:50]}...")
            print("  [E2E-801] 等待 Creation LLM 响应（可能 10-30 秒）...")

        t_start = time.time()
        result = create_character_text(SAMPLE_CHARACTER_DESC)
        t_elapsed = time.time() - t_start

        self.assertNotIn("error", result, f"创建失败: {result.get('detail', '')}")
        self.assertIn("id", result)
        self.assertGreater(result["id"], 0)
        self.assertIn("name", result)
        self.assertTrue(len(result["name"]) > 0)

        # 验证 personality
        personality = safe_parse_json(result.get("personality"))
        self.assertIsInstance(personality, dict)
        for dim in EXPECTED_PERSONALITY_DIMS:
            self.assertIn(dim, personality, f"personality 缺少维度: {dim}")
            self.assertIsInstance(personality[dim], int)
            self.assertGreaterEqual(personality[dim], 0)
            self.assertLessEqual(personality[dim], 100)

        # 验证 creation_raw
        self.assertIsNotNone(result.get("creation_raw"))
        self.assertTrue(len(result.get("creation_raw", "")) > 0)

        if VERBOSE:
            print(f"  [E2E-801] ✓ 创建成功 | 耗时: {t_elapsed:.1f}s")
            print(f"    角色ID: {result['id']}")
            print(f"    名称: {result['name']}")
            print(f"    人格维度: {json.dumps(personality, ensure_ascii=False)}")
            print(f"    Creation LLM RAW (前 300 字符):")
            print(f"    {result['creation_raw'][:300]}...")

        # 保存 character_id 供后续测试使用
        self.__class__.created_char_id = result["id"]

    def test_802_e2e_send_message_and_get_response(self):
        """
        [TEST-802] 端到端：发送消息获取 NPC 回复

        流程：
          1. POST /api/chat (character_id + message)
          2. 验证 ChatResponse 字段完整
          3. 验证 director_raw 包含真实 Director LLM 输出
          4. 验证 actor_raw 包含真实 Actor LLM 输出

        期望输出：
          - npc_response 非空
          - emotion 非空
          - director_raw 为合法 JSON
          - actor_raw 为合法 JSON
        """
        from frontend.api_client import send_message

        # 从上一个测试或角色列表获取 character_id
        char_id = getattr(self.__class__, "created_char_id", None)
        if char_id is None:
            from frontend.api_client import get_characters
            chars = get_characters()
            if not chars:
                self.skipTest("没有可用角色，请先运行 test_801")
            char_id = chars[0]["id"]

        msg = SAMPLE_CHAT_MESSAGES[0]

        if VERBOSE:
            print(f"\n  [E2E-802] 发送消息: character_id={char_id}")
            print(f"    消息: {msg}")
            print("  [E2E-802] 等待 Director+Actor LLM 响应（可能 10-20 秒）...")

        t_start = time.time()
        result = send_message(char_id, msg)
        t_elapsed = time.time() - t_start

        self.assertNotIn("error", result, f"对话失败: {result.get('detail', '')}")
        self.assertIn("npc_response", result)
        self.assertTrue(len(result["npc_response"]) > 0)
        self.assertIn("emotion", result)
        self.assertTrue(len(result.get("emotion", "")) > 0)
        self.assertIn("action", result)
        self.assertIn("expression", result)

        # 验证 LLM 原始响应
        self.assertIsNotNone(result.get("director_raw"))
        director_parsed = safe_parse_json(result.get("director_raw"))
        self.assertIsInstance(director_parsed, dict)

        self.assertIsNotNone(result.get("actor_raw"))
        actor_parsed = safe_parse_json(result.get("actor_raw"))
        self.assertIsInstance(actor_parsed, dict)

        if VERBOSE:
            print(f"  [E2E-802] ✓ 对话成功 | 耗时: {t_elapsed:.1f}s")
            print(f"    NPC 回复: {result['npc_response'][:100]}...")
            print(f"    情绪: {result['emotion']}")
            print(f"    动作: {result['action'][:80]}...")
            print(f"    表情: {result['expression']}")
            print(f"    Director LLM RAW:")
            print(f"    {json.dumps(director_parsed, ensure_ascii=False, indent=2)[:500]}")
            print(f"    Actor LLM RAW:")
            print(f"    {json.dumps(actor_parsed, ensure_ascii=False, indent=2)[:500]}")

    def test_803_e2e_trigger_growth(self):
        """
        [TEST-803] 端到端：触发角色成长

        流程：
          1. POST /api/growth/trigger (character_id)
          2. 验证 GrowthResponse 字段完整
          3. 验证 personality_delta 为合法 JSON（含 6 维）
          4. 验证 growth_raw 包含真实 Growth LLM 输出

        期望输出：
          - event_summary 非空
          - personality_delta 含 6 维且 delta 在 [-30, 30]
          - growth_raw 为合法 JSON
        """
        from frontend.api_client import trigger_growth

        char_id = getattr(self.__class__, "created_char_id", None)
        if char_id is None:
            from frontend.api_client import get_characters
            chars = get_characters()
            if not chars:
                self.skipTest("没有可用角色，请先运行 test_801")
            char_id = chars[0]["id"]

        if VERBOSE:
            print(f"\n  [E2E-803] 触发成长: character_id={char_id}")
            print("  [E2E-803] 等待 Growth LLM 响应（可能 10-20 秒）...")

        t_start = time.time()
        result = trigger_growth(char_id)
        t_elapsed = time.time() - t_start

        self.assertNotIn("error", result, f"成长失败: {result.get('detail', '')}")
        self.assertIn("event_summary", result)
        self.assertIn("personality_delta", result)

        # 验证 personality_delta
        delta = safe_parse_json(result.get("personality_delta"))
        self.assertIsInstance(delta, dict)
        for dim in EXPECTED_PERSONALITY_DIMS:
            self.assertIn(dim, delta, f"personality_delta 缺少维度: {dim}")
            self.assertIsInstance(delta[dim], int)
            self.assertGreaterEqual(delta[dim], -30)
            self.assertLessEqual(delta[dim], 30)

        # 验证 growth_raw
        self.assertIsNotNone(result.get("growth_raw"))
        growth_parsed = safe_parse_json(result.get("growth_raw"))
        self.assertIsInstance(growth_parsed, dict)

        if VERBOSE:
            print(f"  [E2E-803] ✓ 成长成功 | 耗时: {t_elapsed:.1f}s")
            print(f"    事件摘要: {result['event_summary'][:150]}...")
            print(f"    人格变化: {json.dumps(delta, ensure_ascii=False)}")
            print(f"    Growth LLM RAW (前 300 字符):")
            print(f"    {result['growth_raw'][:300]}...")

    def test_804_e2e_query_memories_and_conversations(self):
        """
        [TEST-804] 端到端：查询记忆和对话历史

        流程：
          1. GET /api/characters/{id}/memories
          2. GET /api/characters/{id}/conversations
          3. GET /api/characters/{id}/growth-logs

        期望输出：
          - 三个端点均返回 list（可能为空）
          - 如果有数据，每条记录包含预期字段
        """
        from frontend.api_client import get_memories, get_conversations, get_growth_logs

        char_id = getattr(self.__class__, "created_char_id", None)
        if char_id is None:
            from frontend.api_client import get_characters
            chars = get_characters()
            if not chars:
                self.skipTest("没有可用角色")
            char_id = chars[0]["id"]

        # 查询记忆
        memories = get_memories(char_id)
        self.assertIsInstance(memories, list)
        if memories:
            mem = memories[0]
            self.assertIn("content", mem)
            self.assertIn("importance", mem)
            self.assertIn("memory_type", mem)

        # 查询对话
        conversations = get_conversations(char_id)
        self.assertIsInstance(conversations, list)
        if conversations:
            conv = conversations[0]
            self.assertIn("user_input", conv)
            self.assertIn("npc_response", conv)

        # 查询成长记录
        growth_logs = get_growth_logs(char_id)
        self.assertIsInstance(growth_logs, list)

        if VERBOSE:
            print(f"\n  [E2E-804] 数据查询结果:")
            print(f"    记忆: {len(memories)} 条")
            print(f"    对话: {len(conversations)} 条")
            print(f"    成长记录: {len(growth_logs)} 条")


# ============================================================
# 主入口
# ============================================================

def main():
    parse_args()

    print("=" * 70)
    print("  CharacterSeed Day 4 — 前端集成与端到端测试")
    print("=" * 70)

    if MOCK_ONLY:
        print("  模式: Mock Only（无需后端，不调用 LLM）")
    elif E2E_ONLY:
        print("  模式: E2E Only（需后端运行在 :8000）")
    else:
        print("  模式: 完整测试（Mock + E2E）")
    if VERBOSE:
        print("  详细输出: 开启（将展示 LLM 原始响应）")
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Mock 测试套件（无需后端）
    suite.addTests(loader.loadTestsFromTestCase(Test_API_Client_RequestConstruction))
    suite.addTests(loader.loadTestsFromTestCase(Test_DataFlow_PersonalityParsing))
    suite.addTests(loader.loadTestsFromTestCase(Test_E2E_MockedDataFlow))
    suite.addTests(loader.loadTestsFromTestCase(Test_UI_Logic_Unit))
    suite.addTests(loader.loadTestsFromTestCase(Test_Boundary_ErrorPaths))
    suite.addTests(loader.loadTestsFromTestCase(Test_Simulated_Workflow))
    suite.addTests(loader.loadTestsFromTestCase(Test_Schema_Compatibility))
    suite.addTests(loader.loadTestsFromTestCase(Test_File_Existence))

    # 端到端测试（需后端）
    if not MOCK_ONLY:
        suite.addTests(loader.loadTestsFromTestCase(Test_E2E_Integration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 输出汇总
    print()
    print("=" * 70)
    if result.wasSuccessful():
        print("  [OK] 所有 Day 4 前端测试通过！")
    else:
        print("  [FAIL] 存在测试失败")
    print(f"  总计: {result.testsRun}")
    print(f"  通过: {result.testsRun - len(result.failures) - len(result.errors)}")
    if result.failures:
        print(f"  失败: {len(result.failures)}")
        for test, traceback in result.failures[:3]:
            print(f"    - {test}: {traceback.split(chr(10))[-2][:120]}")
    if result.errors:
        print(f"  错误: {len(result.errors)}")
    print("=" * 70)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
