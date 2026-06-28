from typing import Dict, Any, Optional
from backend.services.llm_service import LLMService
from backend.config import settings

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
    
    def build_prompt(self, validated_input: str) -> str:
        """
        步骤2：组装Prompt
        
        Args:
            validated_input: 验证后的输入
            
        Returns:
            组装好的prompt
        """
        prompt = self.prompt_template.replace(
            "{user_description}",
            validated_input
        )
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
    
    def run(self, user_input: str, input_type: str = "text") -> tuple[Dict[str, Any], str]:
        """
        运行完整的Creation Pipeline
        
        Args:
            user_input: 用户输入（一句话或故事文本）
            input_type: 输入类型（"text"或"file"）
            
        Returns:
            (parsed_data, raw_response) 元组
        """
        # 步骤1：验证输入
        validated_input = self.validate_input(user_input, input_type)
        
        # 步骤2：组装Prompt
        prompt = self.build_prompt(validated_input)
        
        # 步骤3：调用LLM
        raw_response = self.call_llm(prompt)
        
        # 步骤4：解析响应
        parsed_data = self.parse_response(raw_response)
        
        return parsed_data, raw_response
