from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Database Configuration
    DATABASE_URL: str = "sqlite:///./data/character_seed.db"

    # Application Settings
    DEBUG: bool = False
    API_V1_STR: str = "/api"
    PROJECT_NAME: str = "CharacterSeed"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        # 关键：允许 .env 中存在未声明的字段（被忽略）
        # 原因：旧的 LLM_PROVIDER / AGNES_API_KEY 等配置已迁出到
        # usercontext/llm_settings.json，但 .env 中可能仍残留这些变量。
        # 旧版 pydantic 默认 forbid 会让整个 Settings 构造失败、整个后端启动不了。
        # 设 ignore 后未声明字段被默默忽略，保证后端可启动；
        # 实际生效的配置以 settings_store 中的 JSON 为准（env 仅作 fallback）。
        extra="ignore",
    )

settings = Settings()
