import asyncio

from openai import AsyncOpenAI, BadRequestError

from app.config import settings

_project = settings.llm_project or None
_chat = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, project=_project)
_embed = AsyncOpenAI(base_url=settings.embed_base_url, api_key=settings.embed_api_key, project=_project)


async def embed(texts: list[str], query: bool = False) -> list[list[float]]:
    model = settings.embed_query_model if query else settings.embed_model
    try:
        resp = await _embed.embeddings.create(model=model, input=texts, encoding_format="float")
        return [item.embedding for item in resp.data]
    except BadRequestError:
        if len(texts) == 1:
            raise
        # Некоторые провайдеры принимают только один текст за запрос
        out = []
        for t in texts:
            resp = await _embed.embeddings.create(model=model, input=t, encoding_format="float")
            out.append(resp.data[0].embedding)
            await asyncio.sleep(0.05)
        return out


async def chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 700) -> str:
    resp = await _chat.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()
