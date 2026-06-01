from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    GOOGLE_MAPS_API_KEY: str = ""
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    AI_MODE: str = "gemini"  # "mock" or "gemini"
    HTTP_TIMEOUT_S: float = 10.0
    PROVIDER_RETRY_COUNT: int = 2
    ROUTE_CACHE_TTL_S: int = 300
    WEATHER_CACHE_TTL_S: int = 120
    STREET_VIEW_CACHE_TTL_S: int = 300
    REDIS_URL: str | None = None
    CACHE_BACKEND: str = "auto"  # "auto", "memory", or "redis"
    REQUIRE_AUTH: bool = False
    API_KEY_HASHES: str = ""
    SESSION_SECRET_KEY: str | None = None
    SECRET_ENCRYPTION_KEY: str | None = None
    SESSION_TTL_S: int = 86400
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 5
    API_RATE_LIMIT_PER_MINUTE: int = 120
    MAX_REQUEST_BODY_BYTES: int = 1048576
    ALLOWED_HOSTS: str = "*"
    CORS_ORIGINS: str = ""
    SECURE_COOKIES: bool = True
    HSTS_MAX_AGE_S: int = 31536000
    DATABASE_URL: str = "sqlite:///./smartlens.db"
    ALLOW_SIGNUPS: bool = True

    def normalized_ai_mode(self) -> str:
        # Allow values like "mock#comment" from loose .env formatting.
        return (self.AI_MODE or "mock").split("#", 1)[0].strip().lower() or "mock"

    def runtime_issues(self) -> list[str]:
        issues: list[str] = []

        if not self.GOOGLE_MAPS_API_KEY:
            issues.append(
                "Missing GOOGLE_MAPS_API_KEY. Add it to .env to enable maps and route loading."
            )

        ai_mode = self.normalized_ai_mode()
        if ai_mode not in {"mock", "gemini"}:
            issues.append("AI_MODE must be 'mock' or 'gemini'.")

        if ai_mode == "gemini" and not self.GEMINI_API_KEY:
            issues.append("AI_MODE is 'gemini' but GEMINI_API_KEY is missing.")

        cache_backend = (self.CACHE_BACKEND or "auto").strip().lower()
        if cache_backend not in {"auto", "memory", "redis"}:
            issues.append("CACHE_BACKEND must be 'auto', 'memory', or 'redis'.")

        if cache_backend == "redis" and not self.REDIS_URL:
            issues.append("CACHE_BACKEND is 'redis' but REDIS_URL is missing.")

        if self.REQUIRE_AUTH:
            if not self.SESSION_SECRET_KEY:
                issues.append("REQUIRE_AUTH is true but SESSION_SECRET_KEY is missing.")
            if not self.SECRET_ENCRYPTION_KEY:
                issues.append("REQUIRE_AUTH is true but SECRET_ENCRYPTION_KEY is missing.")

        return issues

    def require_runtime_ready(self) -> None:
        issues = self.runtime_issues()
        if issues:
            joined = " | ".join(issues)
            raise RuntimeError(f"Configuration error: {joined}")


settings = Settings()
