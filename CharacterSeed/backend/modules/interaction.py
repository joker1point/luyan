"""
Day 2 — 交互运行时（Interaction Runtime）

设计理念：采用"注意力聚焦 + 行为生成"双 LLM 管路。
    Director.analyze() → 角色"感知"世界，决定该关注什么
    Actor.generate()   → 角色"表达"自我，生成动作/表情/语言

该架构的核心优势：
  - 可解释性：Director 的中间输出（emotion / focus_memories / goal）
              可独立可视化，让观察者看到"角色的思考过程"
  - 可调试性：两个 LLM 独立调试 prompt 与温度参数，互不干扰
  - 鲁棒性：  每个 LLM 调用点都有独立降级策略，任一失败不影响管线完整性

温度参数选择依据：
  - Director temperature=0.5：注意力聚焦需要逻辑一致性，偏低减少随机性
  - Actor temperature=0.8：  行为/语言生成需要创造性，偏高避免千篇一律
"""

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Dict, Any, Optional, List, Tuple, Generator
from datetime import datetime

import hashlib

from sqlalchemy.orm import Session

from backend.services.llm_service import LLMService
from backend.crud import character as character_crud
from backend.crud import memory as memory_crud
from backend.crud import conversation as conversation_crud


# ============================================================================
# 响应缓存：5min TTL + LRU(512) 进程内缓存
# ============================================================================
# 设计动机：
#   - 用户重试同一问题（前次因网络/超时失败）可直接复用 LLM 输出，跳过两次 LLM 调用
#   - 短时间重复提问（多轮测试、用户手抖）也可命中
#   - 同一 session 内的相同问题在 history 变长后会失效（key 包含 history 轮数）
#
# Key: f"{character_id}:{user_input_hash16}:{session_id}:{history_len}"
# Value: 完整 ChatResponse 字典（不含 conversation.id，持久化时新建一行）
# TTL: 5 分钟；MAX_SIZE: 512 条（覆盖活跃用户数）
#
# 命中条件（必须全部满足）：
#   1. character_id + session_id + user_input 完全一致
#   2. history_turns 在缓存时和读取时一致（避免多轮误命中）
#   3. 缓存未过期
#   4. LLM 真实成功（fallback 降级结果不入缓存）
#
# 失效策略：
#   - LRU 淘汰（OrderedDict.move_to_end + popitem(last=False)）
#   - 过期（access_time + TTL < now）
#   - 角色成长/记忆更新后调用 invalidate(character_id) 主动清理
_CACHE_TTL = 300.0
_CACHE_MAX = 512
_response_cache: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_cache_lock = threading.Lock()
_cache_hits = 0
_cache_misses = 0

# 异步持久化的中间结果：result_key → {"id": int, "ok": bool}
# 流式 done 事件可立即 yield，真实 conversation.id 通过此 dict 后台更新
_persist_results: Dict[str, Dict[str, Any]] = {}


def _make_cache_key(character_id: int, user_input: str, session_id: Optional[int], history_len: int) -> str:
    """
    生成缓存 key。

    设计决策：key 不包含 session_id。
      - 原因：用户传不传 session_id 都会触发，但同一问题在同角色下答案通常一致
      - 好处：避免"用户没传 session_id → 每次新建 session → 缓存永远不命中"陷阱
      - 代价：跨 session 的相同问题会复用回答（多轮上下文已由 history bucket 控制）
      - history_len 用分桶：[(0,1), (2,3), (4,5), (6,7), (8+)] 五个桶
    """
    h = hashlib.md5(user_input.encode("utf-8")).hexdigest()[:16]
    # 分桶：0、2、4、6、8+
    if history_len <= 1:
        bucket = 0
    elif history_len <= 3:
        bucket = 2
    elif history_len <= 5:
        bucket = 4
    elif history_len <= 7:
        bucket = 6
    else:
        bucket = 8
    return f"{character_id}:{h}:b{bucket}"


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    """线程安全的 LRU 读取（命中时刷新 access_time）。"""
    global _cache_hits, _cache_misses
    with _cache_lock:
        if key not in _response_cache:
            _cache_misses += 1
            return None
        ts, payload = _response_cache[key]
        if time.monotonic() - ts > _CACHE_TTL:
            _response_cache.pop(key)
            _cache_misses += 1
            return None
        # 刷新 LRU
        _response_cache.move_to_end(key)
        _cache_hits += 1
        return payload


def _cache_put(key: str, payload: Dict[str, Any]) -> None:
    """线程安全的写入。满时淘汰最旧。"""
    with _cache_lock:
        if key in _response_cache:
            _response_cache.move_to_end(key)
        _response_cache[key] = (time.monotonic(), payload)
        while len(_response_cache) > _CACHE_MAX:
            _response_cache.popitem(last=False)


def cache_invalidate(character_id: Optional[int] = None) -> int:
    """
    清空缓存。可选按 character_id 过滤。
    场景：角色成长、记忆更新、性格变化后调用，让下一次对话重新走 LLM。
    """
    with _cache_lock:
        if character_id is None:
            n = len(_response_cache)
            _response_cache.clear()
            return n
        prefix = f"{character_id}:"
        keys = [k for k in _response_cache if k.startswith(prefix)]
        for k in keys:
            _response_cache.pop(k)
        return len(keys)


def cache_stats() -> Dict[str, Any]:
    """导出缓存命中率（调试/监控用）。"""
    with _cache_lock:
        total = _cache_hits + _cache_misses
        hit_rate = (_cache_hits / total) if total else 0.0
        return {
            "size": len(_response_cache),
            "max_size": _CACHE_MAX,
            "ttl_sec": _CACHE_TTL,
            "hits": _cache_hits,
            "misses": _cache_misses,
            "hit_rate": round(hit_rate, 4),
        }

logger = logging.getLogger(__name__)


# ============================================================================
# 角色基础数据解析缓存：60s TTL + LRU(256) 进程内缓存
# ============================================================================
# 设计动机：
#   - 每次 run / run_stream 都会调 _safe_load_json 解析 personality / current_state
#   - 这两个字段是 JSON 字符串，每次 json.loads 有 ~0.1-0.5ms 开销
#   - 在多轮测试、批量对话场景下，同一角色短时间内会被反复读取
#   - 缓存住解析后的 dict，减少重复解析
#
# Key: character_id
# Value: (timestamp, (personality_dict, current_state_dict))
# TTL: 60 秒
# MAX: 256 条
#
# 失效策略：
#   - LRU 淘汰
#   - 过期
#   - 角色创建/更新/删除后清空（由调用方在 CRUD 后调用 char_data_cache_invalidate）
#   - reload_all_llm 时清空
_CHAR_DATA_TTL = 60.0
_CHAR_DATA_MAX = 256
_char_data_cache: "OrderedDict[int, Tuple[float, Tuple[Dict[str, Any], Dict[str, Any]]]]" = OrderedDict()
_char_data_lock = threading.Lock()


def _char_data_cache_get(character_id: int) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """线程安全读取（命中时刷新 LRU）。"""
    with _char_data_lock:
        if character_id not in _char_data_cache:
            return None
        ts, payload = _char_data_cache[character_id]
        if time.monotonic() - ts > _CHAR_DATA_TTL:
            _char_data_cache.pop(character_id)
            return None
        _char_data_cache.move_to_end(character_id)
        return payload


# hits/misses 累计（main.py 暴露为 /api/performance/char-data-cache-stats）
_char_data_hits = 0
_char_data_misses = 0
_char_data_stats_lock = threading.Lock()


def _bump_char_data(hit: bool) -> None:
    global _char_data_hits, _char_data_misses
    with _char_data_stats_lock:
        if hit:
            _char_data_hits += 1
        else:
            _char_data_misses += 1


def _char_data_cache_put(
    character_id: int,
    personality: Dict[str, Any],
    current_state: Dict[str, Any],
) -> None:
    """线程安全写入（满时淘汰最旧）。"""
    with _char_data_lock:
        if character_id in _char_data_cache:
            _char_data_cache.move_to_end(character_id)
        _char_data_cache[character_id] = (time.monotonic(), (personality, current_state))
        while len(_char_data_cache) > _CHAR_DATA_MAX:
            _char_data_cache.popitem(last=False)


def char_data_cache_invalidate(character_id: Optional[int] = None) -> int:
    """
    清空角色基础数据缓存。可选按 character_id 过滤。
    场景：角色创建/更新/删除、成长后调用。
    """
    with _char_data_lock:
        if character_id is None:
            n = len(_char_data_cache)
            _char_data_cache.clear()
            return n
        if character_id in _char_data_cache:
            _char_data_cache.pop(character_id)
            return 1
        return 0


