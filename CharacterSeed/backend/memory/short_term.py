"""
短期记忆管理 (Short-term Memory)

使用纯 Python 实现的滑动窗口记忆：
- 保留最近 K 轮对话（默认 10 轮）
- 即时访问，无延迟
- 会话结束后自动清理（除非显式转存到长期记忆）

设计考量：
- K=10 在上下文覆盖度（一场完整对话约 5-8 轮）
  和 Token 预算（约 2000 tokens）之间取得平衡
- 不持久化，重启即丢失（由长期记忆接管）
- 不依赖 LangChain，避免版本兼容问题
"""

from collections import deque
from typing import List, Dict, Any, Optional


class ShortTermMemory:
    """
    短期记忆管理器
    
    负责管理单个会话内的最近 K 轮对话
    """
    
    def __init__(self, k: int = 10, session_id: Optional[str] = None):
        """
        初始化短期记忆
        
        Args:
            k: 保留的对话轮数（默认 10）
            session_id: 会话 ID（用于标识不同的对话）
        """
        self.k = k
        self.session_id = session_id or "default"
        # 使用 deque 自动维护窗口大小
        # 每条记录为 (role, content)
        self._messages: deque = deque(maxlen=k * 2)  # K 轮 = 2K 条消息
    
    def add_user_message(self, message: str) -> None:
        """添加用户消息"""
        self._messages.append(("user", message))
    
    def add_ai_message(self, message: str) -> None:
        """添加 AI 回复"""
        self._messages.append(("assistant", message))
    
    def get_messages(self) -> List[Any]:
        """获取所有消息（兼容旧接口）"""
        return self._messages.copy()
    
    def get_message_list(self) -> List[Dict[str, str]]:
        """
        获取消息列表（OpenAI 格式）
        
        Returns:
            [{"role": "user", "content": "..."}, ...]
        """
        return [
            {"role": role, "content": content}
            for role, content in self._messages
        ]
    
    def get_context_text(self) -> str:
        """
        获取格式化的对话历史文本
        
        Returns:
            "[用户]: xxx\n[AI]: yyy\n..." 格式
        """
        lines = []
        for role, content in self._messages:
            display_role = "用户" if role == "user" else "AI"
            lines.append(f"[{display_role}]: {content}")
        return "\n".join(lines) if lines else "（暂无对话历史）"
    
    def clear(self) -> None:
        """清空记忆"""
        self._messages.clear()
    
    def __len__(self) -> int:
        """返回当前消息数量"""
        return len(self._messages)
    
    def __repr__(self) -> str:
        return f"ShortTermMemory(k={self.k}, messages={len(self._messages)}, session={self.session_id})"
