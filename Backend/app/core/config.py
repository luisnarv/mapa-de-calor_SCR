"""Variables de entorno con Pydantic. Única fuente de configuración."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = BACKEND_ROOT / "app" / "prompts"


def leer_prompt(nombre: str) -> str:
    """Lee un prompt de `app/prompts/`.

    Falla al arrancar si no está, en vez de dejar al asistente sin instrucciones
    y que se note cuando alguien pregunte algo raro.
    """
    ruta = PROMPTS_DIR / nombre
    if not ruta.is_file():
        raise RuntimeError(
            f"Falta el prompt {ruta}. Sin él el asistente responde sin reglas: "
            "restaura el archivo antes de arrancar."
        )
    return ruta.read_text(encoding="utf-8").strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "SCR API"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # Los JSON que genera el ETL: la misma fuente que consume el mapa.
    DATA_DIR: Path = BACKEND_ROOT / "app" / "data"

    # postgresql+asyncpg://usuario:password@host:5432/basededatos
    DATABASE_URL: str
    DB_ECHO: bool = False

    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: float = 60.0
    OPENAI_MAX_RETRIES: int = 2
    OPENAI_SYSTEM_PROMPT: str = leer_prompt("sistema.md")

    CARGUE_MAX_MB: int = 10
    CARGUE_TTL_MINUTOS: int = 120
    CARGUE_MAXIMOS: int = 20
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cacheado: se lee el entorno una sola vez por proceso."""
    return Settings()


settings = get_settings()
