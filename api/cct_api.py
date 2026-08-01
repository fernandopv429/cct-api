"""API de consulta às CCTs indexadas no pgvector.

Endpoint que o app Base44 (DocFlow) consome para obter as cláusulas relevantes.
Reaproveita a mesma lógica testada em services/ (embeddings + busca híbrida).

Rodar:
    CCT_API_KEY=... VECTOR_DATABASE_URL=... OPENAI_API_KEY=... \
        uvicorn api.cct_api:app --host 0.0.0.0 --port 8090
"""
import os
import logging
from datetime import date
from typing import Optional, List, Any, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from services.embeddings import embed_text
from services import cct_store

logger = logging.getLogger(__name__)

CCT_API_KEY = os.environ.get("CCT_API_KEY")
# Origens permitidas (CORS). Ex.: "https://meuapp.base44.app,https://outro.com"
# Default "*" apenas para facilitar o teste; em produção restrinja à origem do app.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("CCT_ALLOWED_ORIGINS", "*").split(",")]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Aquece o pool e garante o schema na subida.
    await cct_store.get_pool()
    logger.info("[API] Pool do pgvector pronto")
    yield
    await cct_store.close_pool()


app = FastAPI(title="API de Consulta de CCTs", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


async def require_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
    """Exige o header X-API-Key igual à CCT_API_KEY configurada no servidor."""
    if not CCT_API_KEY:
        raise HTTPException(status_code=500, detail="CCT_API_KEY não configurada no servidor")
    if x_api_key != CCT_API_KEY:
        raise HTTPException(status_code=401, detail="API key ausente ou inválida")


class ConsultaRequest(BaseModel):
    pergunta: str = Field(..., min_length=2, description="Pergunta em linguagem natural")
    categoria: Optional[str] = Field(
        None, description="vigilancia | terceirizados | asseio_conservacao"
    )
    data_fato: Optional[str] = Field(
        None, description="AAAA-MM-DD — filtra a CCT vigente nessa data"
    )
    municipio: Optional[str] = Field(
        None, description="Município do caso. CCTs estaduais sempre entram; municipais só se cobrirem"
    )
    limite: int = Field(6, ge=1, le=20)


@app.get("/health")
async def health() -> Dict[str, Any]:
    """Checagem de saúde (sem API key) — útil para monitoramento/uptime."""
    pool = await cct_store.get_pool()
    async with pool.acquire() as conn:
        ndoc = await conn.fetchval("SELECT count(*) FROM cct_documentos")
        nch = await conn.fetchval("SELECT count(*) FROM cct_chunks")
    return {"status": "ok", "documentos": ndoc, "clausulas": nch}


@app.post("/consultar-cct", dependencies=[Depends(require_key)])
async def consultar(req: ConsultaRequest) -> Dict[str, Any]:
    """Busca híbrida (categoria + vigência + similaridade) e devolve os trechos."""
    df: Optional[date] = None
    if req.data_fato:
        try:
            df = date.fromisoformat(req.data_fato)
        except ValueError:
            raise HTTPException(status_code=422, detail="data_fato deve ser AAAA-MM-DD")

    vetor = await embed_text(req.pergunta)
    resultados: List[Dict[str, Any]] = await cct_store.buscar(
        embedding=vetor, categoria=req.categoria, data_fato=df,
        municipio=req.municipio, limite=req.limite,
    )
    return {
        "pergunta": req.pergunta,
        "categoria": req.categoria,
        "data_fato": req.data_fato,
        "municipio": req.municipio,
        "total": len(resultados),
        "resultados": resultados,
    }
