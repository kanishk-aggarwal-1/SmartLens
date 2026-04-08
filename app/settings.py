from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    GOOGLE_MAPS_API_KEY: str = ""
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    AI_MODE: str = "gemini"  # "mock" or "gemini"

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

        return issues

    def require_runtime_ready(self) -> None:
        issues = self.runtime_issues()
        if issues:
            joined = " | ".join(issues)
            raise RuntimeError(f"Configuration error: {joined}")


settings = Settings()
