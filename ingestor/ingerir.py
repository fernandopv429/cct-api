"""
Ingesta os PDFs no banco vivo da cct-api.

Desenho do pipeline, e por quê:

- Os embeddings são gerados AQUI (REST da OpenAI, stdlib pura) e a carga vai
  num único arquivo SQL com COPY, aplicado numa única transação. Ou entra
  tudo, ou não entra nada — uma carga pela metade deixaria documento sem
  chunk, invisível na busca e difícil de notar.

- O vínculo documento->chunk usa o arquivo_hash como chave natural dentro da
  transação (INSERT ... RETURNING id JOIN staging). Nada de id inventado do
  lado de cá.

- Documento cujo hash já existe no banco é pulado: é assim que reexecutar o
  script é seguro, e é assim que os 37 que já estão lá não duplicam.

- Pasta CONTROLADOR DE ACESSO fica fora (decisão de enquadramento pendente).

Uso:
  python3 ingerir.py          -> gera /tmp/cct_carga.sql.gz e mostra o resumo
  (a aplicação no banco é um passo separado, de propósito)
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extrair import CATEGORIA_POR_PASTA, processar  # noqa: E402

# CCT_RAIZ aponta para outra árvore com a MESMA estrutura (CATEGORIA/UF/*.pdf).
# Existe por causa do lote de OCR: os PDFs ocerizados ficam em cct/_ocr e são
# ingeridos numa segunda passada, sem misturar com os originais.
RAIZ = Path(os.environ.get("CCT_RAIZ") or Path(__file__).parent.parent)
SAIDA = Path("/tmp/claude-1000/-home-fernandofav--rea-de-trabalho-servidor-vps/42ab80fe-00f8-4ccc-ad6c-9a312af0a521/scratchpad/cct_carga.sql.gz")
MODELO = "text-embedding-3-small"  # 1536 dims — o mesmo dos 3.060 chunks atuais

# Hashes já no banco (um por linha), gerado antes por psql.
HASHES_EXISTENTES = Path("/tmp/claude-1000/-home-fernandofav--rea-de-trabalho-servidor-vps/42ab80fe-00f8-4ccc-ad6c-9a312af0a521/scratchpad/hashes_existentes.txt")

# O modelo aceita 8191 tokens. Em português isso dá uns 28k caracteres; o corte
# em 24k deixa folga. O texto COMPLETO vai pro banco — só o vetor é calculado
# sobre o começo, e cláusula com 24k+ caracteres é anexo, não regra.
CORTE_EMBEDDING = 24_000


def embeddings(textos: list[str], chave: str) -> list[list[float]]:
    corpo = json.dumps({
        "model": MODELO,
        "input": [t[:CORTE_EMBEDDING] for t in textos],
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=corpo,
        headers={"Authorization": f"Bearer {chave}", "Content-Type": "application/json"},
    )
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                dados = json.load(r)
            return [d["embedding"] for d in dados["data"]]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and tentativa < 3:
                time.sleep(2 ** (tentativa + 1))
                continue
            raise
    raise RuntimeError("inalcançável")


def q(v) -> str:
    """Escapa para literal SQL. None vira NULL."""
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def copy_campo(v) -> str:
    r"""Escapa para linha de COPY (tab-separado). None vira \N."""
    if v is None:
        return r"\N"
    return (
        str(v)
        .replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def main() -> None:
    chave = os.environ.get("OPENAI_API_KEY", "")
    if not chave:
        sys.exit("defina OPENAI_API_KEY no ambiente (sem gravar em arquivo)")

    existentes = set()
    if HASHES_EXISTENTES.exists():
        existentes = {l.strip() for l in HASHES_EXISTENTES.read_text().splitlines() if l.strip()}
    print(f"hashes já no banco: {len(existentes)}")

    pdfs = sorted(
        p for p in RAIZ.rglob("*.pdf")
        if "ingestor" not in p.parts and p.relative_to(RAIZ).parts[0] in CATEGORIA_POR_PASTA
    )
    print(f"PDFs nas pastas aceitas: {len(pdfs)}")

    docs = []
    vistos: set[str] = set()
    pulados_banco = 0
    for pdf in pdfs:
        d = processar(pdf, RAIZ)
        if d.arquivo_hash in existentes:
            pulados_banco += 1
            continue
        if d.arquivo_hash in vistos or d.descartado:
            continue
        vistos.add(d.arquivo_hash)
        docs.append(d)

    total_clausulas = sum(len(d.clausulas) for d in docs)
    print(f"entram: {len(docs)} documentos, {total_clausulas} cláusulas")
    print(f"pulados por já existirem no banco: {pulados_banco}")

    # ---- embeddings, em lotes ----
    LOTE = 96
    vetores: dict[tuple[str, int], list[float]] = {}
    fila: list[tuple[str, int, str]] = [
        (d.arquivo_hash, c.ordem, c.conteudo) for d in docs for c in d.clausulas
    ]
    feito = 0
    for i in range(0, len(fila), LOTE):
        parte = fila[i : i + LOTE]
        embs = embeddings([t for _, _, t in parte], chave)
        for (h, ordem, _), e in zip(parte, embs):
            vetores[(h, ordem)] = e
        feito += len(parte)
        print(f"  embeddings: {feito}/{len(fila)}", flush=True)

    # ---- SQL único, transacional ----
    with gzip.open(SAIDA, "wt", encoding="utf-8") as f:
        f.write("begin;\n")
        f.write("""
create temp table _docs (
  arquivo_hash text primary key, categoria text, uf char(2), titulo text,
  registro text, vi date, vf date, lab text, pat text, abrang text
) on commit drop;
""")
        f.write("copy _docs from stdin;\n")
        for d in docs:
            f.write("\t".join(copy_campo(x) for x in [
                d.arquivo_hash, d.categoria, d.estado, (d.titulo or "")[:300],
                d.registro_mte, d.vigencia_inicio, d.vigencia_fim,
                (d.sindicato_laboral or "")[:300] or None,
                (d.sindicato_patronal or "")[:300] or None,
                "estadual",
            ]) + "\n")
        f.write("\\.\n")

        f.write("""
create temp table _chunks (
  arquivo_hash text, ordem int, ref text, titulo text, conteudo text, emb text
) on commit drop;
""")
        f.write("copy _chunks from stdin;\n")
        for d in docs:
            for c in d.clausulas:
                emb = vetores[(d.arquivo_hash, c.ordem)]
                f.write("\t".join(copy_campo(x) for x in [
                    d.arquivo_hash, c.ordem, c.ref, c.titulo, c.conteudo,
                    "[" + ",".join(f"{v:.7f}" for v in emb) + "]",
                ]) + "\n")
        f.write("\\.\n")

        f.write("""
with ins as (
  insert into cct_documentos
      (categoria, uf, titulo, numero_registro_mte, vigencia_inicio, vigencia_fim,
       sindicato_laboral, sindicato_patronal, abrangencia, arquivo_hash)
  select categoria, uf, titulo, registro, vi, vf, lab, pat, abrang, arquivo_hash
  from _docs
  returning id, arquivo_hash
)
insert into cct_chunks (documento_id, ordem, clausula_ref, clausula_titulo, conteudo, embedding)
select ins.id, c.ordem, c.ref, c.titulo, c.conteudo, c.emb::vector
from _chunks c join ins on ins.arquivo_hash = c.arquivo_hash;

commit;
""")

    tam = SAIDA.stat().st_size / 1e6
    print(f"\ncarga gerada: {SAIDA} ({tam:.1f} MB)")
    print("aplicar com: gunzip -c carga | psql (num passo separado, revisado)")


if __name__ == "__main__":
    main()
