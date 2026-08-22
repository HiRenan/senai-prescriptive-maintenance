# Infraestrutura local

O ambiente local contém somente PostgreSQL 17 com pgvector. Ele não representa
credenciais nem dados de produção e não inclui serviços de aplicação, filas,
armazenamento de objetos ou recursos de nuvem.

## Pré-requisitos

- Docker Desktop com contêineres Linux;
- Docker Compose v2;
- porta `127.0.0.1:5432` livre, ou outra porta local livre escolhida
  explicitamente.

Execute os comandos abaixo a partir da raiz do repositório. A interface
recomendada inicia o banco em modo detached, aguarda o healthcheck e o remove
preservando os dados:

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

## Imagem reproduzível

O serviço usa pgvector `0.8.6` sobre PostgreSQL `17`, pela referência:

```text
pgvector/pgvector:0.8.6-pg17@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f
```

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

`services-down` executa `docker compose down`: remove o contêiner e a rede deste
projeto, mas preserva o volume nomeado. O próximo `services-up` recria o serviço
sobre os dados existentes:

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