def char_data_cache_stats() -> Dict[str, Any]:
    """导出角色数据缓存命中率（监控用）。"""
    with _char_data_stats_lock:
        total = _char_data_hits + _char_data_misses
        hit_rate = (_char_data_hits / total) if total else 0.0
        return {
            "size": len(_char_data_cache),
            "max_size": _CHAR_DATA_MAX,
            "ttl_sec": _CHAR_DATA_TTL,
            "hits": _char_data_hits,
            "misses": _char_data_misses,
            "hit_rate": round(hit_rate, 4),
        }


# ============================================================================
# 降级常量：当 LLM 调用失败时，保证管线不崩溃
# 设计考量：降级值采用"中立/保守"策略——
#   - 宁可返回一个 bland but correct 的回复，也不返回空值或报错
# ============================================================================

FALLBACK_DIRECTOR_OUTPUT: Dict[str, Any] = {
    "emotion": "平静",
    "focus_memories": [],
    "goal": "与玩家进行友好交谈",
    "style": "温和有礼的",
}

FALLBACK_ACTOR_OUTPUT: Dict[str, Any] = {
    "action": "站在原地，注视着玩家",
    "expression": "表情平静",
    # [FB-1 修复] speech 留 None，由 _localized_fallback() 根据上下文语言填充
    "speech": None,
}

# [FB-1 修复] 多语言回退短语映射（Actor LLM 不可用时使用）
_FALLBACK_PHRASES: Dict[str, str] = {
    "zh": "（角色暂时无法回应）",
    "en": "(The character is unable to respond right now)",
    "ja": "（キャラクターは今応答できません）",
    "default": "（角色暂时无法回应）",
}


def _infer_language(text: str) -> str:
    """根据字符集粗略推断文本主语言。"""
    if not text:
        return "default"
    has_cjk = False
    has_kana = False
    for ch in text:
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF:  # 平假名/片假名
            has_kana = True
            break
        if 0x4E00 <= code <= 0x9FFF:  # CJK
            has_cjk = True
    if has_kana:
        return "ja"
    if has_cjk:
        return "zh"
    # 拉丁字符占比高 → 英文
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    if ascii_letters > len(text) * 0.3:
        return "en"
    return "default"


def _localized_fallback(user_input: str, character_name: str = "") -> str:
    """根据 user_input / character_name 推断语言后，返回本地化回退短语。"""
    lang = _infer_language(user_input) if user_input else "default"
    if lang == "default" and character_name:
        lang = _infer_language(character_name)
    return _FALLBACK_PHRASES.get(lang, _FALLBACK_PHRASES["default"])


# ============================================================================
# Director：注意力聚焦模块
# ============================================================================

