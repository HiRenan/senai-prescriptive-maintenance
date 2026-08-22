# Manutenção Prescritiva — Desafio SENAI

Este monorepo concentra o backend, a fronteira de integração web, a
infraestrutura, a documentação, os dados, os experimentos e os scripts do
projeto. A organização mantém uma única fonte de verdade e prepara um monólito
modular sem antecipar funcionalidades de etapas posteriores.

## Estado atual

Esta fundação oferece somente:

- um workspace Python gerenciado por uv, com um único backend instalável em
  `apps/api`;
- uma aplicação FastAPI mínima, inicializável pelo Uvicorn, com liveness em
  `GET /health/live`;
- configuração explícita e tipada para ambiente e URL PostgreSQL;
- um serviço local reproduzível de PostgreSQL 17 com pgvector;
- uma interface Poe para bootstrap, formatação, lint, tipagem estrita, testes,
  hooks, smoke e controle dos serviços locais;
- um workspace Node gerenciado por Corepack e pnpm, com `apps/web` reservado
  como fronteira de integração;
- versões de runtime, locks e regras de texto consistentes entre Windows e
  Linux;
- separação explícita entre código-fonte, materiais fornecidos, fixtures
  sintéticas e artefatos gerados.

Além da liveness e da configuração local, não há regras de negócio,
processamento de dados, similaridade, RAG, persistência integrada à aplicação ou
interface web nesta etapa.

## Pré-requisitos e bootstrap

- Python `>=3.13,<3.14`, com a linha `3.13` registrada em `.python-version`;
- Node.js `>=22,<23`, com a linha `22` registrada em `.node-version`;
- pnpm `10.15.1`, fixado em `packageManager` para uso via Corepack;
- uv, Git, Corepack e, para os comandos de infraestrutura, Docker Desktop ou
  Docker Engine com Compose v2.

Na raiz do repositório, o bootstrap canônico é:

```powershell
uv run --frozen poe setup
```

`setup` sincroniza todos os pacotes Python pelo lock, instala o workspace pnpm
com lock congelado e instala os hooks pre-commit. A tarefa é idempotente e pode
ser executada novamente sem alterar os locks.

O repositório mantém um único `uv.lock` e um único `pnpm-lock.yaml`, ambos na
raiz.

## Comandos locais

Todas as tarefas são executadas da raiz no formato
`uv run --frozen poe <tarefa>`.

| Tarefa | Finalidade |
| --- | --- |
| `setup` | Sincroniza dependências e instala hooks locais. |
| `format` | Aplica correções seguras e formata com Ruff. |
| `format-check` | Verifica a formatação sem escrever. |
| `lint` | Executa Ruff sem correções automáticas. |
| `typecheck` | Executa Pyright em modo estrito. |
| `test` | Executa Pytest com cobertura. |
| `check` | Executa `format-check`, `lint`, `typecheck` e `test`, nessa ordem. |
| `hooks` | Executa todos os hooks pre-commit. |
| `services-up` | Inicia o PostgreSQL e aguarda o healthcheck. |
| `services-down` | Remove contêiner e rede, preservando o volume. |
| `smoke` | Valida runtimes, configuração, Compose e liveness real. |

`format` é a única tarefa Poe de qualidade que reescreve código. `check` é
fail-fast, não inicia Docker e não altera arquivos rastreados.

O smoke padrão não exige banco nem arquivo `.env`:

```powershell
uv run --frozen poe smoke
```

Para a validação opcional do PostgreSQL e do pgvector, inicie antes o serviço e
use a mesma flag no Windows e no Ubuntu:

```powershell
uv run --frozen poe services-up
uv run --frozen poe smoke --with-services
uv run --frozen poe services-down
```

Se `127.0.0.1:5432` estiver ocupada, defina
`PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT` com uma porta host livre antes de
`services-up`. A porta interna permanece `5432`; veja os exemplos por sistema em
[`infra/README.md`](infra/README.md). `services-down` preserva o volume e os
dados locais por padrão.

A configuração tipada do backend está descrita em
[`apps/api/README.md`](apps/api/README.md), e o PostgreSQL local está documentado
em [`infra/README.md`](infra/README.md).

Mensagens de commit são escritas em inglês no formato Conventional Commits
`<type>(<scope opcional>): <description>`. A governança completa de contribuição
pertence à SEN-17.

## Estrutura

```text
.
├── apps/
│   ├── api/          # pacote Python instalável do backend
│   └── web/          # fronteira de workspace, sem implementação de UI
├── data/             # manifesto, fixtures sintéticas e dados locais ignorados
├── docs/             # convenções e documentação do projeto
├── experiments/      # estudos isolados do código de produção
├── infra/            # PostgreSQL e pgvector para desenvolvimento local
└── scripts/          # fronteira reservada para automações
```

Os identificadores técnicos e o código são escritos em inglês. A documentação
e as explicações destinadas ao projeto são escritas em português. As convenções
completas estão em [`docs/README.md`](docs/README.md).

## Materiais originais e dados

Os oito materiais originais fornecidos para o desafio permanecem locais,
ignorados pelo Git e fora do histórico. O arquivo
[`data/source-manifest.json`](data/source-manifest.json) registra somente nomes,
tamanhos e hashes SHA-256 para conferência de integridade; ele não redistribui o
conteúdo recebido.

As instruções de preparação estão em [`data/README.md`](data/README.md). As
fixtures públicas em `data/fixtures/` são pequenas, sintéticas e independentes
dos materiais originais. Dados fornecidos devem ficar em `data/raw/original/`,
enquanto saídas intermediárias, processadas e geradas usam os diretórios
ignorados definidos no `.gitignore`.

## Acesso e direitos

O repositório é público para permitir a leitura, o clone e a avaliação pela
banca. Essa disponibilidade não concede autorização para copiar, modificar,
redistribuir ou reutilizar o conteúdo.

Não há arquivo `LICENSE`. Todos os direitos permanecem reservados a Renan
Mocelin.
