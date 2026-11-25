import os
import logging
import unicodedata
from datetime import date
from typing import Optional, List, Dict, Any

import asyncpg
from pgvector.asyncpg import register_vector

from services.embeddings import EMBEDDING_DIM

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


def normalizar_municipio(nome: str) -> str:
    """Normaliza nome de município para comparação: minúsculo, sem acento, sem espaços nas pontas.
    Ex.: 'São Paulo' -> 'sao paulo'."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", nome) if not unicodedata.combining(c)
    )
    return sem_acento.strip().lower()

# Schema também é criado aqui (idempotente) para que o banco de vetor
# possa ser recriado do zero sem depender de um passo manual.
SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS cct_documentos (
    id                  BIGSERIAL PRIMARY KEY,
    categoria           TEXT NOT NULL,
    sindicato_laboral   TEXT,
    sindicato_patronal  TEXT,
    base_territorial    TEXT,
    ano_base            INT,
    vigencia_inicio     DATE,
    vigencia_fim        DATE,
    numero_registro_mte TEXT,
    titulo              TEXT,
    fonte_url           TEXT,
    arquivo_hash        TEXT UNIQUE,
    abrangencia         TEXT,           -- 'estadual' | 'municipal'
    municipios          TEXT[],         -- municípios cobertos (normalizados); NULL = estadual/todos
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Migração idempotente para bancos criados antes destas colunas:
ALTER TABLE cct_documentos ADD COLUMN IF NOT EXISTS abrangencia TEXT;
ALTER TABLE cct_documentos ADD COLUMN IF NOT EXISTS municipios TEXT[];

CREATE TABLE IF NOT EXISTS cct_chunks (
    id              BIGSERIAL PRIMARY KEY,
    documento_id    BIGINT NOT NULL REFERENCES cct_documentos(id) ON DELETE CASCADE,
    ordem           INT NOT NULL,
    clausula_ref    TEXT,
    clausula_titulo TEXT,
    conteudo        TEXT NOT NULL,
    embedding       vector({EMBEDDING_DIM}),
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cct_chunks_documento ON cct_chunks(documento_id);
CREATE INDEX IF NOT EXISTS idx_cct_docs_categoria   ON cct_documentos(categoria);
CREATE INDEX IF NOT EXISTS idx_cct_docs_vigencia    ON cct_documentos(vigencia_inicio, vigencia_fim);
CREATE INDEX IF NOT EXISTS idx_cct_docs_municipios  ON cct_documentos USING gin (municipios);
CREATE INDEX IF NOT EXISTS idx_cct_chunks_embedding ON cct_chunks USING hnsw (embedding vector_cosine_ops);
"""


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Registra o codec do tipo vector em cada conexão do pool."""
    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = os.environ["VECTOR_DATABASE_URL"]
        _pool = await asyncpg.create_pool(url, init=_init_conn)
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA)
        logger.info("[CCTStore] Pool do Postgres de vetor inicializado e schema garantido")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def documento_existe(arquivo_hash: str) -> Optional[int]:
    """Retorna o id do documento se o hash já foi ingerido, senão None (idempotência)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM cct_documentos WHERE arquivo_hash = $1", arquivo_hash
        )


