# API de Consulta de CCTs — Documentação

Busca semântica em **Convenções Coletivas de Trabalho (CCT)**. Você faz uma pergunta em
linguagem natural e a API devolve as cláusulas mais relevantes, filtrando pela convenção
**vigente na data dos fatos**, pela **categoria** e pela **região** do caso.

| | |
|---|---|
| **Base URL** | `https://ccts.nexusdevhub.com` |
| **Autenticação** | Header `X-API-Key` |
| **Docs interativa** | `https://ccts.nexusdevhub.com/docs` (Swagger) |
| **Conteúdo** | 17 CCTs · 1.499 cláusulas · 2019–2027 |

---

## 1. Autenticação

Toda chamada a `/consultar-cct` exige o header:

```
X-API-Key: SUA_CHAVE
```

Sem a chave (ou com chave errada) → **HTTP 401**.

> ⚠️ Guarde a chave como **secret** (Environment Variables do Base44/Coolify).
> Nunca no código do frontend — quem abrir o navegador consegue lê-la.

---

## 2. `POST /consultar-cct`

Endpoint principal. Combina três filtros + busca semântica.

### Parâmetros (corpo JSON)

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `pergunta` | string | ✅ | Dúvida em linguagem natural. Ex.: `"adicional noturno do vigilante"` |
| `categoria` | string | ❌ | `vigilancia` · `terceirizados` · `asseio_conservacao`. Omitir = busca em todas |
| `data_fato` | `AAAA-MM-DD` | ❌ | Devolve só a CCT **vigente nessa data**. Omitir = todas as vigências |
| `municipio` | string | ❌ | Município do caso. Ex.: `"São Paulo"`, `"Guarulhos"` |
| `similaridade_minima` | número 0–1 | ❌ | Corte de relevância. Default `0.40`. `0` desliga |
| `limite` | inteiro 1–20 | ❌ | Quantidade de trechos. Default `6` |

### Como funciona o filtro de região (`municipio`)

Algumas categorias têm **convenção diferente por cidade/região** (é o caso do
asseio/conservação). O filtro trata isso assim:

- CCT **estadual** (vale para todo o estado) → **sempre aparece**, qualquer município.
- CCT **municipal/regional** → só aparece **se cobrir** o município informado.

Comportamento hoje na base:

| Categoria | Abrangência | Cobertura |
|---|---|---|
| `vigilancia` (SEEVISSP × SESVESP) | estadual | todo o estado de SP |
| `terceirizados` (SINDEEPRES Geral) | estadual | todo o estado de SP |
| `asseio_conservacao` (SIEMACO-SP × SEAC-SP) | **municipal** | **São Paulo (capital)** |

Exemplo prático:
```
asseio_conservacao + municipio="São Paulo"  →  encontra a CCT
asseio_conservacao + municipio="Guarulhos"  →  total: 0  (não temos a regional de Guarulhos)
vigilancia         + municipio="Guarulhos"  →  encontra (é estadual, cobre tudo)
```

> Um `total: 0` com município preenchido é **informação útil**, não erro: significa que
> não há CCT cadastrada para aquela região — sinal de que é preciso carregar a
> convenção regional correspondente.

### Exemplo — cURL

```bash
curl -X POST https://ccts.nexusdevhub.com/consultar-cct \
  -H "Content-Type: application/json" \
  -H "X-API-Key: SUA_CHAVE" \
  -d '{
    "pergunta": "adicional noturno do vigilante",
    "categoria": "vigilancia",
    "data_fato": "2024-06-01",
    "municipio": "São Paulo",
    "limite": 3
  }'
```

### Exemplo — JavaScript (backend function do Base44)

```js
const resp = await fetch("https://ccts.nexusdevhub.com/consultar-cct", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": process.env.CCT_API_KEY,   // secret do Base44
  },
  body: JSON.stringify({
    pergunta:  "adicional noturno do vigilante",
    categoria: "vigilancia",       // opcional
    data_fato: "2024-06-01",       // opcional — data dos fatos do processo
    municipio: "São Paulo",        // opcional — cidade da prestação de serviço
    limite:    3,                   // opcional
  }),
});
const data = await resp.json();

if (!data.total) {
  // Nada relevante encontrado — NÃO redigir sem fundamento
  console.warn(data.aviso);
} else {
  data.resultados.forEach(r => {
    console.log(r.clausula_ref, r.clausula_titulo, r.similaridade);
  });
}
```

### Resposta (HTTP 200)

```json
{
  "pergunta": "adicional noturno do vigilante",
  "categoria": "vigilancia",
  "data_fato": "2024-06-01",
  "municipio": "São Paulo",
  "similaridade_minima": 0.4,
  "total": 1,
  "resultados": [
    {
      "titulo": "CCT Vigilância/Segurança Privada SP 2024/2025 (SEEVISSP x SESVESP)",
      "categoria": "vigilancia",
      "ano_base": 2024,
      "vigencia_inicio": "2024-01-01",
      "vigencia_fim": "2025-12-31",
      "sindicato_laboral": "SEEVISSP",
      "sindicato_patronal": "SESVESP",
      "base_territorial": "São Paulo e região",
      "abrangencia": "estadual",
      "municipios": null,
      "fonte_url": "https://seevissp.org.br/wp-content/uploads/2024/01/CCT-2024_MTE.pdf",
      "clausula_ref": "CLÁUSULA DÉCIMA TERCEIRA",
      "clausula_titulo": "ADICIONAL NOTURNO",
      "conteudo": "CLÁUSULA DÉCIMA TERCEIRA - ADICIONAL NOTURNO\nÉ mantido na categoria, o adicional de 20% ...",
      "similaridade": 0.59
    }
  ]
}
```

