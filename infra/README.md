# Infraestrutura local

O ambiente local usa uma única topologia Compose com PostgreSQL 17/pgvector,
API e a fronteira web sem interface. Ele não representa credenciais nem dados
de produção e não inclui filas, armazenamento de objetos ou recursos de nuvem.

## Pré-requisitos

- Docker Desktop com contêineres Linux;
- Docker Compose v2;
- portas `127.0.0.1:5432`, `127.0.0.1:8000` e `127.0.0.1:3000` livres, ou
  outras portas locais livres escolhidas explicitamente.

Execute os comandos abaixo a partir da raiz do repositório. O fluxo existente
de banco isolado continua iniciando somente o PostgreSQL em modo detached,
aguarda o healthcheck e o remove preservando os dados:

```powershell
uv run --frozen poe services-up
uv run --frozen poe smoke --with-services
uv run --frozen poe services-down
```

O smoke carrega `.env.example` explicitamente e não cria nem exige `.env`. Para
executar manualmente um fluxo da aplicação que instancia `Settings()` sem
informar um arquivo, copie deliberadamente o exemplo versionado para `.env`:

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

O arquivo `.env` é ignorado pelo Git. Os valores do exemplo são fictícios e
exclusivos para desenvolvimento local; nunca os reutilize em outro ambiente.

Como diagnóstico estático avançado, valide a configuração resolvida sem criar
recursos:

```powershell
docker compose config
```

O Compose não depende do arquivo `.env` para interpolar seus valores locais, por
isso esse comando também funciona antes da cópia e usa a porta host `5432` como
padrão. A cópia é necessária para carregar explicitamente as configurações da
aplicação pelo exemplo.

Se a porta host `5432` já estiver ocupada, preserve o serviço existente e defina
uma porta livre antes de executar `services-up`. No PowerShell:

```powershell
$env:PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT = "55432"
uv run --frozen poe services-up
```

No Ubuntu:

```bash
export PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT=55432
uv run --frozen poe services-up
```

`PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT` altera somente a porta no host. A
porta do contêiner permanece `5432`, e a publicação continua restrita a
`127.0.0.1`. Ao conectar a aplicação manualmente, ajuste também
`PRESCRIPTIVE_MAINTENANCE_DATABASE_URL` para a mesma porta host. O comando
`uv run --frozen poe smoke --with-services` é idêntico nos dois sistemas.

## Topologia de aplicação

O ciclo completo constrói as duas imagens, inicia os três serviços, aguarda os
healthchecks, compara o OpenAPI servido com o snapshot v1 e encerra a topologia
sem remover o volume:

```powershell
uv run --frozen poe applications-audit
uv run --frozen poe applications-build
uv run --frozen poe applications-up
uv run --frozen poe smoke --with-services --with-applications
uv run --frozen poe services-down
```

O Compose publica somente em loopback. Para escolher outras portas host no
PowerShell antes de `applications-up` e do smoke:

```powershell
$env:PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT = "55432"
$env:PRESCRIPTIVE_MAINTENANCE_API_HOST_PORT = "58000"
$env:PRESCRIPTIVE_MAINTENANCE_WEB_HOST_PORT = "53000"
```

No Ubuntu:

```bash
export PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT=55432
export PRESCRIPTIVE_MAINTENANCE_API_HOST_PORT=58000
export PRESCRIPTIVE_MAINTENANCE_WEB_HOST_PORT=53000
```

As portas internas permanecem `5432`, `8000` e `3000`. A API recebe uma URL
PostgreSQL exclusivamente local e fictícia apontada ao serviço `postgres`; a
readiness abre uma conexão curta e executa `SELECT 1`, sem reter a conexão. A
liveness da API continua restrita ao processo. A web expõe somente sua liveness
e continua sem UI.

API e web usam raiz somente leitura, `/tmp` efêmero, todas as capabilities
removidas e `no-new-privileges`. O healthcheck da API usa readiness e, no perfil
local do Compose, exige PostgreSQL; a web condiciona sua inicialização à API
healthy.

