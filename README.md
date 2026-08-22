# Manutenção Prescritiva — Desafio SENAI

Este monorepo concentra o backend, a fronteira de integração web, a
infraestrutura, a documentação, os dados, os experimentos e os scripts do
projeto. A organização mantém uma única fonte de verdade e prepara um monólito
modular sem antecipar funcionalidades de etapas posteriores.

## Estado atual

Esta fundação oferece somente:

- um workspace Python gerenciado por uv, com um único backend instalável em
  `apps/api`;
- execução de tarefas com Poe the Poet;
- um workspace Node gerenciado por Corepack e pnpm, com `apps/web` reservado
  como fronteira de integração;
- versões de runtime, locks e regras de texto consistentes entre Windows e
  Linux;
- separação explícita entre código-fonte, materiais fornecidos, fixtures
  sintéticas e artefatos gerados.

Não há endpoints, regras de negócio, processamento de dados, similaridade, RAG,
persistência, serviços locais, interface web ou infraestrutura executável nesta
etapa.

## Runtimes e instalação

- Python `>=3.13,<3.14`, com a linha `3.13` registrada em `.python-version`;
- Node.js `>=22,<23`, com a linha `22` registrada em `.node-version`;
- pnpm `10.15.1`, fixado em `packageManager` para uso via Corepack.

Na raiz do repositório, execute:

```powershell
uv sync --frozen
uv run poe check-api-import
corepack pnpm install --frozen-lockfile
```

O repositório mantém um único `uv.lock` e um único `pnpm-lock.yaml`, ambos na
raiz.

## Estrutura

```text
.
├── apps/
│   ├── api/          # pacote Python instalável do backend
│   └── web/          # fronteira de workspace, sem implementação de UI
├── data/             # manifesto, fixtures sintéticas e dados locais ignorados
├── docs/             # convenções e documentação do projeto
├── experiments/      # estudos isolados do código de produção
├── infra/            # fronteira reservada para infraestrutura
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
