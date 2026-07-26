# Deploy da API de Consulta de CCTs

API que o app Base44 (DocFlow) consome para buscar cláusulas de CCT no pgvector.
Endpoints: `GET /health` (sem chave) e `POST /consultar-cct` (exige header `X-API-Key`).

Servidor alvo: **72.60.61.18** (o mesmo onde roda o pgvector na porta 5435).

---

## Opção A — systemd (recomendada, sem Docker)

### 1. No SEU computador: enviar o bundle para o servidor
No diretório do projeto (onde está esta pasta `deploy/cct-api`):

```bash
scp -r deploy/cct-api root@72.60.61.18:/opt/cct-api
```

### 2. No servidor (ssh root@72.60.61.18):

```bash
apt-get update && apt-get install -y python3-venv
cd /opt/cct-api
python3 -m venv venv
./venv/bin/pip install -r requirements-api.txt

# criar o .env a partir do exemplo e preencher
cp .env.example .env
python3 -c "import secrets; print('CCT_API_KEY =', secrets.token_urlsafe(32))"   # anote e cole no .env
nano .env
#   - VECTOR_DATABASE_URL  -> postgres://postgres:SENHA@127.0.0.1:5435/postgres
#   - OPENAI_API_KEY       -> sua chave OpenAI
#   - CCT_API_KEY          -> a chave gerada acima (a MESMA vai no Base44)

# instalar e subir o serviço
cp cct-api.service /etc/systemd/system/cct-api.service
systemctl daemon-reload
systemctl enable --now cct-api
systemctl status cct-api --no-pager
```

### 3. Testar (no servidor):
```bash
curl -s localhost:8090/health
# -> {"status":"ok","documentos":7,"clausulas":543}

curl -s -X POST localhost:8090/consultar-cct \
  -H "Content-Type: application/json" \
  -H "X-API-Key: SUA_CCT_API_KEY" \
  -d '{"pergunta":"piso salarial do vigilante","categoria":"vigilancia","data_fato":"2024-06-01","limite":2}'
```

### 4. Abrir a porta 8090 (se usar firewall)
```bash
ufw allow 8090/tcp    # se o ufw estiver ativo
```

---

## Opção C — Coolify (recomendada — Git + HTTPS automático)

Este diretório já é um projeto pronto pro Coolify: `Dockerfile` na raiz, porta 8090.
Os segredos NÃO vão pro Git — entram na aba **Environment Variables** do Coolify.

1. **Subir este diretório como um repositório GitHub** (ver comandos no fim do arquivo).
2. No Coolify: **New Resource → Application → Public/Private Repository**, aponte pro repo.
   - **Build Pack:** Dockerfile
   - **Port / Ports Exposes:** `8090`
   - **Health Check Path:** `/health`
3. Aba **Environment Variables** (equivalem ao `.env`):
   ```
   VECTOR_DATABASE_URL = postgres://postgres:SENHA@HOST_DO_PGVECTOR:5435/postgres
   OPENAI_API_KEY      = sk-...
   CCT_API_KEY         = <chave forte; a mesma vai no secret do Base44>
   CCT_ALLOWED_ORIGINS = *
   EMBEDDING_MODEL     = text-embedding-3-small
   ```
   > Se o Coolify estiver no MESMO servidor do pgvector, use o IP interno/host do
   > container do Postgres em `VECTOR_DATABASE_URL` (não `localhost` dentro do container).
4. **Domains:** informe um subdomínio (ex.: `cct-api.seudominio.com.br`) — o Coolify
   emite o certificado HTTPS sozinho. É essa URL que o DocFlow vai chamar.
5. **Deploy.** Depois, teste: `https://cct-api.seudominio.com.br/health`.

---

## Opção B — Docker

```bash
cd /opt/cct-api
docker build -t cct-api .
docker run -d --name cct-api --restart unless-stopped \
  --env-file .env -p 8090:8090 cct-api
```

---

## Como o Base44 (DocFlow) chama

**Recomendado: chamar de uma BACKEND FUNCTION do Base44** (não do frontend). Assim a
`CCT_API_KEY` fica guardada nos *secrets* do Base44 e nunca aparece no navegador.

Exemplo de chamada:
```
POST http://72.60.61.18:8090/consultar-cct
Headers: X-API-Key: <CCT_API_KEY guardada no secret do Base44>
Body: { "pergunta": "...", "categoria": "vigilancia", "data_fato": "2024-06-01" }
```

---

## HTTPS (endurecimento — fazer depois)

Para chamar do frontend do Base44 (ou só por segurança), coloque um proxy com HTTPS
(ex.: Caddy) na frente. Requer um domínio apontando para 72.60.61.18:

```
# /etc/caddy/Caddyfile
cct.seudominio.com.br {
    reverse_proxy 127.0.0.1:8090
}
```
Caddy emite o certificado Let's Encrypt automaticamente. Depois, feche a 8090 externa
(`ufw deny 8090/tcp`) e deixe o tráfego entrar só pela 443 via Caddy.

## Segurança do banco (recomendado)
Hoje o pgvector (5435) está aberto na internet. Com a API rodando no mesmo servidor,
restrinja o 5435 para aceitar só conexões locais (a API usa 127.0.0.1:5435).
