"""
jiwen 引擎 — Python 移植版（基于 https://github.com/ClaraShafiq/jiwen）

本模块是 jiwen Node.js 库的纯算法 Python 移植。
设计动机：CharacterSeed 是 Python FastAPI 项目，jiwen 是 Node.js 库。
为了避免 HTTP sidecar 的跨进程延迟和运维负担，1:1 移植核心算法。

核心特性（与原版一致）：
  - 五轴连续状态：connection / pride / valence / arousal / immersion
  - 数学漂移 + 阈值触发，不依赖概率骰子
  - 持久化接口 on_save / on_load
  - get_prompt_context() / get_style_guidance() 输出自然语言

移植注意事项：
  - 原版 callback 接口 → Python callable 接口
  - 原版 Node.js Date → Python datetime
  - 原版 verbose console.log → Python logger
  - 算法数值与漂移率严格 1:1
"""
from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ======================================================================
# 默认参数
# ======================================================================
DEFAULT_AXES = {
    "connection": (0.0, 1.0),
    "pride":      (-1.0, 1.0),
    "valence":    (-1.0, 1.0),
    "arousal":    (-1.0, 1.0),
    "immersion":  (0.0, 1.0),
}

DEFAULT_RATES = {
    "prideRegression":            0.003,   # /min 回归 0
    "valenceRegression":          0.005,   # /min 回归 setpoint
    "arousalRegression":          0.005,   # /min 回归 0
    "immersionDecay":             0.01,    # /min 衰减
    "connectionAccel":            0.0,     # 非线性加速指数（0=线性）
    "accelDelay":                 30,      # 加速前线性缓冲（min）
    "valenceSetpoint":            0.0,
    "valenceConnectBoost":        0.0,     # 轻度不开心时倍率
    "valenceConnectBoostThresh":  -0.3,
    "valenceConnectDampen":       0.0,     # 严重低落时倍率
    "valenceConnectDampenThresh": -0.7,
    "valenceLockThreshold":       1.0,     # 关闭（>1 永不触发）
    "valenceLockFactor":          0.5,     # 想强时减速到 0.5×
    "arousalConnectionRiseThresh": 0.35,
    "arousalConnectionRiseRate":   0.0,    # /min
    "prideDefendThreshold":       0.30,    # connection > 此值开始防御
    "prideDefendTarget":          0.6,
    "prideDefendRate":            0.005,   # /min
    "prideArousalConflictRate":   0.0,
    "prideErosionRate":           0.0,     # 想强 → 骄傲被迫下降
    "activityConnectionRelief":   0.0,
}

DEFAULT_THRESHOLDS = {
    "observation":        0.20,
    "considerContact":    0.35,
    "forceContact":       0.50,
    "prideBlock":         0.50,
    "valenceActivity":   -0.5,
    "arousalAgitation":   0.7,
}


