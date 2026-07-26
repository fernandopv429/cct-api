import os
import logging
from typing import List

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# text-embedding-3-small -> 1536 dimensões (deve casar com vector(1536) no banco).
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = 1536

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """Gera embeddings para uma lista de textos, preservando a ordem."""
    if not texts:
        return []
    resp = await _get_client().embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in resp.data]


async def embed_text(text: str) -> List[float]:
    """Gera o embedding de um único texto (ex.: a pergunta na consulta)."""
    result = await embed_texts([text])
    return result[0]