class DirectorModule:
    """
    注意力聚焦模块（Director）

    职责：给定角色状态 + 玩家输入，决定角色"该关注什么"。
    ——这是双 LLM 管路的第一阶段，模拟人类的"感知→关注"认知过程。

    输入 → 输出链路：
        character_name + personality + current_state
        + recent_memories + user_input
            ↓  一次 LLM 调用 (temperature=0.5, response_format=json_object)
        emotion + focus_memories + goal + style
    """

    def __init__(self):
        self.llm_service = LLMService()
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """加载 Director prompt 模板文件"""
        with open("backend/prompts/director.txt", "r", encoding="utf-8") as f:
            return f.read()

    def reload(self) -> None:
        """热更新 LLM 配置（设置页改动后调用，复用已加载的 prompt 模板）"""
        self.llm_service.reload_config()

    def analyze(
        self,
        character_name: str,
        personality: Dict[str, Any],
        current_state: Dict[str, Any],
        recent_memories: List[str],
        user_input: str,
        history_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """
        执行注意力聚焦分析。

        Args:
            character_name:  角色名称
            personality:     人格属性字典（如 {"optimism": 70, ...}）
            current_state:   当前状态字典（如 {"location": "酒馆", ...}）
            recent_memories: 最近记忆内容列表（字符串，最多5条）
            user_input:      玩家输入文本
            history_messages: 可选的历史对话消息列表。
                传入时启用多轮模式 ——
                messages 数组会按 [system, ...history, current_user(prompt)] 顺序组装，
                LLM 能感知完整对话上下文。
                传 None 或空列表则回退到单轮（system + user）模式。

        Returns:
            (parsed_data, raw_response) 元组
            - parsed_data: 校验通过后的字典 {emotion, focus_memories, goal, style}
            - raw_response: LLM 原始 JSON 字符串

        降级策略：
            LLM 调用异常 → 返回 FALLBACK_DIRECTOR_OUTPUT + 错误日志
        """
        # --- 步骤 1：组装 prompt ---
        personality_str = json.dumps(personality, ensure_ascii=False)
        current_state_str = json.dumps(current_state, ensure_ascii=False)
        memories_str = "\n".join(
            f"  - {mem}" for mem in (recent_memories or [])
        ) or "  （无最近记忆）"

        prompt = self.prompt_template.format(
            character_name=character_name,
            personality=personality_str,
            current_state=current_state_str,
            recent_memories=memories_str,
            user_input=user_input,
        )

        # --- 步骤 2：调用 LLM ---
        # temperature=0.5 的设计考量：
        #   注意力聚焦是"决策型"任务，需要偏确定的逻辑推导。
        #   过高的温度会导致情绪标签与实际情况不匹配。
        system_prompt = (
            "你是一个专业的角色行为分析师，"
            "擅长根据上下文推导角色的心理状态和注意力焦点。"
        )

        if history_messages:
            # 多轮模式：system + 历史 user/assistant 交替 + 当前 user(prompt)
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": system_prompt}
            ]
            messages.extend(history_messages)
            messages.append({"role": "user", "content": prompt})

            raw_response = self.llm_service.call_with_messages(
                messages=messages,
                temperature=0.5,
                response_format={"type": "json_object"},
                task="chat",
            )
        else:
            # 单轮模式（向后兼容）
            raw_response = self.llm_service.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.5,
                response_format={"type": "json_object"},
                task="chat",
            )

        # --- 步骤 3：解析并校验 ---
        parsed = self.llm_service.parse_json_response(raw_response)
        parsed = LLMService.validate_director_schema(parsed)

        return parsed, raw_response

    def analyze_with_fallback(
        self,
        character_name: str,
        personality: Dict[str, Any],
        current_state: Dict[str, Any],
        recent_memories: List[str],
        user_input: str,
        history_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        带降级的注意力分析。

        与 analyze() 的区别：捕获异常后不向上抛，而是返回降级值。
        这是管线中的"安全网"节点，确保 Director 的失败不会阻塞 Actor。

        history_messages 透传给 analyze()，语义与 analyze() 一致。

        Returns:
            (parsed_data, raw_response_or_None)
            成功时 raw_response 为 LLM 原始 JSON 字符串
            降级时 raw_response 为 None
        """
        try:
            return self.analyze(
                character_name, personality, current_state,
                recent_memories, user_input,
                history_messages=history_messages,
            )
        except Exception as e:
            logger.warning(
                "Director LLM 调用失败，使用降级输出: %s", e
            )
            return dict(FALLBACK_DIRECTOR_OUTPUT), None


# ============================================================================
# Actor：行为生成模块
# ============================================================================

class ActorModule:
    """
    行为生成模块（Actor）

    职责：根据 Director 聚焦结果，生成角色的具体动作/表情/语言。
    ——这是双 LLM 管路的第二阶段，模拟人类的"关注→表达"行为过程。

    输入 → 输出链路：
        character_name + personality
        + emotion + focus_memories + goal + style  ← 来自 Director
        + user_input
            ↓  一次 LLM 调用 (temperature=0.8, response_format=json_object)
        action + expression + speech
    """

    def __init__(self):
        self.llm_service = LLMService()
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """加载 Actor prompt 模板文件"""
        with open("backend/prompts/actor.txt", "r", encoding="utf-8") as f:
            return f.read()

    def reload(self) -> None:
        """热更新 LLM 配置（设置页改动后调用，复用已加载的 prompt 模板）"""
        self.llm_service.reload_config()

    def generate(
        self,
        character_name: str,
        personality: Dict[str, Any],
        emotion: str,
        focus_memories: List[str],
        goal: str,
        style: str,
        user_input: str,
        scene_context: str = "",        # [修复 1] 角色当前场景上下文（世界四要素 + 位置）
        history_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """
        生成角色行为（动作 + 表情 + 语言）。

        Args:
            character_name:  角色名称
            personality:     人格属性字典
            emotion:         Director 输出的情绪标签
            focus_memories:  Director 筛选的关键记忆
            goal:            Director 设定的对话目标
            style:           Director 确定的回复风格
            user_input:      玩家输入文本
            history_messages: 可选的历史对话消息列表。
                传入时启用多轮模式 ——
                messages 数组会按 [system, ...history, current_user(prompt)] 顺序组装，
                让 LLM 在生成回复时能感知到完整对话上下文（不仅是 Director 提供的摘要）。
                传 None 或空列表则回退到单轮（system + user）模式。

        Returns:
            (parsed_data, raw_response) 元组
            - parsed_data: 校验通过后的字典 {action, expression, speech}
            - raw_response: LLM 原始 JSON 字符串

        降级策略：
            LLM 调用异常 → 返回 FALLBACK_ACTOR_OUTPUT + 错误日志
        """
        # --- 步骤 1：组装 prompt ---
        personality_str = json.dumps(personality, ensure_ascii=False)
        memories_str = "\n".join(
            f"  - {mem}" for mem in (focus_memories or [])
        ) or "  （无特殊关注的记忆）"

        # 注意：prompt 模板使用 {} 占位符但 Director 输出中可能含 {}，
        # 故使用 format_map + defaultdict 的安全替换方式，避免 KeyError
        import collections
        safe_dict = collections.defaultdict(str, {
            "character_name": character_name,
            "personality": personality_str,
            "emotion": emotion,
            "focus_memories": memories_str,
            "goal": goal,
            "style": style,
            "user_input": user_input,
            "scene_context": scene_context,       # [修复 1] 填入 actor.txt 第 13 行的 {scene_context} 占位符
        })

        # 使用 string.Template 风格安全性建 prompt
        prompt = self.prompt_template
        for key, val in safe_dict.items():
            prompt = prompt.replace("{" + key + "}", val)

        # --- 步骤 2：调用 LLM ---
        # temperature=0.8 的设计考量：
        #   行为生成是"创意型"任务，需要一定的随机性来产生多样的回复。
        #   但也不宜超过 0.9，否则可能产生不符合角色设定的内容。
        system_prompt = (
            "你是一个沉浸式角色扮演系统，"
            "你能精准地根据角色的情绪、记忆和目标生成自然的动作和对话。"
        )

        if history_messages:
            # 多轮模式：system + 历史 user/assistant 交替 + 当前 user(prompt)
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": system_prompt}
            ]
            messages.extend(history_messages)
            messages.append({"role": "user", "content": prompt})

            raw_response = self.llm_service.call_with_messages(
                messages=messages,
                temperature=0.8,
                response_format={"type": "json_object"},
                task="chat",
            )
        else:
            # 单轮模式（向后兼容）
            raw_response = self.llm_service.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.8,
                response_format={"type": "json_object"},
                task="chat",
            )

        # --- 步骤 3：解析并校验 ---
        parsed = self.llm_service.parse_json_response(raw_response)
        parsed = LLMService.validate_actor_schema(parsed)

        return parsed, raw_response

    def generate_with_fallback(
        self,
        character_name: str,
        personality: Dict[str, Any],
        emotion: str,
        focus_memories: List[str],
        goal: str,
        style: str,
        user_input: str,
        scene_context: str = "",        # [修复 1] 透传给 generate()
        history_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        带降级的行为生成。

        history_messages / scene_context 透传给 generate()，语义与 generate() 一致。

        Returns:
            (parsed_data, raw_response_or_None)
            成功时 raw_response 为 LLM 原始 JSON 字符串
            降级时 raw_response 为 None
        """
        try:
            return self.generate(
                character_name, personality, emotion,
                focus_memories, goal, style, user_input,
                scene_context=scene_context,
                history_messages=history_messages,
            )
        except Exception as e:
            logger.warning(
                "Actor LLM 调用失败，使用降级输出: %s", e
            )
            fallback = dict(FALLBACK_ACTOR_OUTPUT)
            if fallback.get("speech") is None:
                # [FB-1 修复] 根据 user_input/character_name 语言填充本地化回退文本
                fallback["speech"] = _localized_fallback(user_input, character_name)
            return fallback, None

    def generate_stream(
        self,
        character_name: str,
        personality: Dict[str, Any],
        emotion: str,
        focus_memories: List[str],
        goal: str,
        style: str,
        user_input: str,
        scene_context: str = "",        # [修复 1] 流式版本同样需要填入 {scene_context}
        history_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Generator[Tuple[str, Any], None, None]:
        """
        流式生成角色行为。

        与 generate() 的区别：
          - 使用 LLM 流式 API，首个 token 到达即开始返回
          - 通过 _IncrementalActorParser 实时提取 speech 字段并增量产出
          - 适合聊天界面实时打字效果

        Yields:
            Tuple[str, Any]: (event_type, payload)
            - ("speech_delta", str):  speech 文本增量
            - ("done", dict):         完整结果 {action, expression, speech, raw}
            - ("error", str):         错误信息（降级时）

        降级策略：
            LLM 调用异常 → yield ("error", msg) + ("done", FALLBACK_ACTOR_OUTPUT)
        """
        # --- 步骤 1：组装 prompt（与 generate() 一致） ---
        personality_str = json.dumps(personality, ensure_ascii=False)
        memories_str = "\n".join(
            f"  - {mem}" for mem in (focus_memories or [])
        ) or "  （无特殊关注的记忆）"

        import collections
        safe_dict = collections.defaultdict(str, {
            "character_name": character_name,
            "personality": personality_str,
            "emotion": emotion,
            "focus_memories": memories_str,
            "goal": goal,
            "style": style,
            "user_input": user_input,
            "scene_context": scene_context,       # [修复 1] 流式版本填入 scene_context
        })

        prompt = self.prompt_template
        for key, val in safe_dict.items():
            prompt = prompt.replace("{" + key + "}", val)

        system_prompt = (
            "你是一个沉浸式角色扮演系统，"
            "你能精准地根据角色的情绪、记忆和目标生成自然的动作和对话。"
        )

        if history_messages:
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": system_prompt}
            ]
            messages.extend(history_messages)
            messages.append({"role": "user", "content": prompt})
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]

        # --- 步骤 2：流式调用 LLM ---
        parser = _IncrementalActorParser()
        stream_failed: Optional[Exception] = None
        try:
            for chunk in self.llm_service.call_with_messages_stream(
                messages=messages,
                temperature=0.8,
                response_format={"type": "json_object"},
                task="chat_stream",
            ):
                delta = parser.feed(chunk)
                if delta:
                    yield ("speech_delta", delta)

            # --- 步骤 3：流结束，解析完整结果 ---
            final = parser.finalize()
            final = LLMService.validate_actor_schema(final)
            yield ("done", {
                "action": final["action"],
                "expression": final["expression"],
                "speech": final["speech"],
                "raw": parser.raw_buffer,
            })
            return

        except Exception as e:
            # 流式调用失败（LLM 偶发 Connection error / API 暂不可达）
            # → 记录错误并 fallback 到非流式调用，确保用户仍能拿到完整回复
            logger.warning(
                "Actor 流式 LLM 调用失败，将 fallback 到非流式: %s", e
            )
            stream_failed = e
            # 继续往下走 fallback 逻辑

        # --- Fallback：流式失败时调用非流式接口 ---
        # 保留一次完整回复，但失去打字机效果（一次性发出整段 speech）
        try:
            fallback_data, fallback_raw = self.generate(
                character_name=character_name,
                personality=personality,
                emotion=emotion,
                focus_memories=focus_memories,
                goal=goal,
                style=style,
                user_input=user_input,
                scene_context=scene_context,        # [修复 1] 透传给 fallback
                history_messages=history_messages if history_messages else None,  # [EH-1 修复] 显式空值判断，避免空列表被替换为 None
            )
            # 把整段 speech 作为单次 speech_delta 发出，前端会一次性追加
            fallback_speech = fallback_data.get("speech", "")
            if fallback_speech:
                yield ("speech_delta", fallback_speech)

            if stream_failed is not None:
                yield ("error", f"[fallback] {str(stream_failed)[:150]}")
            else:
                yield ("error", "stream fallback used")

            yield ("done", {
                "action": fallback_data.get("action", "stand"),
                "expression": fallback_data.get("expression", "neutral"),
                "speech": fallback_speech,
                "raw": fallback_raw or json.dumps(fallback_data, ensure_ascii=False),
            })
        except Exception as e2:
            # fallback 也失败 → 真正的降级输出（占位回复）
            logger.error("Actor fallback 也失败，使用降级输出: %s", e2)
            yield ("error", f"{str(stream_failed)[:100] if stream_failed else ''} | fallback: {str(e2)[:100]}")
            fallback = dict(FALLBACK_ACTOR_OUTPUT)
            if fallback.get("speech") is None:
                # [FB-1 修复] 二次 fallback 也填充本地化文本
                fallback["speech"] = _localized_fallback(user_input, character_name)
            yield ("done", {
                **fallback,
                "raw": parser.raw_buffer or json.dumps(fallback, ensure_ascii=False),
            })


