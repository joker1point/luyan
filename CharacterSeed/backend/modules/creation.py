"""
角色创建模块：把用户一句话描述交给 LLM，产出结构化角色数据。
"""
from __future__ import annotations
import json
import logging
from typing import Dict, Any, Optional

from backend.services.llm_service import LLMService
from backend.config import settings

logger = logging.getLogger(__name__)

# [F3] 维度键 → 中文标签 + 等级换算（用于在 prompt 中追加「用户设定倾向」）
_DIMENSION_LABELS = {
    "optimism": "乐观度",
    "courage": "勇气",
    "empathy": "同理心",
    "loyalty": "忠诚度",
    "intelligence": "智力",
    "sociability": "社交性",
}


def _level_text(val: int) -> str:
    """把 0-100 数值转成「极高/偏高/中等/偏低/极低」五档描述。"""
    if val >= 80:
        return "极高"
    if val >= 60:
        return "偏高"
    if val >= 40:
        return "中等"
    if val >= 20:
        return "偏低"
    return "极低"


def _format_dimensions_hint(dimensions_hint: Optional[str]) -> str:
    """
    [F3] 把前端传来的 dimensions JSON 字符串翻译为 LLM 易读的「用户倾向」段落。
    - 输入非法 / 空 / 非 dict：返回空串（不注入 prompt，向后兼容）
    - 输入合法：返回多行中文描述，提示 LLM 在生成 personality 时参考
    """
    if not dimensions_hint:
        return ""
    try:
        dims = json.loads(dimensions_hint)
    except (json.JSONDecodeError, TypeError):
        logger.warning("dimensions_hint 解析失败，已忽略: %r", dimensions_hint[:200])
        return ""
    if not isinstance(dims, dict):
        return ""

    lines = []
    for key, label in _DIMENSION_LABELS.items():
        val = dims.get(key)
        if isinstance(val, (int, float)):
            iv = int(val)
            lines.append(f"- {label}: {iv}/100 （{_level_text(iv)}）")
    if not lines:
        return ""
    return (
        "\n【用户设定的性格倾向参考】\n"
        + "\n".join(lines)
        + "\n请在生成 personality 时参考以上数值，让生成的属性与用户的期望尽量一致。\n"
    )


def _format_name_hint(preferred_name: Optional[str]) -> str:
    """[F2] 把用户填写的名字翻译为 prompt 提示，要求 LLM 优先使用此名。"""
    name = (preferred_name or "").strip()
    if not name:
        return ""
    return f"\n【用户指定的角色名称】: {name}\n请使用此名称作为角色的正式名字。\n"


class CreationModule:
    """角色创建模块（Pipeline模式）"""

    def __init__(self):
        self.llm_service = LLMService()
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """加载prompt模板"""
        with open("backend/prompts/creation.txt", "r", encoding="utf-8") as f:
            return f.read()

    def reload(self) -> None:
        """热更新 LLM 配置（设置页改动后调用，复用已加载的 prompt 模板）"""
        self.llm_service.reload_config()

    def validate_input(self, user_input: str, input_type: str = "text") -> str:
        """
        步骤1：验证输入

        Args:
            user_input: 用户输入
            input_type: 输入类型（"text"或"file"）

        Returns:
            验证后的输入字符串
        """
        if not user_input or len(user_input.strip()) == 0:
            raise ValueError("输入不能为空")

        # 如果是文件输入，可能已经读取为字符串
        return user_input.strip()

    def build_prompt(
        self,
        validated_input: str,
        dimensions_text: str = "",
        name_hint_text: str = "",
    ) -> str:
        """
        步骤2：组装Prompt

        Args:
            validated_input: 验证后的输入
            dimensions_text: [F3] 维度倾向提示（可空）
            name_hint_text: [F2] 角色名提示（可空）

        Returns:
            组装好的prompt
        """
        prompt = self.prompt_template.replace(
            "{user_description}",
            validated_input
        )
        # [F3] 追加 dimensions 倾向（如果有）
        if dimensions_text:
            prompt = prompt.rstrip() + "\n\n" + dimensions_text
        # [F2] 追加用户指定名（如果有）
        if name_hint_text:
            prompt = prompt.rstrip() + "\n\n" + name_hint_text
        return prompt

    def call_llm(self, prompt: str) -> str:
        """
        步骤3：调用LLM

        Args:
            prompt: 组装好的prompt

        Returns:
            LLM的原始响应（JSON字符串）
        """
        system_prompt = "你是一个专业的角色创建助手，擅长从描述中提取角色特征。"
        raw_response = self.llm_service.call(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.7,
            response_format={"type": "json_object"},  # Creation 需要 JSON 输出
            task="creation",
        )
        return raw_response

    def parse_response(self, raw_response: str) -> Dict[str, Any]:
        """
        步骤4：解析并校验LLM响应

        Args:
            raw_response: LLM的原始响应

        Returns:
            校验通过后的字典

        Raises:
            ValueError: 解析失败或 schema 校验失败时
        """
        # 先做 robust JSON 解析（含 regex fallback）
        parsed = self.llm_service.parse_json_response(raw_response)
        # 再做轻量级 schema 校验
        parsed = LLMService.validate_creation_schema(parsed)
        return parsed

    def run(
        self,
        user_input: str,
        input_type: str = "text",
        preferred_name: Optional[str] = None,      # [F2] 用户指定名
        dimensions_hint: Optional[str] = None,     # [F3] 维度倾向 JSON
    ) -> tuple[Dict[str, Any], str]:
        """
        运行完整的Creation Pipeline

        Args:
            user_input: 用户输入（一句话或故事文本）
            input_type: 输入类型（"text"或"file"）
            preferred_name: [F2] 用户指定的角色名（None → 让 LLM 猜）
            dimensions_hint: [F3] JSON 字符串，描述用户设定的六维倾向

        Returns:
            (parsed_data, raw_response) 元组
        """
        # 步骤1：验证输入
        validated_input = self.validate_input(user_input, input_type)

        # [F3] 把 dimensions 翻译为 LLM 易读的 hint 文本
        dimensions_text = _format_dimensions_hint(dimensions_hint)
        # [F2] 把 name 翻译为 hint 文本
        name_hint_text = _format_name_hint(preferred_name)

        # 步骤2：组装Prompt（带 dimensions + name 提示）
        prompt = self.build_prompt(
            validated_input,
            dimensions_text=dimensions_text,
            name_hint_text=name_hint_text,
        )

        # 步骤3：调用LLM
        raw_response = self.call_llm(prompt)

        # 步骤4：解析响应
        parsed_data = self.parse_response(raw_response)

        return parsed_data, raw_response
