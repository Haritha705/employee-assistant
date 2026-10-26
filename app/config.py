import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Database
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ops_assistant"
    
    # Model Provider (Google Gemini)
    GEMINI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    MODEL_NAME: str = "gemini-1.5-flash"
    MODEL_TIMEOUT_SECONDS: float = 30.0
    MODEL_MAX_RETRIES: int = 1
    
    # Pricing for Cost Estimation ($ per 1K tokens)
    COST_PER_1K_INPUT_TOKENS: float = 0.00015
    COST_PER_1K_OUTPUT_TOKENS: float = 0.00060
    
    # Redis Rate Limiting (Stretch Feature)
    REDIS_URL: Optional[str] = None
    RATE_LIMIT_REQUESTS: int = 30
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    
    # Authentication
    JWT_SECRET_KEY: str = "factory-ops-super-secret-key-2026"
    JWT_ALGORITHM: str = "HS256"
    
    # App
    APP_NAME: str = "Ops Assistant Service"
    LOG_LEVEL: str = "INFO"

    @property
    def effective_api_key(self) -> Optional[str]:
        raw_key = self.GEMINI_API_KEY or self.GOOGLE_API_KEY or os.getenv("GOOGLE_GEMINI_API_KEY")
        if not raw_key:
            return None
        cleaned = raw_key.strip()
        if not cleaned or "your_" in cleaned.lower() or cleaned.lower() in ["placeholder", "none", "fake", ""]:
            return None
        return cleaned


settings = Settings()
