# Manutenção Prescritiva — Desafio SENAI

[![CI](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/ci.yml?query=branch%3Adevelop)
[![Pull Request Policy](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/pull-request-policy.yml/badge.svg?branch=develop)](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/pull-request-policy.yml?query=branch%3Adevelop)
[![Security](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/security.yml/badge.svg?branch=develop)](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/security.yml?query=branch%3Adevelop)

## Problema

Ativos industriais perdem disponibilidade quando sinais de degradação são
percebidos tarde ou analisados sem contexto. A manutenção prescritiva busca
transformar medições, histórico e conhecimento técnico em recomendações
justificáveis para apoiar a decisão de manutenção: o que observar, qual ação
avaliar e por que ela merece prioridade.

Este repositório organiza a fundação técnica de uma solução para esse problema.
O foco atual é oferecer uma base local reproduzível, testável e segura para que
as capacidades de domínio sejam acrescentadas sem confundir intenção com
funcionalidade entregue.

## Visão proposta

A visão de produto é uma plataforma capaz de receber dados autorizados de
ativos, contextualizar condições observadas e apoiar recomendações de
manutenção com evidências rastreáveis. Essa visão orienta as decisões
arquiteturais, mas **ainda não representa o estado implementado**.

## Estado implementado

A versão atual contém:

- um workspace Python gerenciado por uv, com um backend instalável em
  `apps/api`;
- uma aplicação FastAPI mínima, inicializável pelo Uvicorn, com liveness em
  `GET /health/live`;
- um contrato OpenAPI v1 congelado para análise com 18 features e cinco
  resultados fechados, ciclo documental mínimo, portas internas tipadas e fakes
  inteiramente sintéticos, com snapshot determinístico para futura geração de
  cliente;
- um domínio documental em memória com os sete estados governados, versões
  idempotentes por identidade, número e SHA-256, auditoria imutável, relógio UTC
  injetável e controle otimista de revisão por compare-and-swap;
- configuração tipada e explícita para ambiente e URL PostgreSQL;
- PostgreSQL 17 com pgvector 0.8.6 para desenvolvimento local via Docker
  Compose;
- imagens multi-stage da API e da fronteira web, construídas por bases fixadas
  por digest e locks congelados, com processos não privilegiados e healthchecks;
- automação Poe para bootstrap, formatação, lint, tipagem estrita, testes,
  hooks, smoke e controle dos serviços locais;
- um workspace Node gerenciado por Corepack e pnpm, com `apps/web` reservado
  como fronteira de integração e um processo HTTP exclusivo para liveness,
  ainda sem interface;
- manifesto de integridade dos materiais locais, duas fixtures públicas
  inteiramente sintéticas, uma baseline agregada e um inventário categórico de
  rótulos, ambos versionados sob a identidade pública da fonte;
- uma única porta tipada e somente leitura para `banner.csv`, com caminho de
  entrada explícito, validação de tamanho e SHA-256 antes e depois do consumo;
  `consume_banner_source_audited()` vincula a baseline aos fingerprints
  efetivamente observados no recibo pre/post, enquanto
  `consume_banner_source()` preserva a interface compatível;
- uma porta tipada e somente leitura para inventariar e extrair `Doc1.pdf` a
  `Doc6.pdf`, com validação pre/post pelo manifesto, rastreabilidade e qualidade
  por página, extração nativa preferencial e adapter RapidOCR local, explícito e
  lazy; as saídas reais ficam somente em diretório ignorado;
- segmentação determinística somente sobre a extração estruturada desses
  documentos, sem reabrir PDFs, com limites e overlap versionados, IDs por
  conteúdo e proveniência, embeddings fake hash locais não semânticos para CI,
  repositório em memória e fronteira pgvector dependente de writer injetado;
- um catálogo v2 das 26 colunas e um contrato Pandera estrito, ordenado e sem
  coerção implícita, acompanhado de relatórios sanitizados de violação;
- um profiler determinístico sobre DataFrames já carregados, com indicadores
  agregados de estrutura, tempo, qualidade por coluna, duplicidade, estatística,
  rótulos protegidos por vocabulário confiável ou categorias anônimas e pares
  redundantes, além de JSON estável e Markdown resumido;
- um runner auditado de baseline que executa duas rodadas independentes, aplica
  gates fail-closed e só publica JSON e Markdown sanitizados quando os bytes das
  duas rodadas e seus recibos de integridade coincidem;
- normalização textual versionada de `fault` e um inventário JSON com os 151
  rótulos brutos, frequências globais, forma normalizada, slug estável e estado
  explícito de colisão, gerado em duas rodadas pela mesma porta auditada e
  validável offline sem a fonte;
- uma política declarativa e versionada de qualidade, validada contra contrato e
  profiler, com identificador semântico SHA-256, precedências imutáveis e visão
  pública derivada apenas da política e dos agregados rastreados da baseline;
- contratos v1 de geração para diagnóstico imutável de entrada, evidências,
  avaliação de suporte, prescrições, citações e warnings, com prompt versionado,
  provider fake determinístico e adaptador Bedrock desabilitável;
- um pipeline canônico local e determinístico para `banner.csv`, com 18 features
  de inferência, ledger completo de disposições, agrupamento temporal de
  ocorrências independente do target, partições cronológicas com purga e
  estatísticas IQR ajustadas somente em treino;
- CI em Ubuntu e Windows, política automatizada para títulos, origens de pull
  request e integridade Git de releases, além de verificações de segurança com
  CodeQL, revisão de dependências e varredura de segredos;
- atualizações semanais agrupadas pelo Dependabot para os ecossistemas uv, npm,
  GitHub Actions e Docker Compose, sempre direcionadas a `develop`;
- documentação de governança, segurança, arquitetura e decisões fundamentais.

Não há, nesta etapa, regras de negócio completas, ingestão contínua pela
aplicação, execução de modelo ou busca real por vizinhos, vetores integrados
à aplicação, recuperação de contexto, execução RAG integrada, chamada
automática a LLM, autenticação, persistência integrada, readiness,
infraestrutura AWS, deploy ou interface web.

## Arquitetura atual

O repositório é um monorepo e o backend segue um monólito modular: uma única
aplicação implantável, com fronteiras internas que poderão receber módulos de
domínio quando houver requisitos implementáveis. Essa escolha reduz a
complexidade operacional da fundação sem impedir separação de responsabilidades
no código.

Os componentes existentes são:

| Componente | Responsabilidade atual |
| --- | --- |
| `apps/api` | Pacote `prescriptive_maintenance`, aplicação FastAPI, liveness, contrato OpenAPI v1 com fakes e portas internas tipadas, domínio documental governado com repositório em memória, settings, contratos de dados, profiler, inventário categórico, política de qualidade, pipeline canônico local, extração e indexação locais rastreáveis dos documentos autorizados e fronteira versionada de geração prescritiva com provider offline e adaptador Bedrock injetável. |
| `apps/web` | Processo Node mínimo para liveness da fronteira; nenhuma UI foi implementada. |
| `compose.yaml` e `infra/` | Topologia local com API, web, PostgreSQL/pgvector e script de habilitação da extensão. |
| `scripts/smoke.py` | Verificação de runtimes, configuração, Compose, importação e liveness; banco e aplicações em contêineres são opcionais. |
| `data/` | Manifesto dos materiais locais, fixtures sintéticas, artefatos públicos aprovados e destinos ignorados para derivados canônicos locais. |

O inventário detalhado está em
[`docs/architecture/README.md`](docs/architecture/README.md), e as decisões que
sustentam essa estrutura estão registradas em [`docs/adr/`](docs/adr/README.md).

## Estrutura do repositório

```text
.
├── .github/          # governança de pull requests, responsáveis e CI
├── apps/
│   ├── api/          # pacote Python instalável do backend
│   └── web/          # fronteira de workspace, sem implementação de UI
├── data/
│   ├── baselines/banner/<source-sha>/
│   │   ├── baseline.v1.json # agregado determinístico e sanitizado
│   │   └── summary.md       # resumo derivado somente do JSON
│   ├── inventories/banner/<source-sha>/
│   │   └── fault-labels.v1.json # inventário categórico determinístico
│   ├── fixtures/            # dados públicos inteiramente sintéticos
│   └── source-manifest.json # identidade pública dos materiais locais
├── docs/
│   ├── adr/          # decisões arquiteturais
│   ├── architecture/ # inventário da arquitetura implementada
│   └── data/         # visões humanas derivadas de contratos de dados
├── experiments/      # estudos isolados do código de produção
├── infra/            # PostgreSQL e pgvector para desenvolvimento local
└── scripts/          # automação cross-platform exposta pelo Poe
```

## Pré-requisitos

- Git;
- Python `>=3.13,<3.14`, com a linha `3.13` registrada em
  `.python-version`;
- uv compatível com o lock do projeto;
- Node.js `>=22,<23`, com a linha `22` registrada em `.node-version`;
- Corepack e pnpm `10.15.1`;
- Docker Desktop ou Docker Engine com Compose v2 para o smoke e os comandos de
  infraestrutura.

## Quickstart

Clone o repositório, use a branch de integração e prepare o ambiente pela raiz:

```powershell
git clone https://github.com/HiRenan/senai-prescriptive-maintenance.git
Set-Location senai-prescriptive-maintenance
git switch develop
uv run --frozen poe setup
uv run --frozen poe check
uv run --frozen poe smoke
```

`setup` sincroniza todos os pacotes Python pelo lock, instala o workspace pnpm
com lock congelado e instala os hooks pre-commit. O projeto mantém um único
`uv.lock` e um único `pnpm-lock.yaml`, ambos na raiz.

O smoke padrão não exige banco em execução nem cria `.env`. O Docker precisa
estar disponível porque a validação inclui `docker compose config`.

## Comandos canônicos

Todas as tarefas abaixo são executadas da raiz. Somente `format` reescreve
código; `check` é uma sequência fail-fast somente leitura.

| Comando | Finalidade |
| --- | --- |
| `uv run --frozen poe setup` | Sincroniza dependências congeladas e instala os hooks locais. |
| `uv run --frozen poe format` | Aplica correções seguras e formata arquivos Python com Ruff. |
| `uv run --frozen poe format-check` | Verifica a formatação sem escrever. |
| `uv run --frozen poe lint` | Executa Ruff sem correções automáticas. |
| `uv run --frozen poe typecheck` | Executa Pyright em modo estrito. |
| `uv run --frozen poe test` | Executa Pytest com cobertura mínima configurada. |
| `uv run --frozen poe check` | Executa format-check, lint, typecheck e test, nessa ordem. |
| `uv run --frozen python -m prescriptive_maintenance.data.cli build --help` | Exibe os caminhos explícitos exigidos para construir derivados canônicos locais. |
| `uv run --frozen python -m prescriptive_maintenance.data.cli check --help` | Exibe os caminhos exigidos para verificar um build local sem reescrever. |
| `uv run --frozen poe hooks` | Executa todos os hooks pre-commit em todos os arquivos. |
| `uv run --frozen poe services-up` | Inicia o PostgreSQL local e aguarda o healthcheck. |
| `uv run --frozen poe applications-audit` | Audita os contextos Docker reais e os filesystems dos builders. |
| `uv run --frozen poe applications-build` | Constrói as imagens locais multi-stage da API e da web. |
| `uv run --frozen poe applications-up` | Constrói e inicia PostgreSQL, API e web e aguarda os healthchecks. |
| `uv run --frozen poe services-down` | Remove contêiner e rede, preservando o volume local. |
| `uv run --frozen poe smoke` | Valida runtimes, configuração, Compose e liveness real. |
| `uv run --frozen poe smoke --with-services` | Acrescenta a validação do PostgreSQL e do pgvector já iniciados. |
| `uv run --frozen poe smoke --with-services --with-applications` | Acrescenta PostgreSQL, pgvector, health da web, liveness e OpenAPI v1 da API em contêineres. |

Para incluir o banco no smoke:

```powershell
uv run --frozen poe services-up
uv run --frozen poe smoke --with-services
uv run --frozen poe services-down
```

Se `127.0.0.1:5432` estiver ocupada, escolha uma porta host livre por
`PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT`. A porta interna permanece
`5432`; os exemplos para PowerShell e Bash estão em
[`infra/README.md`](infra/README.md).

## Topologia containerizada local

O fluxo completo usa o mesmo `compose.yaml` do banco, sem duplicar serviços.
Cada Dockerfile tem uma allowlist própria, e a `.dockerignore` da raiz mantém
uma união segura para clientes sem suporte à convenção específica. Somente os
manifests, locks e fontes necessários chegam ao builder; testes, snapshots
OpenAPI, READMEs desnecessários, materiais originais, demais dados, `.env`, Git,
caches e dependências de desenvolvimento ficam fora do contexto e das imagens.

Execute build, start, smoke e stop a partir da raiz:

```powershell
uv run --frozen poe applications-audit
uv run --frozen poe applications-build
uv run --frozen poe applications-up
uv run --frozen poe smoke --with-services --with-applications
uv run --frozen poe services-down
```

A API fica em `127.0.0.1:8000` e a liveness da web em `127.0.0.1:3000`. As
portas host podem ser alteradas por
`PRESCRIPTIVE_MAINTENANCE_API_HOST_PORT` e
`PRESCRIPTIVE_MAINTENANCE_WEB_HOST_PORT`; as portas internas permanecem `8000`
e `3000`. `applications-up` usa apenas a configuração local fictícia declarada
no Compose, e `services-down` continua preservando o volume PostgreSQL.

## Configuração local

O backend exige `PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT` e
`PRESCRIPTIVE_MAINTENANCE_DATABASE_URL` somente quando `Settings` é
instanciado. A liveness não carrega essas configurações.

`.env.example` contém valores obviamente fictícios e exclusivos para
desenvolvimento local. Copie-o para `.env` apenas quando um fluxo manual
precisar carregar settings; `.env` permanece ignorado pelo Git. Consulte
[`apps/api/README.md`](apps/api/README.md) para o contrato completo.

## Dados e materiais

Os oito materiais originais fornecidos para o desafio são locais, ignorados
pelo Git e não podem ser redistribuídos. O arquivo rastreado
[`data/source-manifest.json`](data/source-manifest.json) contém somente nomes,
tamanhos e hashes SHA-256 para verificação de integridade; não contém nem
concede direitos sobre o conteúdo recebido.

Além das fixtures sintéticas em `data/fixtures/`, há duas exceções rastreadas
para saídas derivadas. O par
`data/baselines/banner/<source-sha>/baseline.v1.json` e `summary.md` contém
somente auditoria agregada e sanitizada. O arquivo
`data/inventories/banner/<source-sha>/fault-labels.v1.json` contém somente o
conjunto categórico aprovado: rótulo bruto, frequência global, forma
normalizada, slug e estado/resolução de colisão, além de versões, identidade da
fonte, recibos e reconciliações. Nenhum desses artefatos contém linha,
identificador, medição, associação temporal, caminho local ou arquivo original;
todos são validados offline contra o manifesto e a baseline pública.

A matriz pública de regras, precedências e comparação exclusivamente agregada
está em
[`docs/data/banner-quality-policy.md`](docs/data/banner-quality-policy.md). A
fonte canônica dessa visão é a política JSON validada pelo backend; o documento
não contém valores por registro e não autoriza reprocessamento da fonte.

O pipeline canônico consome a fonte somente pela porta auditada e grava seus seis
artefatos exclusivamente em um destino local explicitamente informado e já
ignorado pelo Git. `manifest.json` registra IDs, hashes físicos e lógicos,
reconciliações, contagens e gates; os Parquets por partição contêm somente as 18
features ordenadas e o target pós-partição como `y`. Consulte
[`apps/api/README.md`](apps/api/README.md) para os contratos e comandos.

Dados originais devem ficar em `data/raw/original/`. Originais, dados brutos,
saídas intermediárias, dados processados e quaisquer outros artefatos gerados
continuam locais, proibidos de versionamento e cobertos pelos caminhos ignorados
em `.gitignore`; os artefatos públicos acima não ampliam essas exceções. As regras de
preparação, conferência e publicação estão em
[`data/README.md`](data/README.md).

## Segurança

Credenciais, tokens, chaves, `.env`, dumps, volumes e dados locais nunca devem
ser versionados. Os valores de `.env.example` e `compose.yaml` são fictícios e
não podem ser usados em produção.

Vulnerabilidades devem ser comunicadas de forma privada conforme
[`SECURITY.md`](SECURITY.md); não publique detalhes exploráveis em issues ou
pull requests.

## Contribuição e governança

`develop` é a branch de integração e `main` contém somente baselines e releases
estáveis. Implementações são feitas em branches curtas, dentro de worktrees
isoladas criadas a partir do `origin/develop` atualizado, e entram por pull
request com squash. Releases usam uma branch curta `release/*` criada da
`origin/develop` validada, que apenas reconcilia a `origin/main` vigente e deve
preservar exatamente a árvore de `develop`; seu pull request para `main` é
integrado por merge commit. Hotfixes entram em `main` por squash e são
sincronizados de volta para `develop` antes da promoção seguinte.

O gate obrigatório rejeita promoções diretas de `develop`, branches de tarefa e
forks para `main`. Para uma release, ele exige um merge commit de dois pais
exatos — `origin/develop` primeiro e `origin/main` segundo —, ancestralidade da
`main` vigente e equivalência da árvore com a `develop` vigente. A sincronização
é provada por um merge virtual limpo de `develop` com `main` que preserve a
árvore de `develop`; a equivalência da árvore de `main` com um commit histórico
de `develop` existe somente como fallback para a divergência legada causada pelo
squash anterior. Nenhuma etapa permite alteração direta das branches
permanentes.

Nomes de branches, commits Conventional Commits e títulos de pull request são
escritos em inglês. Documentação, ADRs, apresentações e descrições de pull
request são escritos em português. O fluxo completo está em
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Limitações e próximos passos não implementados

As seguintes capacidades pertencem à evolução futura e **não estão
implementadas**:

- regras operacionais completas de diagnóstico e manutenção prescritiva;
- ingestão contínua ou orquestração do pipeline pela aplicação;
- persistência da aplicação no PostgreSQL e uso de vetores pelo backend;
- busca por similaridade, recuperação governada de contexto, execução RAG
  integrada ou configuração operacional de LLM;
- autenticação, autorização, readiness e observabilidade de produção;
- frontend ou qualquer experiência de usuário;
- infraestrutura AWS, pipeline de deploy, release publicada ou ambiente de
  produção.

Cada capacidade deverá entrar por tarefa própria, com critérios verificáveis e
sem enfraquecer a fronteira dos materiais locais.

## Documentação

- [`CONTRIBUTING.md`](CONTRIBUTING.md): GitFlow, papéis e critérios de revisão;
- [`SECURITY.md`](SECURITY.md): reporte responsável e política de segredos;
- [`docs/README.md`](docs/README.md): índice e convenções da documentação;
- [`docs/adr/README.md`](docs/adr/README.md): decisões arquiteturais;
- [`docs/architecture/README.md`](docs/architecture/README.md): estado técnico
  implementado.

## Acesso e direitos

Este repositório é público exclusivamente para leitura, clone e avaliação pela
banca. A disponibilidade pública não concede autorização para copiar,
modificar, redistribuir ou reutilizar o conteúdo.

Não há arquivo `LICENSE`, não existe licença implícita de reutilização e todos
os direitos permanecem reservados a Renan Mocelin.