# ======================================================================
# 数据类
# ======================================================================
@dataclass
class JiwenStateSnapshot:
    """jiwen 状态快照（可序列化）"""
    connection: float = 0.0
    pride: float = 0.0
    valence: float = 0.0
    arousal: float = 0.0
    immersion: float = 0.0
    # 元数据
    last_chat_message_id: Optional[int] = None
    last_chat_content: Optional[str] = None
    last_chat_at: Optional[str] = None  # ISO format
    user_status: str = "active"          # active / busy / away / sleeping
    activity_type: str = "none"          # none / reading / search / browse / observe
    activity_label: Optional[str] = None
    last_tick_at: Optional[str] = None   # ISO format
    last_delta: Optional[Dict[str, float]] = None
    # 累计统计
    total_ticks: int = 0
    total_contact_triggers: int = 0
    total_activity_triggers: int = 0
    total_observation_triggers: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JiwenStateSnapshot":
        # 兼容缺字段
        if not isinstance(data, dict):
            return cls()
        valid_keys = {f for f in cls.__dataclass_fields__.keys()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


# ======================================================================
# 工具：clip + 漂移
# ======================================================================
def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_call(fn: Optional[Callable], *args, default=None):
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception as e:
        logger.warning("jiwen callback 异常: %s", e)
        return default


# ======================================================================
# 核心类
# ======================================================================
class JiwenEngine:
    """
    jiwen 引擎实例（per character）。

    生命周期：
      jiwen = JiwenEngine(character_id=1, ...)
      loaded = jiwen.load()             # 从 on_load 拉取历史
      triggers = jiwen.tick(minutes=5)  # 推进状态 + 返回触发器
      jiwen.apply_delta({pride: -0.1})  # 聊天后调整
      jiwen.save()                      # 落盘
    """

    def __init__(
        self,
        character_id: int,
        # 必填：消息源
        get_last_message: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        # 可选：连接需求增长速率
        connection_rate_fn: Optional[Callable[[Optional[Dict[str, Any]]], float]] = None,
        # 可选：持久化
        on_save: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_load: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        # 可选：参数覆盖
        rates: Optional[Dict[str, float]] = None,
        thresholds: Optional[Dict[str, float]] = None,
        axes: Optional[Dict[str, tuple]] = None,
        # 可选：状态描述/风格生成（高级覆盖）
        get_prompt_context_fn: Optional[Callable[[JiwenStateSnapshot], str]] = None,
        get_style_guidance_fn: Optional[Callable[[JiwenStateSnapshot], str]] = None,
        # 可选：日志回调
        on_log: Optional[Callable[[str], None]] = None,
        verbose: bool = False,
    ):
        self.character_id = character_id
        self.get_last_message = get_last_message
        self.connection_rate_fn = connection_rate_fn or _default_connection_rate
        self.on_save = on_save
        self.on_load = on_load
        self.rates = {**DEFAULT_RATES, **(rates or {})}
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.axes = {**DEFAULT_AXES, **(axes or {})}
        self.get_prompt_context_fn = get_prompt_context_fn
        self.get_style_guidance_fn = get_style_guidance_fn
        self.on_log = on_log
        self.verbose = verbose

        # 状态
        self._state = JiwenStateSnapshot()
        self._lock = threading.RLock()
        self._loaded = False

    # ----------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------
    def load(self) -> bool:
        """
        从 on_load 拉取历史状态。
        Returns:
            True = 成功加载，False = 无历史（使用默认值）
        """
        with self._lock:
            if self.on_load is None:
                self._loaded = True
                return False
            try:
                data = self.on_load()
            except Exception as e:
                logger.warning("jiwen load 失败: %s", e)
                self._loaded = True
                return False
            if data is None:
                self._loaded = True
                return False
            self._state = JiwenStateSnapshot.from_dict(data)
            self._loaded = True
            return True

    def save(self) -> None:
        """落盘"""
        with self._lock:
            if self.on_save is None:
                return
            try:
                self.on_save(self._state.to_dict())
            except Exception as e:
                logger.warning("jiwen save 失败: %s", e)

    # ----------------------------------------------------------
    # 推进
    # ----------------------------------------------------------
    def tick(self, minutes: float) -> List[Dict[str, Any]]:
        """
        推进状态漂移，返回触发器列表。

        Args:
            minutes: 距离上次 tick 经过的分钟数

        Returns:
            triggers: [{action: "contact" | "find_activity" | "observation",
                        reason: "...", state_at_trigger: {...}}, ...]
        """
        with self._lock:
            minutes = max(0.0, float(minutes))
            if minutes == 0:
                return []

            state = self._state
            rates = self.rates
            axes = self.axes

            # ---- connection: 动态增长曲线 ----
            last_msg = _safe_call(self.get_last_message, default=None)
            base_rate = max(0.0, self.connection_rate_fn(last_msg) or 0.0)

            # 距 last_chat_at 的实际时间（用于 accelFactor）
            actual_age_min = _age_minutes_since(state.last_chat_at)
            # 用传入 minutes 与 actual_age_min 中较大者，避免"过夜忘 tick"导致漂移不连续
            eff_minutes = max(minutes, actual_age_min) if actual_age_min is not None else minutes

            # accelFactor
            accel_delay = rates["accelDelay"]
            if accel_delay > 0 and eff_minutes <= accel_delay:
                accel_factor = 1.0
            else:
                t = eff_minutes - accel_delay if accel_delay > 0 else eff_minutes
                c = rates["connectionAccel"]
                accel_factor = (1.0 + c) ** t if c > 0 else 1.0

            # valenceFactor
            if state.valence < rates["valenceConnectDampenThresh"]:
                vf = rates["valenceConnectDampen"] or 1.0
            elif state.valence < rates["valenceConnectBoostThresh"]:
                vf = rates["valenceConnectBoost"] or 1.0
            else:
                vf = 1.0
            vf = vf if vf > 0 else 1.0

            delta_connection = base_rate * accel_factor * vf * eff_minutes
            state.connection = _clip(
                state.connection + delta_connection,
                axes["connection"][0], axes["connection"][1],
            )

            # ---- pride: 回归 0；高 connection 时防御性上升 ----
            pride_regression = rates["prideRegression"] * minutes
            if state.connection > rates["prideDefendThreshold"]:
                # 朝 prideDefendTarget 方向漂移
                target = rates["prideDefendTarget"]
                delta_pride = (target - state.pride) * rates["prideDefendRate"] * minutes
            else:
                delta_pride = -state.pride * (pride_regression / max(0.01, abs(state.pride) + 0.01))
                # 简化：朝 0 回归
                if state.pride > 0:
                    delta_pride = -min(state.pride, pride_regression)
                elif state.pride < 0:
                    delta_pride = min(-state.pride, pride_regression)
            state.pride = _clip(state.pride + delta_pride, axes["pride"][0], axes["pride"][1])

            # ---- valence: 朝 setpoint 回归；高 connection 时减速 ----
            sp = rates["valenceSetpoint"]
            if state.connection > rates["valenceLockThreshold"]:
                vr = rates["valenceRegression"] * rates["valenceLockFactor"] * minutes
            else:
                vr = rates["valenceRegression"] * minutes
            delta_valence = (sp - state.valence) * (vr / max(0.01, abs(state.valence - sp) + 0.01))
            if state.valence > sp:
                delta_valence = -min(state.valence - sp, vr)
            elif state.valence < sp:
                delta_valence = min(sp - state.valence, vr)
            state.valence = _clip(state.valence + delta_valence, axes["valence"][0], axes["valence"][1])

            # ---- arousal: 回归 0；高 connection 时攀升 ----
            if state.connection > rates["arousalConnectionRiseThresh"]:
                delta_arousal = rates["arousalConnectionRiseRate"] * minutes
            else:
                ar = rates["arousalRegression"] * minutes
                if state.arousal > 0:
                    delta_arousal = -min(state.arousal, ar)
                elif state.arousal < 0:
                    delta_arousal = min(-state.arousal, ar)
                else:
                    delta_arousal = 0.0
            state.arousal = _clip(state.arousal + delta_arousal, axes["arousal"][0], axes["arousal"][1])

            # ---- immersion: 衰减 ----
            delta_immersion = -rates["immersionDecay"] * minutes
            state.immersion = _clip(state.immersion + delta_immersion, axes["immersion"][0], axes["immersion"][1])

            # ---- 元数据 ----
            state.last_tick_at = _now_iso()
            state.total_ticks += 1

            # ---- 触发器检测 ----
            triggers = self._check_thresholds()
            for t in triggers:
                if t["action"] == "contact":
                    state.total_contact_triggers += 1
                elif t["action"] == "find_activity":
                    state.total_activity_triggers += 1
                elif t["action"] == "observation":
                    state.total_observation_triggers += 1

            # ---- 日志 ----
            if self.verbose or triggers:
                self._log_tick(minutes, triggers)

            return triggers

    def _check_thresholds(self) -> List[Dict[str, Any]]:
        """纯阈值检测（不推进状态）"""
        s = self._state
        t = self.thresholds
        triggers: List[Dict[str, Any]] = []

        c = s.connection
        # observation：注意到沉默
        if c >= t["observation"]:
            triggers.append({
                "action": "observation",
                "reason": f"connection {c:.2f} >= observation {t['observation']}",
                "state_at_trigger": s.to_dict(),
            })
        # contact
        if c >= t["forceContact"]:
            triggers.append({
                "action": "contact",
                "reason": f"connection {c:.2f} >= forceContact {t['forceContact']}",
                "forced": True,
                "state_at_trigger": s.to_dict(),
            })
        elif c >= t["considerContact"]:
            if s.pride < t["prideBlock"]:
                triggers.append({
                    "action": "contact",
                    "reason": f"connection {c:.2f} >= considerContact {t['considerContact']}, pride {s.pride:.2f} < prideBlock {t['prideBlock']}",
                    "forced": False,
                    "state_at_trigger": s.to_dict(),
                })
            else:
                triggers.append({
                    "action": "find_activity",
                    "reason": f"想开口但骄傲阻断 (pride {s.pride:.2f} >= prideBlock {t['prideBlock']})",
                    "state_at_trigger": s.to_dict(),
                })
        # find_activity by 情绪/唤醒
        if s.valence <= t["valenceActivity"]:
            triggers.append({
                "action": "find_activity",
                "reason": f"valence {s.valence:.2f} <= valenceActivity {t['valenceActivity']}（心情差，自我调节）",
                "state_at_trigger": s.to_dict(),
            })
        if s.arousal >= t["arousalAgitation"]:
            triggers.append({
                "action": "find_activity",
                "reason": f"arousal {s.arousal:.2f} >= arousalAgitation {t['arousalAgitation']}（焦躁，宣泄）",
                "state_at_trigger": s.to_dict(),
            })
        return triggers

    def check_thresholds(self) -> List[Dict[str, Any]]:
        """对外：纯检测，不推进状态"""
        with self._lock:
            return self._check_thresholds()

    # ----------------------------------------------------------
    # 状态调整
    # ----------------------------------------------------------
    def apply_delta(self, delta: Dict[str, float]) -> None:
        """
        聊天后调整状态。
        delta: {pride?, valence?, arousal?, connection?}
        兼容 'mood' 别名 → valence
        """
        with self._lock:
            s = self._state
            d = dict(delta or {})
            if "mood" in d and "valence" not in d:
                d["valence"] = d.pop("mood")

            for axis, val in d.items():
                if axis not in self.axes:
                    continue
                lo, hi = self.axes[axis]
                cur = getattr(s, axis, 0.0)
                new = _clip(cur + float(val), lo, hi)
                setattr(s, axis, new)
            s.last_delta = d

    def reset_connection(self) -> None:
        """对方回复后调用：连接需求归零（不是开口后）"""
        with self._lock:
            self._state.connection = 0.0

    def set_activity(self, activity_type: str, label: Optional[str] = None) -> None:
        """设置沉浸度（reading / search / browse / observe）"""
        with self._lock:
            valid = {"none", "reading", "search", "browse", "observe"}
            self._state.activity_type = activity_type if activity_type in valid else "none"
            self._state.activity_label = label
            # immersion 提升
            immersion_boosts = {
                "reading": 0.7,
                "search":  0.5,
                "browse":  0.4,
                "observe": 0.3,
                "none":    0.0,
            }
            boost = immersion_boosts.get(self._state.activity_type, 0.0)
            if boost > 0:
                self._state.immersion = _clip(
                    max(self._state.immersion, boost),
                    self.axes["immersion"][0], self.axes["immersion"][1],
                )
            # activity 部分缓解 connection
            relief = self.rates.get("activityConnectionRelief", 0.0) or 0.0
            if relief > 0 and boost > 0:
                self._state.connection = _clip(
                    self._state.connection - relief * boost,
                    self.axes["connection"][0], self.axes["connection"][1],
                )

    def set_user_status(self, status: str) -> None:
        """设置对方状态"""
        with self._lock:
            valid = {"active", "busy", "away", "sleeping"}
            self._state.user_status = status if status in valid else "active"

    def get_user_status(self) -> str:
        with self._lock:
            return self._state.user_status

    def set_last_chat_message_id(self, msg_id: int, content: Optional[str] = None) -> None:
        with self._lock:
            self._state.last_chat_message_id = msg_id
            self._state.last_chat_content = content
            self._state.last_chat_at = _now_iso()

    def get_last_chat_message_id(self) -> Optional[int]:
        with self._lock:
            return self._state.last_chat_message_id

    # ----------------------------------------------------------
    # 状态查询
    # ----------------------------------------------------------
    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def get_state_summary(self) -> str:
        with self._lock:
            s = self._state
            return (
                f"[积温] c:{s.connection:.2f}({'悠闲' if s.connection<0.2 else '在想念' if s.connection<0.4 else '想开口' if s.connection<0.5 else '坐不住'}) "
                f"p:{s.pride:.2f}({'放软' if s.pride<0 else '端着' if s.pride>0.3 else '中性'}) "
                f"v:{s.valence:.2f}({'低落' if s.valence<-0.3 else '中性' if s.valence<0.3 else '开心'}) "
                f"a:{s.arousal:.2f}({'焦躁' if s.arousal>0.3 else '平静' if s.arousal>-0.3 else '慵懒'}) "
                f"i:{s.immersion:.2f}({_activity_desc(s.activity_type)}) "
                f"| userStatus: {s.user_status}"
            )

    # ----------------------------------------------------------
    # LLM 上下文生成
    # ----------------------------------------------------------
    def get_prompt_context(self) -> str:
        """生成 LLM 用的状态自然语言描述（角色视角）"""
        if self.get_prompt_context_fn:
            with self._lock:
                return self.get_prompt_context_fn(self._state)
        with self._lock:
            return _default_prompt_context(self._state)

    def get_style_guidance(self) -> str:
        """生成 LLM 用的说话风格指引（状态到语气映射）"""
        if self.get_style_guidance_fn:
            with self._lock:
                return self.get_style_guidance_fn(self._state)
        with self._lock:
            return _default_style_guidance(self._state)

    # ----------------------------------------------------------
    # 内部：日志
    # ----------------------------------------------------------
    def _log_tick(self, minutes: float, triggers: List[Dict[str, Any]]) -> None:
        s = self._state
        msg = (
            f"[积温 char={self.character_id}] tick {minutes:.1f}min | "
            f"c:{s.connection:.2f} p:{s.pride:.2f} v:{s.valence:.2f} "
            f"a:{s.arousal:.2f} i:{s.immersion:.2f} | "
            f"触发: {[t['action'] for t in triggers] or '—'}"
        )
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass
        if self.verbose:
            logger.info(msg)


# ======================================================================
# 默认辅助函数
# ======================================================================
def _default_connection_rate(last_msg: Optional[Dict[str, Any]]) -> float:
    """
    默认 connectionRateFn：
      - 对方最后说"晚安" → 慢（让角色也准备睡）
      - 对方说"出门" → 慢
      - 短消息（<10 字）→ 快（可能没说完）
      - 默认 0.0007/min
    """
    if not last_msg:
        return 0.0007
    content = (last_msg.get("content") or "").strip()
    if "晚安" in content or "再见" in content:
        return 0.0003
    if "出门" in content or "忙" in content:
        return 0.0005
    if len(content) < 10:
        return 0.0010
    return 0.0007


def _activity_desc(activity_type: str) -> str:
    mapping = {
        "none":    "无活动",
        "reading": "阅读",
        "search":  "搜索",
        "browse":  "浏览",
        "observe": "观察",
    }
    return mapping.get(activity_type, "未知")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_minutes_since(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 60.0)
    except Exception:
        return None


def _default_prompt_context(s: JiwenStateSnapshot) -> str:
    """默认状态描述（角色视角）"""
    lines: List[str] = []
    if s.connection >= 0.5:
        lines.append("User 沉默已久。你心里已经坐不住了，想主动找对方说话。")
    elif s.connection >= 0.35:
        lines.append("User 一阵子没说话。你开始有点想找对方了。")
    elif s.connection >= 0.2:
        lines.append("User 好一会儿没出声了。开始在想对方在干嘛。")
    else:
        lines.append("最近和 User 聊得还不错，没什么特别惦记的。")
    if s.pride >= 0.5:
        lines.append("你端着，不太愿意先低头。")
    elif s.pride >= 0.2:
        lines.append("有点端着，但也不是不能开口。")
    elif s.pride <= -0.2:
        lines.append("你心里是放软的，愿意先开口。")
    if s.valence <= -0.5:
        lines.append("你今天心情不太好，不想被人打扰。")
    elif s.valence <= -0.2:
        lines.append("今天有点低落，话可能少一些。")
    elif s.valence >= 0.3:
        lines.append("今天心情不错，情绪明亮。")
    if s.arousal >= 0.5:
        lines.append("你有点焦躁，坐不下来。")
    elif s.arousal <= -0.3:
        lines.append("你有点慵懒，反应慢半拍。")
    if s.immersion >= 0.5 and s.activity_type != "none":
        label = s.activity_label or _activity_desc(s.activity_type)
        lines.append(f"你刚才在 {label}，脑子里还留着点刚才的内容。")
    return "\n".join(lines)


def _default_style_guidance(s: JiwenStateSnapshot) -> str:
    """默认风格指引（状态到语气映射）"""
    guides: List[str] = []
    if s.pride >= 0.5:
        guides.append("骄傲高：嘴硬，不承认在等，必须找借口开口。")
    elif s.pride >= 0.3 and s.connection >= 0.3:
        guides.append("别扭，想找又拉不下脸，话里带赌气的味道。")
    if s.valence <= -0.3:
        guides.append("情绪低：能用句号就别用逗号，少用感叹号。")
    if s.connection >= 0.5:
        guides.append("坐不住了，可能直接问『人呢？』或干脆找个话题。")
    if s.arousal >= 0.5:
        guides.append("焦躁：句式更短，更直接，少绕弯。")
    if s.arousal <= -0.3:
        guides.append("慵懒：节奏放慢，留白多一点。")
    if not guides:
        guides.append("自然对话，不刻意调整。")
    return "\n".join(guides)


# ======================================================================
# 工厂函数
# ======================================================================
def create_jiwen(**kwargs) -> JiwenEngine:
    """
    工厂函数：创建 jiwen 实例。

    Example:
        jiwen = create_jiwen(
            character_id=1,
            get_last_message=lambda: db.query_last_msg(1),
            on_save=lambda state: db.set_jiwen_state(1, state),
            on_load=lambda: db.get_jiwen_state(1),
        )
        jiwen.load()
        triggers = jiwen.tick(5)
    """
    return JiwenEngine(**kwargs)


# ======================================================================
# 模块导出
# ======================================================================
__all__ = [
    "JiwenEngine",
    "JiwenStateSnapshot",
    "create_jiwen",
    "DEFAULT_RATES",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_AXES",
]
