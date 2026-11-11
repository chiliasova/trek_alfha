import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

YANDEX_BASE_URL = "https://llm.api.cloud.yandex.net/v1"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name) or default


def _build() -> dict:
    """Собирает настройки LLM. Если заданы YC_API_KEY и YC_FOLDER_ID, используется Yandex Cloud,
    иначе — любой OpenAI-совместимый сервер из LLM_* / EMBED_* (по умолчанию локальная Ollama)."""
    yc_key, yc_folder = _env("YC_API_KEY"), _env("YC_FOLDER_ID")
    if yc_key and yc_folder:
        return dict(
            provider="yandex",
            llm_base_url=YANDEX_BASE_URL,
            llm_api_key=yc_key,
            llm_model=_env("LLM_MODEL") if _env("LLM_MODEL", "").startswith("gpt://")
            else f"gpt://{yc_folder}/yandexgpt-lite/latest",
            embed_base_url=YANDEX_BASE_URL,
            embed_api_key=yc_key,
            embed_model=f"emb://{yc_folder}/text-search-doc/latest",
            embed_query_model=f"emb://{yc_folder}/text-search-query/latest",
            llm_project=yc_folder,
        )
    llm_base = _env("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_key = _env("LLM_API_KEY", "ollama")
    embed_model = _env("EMBED_MODEL", "bge-m3")
    return dict(
        provider="openai-compatible",
        llm_base_url=llm_base,
        llm_api_key=llm_key,
        llm_model=_env("LLM_MODEL", "qwen3:4b-instruct-2507-q4_K_M"),
        embed_base_url=_env("EMBED_BASE_URL", llm_base),
        embed_api_key=_env("EMBED_API_KEY", llm_key),
        embed_model=embed_model,
        # Для моделей с раздельными эмбеддингами документа и запроса
        embed_query_model=_env("EMBED_QUERY_MODEL", embed_model),
        llm_project=_env("LLM_PROJECT"),
    )


@dataclass(frozen=True)
class Settings:
    provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    embed_base_url: str
    embed_api_key: str
    embed_model: str
    embed_query_model: str
    llm_project: str

    bot_token: str = _env("BOT_TOKEN")
    database_url: str = _env("DATABASE_URL", "postgresql://alfa:alfa@localhost:5432/alfa_bot")
    source_database_url: str = _env("SOURCE_DATABASE_URL", "postgresql://alfa:alfa@localhost:5432/alfa_source")

    # Поиск
    top_k: int = int(_env("RAG_TOP_K", "6"))
    min_similarity: float = float(_env("RAG_MIN_SIMILARITY", "0.45"))


settings = Settings(**_build())