async def inserir_documento(
    *,
    categoria: str,
    sindicato_laboral: Optional[str],
    sindicato_patronal: Optional[str],
    base_territorial: Optional[str],
    ano_base: Optional[int],
    vigencia_inicio: Optional[date],
    vigencia_fim: Optional[date],
    numero_registro_mte: Optional[str],
    titulo: Optional[str],
    fonte_url: Optional[str],
    arquivo_hash: str,
    abrangencia: Optional[str] = None,
    municipios: Optional[List[str]] = None,
) -> int:
    # Normaliza a lista de municípios (minúsculo, sem acento). NULL/vazia = estadual/todos.
    municipios_norm = (
        [normalizar_municipio(m) for m in municipios if m and m.strip()]
        if municipios else None
    ) or None
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO cct_documentos
                (categoria, sindicato_laboral, sindicato_patronal, base_territorial,
                 ano_base, vigencia_inicio, vigencia_fim, numero_registro_mte,
                 titulo, fonte_url, arquivo_hash, abrangencia, municipios)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING id
            """,
            categoria, sindicato_laboral, sindicato_patronal, base_territorial,
            ano_base, vigencia_inicio, vigencia_fim, numero_registro_mte,
            titulo, fonte_url, arquivo_hash, abrangencia, municipios_norm,
        )


async def inserir_chunks(documento_id: int, chunks: List[Dict[str, Any]]) -> int:
    """chunks: lista de {ordem, clausula_ref, clausula_titulo, conteudo, embedding}."""
    pool = await get_pool()
    rows = [
        (
            documento_id,
            c["ordem"],
            c.get("clausula_ref"),
            c.get("clausula_titulo"),
            c["conteudo"],
            c["embedding"],
        )
        for c in chunks
    ]
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO cct_chunks
                (documento_id, ordem, clausula_ref, clausula_titulo, conteudo, embedding)
            VALUES ($1,$2,$3,$4,$5,$6)
            """,
            rows,
        )
    return len(rows)


# Corte mínimo de similaridade. Busca vetorial sempre devolve "o mais próximo",
# mesmo quando o assunto não existe na CCT — abaixo deste valor o trecho é ruído
# e não deve chegar à IA que redige.
SIMILARIDADE_MINIMA_PADRAO = 0.40


async def buscar(
    *,
    embedding: List[float],
    categoria: Optional[str] = None,
    data_fato: Optional[date] = None,
    municipio: Optional[str] = None,
    uf: Optional[str] = None,
    limite: int = 8,
    similaridade_minima: float = SIMILARIDADE_MINIMA_PADRAO,
) -> List[Dict[str, Any]]:
    """Busca híbrida: filtra por categoria/vigência/município e ordena por similaridade de cosseno.

    Município: CCTs estaduais (municipios NULL) sempre entram; as regionais/municipais
    só entram se cobrirem o município informado.

    UF: documento sem uf (NULL) sempre entra — é o legado anterior à coluna.
    Com uf informada, convenção de OUTRO estado fica de fora. Sem esse filtro,
    uma consulta de SP receberia cláusula de SC com piso diferente: número
    real, do lugar errado — o pior erro possível para quem calcula verba.

    similaridade_minima: descarta trechos abaixo do corte (0 desliga o filtro).
    """
    condicoes = []
    params: List[Any] = [embedding]
    if categoria:
        params.append(categoria)
        condicoes.append(f"d.categoria = ${len(params)}")
    if data_fato:
        params.append(data_fato)
        idx = len(params)
        # vigência aberta (NULL) não filtra fora
        condicoes.append(
            f"(d.vigencia_inicio IS NULL OR d.vigencia_inicio <= ${idx}) "
            f"AND (d.vigencia_fim IS NULL OR d.vigencia_fim >= ${idx})"
        )
    if uf and len(uf.strip()) == 2:
        params.append(uf.strip().upper())
        condicoes.append(f"(d.uf IS NULL OR d.uf = ${len(params)})")
    if municipio:
        params.append(normalizar_municipio(municipio))
        idx = len(params)
        # estadual (municipios NULL) sempre entra; municipal só se cobrir o município
        condicoes.append(f"(d.municipios IS NULL OR d.municipios @> ARRAY[${idx}]::text[])")
    if similaridade_minima and similaridade_minima > 0:
        params.append(float(similaridade_minima))
        condicoes.append(f"(1 - (c.embedding <=> $1)) >= ${len(params)}")
    where = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
    params.append(limite)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT d.titulo, d.categoria, d.uf, d.ano_base, d.vigencia_inicio, d.vigencia_fim,
                   d.sindicato_laboral, d.sindicato_patronal, d.base_territorial,
                   d.abrangencia, d.municipios, d.fonte_url,
                   c.clausula_ref, c.clausula_titulo, c.conteudo,
                   1 - (c.embedding <=> $1) AS similaridade
            FROM cct_chunks c
            JOIN cct_documentos d ON d.id = c.documento_id
            {where}
            ORDER BY c.embedding <=> $1
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(r) for r in rows]
