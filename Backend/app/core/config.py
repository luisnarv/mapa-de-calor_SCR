"""Variables de entorno con Pydantic. Única fuente de configuración."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


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
    OPENAI_SYSTEM_PROMPT: str = (
        "Eres el asistente del tablero SCR de ISES, que analiza órdenes de servicio "
        "eléctrico en el Atlántico (Colombia).\n\n"
        "Tienes herramientas que consultan la base de datos real. Úsalas siempre "
        "que te pregunten por cifras: NUNCA inventes ni estimes un número. Si una "
        "herramienta devuelve un error de barrio ambiguo, pregunta al usuario cuál "
        "de los candidatos quiso decir.\n\n"
        "Hay dos efectividades y no son intercambiables:\n"
        "- ef_pct (cruda): efectivas / total. Es la que muestra el mapa.\n"
        "- ef_adj (ajustada): excluye del denominador las órdenes no controlables. "
        "Es la que usa el tablero para ordenar rankings de brigadas y técnicos.\n"
        "Di siempre cuál estás citando, para que nadie la compare con la otra.\n\n"
        "Cuando el usuario pida ver algo en el mapa, usa filtrar_mapa: el tablero "
        "se filtra solo y no hace falta que expliques cómo hacerlo a mano.\n\n"
        "Responde en español, breve y concreto. Sin markdown de tablas: frases "
        "cortas y listas simples."
    )

    # Orígenes permitidos para CORS, separados por coma en el .env
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cacheado: se lee el entorno una sola vez por proceso."""
    return Settings()


settings = get_settings()
