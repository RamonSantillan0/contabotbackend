from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "ramon_agenteia"
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    CORS_ORIGINS: str = "http://localhost:5173"

    # ✅ Agregar Ollama (para que no sean "extra")
    OLLAMA_API_BASE: str = "https://ollama.com/api"
    OLLAMA_API_KEY: str = "781c5e015fcd44a3871f7fed3a0c7c64.nij_BIa2rroz_kJ2_Fx5YEuM"
    OLLAMA_MODEL: str = "gpt-oss:120b"

    # ✅ Pydantic v2
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