# ============================================================================
# 增量 JSON 提取器：从流式 LLM 输出中实时提取 speech / action / expression
# ============================================================================

# 匹配 JSON 字符串值的正则（支持转义）：
#   "field"\s*:\s*"((?:[^"\\]|\\.)*)"
# 其中 (?:[^"\\]|\\.)* 匹配：非引号反斜杠字符，或反斜杠+任意字符（转义序列）
_FIELD_COMPLETE_RE = re.compile(
    r'"(?P<field>action|expression|speech)"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)
# 部分匹配（尚未收到闭合引号）——用于 speech 的增量提取
_FIELD_PARTIAL_RE = re.compile(
    r'"(?P<field>speech)"\s*:\s*"((?:[^"\\]|\\.)*)',
    re.DOTALL,
)


def _json_unescape(s: str) -> str:
    """
    将 JSON 字符串字面量内部内容（不含外层引号）反转义为真实文本。

    处理的转义序列：\\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX
    末尾若出现孤立的 \\（转义序列不完整），则忽略它（流式场景下下个 chunk 会补齐）。
    """
    # 用 json.loads 反转义最稳妥：把内容用引号包起来解析
    # 但需处理末尾孤立反斜杠（流式中间态）
    # 策略：截断到最后一个完整转义序列
    # 找到最后一个反斜杠的位置，如果它在字符串末尾，说明转义不完整
    if s.endswith("\\"):
        s = s[:-1]
    try:
        return json.loads('"' + s + '"')
    except (json.JSONDecodeError, ValueError):
        # 如果 json.loads 失败（例如中间有不完整转义），做基本替换
        return (
            s.replace('\\"', '"')
             .replace('\\\\', '\\')
             .replace('\\n', '\n')
             .replace('\\r', '\r')
             .replace('\\t', '\t')
        )


class _IncrementalActorParser:
    """
    增量解析 Actor LLM 的流式 JSON 输出。

    核心职责：
      1. 累积 LLM chunk
      2. 实时提取 speech 字段内容（即使 JSON 尚未闭合）
      3. 追踪已发送的 speech 长度，计算增量
      4. 在流结束时提供完整的 action / expression / speech

    使用方式：
        parser = _IncrementalActorParser()
        for chunk in llm_stream:
            delta = parser.feed(chunk)
            if delta:
                yield delta  # speech 增量文本
        final = parser.finalize()  # {"action":..., "expression":..., "speech":...}
    """

    def __init__(self):
        self._buffer: str = ""
        self._speech_sent_len: int = 0  # 已发送的 *反转义后* speech 字符数

    def feed(self, chunk: str) -> str:
        """
        喂入一个 LLM chunk，返回 speech 字段的新增文本（反转义后）。

        Returns:
            str: speech 增量文本（可能为空字符串）
        """
        self._buffer += chunk

        # 尝试找到 speech 字段并提取当前可见的内容（可能不完整）
        match = _FIELD_PARTIAL_RE.search(self._buffer)
        if not match:
            return ""

        raw_content = match.group(2)  # 尚未反转义的原始内容
        # 反转义得到用户可见文本
        try:
            decoded = _json_unescape(raw_content)
        except Exception:
            decoded = raw_content

        # 计算增量
        if len(decoded) > self._speech_sent_len:
            delta = decoded[self._speech_sent_len:]
            self._speech_sent_len = len(decoded)
            return delta
        return ""

    def finalize(self) -> Dict[str, str]:
        """
        流结束后，解析完整 JSON 并返回 {action, expression, speech}。

        若 JSON 解析失败，使用已提取的 speech + 降级 action/expression。
        """
        # 尝试完整解析
        try:
            data = json.loads(self._buffer)
            if isinstance(data, dict):
                return {
                    "action": str(data.get("action", "")).strip() or "stand",
                    "expression": str(data.get("expression", "")).strip() or "neutral",
                    "speech": str(data.get("speech", "")).strip()
                              or _json_unescape(
                                  _FIELD_PARTIAL_RE.search(self._buffer).group(2)
                                  if _FIELD_PARTIAL_RE.search(self._buffer) else ""
                              ),
                }
        except (json.JSONDecodeError, ValueError):
            pass

        # 降级：用正则提取各字段
        result = {"action": "stand", "expression": "neutral", "speech": ""}
        for m in _FIELD_COMPLETE_RE.finditer(self._buffer):
            field = m.group(1)
            value = _json_unescape(m.group(2))
            result[field] = value

        # 若 speech 仍为空，用 partial 提取
        if not result["speech"]:
            pm = _FIELD_PARTIAL_RE.search(self._buffer)
            if pm:
                result["speech"] = _json_unescape(pm.group(2))

        return result

    @property
    def raw_buffer(self) -> str:
        """返回完整原始缓冲（用于持久化 actor_raw）"""
        return self._buffer


# ============================================================================
# InteractionPipeline：对话管线编排层
# ============================================================================

class InteractionPipeline:
    """
    对话管线编排层

    职责：
      1. 数据库读取（角色 → 记忆 → 历史对话）
      2. 数据组装（字典反序列化、列表提取）
      3. 串联 Director → Actor 两步 LLM 调用
      4. 持久化对话记录到数据库
      5. 返回完整 ChatResponse

    管线节点依赖图（→ 表示数据流方向）：

        character_crud.get_character ────┐
        memory_crud.get_character_memories ─┤──□ Director.analyze()
        conversation_crud.get_character_conversations ─┘     │
                                                             ▼
                                                      Actor.generate()
                                                             │
                                                             ▼
                                            conversation_crud.create()

    设计考量：
      - Pipeline 本身不调用 LLM，LLM 调用封装在 Director/Actor 中
      - Pipeline 仅负责"读数据 → 协调调用 → 写数据"的编排逻辑
      - 这样保证了单一职责：模块内聚 LLM 调用，管线负责流程
    """

    def __init__(self):
        """初始化 Director 和 Actor 实例（两个 LLM 子模块）"""
        self.director = DirectorModule()
        self.actor = ActorModule()

    def reload(self) -> None:
        """热更新内部 Director/Actor 的 LLM 配置（设置页改动后调用）"""
        self.director.reload()
        self.actor.reload()

    @staticmethod
    def _persist_in_background(
        db: Session,
        character_id: int,
        user_message: str,
        npc_response: str,
        emotion: str,
        action: str,
        expression: str,
        director_raw: Optional[str],
        actor_raw: Optional[str],
        session_id: Optional[int],
    ) -> int:
        """
        异步持久化对话记录到数据库（用于 run_stream 的 done 事件路径）。

        行为：
          - 在独立线程中创建新的 DB session（避免与请求线程的 session 冲突）
          - 写入 conversation + touch_session
          - 写完后通过共享 dict 把 conversation.id 写回（key = "cid_<timestamp>_<counter>"）
          - 流式 done 事件可以立即 yield，不等持久化

        缺点：done 事件的 conversation.id 拿不到（异步执行中）
        解决：调用方约定用 `id: -1` 标记，historical list 不影响

        Returns:
            int: -1（占位）。真实 id 通过 _persist_results dict 后台可查。
        """
        import uuid as _uuid
        result_key = f"cid_{_uuid.uuid4().hex[:16]}"
        result_holder: Dict[str, int] = {}

        def _do_persist():
            try:
                # 复用全局 SessionLocal —— 避免每线程 create_engine
                # 之前 self.create_engine(settings.database_url) 报错：
                #   Settings 模型字段是 DATABASE_URL（大写），不是 database_url
                # 改用 backend.database.SessionLocal 后，connect_args / bind 全部沿用
                from backend.database import SessionLocal as GlobalSessionLocal
                with GlobalSessionLocal() as bg_db:
                    conv = conversation_crud.create_conversation(
                        db=bg_db,
                        character_id=character_id,
                        user_input=user_message,
                        npc_response=npc_response,
                        emotion=emotion,
                        action=action,
                        expression=expression,
                        director_raw=director_raw,
                        actor_raw=actor_raw,
                        session_id=session_id,
                    )
                    from backend.services import chat_session_crud
                    if session_id is not None:
                        chat_session_crud.touch_session(bg_db, session_id)
                    bg_db.commit()
                    result_holder["id"] = conv.id
            except Exception as e:
                logger.error("后台持久化失败: %s", e)
                result_holder["id"] = -1

        thread = threading.Thread(target=_do_persist, daemon=True)
        thread.start()

        # 把 result_holder 存到全局 dict（便于后续查询）
        _persist_results[result_key] = result_holder
        return -1  # 占位

    @staticmethod
    def _safe_load_json(raw: Optional[str]) -> dict:
        """安全地将数据库中的 JSON 字符串转为 dict"""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _get_character_data(
        character_id: int, character: Any,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        从缓存或 DB 行获取角色的 personality / current_state（已解析）。

        命中：直接返回 (personality, current_state)，跳过两次 json.loads。
        未命中：解析 + 写入缓存。
        过期或角色更新：清空后重读。
        """
        cached = _char_data_cache_get(character_id)
        if cached is not None:
            _bump_char_data(True)
            return cached
        personality = InteractionPipeline._safe_load_json(character.personality)
        current_state = InteractionPipeline._safe_load_json(character.current_state)
        _char_data_cache_put(character_id, personality, current_state)
        _bump_char_data(False)
        return personality, current_state

    @staticmethod
    def _build_history_messages(
        conversations: List[Any],
        max_turns: int = 10,
    ) -> List[Dict[str, str]]:
        """
        把数据库中最近 N 条对话记录组装为 OpenAI 风格的 messages 数组。

        数据结构（OpenAI 格式）：
            [
              {"role": "user",      "content": <user_input>},
              {"role": "assistant", "content": <npc_response>},
              ... 交替 ...
            ]

        Args:
            conversations: Conversation ORM 对象列表（按时间升序）。
                          调用方需自行做"取最近 N 条"的截断。
            max_turns: 最多保留多少轮（每轮 = 1 user + 1 assistant）。
                       截断采用"保留最近 N 轮"策略：取列表尾部而非头部，
                       避免最早的对话覆盖最近的语义。

        Returns:
            OpenAI 风格 messages 数组（不含 system，由调用方追加在最前）。
            空列表表示无历史。

        健壮性设计：
          - 一轮对话必须 user_input *和* npc_response 都非空才保留。
            原因：OpenAI messages 必须 user/assistant 严格交替，
            若只保留一侧会导致连续同角色消息，触发 API 报错或语义混乱。
          - 跳过"半轮"（任一字段为空）—— 在脏数据或部分写入失败时保护 LLM 调用。
        """
        if not conversations or max_turns <= 0:
            return []

        # 截断到最近 N 轮
        recent = conversations[-max_turns:]

        history: List[Dict[str, str]] = []
        for conv in recent:
            user_text = (conv.user_input or "").strip()
            npc_text = (conv.npc_response or "").strip()
            # 严格成对：两端都有非空内容才纳入 messages
            if user_text and npc_text:
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": npc_text})
        return history

    def run(
        self,
        character_id: int,
        user_message: str,
        db: Session,
        history_turns: int = 8,  # [PIPE-1 修复] 与 run_stream() 统一为 8，消除非对称行为
        session_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        运行完整的对话管线。

        Args:
            character_id:  角色 ID
            user_message:  玩家输入文本
            db:            SQLAlchemy 数据库会话
            history_turns: 注入到 LLM messages 的最近对话轮数。
                           默认 10 轮 = 20 条消息；
                           设为 0 即可禁用多轮模式（回退到单轮）。
            session_id:    会话 ID（None → 自动创建新 session 并用首条消息做标题）

        Returns:
            字典，包含以下字段，可直接用于 ChatResponse schema：
            {
                "id": int,               # 对话记录 ID
                "character_id": int,
                "user_input": str,
                "npc_response": str,     # 角色的语言回复
                "emotion": str,          # Director 输出的情绪
                "action": str,           # Actor 输出的动作
                "expression": str,       # Actor 输出的表情
                "director_raw": str|None,# Director LLM 原始响应
                "actor_raw": str|None,   # Actor LLM 原始响应
                "timestamp": datetime,
                "session_id": int,       # 新增：所属会话
                "session_title": str,    # 新增：会话标题（前端可立即更新侧栏）
            }

        Raises:
            ValueError: 角色不存在时抛出
        """
        import time as _time
        _t_total0 = _time.monotonic()
        # ---- 节点 1：获取角色基础数据 ----
        character = character_crud.get_character(db, character_id)
        if not character:
            raise ValueError(f"角色不存在: id={character_id}")

        # personality / current_state 走 60s 缓存，避免每轮对话重复 json.loads
        personality, current_state = self._get_character_data(character_id, character)

        # ---- 节点 1.5：jiwen 情绪引擎状态注入 ----
        # 把 jiwen 的五轴连续状态（connection / pride / valence / arousal / immersion）
        # 合并到 current_state 的 _jiwen 子字段，Director 能在 prompt 里看到。
        # 失败不阻塞主流程（jiwen 可能尚未初始化）。
        try:
            from backend.jiwen import get_jiwen_manager
            mgr = get_jiwen_manager()
            jiwen_ctx = mgr.get_prompt_context(character_id)
            jiwen_state = mgr.get_state(character_id)
            current_state = {
                **current_state,
                "_jiwen": {
                    "summary": jiwen_ctx,
                    "connection": jiwen_state.get("connection", 0.0),
                    "pride": jiwen_state.get("pride", 0.0),
                    "valence": jiwen_state.get("valence", 0.0),
                    "arousal": jiwen_state.get("arousal", 0.0),
                    "immersion": jiwen_state.get("immersion", 0.0),
                    "user_status": jiwen_state.get("user_status", "active"),
                    "activity_type": jiwen_state.get("activity_type", "none"),
                    "activity_label": jiwen_state.get("activity_label"),
                },
            }
        except Exception as _e:
            # jiwen 状态不可用时静默跳过，current_state 保持原样
            logger.debug("jiwen 状态注入跳过: %s", _e)

        # ---- 节点 1.6：世界四要素上下文注入（ADR-009）----
        # 把世界（季节/天气/最近事件）+ 当前位置（嵌套路径）合并到
        # current_state._world 子字段。零模板侵入（与 jiwen 同样的策略）。
        # 失败不阻塞主流程：WorldEngine 未初始化或角色无 location 时静默跳过。
        try:
            from backend.world import build_world_subfield
            world_sub = build_world_subfield(character_id)
            if world_sub:
                current_state = {
                    **current_state,
                    "_world": world_sub,
                }
        except Exception as _e:
            logger.debug("world 状态注入跳过: %s", _e)
            world_sub = None

        # ---- 节点 1.6.1：世界设定背景注入（修复 4）----
        # 把 Character.world_setting 注入到 current_state._world_setting，
        # Director 即可看到"这个世界是什么样的"设定信息。
        if character.world_setting and character.world_setting.strip():
            current_state = {
                **current_state,
                "_world_setting": character.world_setting,
            }

        # ---- 节点 1.7：最近完成事件注入（修复 2）----
        # 把当日 / 最近 3 条 completed 事件摘要注入到 current_state._events 子字段。
        # 设计：与 _world / _jiwen 同样的零模板侵入策略。
        # 失败不阻塞主流程：Event 表为空或查询失败时静默跳过。
        try:
            from backend.models import Event as _Event
            recent_events = db.query(_Event).filter(
                _Event.character_id == character_id,
                _Event.status == "completed",
            ).order_by(_Event.day_number.desc(), _Event.order_index.desc()).limit(3).all()

            if recent_events:
                event_lines = []
                for ev in recent_events:
                    label = f"[Day{ev.day_number}] {ev.content[:80]}"
                    if ev.result_json:
                        try:
                            result = json.loads(ev.result_json)
                            if result.get("narrative_delta"):
                                label += f" → {result['narrative_delta'][:60]}"
                        except Exception:
                            pass
                    event_lines.append(label)

                current_state = {
                    **current_state,
                    "_events": {
                        "summary": "最近发生的事：\n" + "\n".join(event_lines),
                        "events": [
                            {
                                "day": ev.day_number,
                                "type": ev.event_type,
                                "content": ev.content[:120],
                                "time_period": ev.time_period,
                            }
                            for ev in recent_events
                        ],
                    },
                }
        except Exception as _e:
            logger.debug("事件上下文注入跳过: %s", _e)

        # ---- 节点 2：获取最近记忆（最多 5 条） ----
        # 设计考量：限制 5 条是 prompt token 预算与上下文丰富度之间的平衡点。
        # 5 条记忆 + 其他变量 ≈ 总 token < 2000，确保在模型上下文限制内安全。
        recent_memories = memory_crud.get_character_memories(
            db, character_id, limit=5
        )
        memory_texts = [mem.content for mem in recent_memories]

        # ---- 节点 2.4：获取/创建会话（多轮消息的容器） ----
        #   - session_id 传了就复用（角色不匹配时降级为创建新 session）
        #   - 没传就创建一个新 session，标题取首条消息前 30 字
        #   - 必须在"取历史消息"之前完成，否则会把"上一会话"的内容串味到新会话
        from backend.services import chat_session_crud
        session = chat_session_crud.get_or_create_session(
            db, session_id=session_id, character_id=character_id,
            first_user_message=user_message,
        )
        session_id = session.id
        session_title = session.title

        # ---- 节点 2.5：组装多轮历史消息 ----
        #   从当前 session 取最近 N 轮对话，按时间升序拼接为 OpenAI 风格 messages。
        #   重要：必须在持久化新对话 *之前* 取历史，否则会把"当前轮"也塞回去造成重复。
        history_messages: List[Dict[str, str]] = []
        if history_turns and history_turns > 0:
            # 优先用 session 级历史（更聚焦），但若 session 为空且没有显式 session_id
            # 则退回到角色级历史，避免首次进入"默认会话"时空白
            recent_conversations = conversation_crud.get_session_conversations(
                db, session_id=session_id, limit=history_turns,
            )
            if not recent_conversations and session_id is None:
                recent_conversations = conversation_crud.get_character_conversations(
                    db, character_id, skip=0, limit=history_turns,
                )
            history_messages = self._build_history_messages(
                recent_conversations, max_turns=history_turns,
            )
            if history_messages:
                logger.info(
                    "InteractionPipeline: 注入 %d 条历史消息（%d 轮）",
                    len(history_messages), len(history_messages) // 2,
                )

        # ---- 节点 2.6：响应缓存查询（命中即跳过两次 LLM） ----
        # 命中条件：character_id + user_input + history_bucket 一致
        # 注意：必须在 history 准备好后才能算 key（不同轮次答案可能不同）
        cache_key = _make_cache_key(
            character_id, user_message, session_id, len(history_messages)
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            _t_total_ms = int((_time.monotonic() - _t_total0) * 1000)
            logger.info(
                "InteractionPipeline.run: 缓存命中! total=%dms (跳过 LLM)", _t_total_ms
            )
            # 复用缓存的 LLM 输出，但持久化新一行（用新的 conversation.id）
            conversation = conversation_crud.create_conversation(
                db=db,
                character_id=character_id,
                user_input=user_message,
                npc_response=cached["npc_response"],
                emotion=cached["emotion"],
                action=cached["action"],
                expression=cached["expression"],
                director_raw=None,  # 缓存命中不重新生成，不写 director_raw
                actor_raw=None,
                session_id=session_id,
            )
            chat_session_crud.touch_session(db, session_id)
            # 构造返回（替换 conversation.id 和 elapsed_ms）
            return {
                **cached,
                "id": conversation.id,
                "character_id": character_id,        # 缓存 payload 不含，补充
                "user_input": user_message,          # 缓存 payload 不含，补充
                "timestamp": conversation.timestamp,
                "session_id": session_id,
                "session_title": session_title,
                "elapsed_ms": {
                    "director": 0,
                    "actor": 0,
                    "persist": int((_time.monotonic() - _t_total0) * 1000),
                    "total": _t_total_ms,
                },
                "cached": True,
            }

        # ---- 节点 3：执行 Director 注意力聚焦 ----
        # 使用带降级的版本，确保 LLM 失败时管线不崩溃
        _t_d0 = _time.monotonic()
        director_data, director_raw = self.director.analyze_with_fallback(
            character_name=character.name,
            personality=personality,
            current_state=current_state,
            recent_memories=memory_texts,
            user_input=user_message,
            history_messages=history_messages if history_messages else None,  # [EH-1 修复] 显式空值判断，避免空列表被替换为 None
        )
        _director_ms = int((_time.monotonic() - _t_d0) * 1000)

        # ---- 节点 4：执行 Actor 行为生成 ----
        # Actor 接收 Director 的完整输出作为上下文
        # 附加风格指引（jiwen 情绪 + world 场景）到 style 字段
        # 修复 3：world 场景感知（天气/季节）也注入风格指引
        style_guidance_parts = []

        # 1) jiwen 情绪风格
        try:
            from backend.jiwen import get_jiwen_manager as _gjm
            jiwen_style = _gjm().get_style_guidance(character_id)
            if jiwen_style and jiwen_style.strip():
                style_guidance_parts.append(f"[情绪状态]\n{jiwen_style}")
        except Exception:
            pass

        # 2) world 场景风格（天气/季节）
        try:
            from backend.world.location_aware import get_style_guidance as _world_style
            world_style_g = _world_style(character_id)
            if world_style_g and world_style_g.strip():
                style_guidance_parts.append(f"[场景感知]\n{world_style_g}")
        except Exception:
            pass

        if style_guidance_parts:
            actor_style = f"{director_data['style']}\n\n" + "\n\n".join(style_guidance_parts)
        else:
            actor_style = director_data["style"]

        # 修复 1-C：从 world_sub 中提取场景上下文，供 Actor prompt 的 {scene_context} 替换
        scene_context = ""
        if world_sub and world_sub.get("summary"):
            scene_context = world_sub["summary"]

        _t_a0 = _time.monotonic()
        actor_data, actor_raw = self.actor.generate_with_fallback(
            character_name=character.name,
            personality=personality,
            emotion=director_data["emotion"],
            focus_memories=director_data["focus_memories"],
            goal=director_data["goal"],
            style=actor_style,
            user_input=user_message,
            scene_context=scene_context,        # [修复 1-C] 传入场景上下文
            history_messages=history_messages if history_messages else None,  # [EH-1 修复] 显式空值判断，避免空列表被替换为 None
        )
        _actor_ms = int((_time.monotonic() - _t_a0) * 1000)

        # ---- 节点 5：持久化对话记录（带 session_id） ----
        _t_p0 = _time.monotonic()
        conversation = conversation_crud.create_conversation(
            db=db,
            character_id=character_id,
            user_input=user_message,
            npc_response=actor_data["speech"],
            emotion=director_data["emotion"],
            action=actor_data["action"],
            expression=actor_data["expression"],
            director_raw=director_raw,
            actor_raw=actor_raw,
            session_id=session_id,
        )

        # 刷新 session.updated_at，让活跃会话在侧栏里排前面
        chat_session_crud.touch_session(db, session_id)

        # jiwen + 记忆/遗忘系统 后处理钩子（异步，不阻塞响应）
        # 设计：失败不影响主流程，所有异常在 post_chat_hooks 内部 try/except
        try:
            from backend.modules.post_chat import post_chat_hooks
            post_chat_hooks(
                character_id=character_id,
                user_input=user_message,
                npc_response=actor_data["speech"],
                emotion_label=director_data.get("emotion"),
                conversation_id=conversation.id,
                run_in_background=True,
            )
        except Exception as _e:
            logger.warning("post_chat_hooks dispatch 失败: %s", _e)
        _persist_ms = int((_time.monotonic() - _t_p0) * 1000)
        _total_ms = int((_time.monotonic() - _t_total0) * 1000)
        logger.info(
            "InteractionPipeline.run: director=%dms, actor=%dms, persist=%dms, total=%dms",
            _director_ms, _actor_ms, _persist_ms, _total_ms,
        )

        # ---- 节点 6：返回结果 + 写入缓存（仅 LLM 真实成功时缓存） ----
        result = {
            "id": conversation.id,
            "character_id": character_id,
            "user_input": user_message,
            "npc_response": actor_data["speech"],
            "emotion": director_data["emotion"],
            "action": actor_data["action"],
            "expression": actor_data["expression"],
            "director_raw": director_raw,
            "actor_raw": actor_raw,
            "timestamp": conversation.timestamp,
            "session_id": session_id,
            "session_title": session_title,
            "elapsed_ms": {
                "director": _director_ms,
                "actor": _actor_ms,
                "persist": _persist_ms,
                "total": _total_ms,
            },
        }

        # 入缓存（包含 fallback 路径，让 API 抖动时仍能稳定响应用户重试）
        # 设计权衡：
        #   - LLM 真实成功 → 5min TTL 内重复提问直接返回，节省 80%+ 延迟
        #   - LLM fallback → 同样入缓存，抖动期间用户重试立刻拿到稳定结果
        #   - 失效触发：设置切换（reload_all_llm）会清缓存；角色成长会按 character_id 失效
        cache_payload = {
            "npc_response": actor_data["speech"],
            "emotion": director_data["emotion"],
            "action": actor_data["action"],
            "expression": actor_data["expression"],
            "director_raw": director_raw,
            "actor_raw": actor_raw,
        }
        _cache_put(cache_key, cache_payload)
        if director_raw and actor_raw:
            logger.debug("InteractionPipeline.run: 已写入响应缓存(LLM成功), key=%s", cache_key)
        else:
            logger.info("InteractionPipeline.run: 已写入响应缓存(fallback), key=%s", cache_key)

        return result

    def run_stream(
        self,
        character_id: int,
        user_message: str,
        db: Session,
        history_turns: int = 8,  # [PIPE-1 修复] 与 run() 统一为 8，折中 token 成本与上下文一致性
        session_id: Optional[int] = None,
    ) -> Generator[Tuple[str, Any], None, None]:
        """
        流式运行对话管线。

        与 run() 的区别：
          - Director 仍为非流式（其输出是元数据，非用户可见文本）
          - Actor 使用流式调用，speech 增量实时产出
          - 通过 generator 逐步 yield SSE 事件

        Yields:
            Tuple[str, Any]: (event_type, payload)
            - ("thinking", dict):   管线阶段通知（早期 yield 解决"点发送后沉默"问题）
            - ("meta", dict):       Director 完成，含 session_id/title/emotion/action/expression/elapsed_ms
            - ("speech", str):      speech 文本增量
            - ("done", dict):       完整 ChatResponse 字典
            - ("error", str):       错误信息

        事件顺序示例：
            thinking → meta → speech → speech → ... → done
            （异常时：thinking → error → done）
            （缓存命中：thinking → meta → speech → done）
        """
        import time as _time
        _t_total0 = _time.monotonic()
        # ---- 节点 0：立即 yield thinking 事件（让前端立刻知道请求已被接收） ----
        # 设计动机：之前 Director/Actor 全程同步执行，前端要等 2-5s 才看到第一个字节。
        # 立即推一个"思考中"事件，前端可立即显示阶段文案占位，用户感知更流畅。
        # 这是个低成本的"心理加速" —— 不改变总耗时，但消除"点了没反应"的卡顿感。
        yield ("thinking", {
            "phase": "starting",
            "started_at_ms": int(_t_total0 * 1000),
            "message": "正在处理请求…",
        })

        # ---- 节点 1：获取角色基础数据 ----
        character = character_crud.get_character(db, character_id)
        if not character:
            yield ("error", f"角色不存在: id={character_id}")
            return

        # personality / current_state 走 60s 缓存，避免每轮对话重复 json.loads
        personality, current_state = self._get_character_data(character_id, character)

        # ---- 节点 1.5：jiwen 情绪引擎状态注入 ----
        # 把 jiwen 五轴状态合并到 current_state._jiwen 子字段，Director 可见
        try:
            from backend.jiwen import get_jiwen_manager
            mgr = get_jiwen_manager()
            jiwen_ctx = mgr.get_prompt_context(character_id)
            jiwen_state = mgr.get_state(character_id)
            current_state = {
                **current_state,
                "_jiwen": {
                    "summary": jiwen_ctx,
                    "connection": jiwen_state.get("connection", 0.0),
                    "pride": jiwen_state.get("pride", 0.0),
                    "valence": jiwen_state.get("valence", 0.0),
                    "arousal": jiwen_state.get("arousal", 0.0),
                    "immersion": jiwen_state.get("immersion", 0.0),
                    "user_status": jiwen_state.get("user_status", "active"),
                    "activity_type": jiwen_state.get("activity_type", "none"),
                    "activity_label": jiwen_state.get("activity_label"),
                },
            }
        except Exception as _e:
            logger.debug("jiwen 状态注入跳过: %s", _e)

        # ---- 节点 1.6：世界四要素上下文注入（ADR-009，run_stream 同步）----
        # run() 已有此节点；run_stream 之前缺失，导致流式路径下：
        #   - scene_context 为空（修复 1-C 透传不到 Actor）
        #   - world 风格指引无法生成（修复 3 失效）
        # 失败不阻塞主流程。
        world_sub = None
        try:
            from backend.world import build_world_subfield
            world_sub = build_world_subfield(character_id)
            if world_sub:
                current_state = {
                    **current_state,
                    "_world": world_sub,
                }
        except Exception as _e:
            logger.debug("world 状态注入跳过: %s", _e)

        # ---- 节点 1.6.1：世界设定背景注入（修复 4）----
        if character.world_setting and character.world_setting.strip():
            current_state = {
                **current_state,
                "_world_setting": character.world_setting,
            }

        # ---- 节点 1.7：最近完成事件注入（修复 2）----
        try:
            from backend.models import Event as _Event
            recent_events = db.query(_Event).filter(
                _Event.character_id == character_id,
                _Event.status == "completed",
            ).order_by(_Event.day_number.desc(), _Event.order_index.desc()).limit(3).all()

            if recent_events:
                event_lines = []
                for ev in recent_events:
                    label = f"[Day{ev.day_number}] {ev.content[:80]}"
                    if ev.result_json:
                        try:
                            result = json.loads(ev.result_json)
                            if result.get("narrative_delta"):
                                label += f" → {result['narrative_delta'][:60]}"
                        except Exception:
                            pass
                    event_lines.append(label)

                current_state = {
                    **current_state,
                    "_events": {
                        "summary": "最近发生的事：\n" + "\n".join(event_lines),
                        "events": [
                            {
                                "day": ev.day_number,
                                "type": ev.event_type,
                                "content": ev.content[:120],
                                "time_period": ev.time_period,
                            }
                            for ev in recent_events
                        ],
                    },
                }
        except Exception as _e:
            logger.debug("事件上下文注入跳过: %s", _e)

        # ---- 节点 2：获取最近记忆 ----
        recent_memories = memory_crud.get_character_memories(
            db, character_id, limit=5
        )
        memory_texts = [mem.content for mem in recent_memories]

        # ---- 节点 2.4：获取/创建会话 ----
        from backend.services import chat_session_crud
        session = chat_session_crud.get_or_create_session(
            db, session_id=session_id, character_id=character_id,
            first_user_message=user_message,
        )
        session_id = session.id
        session_title = session.title

        # ---- 节点 2.5：组装多轮历史消息 ----
        history_messages: List[Dict[str, str]] = []
        if history_turns and history_turns > 0:
            recent_conversations = conversation_crud.get_session_conversations(
                db, session_id=session_id, limit=history_turns,
            )
            if not recent_conversations and session_id is None:
                recent_conversations = conversation_crud.get_character_conversations(
                    db, character_id, skip=0, limit=history_turns,
                )
            history_messages = self._build_history_messages(
                recent_conversations, max_turns=history_turns,
            )

        # ---- 节点 2.6：响应缓存查询（命中即跳过两次 LLM，直接流式返回） ----
        cache_key = _make_cache_key(
            character_id, user_message, session_id, len(history_messages)
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            _t_total_ms = int((_time.monotonic() - _t_total0) * 1000)
            logger.info(
                "InteractionPipeline.run_stream: 缓存命中! total=%dms (跳过 LLM)", _t_total_ms
            )
            # 缓存命中 → 立即 yield "已从缓存加载" 的 thinking 事件
            yield ("thinking", {
                "phase": "cache_hit",
                "started_at_ms": int(_t_total0 * 1000),
                "message": "已从缓存加载回复",
            })
            # 立即 yield meta（前端可立即更新 session + emotion）
            yield ("meta", {
                "session_id": session_id,
                "session_title": session_title,
                "emotion": cached["emotion"],
                "cached": True,
                "elapsed_ms": {
                    "director": 0,
                    "total_so_far": _t_total_ms,
                },
            })
            # 流式输出完整 speech（拆成单次大 chunk 模拟一次性发送，保留流式接口）
            yield ("speech", cached["npc_response"])
            # 异步持久化（不阻塞 done 事件发送）
            conversation_id = self._persist_in_background(
                db=db,
                character_id=character_id,
                user_message=user_message,
                npc_response=cached["npc_response"],
                emotion=cached["emotion"],
                action=cached["action"],
                expression=cached["expression"],
                director_raw=None,
                actor_raw=None,
                session_id=session_id,
            )
            _done_ms = int((_time.monotonic() - _t_total0) * 1000)
            yield ("done", {
                "id": conversation_id,
                "character_id": character_id,
                "user_input": user_message,
                "npc_response": cached["npc_response"],
                "emotion": cached["emotion"],
                "action": cached["action"],
                "expression": cached["expression"],
                "director_raw": None,
                "actor_raw": None,
                "timestamp": None,  # 异步写入，暂无
                "session_id": session_id,
                "session_title": session_title,
                "cached": True,
                "elapsed_ms": {
                    "director": 0,
                    "actor": 0,
                    "persist": 0,  # 异步
                    "total": _done_ms,
                },
            })
            return

        # ---- 节点 3：执行 Director 注意力聚焦（非流式） ----
        # 阶段通知：让前端知道现在处于 Director 阶段（"正在分析…"）
        yield ("thinking", {
            "phase": "directing",
            "started_at_ms": int(_t_total0 * 1000),
            "message": "正在分析上下文…",
        })
        _t_d0 = _time.monotonic()
        director_data, director_raw = self.director.analyze_with_fallback(
            character_name=character.name,
            personality=personality,
            current_state=current_state,
            recent_memories=memory_texts,
            user_input=user_message,
            history_messages=history_messages if history_messages else None,  # [EH-1 修复] 显式空值判断，避免空列表被替换为 None
        )
        _director_ms = int((_time.monotonic() - _t_d0) * 1000)

        # 发送 meta 事件：前端可立即更新 session 信息 + Director 元数据
        # elapsed_ms 字段实时上报 Director 耗时，让前端可显示"思考中"阶段时长
        yield ("meta", {
            "session_id": session_id,
            "session_title": session_title,
            "emotion": director_data["emotion"],
            "director_raw": director_raw,
            "elapsed_ms": {
                "director": _director_ms,
                "total_so_far": int((_time.monotonic() - _t_total0) * 1000),
            },
        })

        # ---- 节点 4：流式执行 Actor 行为生成 ----
        # 阶段通知：让前端知道现在处于 Actor 阶段（"正在生成回复…"）
        yield ("thinking", {
            "phase": "acting",
            "started_at_ms": int(_t_total0 * 1000),
            "message": "正在生成回复…",
        })
        _t_a0 = _time.monotonic()
        actor_action = ""
        actor_expression = ""
        actor_speech = ""
        actor_raw = ""

        # 附加风格指引（jiwen 情绪 + world 场景，修复 3）
        style_guidance_parts = []
        try:
            from backend.jiwen import get_jiwen_manager as _gjm
            jiwen_style = _gjm().get_style_guidance(character_id)
            if jiwen_style and jiwen_style.strip():
                style_guidance_parts.append(f"[情绪状态]\n{jiwen_style}")
        except Exception:
            pass
        try:
            from backend.world.location_aware import get_style_guidance as _world_style
            world_style_g = _world_style(character_id)
            if world_style_g and world_style_g.strip():
                style_guidance_parts.append(f"[场景感知]\n{world_style_g}")
        except Exception:
            pass

        if style_guidance_parts:
            actor_style = f"{director_data['style']}\n\n" + "\n\n".join(style_guidance_parts)
        else:
            actor_style = director_data["style"]

        # 修复 1-C：run_stream 同步传入 scene_context
        scene_context = ""
        if world_sub and world_sub.get("summary"):
            scene_context = world_sub["summary"]

        for event_type, payload in self.actor.generate_stream(
            character_name=character.name,
            personality=personality,
            emotion=director_data["emotion"],
            focus_memories=director_data["focus_memories"],
            goal=director_data["goal"],
            style=actor_style,
            user_input=user_message,
            scene_context=scene_context,        # [修复 1-C] 流式路径同步传入
            history_messages=history_messages if history_messages else None,  # [EH-1 修复] 显式空值判断，避免空列表被替换为 None
        ):
            if event_type == "speech_delta":
                actor_speech += payload
                yield ("speech", payload)
            elif event_type == "error":
                yield ("error", payload)
            elif event_type == "done":
                actor_action = payload.get("action", "")
                actor_expression = payload.get("expression", "")
                # 用 finalize 的完整 speech 修正（避免增量拼接遗漏）
                final_speech = payload.get("speech", "")
                if final_speech:
                    actor_speech = final_speech
                actor_raw = payload.get("raw", "")
        _actor_ms = int((_time.monotonic() - _t_a0) * 1000)

        # ---- 节点 5：异步持久化对话记录（不阻塞 done 事件发送） ----
        # 写库通常是 24-71ms 阻塞，提前到后台线程可让前端立即看到 done
        _persist_start = _time.monotonic()
        self._persist_in_background(
            db=db,
            character_id=character_id,
            user_message=user_message,
            npc_response=actor_speech,
            emotion=director_data["emotion"],
            action=actor_action,
            expression=actor_expression,
            director_raw=director_raw,
            actor_raw=actor_raw,
            session_id=session_id,
        )

        # jiwen + 记忆/遗忘系统 后处理钩子（延迟到持久化完成后执行）
        # [POST-1 修复] 流式管线的持久化是后台线程，conversation.id 在 yield done 时还拿不到。
        # 这里启动一个独立的 daemon 线程，等持久化完成后用真实 id 调用 post_chat_hooks，
        # 以保证记忆提取等需要 conversation_id 的钩子能正常执行。
        try:
            from backend.modules.post_chat import post_chat_hooks

            def _deferred_post_chat():
                # 等待持久化结果出现：轮询 DB，最长等 2s
                # [POST-1 修复] 持久化是后台线程，conversations 行还没插入时拿不到 id
                from backend.database import SessionLocal as _SessionLocal
                from backend.models import Conversation as _Conversation
                conv_id: Optional[int] = None
                for _ in range(20):
                    time.sleep(0.1)
                    try:
                        with _SessionLocal() as check_db:
                            q = check_db.query(_Conversation).filter(
                                _Conversation.character_id == character_id,
                                _Conversation.user_input == user_message,
                            )
                            if session_id is not None:
                                q = q.filter(_Conversation.session_id == session_id)
                            conv = q.order_by(_Conversation.id.desc()).first()
                            if conv and conv.npc_response == actor_speech:
                                conv_id = conv.id
                                break
                    except Exception:
                        continue
                try:
                    post_chat_hooks(
                        character_id=character_id,
                        user_input=user_message,
                        npc_response=actor_speech,
                        emotion_label=director_data.get("emotion"),
                        conversation_id=conv_id,  # 真实 id（拿不到时为 None，会跳过 extract）
                        run_in_background=True,
                    )
                except Exception as _e:
                    logger.warning("deferred post_chat_hooks dispatch 失败: %s", _e)

            threading.Thread(target=_deferred_post_chat, daemon=True).start()
        except Exception as _e:
            logger.warning("post_chat_hooks dispatch 失败: %s", _e)
        _persist_ms = int((_time.monotonic() - _persist_start) * 1000)
        _total_ms = int((_time.monotonic() - _t_total0) * 1000)
        logger.info(
            "InteractionPipeline.run_stream: director=%dms, actor=%dms, persist_dispatch=%dms, total=%dms",
            _director_ms, _actor_ms, _persist_ms, _total_ms,
        )

        # LLM 真实成功时写入缓存
        if director_raw and actor_raw:
            cache_payload = {k: v for k, v in [
                ("npc_response", actor_speech),
                ("emotion", director_data["emotion"]),
                ("action", actor_action),
                ("expression", actor_expression),
                ("director_raw", director_raw),
                ("actor_raw", actor_raw),
            ]}
            _cache_put(cache_key, cache_payload)

        # ---- 节点 6：发送 done 事件（持久化在后台跑，id=-1 标记） ----
        yield ("done", {
            "id": -1,  # 异步持久化中，真实 id 不可用
            "character_id": character_id,
            "user_input": user_message,
            "npc_response": actor_speech,
            "emotion": director_data["emotion"],
            "action": actor_action,
            "expression": actor_expression,
            "director_raw": director_raw,
            "actor_raw": actor_raw,
            "timestamp": None,  # 异步
            "session_id": session_id,
            "session_title": session_title,
            "elapsed_ms": {
                "director": _director_ms,
                "actor": _actor_ms,
                "persist": 0,  # 异步
                "total": _total_ms,
            },
        })
