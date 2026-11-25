"""
Extrai metadados e cláusulas de um PDF de convenção coletiva.

Trabalha sobre o texto do `pdftotext -layout`, que preserva a disposição das
colunas. Sem o `-layout`, tabelas dentro da convenção viram uma fileira de
números sem dono.

O que este módulo NÃO faz: adivinhar. Campo que não aparece no documento volta
None, e o chamador decide. Preencher vigência por estimativa é o tipo de erro
que só aparece quando alguém cita uma convenção vencida como se valesse.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

# "CLÁUSULA PRIMEIRA - VIGÊNCIA E DATA-BASE" e variações com número e acento.
RE_CLAUSULA = re.compile(
    r"^\s*(CL[ÁA]USULA\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ\d\.\-º°\s]{2,60}?)\s*[-–—:]\s*(.{3,120})$",
    re.MULTILINE | re.IGNORECASE,
)

RE_REGISTRO = re.compile(r"\b([A-Z]{2}\s*\d{6}\s*/\s*\d{4})\b")
# [ºo°] porque os três aparecem: ordinal, a letra "o" e o símbolo de grau. São
# indistinguíveis na tela e qualquer um deles sozinho já derrubava a detecção.
# \s+ em vez de " " porque a frase se parte entre linhas em PDF de duas colunas.
RE_VIGENCIA = re.compile(
    r"(\d{1,2})\s*[ºo°]?\s*de\s+([a-zç]+)\s+de\s+(\d{4})\s+(?:a|at[ée])\s+"
    r"(\d{1,2})\s*[ºo°]?\s*de\s+([a-zç]+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
RE_VIGENCIA_NUM = re.compile(
    r"(\d{2})/(\d{2})/(\d{4})\s*(?:a|at[ée])\s*(\d{2})/(\d{2})/(\d{4})"
)


def sem_acento(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


@dataclass
class Clausula:
    ordem: int
    ref: str | None
    titulo: str | None
    conteudo: str


@dataclass
class Documento:
    caminho: Path
    categoria: str
    estado: str
    arquivo_hash: str
    texto: str
    titulo: str | None = None
    registro_mte: str | None = None
    vigencia_inicio: date | None = None
    vigencia_fim: date | None = None
    sindicato_laboral: str | None = None
    sindicato_patronal: str | None = None
    clausulas: list[Clausula] = field(default_factory=list)
    descartado: str | None = None


def ler_texto(pdf: Path) -> str:
    r = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True, timeout=120,
    )
    # errors="replace" em vez de estourar: PDF com byte inválido no meio ainda
    # tem 99% do conteúdo aproveitável, e perder a convenção inteira por causa
    # de um caractere seria pior.
    return r.stdout.decode("utf-8", errors="replace")


def hash_arquivo(pdf: Path) -> str:
    h = hashlib.sha256()
    with pdf.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def achar_vigencia(texto: str) -> tuple[date | None, date | None]:
    # Recorta a partir da cláusula de vigência quando ela existir, em vez de
    # cortar por número de caracteres: em documento com índice ou capa longa
    # ela passa dos 6.000 e ficava de fora. O limite ainda existe depois do
    # ponto de corte porque mais adiante o texto cita datas de OUTRAS
    # convenções, e pegar uma delas daria vigência errada com cara de certa.
    marca = re.search(r"VIG[ÊE]NCIA\s+E\s+DATA[\s-]*BASE", texto, re.IGNORECASE)
    inicio = marca.start() if marca else 0
    cabeca = texto[inicio:inicio + 6000]

    # Junta linhas: "de janeiro de\n2026" precisa virar uma frase só.
    cabeca = re.sub(r"\s*\n\s*", " ", cabeca)

    m = RE_VIGENCIA.search(cabeca)
    if m:
        d1, mes1, a1, d2, mes2, a2 = m.groups()
        mi = MESES.get(sem_acento(mes1.lower()))
        mf = MESES.get(sem_acento(mes2.lower()))
        if mi and mf:
            try:
                return date(int(a1), mi, int(d1)), date(int(a2), mf, int(d2))
            except ValueError:
                pass

    m = RE_VIGENCIA_NUM.search(cabeca)
    if m:
        d1, m1, a1, d2, m2, a2 = (int(x) for x in m.groups())
        try:
            return date(a1, m1, d1), date(a2, m2, d2)
        except ValueError:
            pass

    return None, None


def achar_sindicatos(texto: str) -> tuple[str | None, str | None]:
    """
    O padrão do Mediador: o sindicato laboral vem primeiro, o patronal depois,
    cada um seguido de CNPJ. Quando o layout foge disso, devolve None em vez
    de chutar.
    """
    cabeca = texto[:4000]
    nomes = re.findall(
        r"^\s*(SINDICATO[^\n,]{10,120}|FEDERA[ÇC][ÃA]O[^\n,]{10,120})",
        cabeca, re.MULTILINE | re.IGNORECASE,
    )
    nomes = [re.sub(r"\s+", " ", n).strip() for n in nomes]
    laboral = nomes[0] if nomes else None
    patronal = nomes[1] if len(nomes) > 1 else None
    return laboral, patronal


def partir_clausulas(texto: str) -> list[Clausula]:
    """
    Uma cláusula por chunk, não um tamanho fixo.

    É assim que os 18 documentos atuais estão, e é o que faz a busca devolver
    a regra inteira. Cortar por número de caracteres parte a cláusula no meio
    e devolve metade de uma condição — pior que não achar.
    """
    marcas = list(RE_CLAUSULA.finditer(texto))
    if not marcas:
        return []

    clausulas: list[Clausula] = []

    preambulo = texto[: marcas[0].start()].strip()
    if len(preambulo) > 80:
        clausulas.append(Clausula(0, None, "Preâmbulo", preambulo))

    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        corpo = texto[m.start():fim].strip()
        if len(corpo) < 40:
            continue
        clausulas.append(
            Clausula(
                ordem=len(clausulas),
                ref=re.sub(r"\s+", " ", m.group(1)).strip(" -–—:"),
                titulo=re.sub(r"\s+", " ", m.group(2)).strip(),
                conteudo=corpo,
            )
        )
    return clausulas


# Pasta -> categoria. A grafia é a do banco VIVO (klx3bc42, porta 5435), que é
# a que o app de petições envia — "porteiros"/"asseio" eram do protótipo e
# devolveriam zero resultado na API.
CATEGORIA_POR_PASTA = {
    "VIGILANTE": "vigilancia",
    "PORTEIRO": "terceirizados",
    "AUXILIAR DE LIMPEZA": "asseio_conservacao",
    "CARRO FORTE": "carro_forte",
    "BOMBEIRO CIVIL": "bombeiro_civil",
    # CONTROLADOR DE ACESSO fica fora até a decisão de enquadramento sindical:
    # as CCTs da pasta são SIEMACO/SEAC (família do asseio), mas no app a
    # função cai em terceirizados (SINDEEPRES). Classificar errado aqui faria
    # a peça citar convenção de outro sindicato.
}

# Nome que indica documento que não é convenção nem aditivo registrado.
# "ata" entra na lista: ata de reunião/errata não é instrumento coletivo.
NAO_E_CCT = re.compile(r"liminar|minuta|tabela|\bata\b|ata-", re.IGNORECASE)


def processar(pdf: Path, raiz: Path) -> Documento:
    rel = pdf.relative_to(raiz)
    pasta, estado = rel.parts[0], rel.parts[1]
    categoria = CATEGORIA_POR_PASTA.get(pasta, sem_acento(pasta).lower().replace(" ", "_"))

    doc = Documento(
        caminho=pdf,
        categoria=categoria,
        estado=estado,
        arquivo_hash=hash_arquivo(pdf),
        texto="",
    )

    if NAO_E_CCT.search(pdf.name):
        doc.descartado = "não é CCT nem aditivo (liminar, minuta ou tabela)"
        return doc

    doc.texto = ler_texto(pdf)
    if len(doc.texto.strip()) < 500:
        # PDF escaneado sem camada de texto. Precisaria de OCR, que é outra
        # ferramenta e outro custo — melhor sinalizar que ingerir vazio.
        doc.descartado = f"texto insuficiente ({len(doc.texto.strip())} caracteres): provável PDF escaneado"
        return doc

    reg = RE_REGISTRO.search(doc.texto[:6000])
    doc.registro_mte = re.sub(r"\s+", "", reg.group(1)) if reg else None
    doc.vigencia_inicio, doc.vigencia_fim = achar_vigencia(doc.texto)
    doc.sindicato_laboral, doc.sindicato_patronal = achar_sindicatos(doc.texto)

    primeira = next((l.strip() for l in doc.texto.splitlines() if len(l.strip()) > 15), None)
    doc.titulo = primeira[:200] if primeira else pdf.stem

    doc.clausulas = partir_clausulas(doc.texto)
    if not doc.clausulas:
        doc.descartado = "nenhuma cláusula reconhecida no texto"

    return doc