## Bases e insumos fixados

O serviço usa pgvector `0.8.6` sobre PostgreSQL `17`, pela referência:

```text
pgvector/pgvector:0.8.6-pg17@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f
```

Os Dockerfiles multi-stage das aplicações usam os índices OCI fixados abaixo:

```text
python:3.13-slim-bookworm@sha256:00faa2debb87529f9f0764e9491d8ba400a3678976616c3bd7cb193745ac20d1
ghcr.io/astral-sh/uv:0.9.4@sha256:c4089b0085cf4d38e38d5cdaa5e57752c1878a6f41f2e3a3a234dc5f23942cb4
node:22-alpine3.22@sha256:cd7807368cf24826297cbad5dca1a44972ccfd770647db52a8c7589eb4599ac8
```

A API instala somente o grupo de produção por `uv sync --frozen --no-dev`; a
web confirma o workspace por `pnpm install --frozen-lockfile --prod` e não tem
dependências de aplicação. Cada Dockerfile possui uma allowlist específica; a
API inclui também o README exigido pelos metadados do pacote. A tarefa
`applications-audit` exporta o contexto filtrado que o BuildKit recebeu e
executa auditorias dentro dos builders. `.env`, Git, dados, materiais originais,
caches, testes, snapshots OpenAPI, READMEs desnecessários e ferramentas de
desenvolvimento não chegam ao builder nem às imagens finais.

Neste fluxo, reprodutibilidade significa bases imutáveis por digest, ferramentas
e dependências fixadas, locks congelados e contexto controlado. O image ID local
pode variar em razão de metadados e proveniência produzidos pelo BuildKit; não há
promessa de identidade bit a bit, publicação em registry ou attestation nesta
tarefa.

O digest é o índice OCI multi-arquitetura da tag. O índice oferece
`linux/amd64`, usado pelo Docker Desktop validado, e `linux/arm64`. Verifique a
correspondência entre tag, digest e plataformas diretamente no registry:

```powershell
docker buildx imagetools inspect pgvector/pgvector:0.8.6-pg17
docker pull pgvector/pgvector:0.8.6-pg17@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f
```

## Inicialização e inspeção

Inicie a referência fixada e aguarde o banco ficar saudável:

```powershell
uv run --frozen poe services-up
docker compose ps
```

O banco e o usuário se chamam `prescriptive_maintenance`. A porta é publicada
somente em `127.0.0.1`, usando a porta host `5432` por padrão, e os dados ficam
no volume nomeado `postgres_data`. Na criação inicial do volume, o script
versionado e montado como somente leitura executa
`CREATE EXTENSION IF NOT EXISTS vector`.

Consulte a versão instalada e faça uma operação vetorial mínima:

```powershell
docker compose exec postgres psql --username prescriptive_maintenance --dbname prescriptive_maintenance --command "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
docker compose exec postgres psql --username prescriptive_maintenance --dbname prescriptive_maintenance --command "SELECT '[1,2,3]'::vector <-> '[3,2,1]'::vector AS distance;"
```

## Parada, persistência e limpeza

`stop` interrompe o contêiner sem remover o contêiner nem o volume. Um novo
`up --wait` reutiliza os mesmos dados:

```powershell
docker compose stop
docker compose up --wait
```

`services-down` executa `docker compose down`: remove os contêineres e a rede
deste projeto, mas preserva o volume nomeado. O próximo `services-up` recria o
banco sobre os dados existentes; `applications-up` recria a topologia completa:

```powershell
uv run --frozen poe services-down
uv run --frozen poe services-up
```

> **Operação destrutiva para dados locais:** o comando abaixo remove também o
> volume nomeado e todos os dados locais deste projeto. Use-o somente quando a
> perda desses dados for intencional.

```powershell
docker compose down --volumes --remove-orphans
```

Esses comandos atuam somente sobre o projeto Compose executado nesta raiz. Não
use limpeza global do Docker para administrar este ambiente.
