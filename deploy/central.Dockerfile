# syntax=docker/dockerfile:1
#
# Imagem do CENTRAL: backend FastAPI + painel React compilado, num processo só. Para desenvolvimento e validação
# isolados (deploy/compose.yaml); a produção continua no Windows (scripts/deploy.ps1). Procedimento, limites e o
# que ainda não foi provado: docs/operacao.md, seção "Contêineres".
#
# O que NÃO está aqui, de propósito:
#   - Android SDK, emulador, adb, Appium: os aparelhos vivem fora (Windows e worker). Emulador em contêiner continua
#     sendo VM e exige KVM — é outra frente, não esta imagem.
#   - config/, .env, data/, apks/: configuração e segredo entram em TEMPO DE EXECUÇÃO (bind mount somente leitura e
#     env_file do compose); dado vive em volume nomeado. O .dockerignore da raiz é lista de permissão.
#
# Versões: as mesmas do CI (.github/workflows/ci.yml: NODE_VERSION, PYTHON_VERSION) — o teste
# scripts/tests/test_conteineres.py confere. Tags conferidas no Docker Hub em 26/09/2026; fixar por digest
# (`@sha256:`) depois do primeiro pull real.
ARG NODE_VERSION=22.12.0
ARG PYTHON_VERSION=3.13.15

# ------------------------------------------------------------------ 1) painel: npm ci + build (tsc + vite)
FROM node:${NODE_VERSION}-bookworm-slim AS painel
WORKDIR /src/frontend
# Manifesto primeiro: a camada do `npm ci` só refaz quando as dependências mudam, não a cada edição de tela.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------ 2) central: Python slim, sem compilador
FROM python:${PYTHON_VERSION}-slim-bookworm AS central
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# Usuário sem privilégio: o backend só precisa escrever em data/ e apks/ (volumes) e na /tmp.
RUN groupadd --system --gid 10001 farm \
 && useradd --system --uid 10001 --gid farm --home-dir /app --no-create-home --shell /usr/sbin/nologin farm
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
# `--only-binary=:all:`: a imagem slim não tem compilador, e um pacote sem wheel tem de falhar AQUI, com o nome dele,
# e não virar uma compilação que ninguém pediu. O lock (requirements.txt) é o mesmo do Windows e do CI.
RUN pip install --only-binary=:all: -r backend/requirements.txt
COPY backend/app backend/app
COPY backend/migrations backend/migrations
# `main.create_app` serve `<raiz>/frontend/dist`; a raiz é /app (config.PROJECT_ROOT = pai de backend/).
COPY --from=painel /src/frontend/dist frontend/dist
COPY deploy/saude.py deploy/iniciar.py deploy/
# Cópia consistente do SQLite (API de backup online) para o procedimento de backup no contêiner.
COPY scripts/sqlite-copia.py scripts/sqlite-copia.py
# O commit em `/api/health` sai de `<raiz>/.git/HEAD` (app/version.py, sem chamar git). Sem `.git` na imagem, ele
# seria `null` para sempre e o "conferir o deploy" (commit + migração) perderia metade. HEAD destacado é um arquivo
# com o sha — exatamente o formato que `commit_em_execucao` lê. Vazio = `null`, que é a verdade ("não sei").
ARG FARM_COMMIT=""
RUN if [ -n "$FARM_COMMIT" ]; then \
      printf '%s' "$FARM_COMMIT" | grep -Eq '^[0-9a-f]{40}$' \
        || { echo "FARM_COMMIT precisa ser o sha completo (40 hex): git rev-parse HEAD" >&2; exit 1; }; \
      mkdir -p .git && printf '%s\n' "$FARM_COMMIT" > .git/HEAD; \
    fi \
 && mkdir -p data apks config \
 && chown farm:farm data apks
# Volume nomeado novo herda dono e conteúdo do diretório da imagem no primeiro uso: por isso o chown acima.
# POC_CONFIG absoluto e sem arquivo na imagem: `deploy/iniciar.py` recusa subir se o bind mount faltar.
# CONTAINER_LISTEN_HOST: só a imagem define; ver `app.main.endereco_de_escuta`.
ENV POC_CONFIG=/app/config/config.yaml \
    CONTAINER_LISTEN_HOST=0.0.0.0
USER farm
EXPOSE 8000
# Vivo = quem responde É a Farm (identidade), com qualquer `status`. Ver deploy/saude.py.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD ["python", "/app/deploy/saude.py"]
CMD ["python", "/app/deploy/iniciar.py"]