Quando **nenhum** trecho atinge a relevância mínima, a resposta inclui:

```json
{ "total": 0, "resultados": [], "aviso": "Nenhum trecho atingiu a relevância mínima..." }
```

### Campos de cada resultado

| Campo | Descrição |
|---|---|
| `titulo` | Nome da CCT |
| `categoria` | `vigilancia` / `terceirizados` / `asseio_conservacao` |
| `ano_base` | Ano-base da convenção |
| `vigencia_inicio` / `vigencia_fim` | Período de vigência |
| `sindicato_laboral` / `sindicato_patronal` | Sindicatos signatários |
| `base_territorial` | Descrição da abrangência territorial |
| `abrangencia` | `estadual` ou `municipal` |
| `municipios` | Municípios cobertos (se municipal); `null` = estadual |
| `fonte_url` | Link do **PDF oficial** — use para citar a fonte |
| `clausula_ref` | Ex.: `"CLÁUSULA DÉCIMA TERCEIRA"` |
| `clausula_titulo` | Ex.: `"ADICIONAL NOTURNO"` |
| `conteudo` | **Texto da cláusula** — é o que a IA usa para redigir |
| `similaridade` | Relevância 0–1 (veja abaixo) |

---

## 3. Como interpretar `similaridade`

A busca vetorial **sempre devolve o trecho mais próximo**, mesmo quando o assunto não
existe naquela convenção. O campo `similaridade` é o termômetro:

| Faixa | Leitura |
|---|---|
| **acima de 0.60** | Forte — pode usar com confiança |
| **0.45 a 0.60** | Confira o texto antes de usar |
| **abaixo de 0.45** | Indício fraco — pode ser assunto inexistente na CCT |

**Por que não existe um corte automático perfeito:** em calibração com 15 perguntas
legítimas e 6 de ruído, as faixas se sobrepõem — perguntas válidas pontuaram de 0.42 a
0.70, enquanto ruído chegou a 0.47. Perguntas curtas ("cesta básica") contra cláusulas
longas naturalmente pontuam mais baixo, mesmo sendo pertinentes.

Por isso o corte padrão é conservador (`0.40`): remove resultados claramente fora de
contexto sem barrar consultas reais. **A conferência final é de quem consome** — por
isso o `similaridade` vem em cada resultado.

---

## 4. `GET /health`

Checagem de saúde. **Não** exige chave.

```bash
curl https://ccts.nexusdevhub.com/health
```
```json
{"status": "ok", "documentos": 17, "clausulas": 1499}
```

---

## 5. Erros

| HTTP | Quando |
|---|---|
| `401` | `X-API-Key` ausente ou inválida |
| `422` | Corpo inválido (ex.: `data_fato` fora do formato `AAAA-MM-DD`) |
| `500` | `CCT_API_KEY` não configurada no servidor |

---

## 6. Conteúdo indexado

| Categoria | Sindicatos | Anos cobertos |
|---|---|---|
| `vigilancia` | SEEVISSP × SESVESP | 2019 · 2020 · 2021 · 2022 · 2023 · 2024 · 2025 · 2026 · 2027 |
| `terceirizados` | SINDEEPRES (Geral) | 2020 · 2021 · 2022 · 2023 · 2024 · 2025 · 2026 |
| `asseio_conservacao` | SIEMACO-SP × SEAC-SP | 2019 · 2020 · 2021 · 2022 · 2023 · 2024 · 2025 · 2026 · 2027 |

**Lacunas conhecidas:**
- Terceirizados **2019** (não indexado).
- Asseio fora da capital (Guarulhos, Osasco, Sorocaba…) — o SEAC-SP publica uma CCT por
  região; só a de São Paulo capital está carregada.
- Três PDFs escaneados aguardam OCR (limpeza urbana SIEMACO, aditivos SEEVISSP 2023 e
  SIEMACO 2025).

---

## 7. Boas práticas de uso

1. **Sempre informe `data_fato`** com a data dos fatos do processo. É o que garante a
   convenção vigente à época — o erro mais comum é usar a CCT do ano errado.
2. **Informe `municipio`** quando a categoria tiver convenção regional (asseio).
3. **Cite pela `fonte_url`** — todo resultado traz o link do PDF oficial registrado no MTE.
4. **Trate `total: 0` como resposta válida** — significa "não há previsão", e a IA não
   deve redigir sem fundamento.
5. **Combine os filtros**: `categoria` + `data_fato` + `municipio` é a consulta mais precisa.

---

## 8. Exemplos de perguntas que funcionam bem

| Pergunta | Similaridade típica |
|---|---|
| `"adicional de periculosidade"` | 0.69 |
| `"equipamento de proteção individual EPI"` | 0.70 |
| `"estabilidade provisória"` | 0.67 |
| `"intervalo para refeição e descanso"` | 0.66 |
| `"seguro de vida em grupo"` | 0.65 |
| `"adicional noturno"` | 0.65 |
| `"contribuição mensal sindical"` | 0.65 |
| `"uniformes e instrumentos de trabalho"` | 0.62 |
| `"insalubridade"` | 0.59 |
| `"piso salarial da categoria"` | 0.54 |

> Dica: perguntas com 2–5 palavras que reproduzam o vocabulário da convenção
> ("adicional noturno", "auxílio refeição") recuperam melhor que frases longas.
