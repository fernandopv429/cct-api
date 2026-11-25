"""
Passa por todos os PDFs e mostra o que seria ingerido, sem gravar nada e sem
gastar embedding.

Existe porque errar a extração de metadados é silencioso: a convenção entra,
a busca funciona, e meses depois alguém cita uma cláusula com vigência errada.
Conferir antes custa minutos; descobrir depois custa um cálculo refeito.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from extrair import processar  # noqa: E402

RAIZ = Path(__file__).parent.parent
HOJE = date.today()


def main() -> None:
    pdfs = sorted(p for p in RAIZ.rglob("*.pdf") if "ingestor" not in p.parts)
    print(f"{len(pdfs)} PDFs encontrados\n")

    ok: list = []
    descartados: list = []
    sem_vigencia: list = []
    sem_registro: list = []
    hashes: dict[str, Path] = {}
    duplicados: list[tuple[Path, Path]] = []

    for pdf in pdfs:
        d = processar(pdf, RAIZ)

        if d.arquivo_hash in hashes:
            duplicados.append((pdf, hashes[d.arquivo_hash]))
            continue
        hashes[d.arquivo_hash] = pdf

        if d.descartado:
            descartados.append(d)
            continue

        ok.append(d)
        if not d.vigencia_inicio:
            sem_vigencia.append(d)
        if not d.registro_mte:
            sem_registro.append(d)

    print("=" * 72)
    print(f"ENTRARIAM: {len(ok)} documentos, {sum(len(d.clausulas) for d in ok)} cláusulas")
    print("=" * 72)

    por_cat = Counter((d.categoria, d.estado) for d in ok)
    for (cat, uf), n in sorted(por_cat.items()):
        print(f"  {cat:20} {uf}  {n:3} documentos")

    vigentes = [d for d in ok if d.vigencia_fim and d.vigencia_fim >= HOJE]
    print(f"\n  vigentes hoje: {len(vigentes)} de {len(ok)}")
    for d in sorted(vigentes, key=lambda x: (x.categoria, x.estado)):
        print(f"    {d.categoria:20} {d.estado}  até {d.vigencia_fim}  {d.caminho.name[:44]}")

    if duplicados:
        print(f"\n--- DUPLICADOS por conteúdo idêntico: {len(duplicados)} ---")
        for novo, antigo in duplicados[:10]:
            print(f"    {novo.name[:44]}\n      = {antigo.name[:44]}")

    if sem_vigencia:
        print(f"\n--- SEM VIGÊNCIA detectada: {len(sem_vigencia)} ---")
        print("    (entram, mas não dá para filtrar por data: risco de citar convenção vencida)")
        for d in sem_vigencia[:12]:
            print(f"    {d.categoria:20} {d.estado}  {d.caminho.name[:48]}")

    if sem_registro:
        print(f"\n--- sem registro MTE: {len(sem_registro)} ---")

    if descartados:
        print(f"\n--- DESCARTADOS: {len(descartados)} ---")
        motivos = Counter(d.descartado.split(":")[0] for d in descartados)
        for motivo, n in motivos.most_common():
            print(f"    {n:3}  {motivo}")
        escaneados = [d for d in descartados if "escaneado" in (d.descartado or "")]
        if escaneados:
            print("\n    prováveis PDFs escaneados (precisariam de OCR):")
            for d in escaneados[:10]:
                print(f"      {d.categoria:20} {d.estado}  {d.caminho.name[:48]}")


if __name__ == "__main__":
    main()
