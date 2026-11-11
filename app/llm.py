from openai import AsyncOpenAI

from app.config import settings

_chat = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
_embed = AsyncOpenAI(base_url=settings.embed_base_url, api_key=settings.embed_api_key)


async def embed(texts: list[str]) -> list[list[float]]:
    resp = await _embed.embeddings.create(model=settings.embed_model, input=texts)
    return [item.embedding for item in resp.data]


async def chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 700) -> str:
    resp = await _chat.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()
