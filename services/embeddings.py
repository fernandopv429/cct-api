"""Camada de embeddings — provedor plugável (Voyage AI ou OpenAI).

Selecione com a env EMBEDDING_PROVIDER: "openai" (padrão) ou "voyage".
O padrão é openai porque a coluna do banco está em 1536 dimensões — trocar de
provedor exige re-embeddar tudo (scripts/reembeddar.py).
A dimensão do vetor DEVE casar com a coluna `embedding vector(N)` no banco.
"""
import os
import asyncio
import logging
import random
from typing import List

import httpx

logger = logging.getLogger(__name__)

PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()

# Dimensões por modelo. Série 4 e 3.5 usam 1024 por padrão; OpenAI small usa 1536.
_DIMS = {
    "voyage-4": 1024,
    "voyage-4-large": 1024,
    "voyage-4-lite": 1024,
    "voyage-3.5": 1024,
    "voyage-3.5-lite": 1024,
    "voyage-law-2": 1024,
    "voyage-finance-2": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

if PROVIDER == "voyage":
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "voyage-4")
else:
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", _DIMS.get(EMBEDDING_MODEL, 1024)))

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
# Tamanho do lote. Contas Voyage SEM forma de pagamento têm limite de 3 RPM / 10K TPM,
# então lotes grandes estouram o teto de tokens por minuto. Ajustável por env.
_LOTE_MAX = int(os.getenv("EMBEDDING_LOTE", "96"))
# Tentativas em caso de 429 (rate limit) com espera exponencial.
_MAX_TENTATIVAS = int(os.getenv("EMBEDDING_MAX_TENTATIVAS", "6"))

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI  # import tardio: só se o provedor for openai
        _openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


async def _voyage_lote(client: httpx.AsyncClient, textos: List[str], input_type: str) -> List[List[float]]:
    payload = {
        "model": EMBEDDING_MODEL,
        "input": textos,
        # input_type melhora a recuperação: 'document' ao indexar, 'query' ao consultar.
        "input_type": input_type,
    }
    if EMBEDDING_MODEL.startswith(("voyage-4", "voyage-3.5")):
        payload["output_dimension"] = EMBEDDING_DIM
    headers = {"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"}
    for tentativa in range(_MAX_TENTATIVAS):
        resp = await client.post(VOYAGE_URL, headers=headers, json=payload)
        if resp.status_code != 429:
            break
        # Rate limit: espera exponencial com jitter e tenta de novo.
        espera = min(60.0, 2.0 ** tentativa) + random.uniform(0, 1.5)
        logger.warning(
            f"[Embeddings] Voyage 429 (tentativa {tentativa + 1}/{_MAX_TENTATIVAS}); "
            f"aguardando {espera:.1f}s"
        )
        await asyncio.sleep(espera)
    if resp.status_code >= 400:
        logger.error(f"[Embeddings] Voyage {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    dados = resp.json()["data"]
    # A API pode devolver fora de ordem; ordena pelo índice.
    dados.sort(key=lambda d: d["index"])
    return [d["embedding"] for d in dados]


async def embed_texts(texts: List[str], input_type: str = "document") -> List[List[float]]:
    """Gera embeddings para uma lista de textos, preservando a ordem.

    input_type: 'document' para indexação, 'query' para a pergunta da busca.
    """
    if not texts:
        return []

    if PROVIDER == "openai":
        resp = await _get_openai().embeddings.create(model=EMBEDDING_MODEL, input=texts)
        return [item.embedding for item in resp.data]

    vetores: List[List[float]] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for i in range(0, len(texts), _LOTE_MAX):
            vetores.extend(await _voyage_lote(client, texts[i:i + _LOTE_MAX], input_type))
    return vetores


async def embed_text(text: str) -> List[float]:
    """Gera o embedding de um único texto (a pergunta na consulta)."""
    result = await embed_texts([text], input_type="query")
    return result[0]
