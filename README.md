# cct-api — API de Consulta de CCTs

Busca semântica em **Convenções Coletivas de Trabalho (CCT)** indexadas em Postgres + pgvector.
Dada uma pergunta em linguagem natural, retorna as cláusulas mais relevantes, filtrando por
**categoria** e pela CCT **vigente na data informada**.

- **Base URL (produção):** `https://ccts.nexusdevhub.com`
- **Docs interativa (Swagger):** `https://ccts.nexusdevhub.com/docs` · ReDoc: `/redoc`
- **Stack:** FastAPI · asyncpg · pgvector · embeddings OpenAI (`text-embedding-3-small`, 1536d)

```
pergunta ──► embedding (OpenAI) ──► busca híbrida no pgvector ──► cláusulas + metadados
                                    (categoria + vigência + similaridade)
```

---

## Autenticação

Todas as chamadas a `/consultar-cct` exigem o header:

```
X-API-Key: SUA_CHAVE
```

Sem o header (ou com chave errada) → **401**. Guarde a chave como *secret* (ex.: nas
Environment Variables do Base44/Coolify) — nunca no código do frontend.

---

## `POST /consultar-cct`

Busca híbrida (categoria + vigência + similaridade semântica).

### Corpo (JSON)

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `pergunta` | string | ✅ | Dúvida em linguagem natural. Ex.: "adicional noturno do vigilante" |
| `categoria` | string | ❌ | `vigilancia` · `terceirizados` · `asseio_conservacao`. Omitir = todas |
| `data_fato` | string `AAAA-MM-DD` | ❌ | Retorna só a CCT **vigente nessa data**. Omitir = todas as vigências |
| `municipio` | string | ❌ | Município do caso (ex.: "Guarulhos"). CCTs **estaduais** sempre entram; **municipais/regionais** só entram se cobrirem esse município. Omitir = não filtra por região |
| `limite` | inteiro (1–20) | ❌ | Quantidade de trechos. Default `6` |

> **Por que `municipio` importa:** algumas categorias têm CCT **diferente por região**
> (ex.: asseio/conservação do SEAC-SP tem convenção própria por cidade/região). Passe o
> município do caso para não pegar a regra de outra localidade.

### Exemplo — cURL

```bash
curl -X POST https://ccts.nexusdevhub.com/consultar-cct \
  -H "Content-Type: application/json" \
  -H "X-API-Key: SUA_CHAVE" \
  -d '{
    "pergunta": "adicional noturno do vigilante",
    "categoria": "vigilancia",
    "data_fato": "2024-06-01",
    "limite": 3
  }'
```

### Exemplo — JavaScript (backend function do Base44)

```js
const resp = await fetch("https://ccts.nexusdevhub.com/consultar-cct", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": process.env.CCT_API_KEY,   // secret no Base44
  },
  body: JSON.stringify({
    pergunta: "adicional noturno do vigilante",
    categoria: "vigilancia",      // opcional
    data_fato: "2024-06-01",      // opcional
    municipio: "São Paulo",       // opcional (filtra CCTs regionais)
    limite: 3,                     // opcional
  }),
});
const data = await resp.json();
// data.resultados -> array de cláusulas (ver abaixo)
```

### Resposta (200)

```json
{
  "pergunta": "adicional noturno do vigilante",
  "categoria": "vigilancia",
  "data_fato": "2024-06-01",
  "total": 2,
  "resultados": [
    {
      "titulo": "CCT Vigilância/Segurança Privada SP 2024/2025 (SEEVISSP x SESVESP)",
      "categoria": "vigilancia",
      "ano_base": 2024,
      "vigencia_inicio": "2024-01-01",
      "vigencia_fim": "2025-12-31",
      "sindicato_laboral": "SEEVISSP",
      "sindicato_patronal": "SESVESP",
      "fonte_url": "https://seevissp.org.br/wp-content/uploads/2024/01/CCT-2024_MTE.pdf",
      "clausula_ref": "CLÁUSULA DÉCIMA TERCEIRA",
      "clausula_titulo": "ADICIONAL NOTURNO",
      "conteudo": "CLÁUSULA DÉCIMA TERCEIRA - ADICIONAL NOTURNO\nÉ mantido na categoria, o adicional de 20% ...",
      "similaridade": 0.59
    }
  ]
}
```

### Campos de cada resultado

| Campo | Descrição |
|---|---|
| `titulo` | Nome da CCT |
| `categoria` | `vigilancia` / `terceirizados` / `asseio_conservacao` |
| `ano_base` | Ano-base da convenção |
| `vigencia_inicio` / `vigencia_fim` | Período de vigência (datas) |
| `sindicato_laboral` / `sindicato_patronal` | Sindicatos signatários |
| `base_territorial` | Descrição da abrangência territorial |
| `abrangencia` | `estadual` (todo o estado) ou `municipal` (municípios específicos) |
| `municipios` | Lista de municípios cobertos (quando `municipal`); `null` = estadual |
| `fonte_url` | Link do PDF oficial de origem |
| `clausula_ref` | Ex.: "CLÁUSULA DÉCIMA TERCEIRA" |
| `clausula_titulo` | Ex.: "ADICIONAL NOTURNO" |
| `conteudo` | Texto da cláusula (para exibir/usar na redação) |
| `similaridade` | 0–1; quanto maior, mais relevante |

---

## `GET /health`

Checagem de saúde, **sem** chave. Útil para monitoramento.

```bash
curl https://ccts.nexusdevhub.com/health
# {"status":"ok","documentos":7,"clausulas":543}
```

---

## Erros

| HTTP | Quando |
|---|---|
| `401` | `X-API-Key` ausente ou inválida |
| `422` | Corpo inválido (ex.: `data_fato` fora do formato `AAAA-MM-DD`, `pergunta` vazia) |
| `500` | `CCT_API_KEY` não configurada no servidor |

---

## Categorias e conteúdo indexado

| Categoria | Sindicatos | CCTs disponíveis |
|---|---|---|
| `vigilancia` | SEEVISSP × SESVESP | 2024/2025, Aditivo 2025, 2026/2027 |
| `terceirizados` | SINDEEPRES (Geral) | 2024, 2025/2026, Aditivo 2026 |
| `asseio_conservacao` | SIEMACO-SP × SEAC-SP | 2026/2027 |

> **Dica jurídica:** para pegar a CCT correta de um caso, passe `data_fato` = data dos
> fatos do processo — a API devolve a convenção vigente naquela data.

---

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `VECTOR_DATABASE_URL` | ✅ | `postgres://usuario:senha@host:5435/postgres` (banco pgvector) |
| `OPENAI_API_KEY` | ✅ | Chave OpenAI (embedding da pergunta) |
| `CCT_API_KEY` | ✅ | Chave da API (header `X-API-Key`) |
| `CCT_ALLOWED_ORIGINS` | ❌ | Origens CORS (default `*`) |
| `EMBEDDING_MODEL` | ❌ | Default `text-embedding-3-small` (1536d) |

## Deploy

Veja [DEPLOY.md](DEPLOY.md) — instruções para **Coolify** (recomendado, HTTPS automático),
Docker e systemd.
