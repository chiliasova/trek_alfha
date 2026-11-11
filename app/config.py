import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str = _env("BOT_TOKEN", "")
    database_url: str = _env("DATABASE_URL", "postgresql://alfa:alfa@localhost:5432/alfa_bot")
    source_database_url: str = _env("SOURCE_DATABASE_URL", "postgresql://alfa:alfa@localhost:5432/alfa_source")

    # Любой OpenAI-совместимый API: Ollama, OpenAI, YandexGPT, GigaChat-прокси и т.п.
    llm_base_url: str = _env("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key: str = _env("LLM_API_KEY", "ollama")
    llm_model: str = _env("LLM_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
    embed_base_url: str = _env("EMBED_BASE_URL", _env("LLM_BASE_URL", "http://localhost:11434/v1"))
    embed_api_key: str = _env("EMBED_API_KEY", _env("LLM_API_KEY", "ollama"))
    embed_model: str = _env("EMBED_MODEL", "bge-m3")
    # Для моделей с раздельными эмбеддингами документа и запроса (например, Yandex text-search-query)
    embed_query_model: str = _env("EMBED_QUERY_MODEL", "") or _env("EMBED_MODEL", "bge-m3")
    # Идентификатор каталога/проекта, если провайдер его требует (Yandex Cloud: folder id)
    llm_project: str = _env("LLM_PROJECT", "")

    # Поиск
    top_k: int = int(_env("RAG_TOP_K", "6"))
    min_similarity: float = float(_env("RAG_MIN_SIMILARITY", "0.45"))

    platform_base_url: str = _env("PLATFORM_BASE_URL", "")


settings = Settings()
