# Arquitetura implementada

Este documento é um inventário do estado atual. Ele registra somente
componentes presentes no repositório e separa explicitamente qualquer direção
futura para evitar diagramas ou integrações fictícias.

## Visão geral

O projeto usa um monorepo. O único processo de aplicação implementado é o
backend FastAPI em `apps/api`, estruturado como base para um monólito modular.
PostgreSQL/pgvector existe como serviço local independente; a aplicação ainda
não abre conexão com ele.

## Componentes existentes

| Área | Implementação comprovável | Limite atual |
| --- | --- | --- |
| `apps/api/src/prescriptive_maintenance/main.py` | Fábrica `create_app()`, alvo ASGI `app` e `GET /health/live`. | A liveness verifica apenas o processo e não acessa dependências. |
| `apps/api/src/prescriptive_maintenance/settings.py` | Settings tipados para `environment` e `database_url`, carregados sob demanda. | A aplicação não instancia settings na criação nem na liveness. |
| `apps/api/src/prescriptive_maintenance/data/` | Fronteira interna com a única porta tipada para abrir `banner.csv` em modo binário read-only, emitir recibos pre/post efetivos, aplicar o contrato v2 estrito das 26 colunas, perfilar um DataFrame, executar a baseline determinística e o inventário categórico normalizado de `fault` em duas rodadas e carregar a política declarativa de qualidade. | Exige caminhos explícitos somente no acesso à fonte; os runners só persistem artefatos aprovados após integridade, gates, reconciliações e igualdade byte a byte. O inventário pode ser validado offline, e a política oferece consulta imutável e resolve a ação efetiva de matches contextuais sem aplicar regras a linhas. |
| `apps/api/src/prescriptive_maintenance/generation/` | Diagnóstico imutável de entrada, contratos `prescriptive-generation.v1`, limites de evidência, prompt v1, validação da saída, porta neutra, provider fake determinístico e adaptador Bedrock com cliente injetado de forma preguiçosa. | Não recupera contexto, não faz chamada automática, não lê credenciais, não valida suporte semântico das citações e não contém SDK ou configuração de infraestrutura AWS. |
| `apps/api/tests/` | Contratos do pacote, aplicação, liveness, configuração, dados e geração, incluindo JSON, golden e cenários inteiramente sintéticos. | Os testes não acessam materiais originais, serviços externos nem credenciais; guardrails semânticos e regras prescritivas completas não estão implementados. |
| `apps/web` | Workspace privado e README de fronteira. | Não contém UI, framework, componentes, estilos, assets ou dependências. |
| `compose.yaml` | Serviço PostgreSQL 17 com pgvector 0.8.6, bind em loopback, healthcheck e volume nomeado. | É infraestrutura de desenvolvimento local, não ambiente de produção. |
| `infra/postgres/init/001-enable-vector.sql` | Habilita a extensão `vector` na primeira criação do volume. | Não cria esquema ou tabelas da aplicação. |
| `scripts/smoke.py` | Verifica runtimes, importação, `.env.example`, Compose e liveness HTTP; opcionalmente banco/pgvector. | Não inicia nem encerra serviços e não valida funcionalidades futuras. |
| `data/source-manifest.json` | Nomes, tamanhos e hashes dos materiais locais. | Não contém nem redistribui os arquivos originais. |
| `data/fixtures/` | Uma fixture tabular e um relato textual, ambos sintéticos. | Não são amostras dos materiais originais nem dados de produção. |
| `data/inventories/banner/<source-sha>/fault-labels.v1.json` | Inventário categórico determinístico dos 151 raws, com frequência global, normalização, slug, colisões, versões, recibos e ID de conteúdo. | Não contém ocorrência, identificador, tempo, sensor ou medição e não define equivalência semântica. |
| `.github/workflows/ci.yml` | Qualidade completa em Ubuntu e teste/smoke essenciais em Windows. | Não faz deploy nem publica release. |
| `.github/workflows/pull-request-policy.yml` | Testa e aplica a política de título, origem e integridade Git de releases. | O gate Git atua somente em `release/*` → `main`; tarefas e hotfixes recebem apenas a validação de metadados. |
| `.github/workflows/security.yml` | CodeQL, revisão de dependências e varredura de segredos. | Revisão de dependências existe somente no evento de pull request. |
| `.github/dependabot.yml` | Atualizações semanais agrupadas para uv, npm, GitHub Actions e Docker Compose. | Todos os pull requests gerados têm `develop` como destino. |

`pandas`, `pandera` e `pyarrow` compõem as dependências de produção da camada de
dados. `matplotlib` e `pandas-stubs` permanecem no grupo de desenvolvimento
porque apoiam a inspeção gráfica e a tipagem estática sem ampliar as
dependências instaladas do backend em produção.

## Execução local existente

O caminho mínimo validado é:

1. `uv run --frozen poe setup` sincroniza os workspaces Python e Node pelos
   locks e instala hooks;
2. `uv run --frozen poe check` verifica formatação, lint, tipos e testes;
3. `uv run --frozen poe smoke` valida runtimes, configuração estática do
   Compose e a resposta HTTP real da liveness;
4. quando o PostgreSQL já está iniciado por `services-up`,
   `uv run --frozen poe smoke --with-services` também verifica o healthcheck, a
   extensão pgvector e uma operação vetorial mínima.

O processo Uvicorn usado pelo smoke escuta apenas em loopback e em uma porta
efêmera. O serviço PostgreSQL publica a porta somente em `127.0.0.1` e persiste
dados em volume local.

## Integração contínua existente

Os três workflows rastreados estão ativos:

- **CI** executa em pushes e pull requests para `develop` e `main`. Em Ubuntu,
  instala os dois workspaces pelos locks, executa `poe check`, `poe hooks` e
  `poe smoke`; em Windows, executa `poe test` e `poe smoke` após a instalação
  congelada;
- **Pull Request Policy** executa quando um pull request para `develop` ou
  `main` é aberto, editado, reaberto, atualizado ou marcado como pronto para
  revisão. Ele testa a própria regra, valida título, destino e origem e, para
  `release/*` → `main`, busca as refs vigentes e prova ancestralidade, dois pais
  exatos e equivalência de árvore com `origin/develop`. A sincronização de
  `main` é comprovada por merge virtual limpo e neutro; a árvore de `main` no
  histórico de `develop` é aceita somente como fallback da divergência legada;
- **Security** executa em pushes e pull requests para `develop` e `main`, toda
  segunda-feira e sob acionamento manual. Ele analisa Python e
  JavaScript/TypeScript com CodeQL, bloqueia dependências novas com severidade
  alta em pull requests e usa Gitleaks para varrer conteúdo e histórico
  relevante.

Todas as ações de terceiros são fixadas por SHA. O checkout da política não
persiste credenciais, e o gate invoca Git por argumentos estruturados, sem
dependência npm adicional e com prompts do Git e do gerenciador de credenciais
desabilitados. Não existe workflow de deploy, publicação de release ou envio de
cobertura para um serviço externo. As proteções, os oito checks e a diferença
intencional de histórico linear entre `develop` e `main` estão registrados no
[ADR 0004](../adr/0004-gitflow-ci-and-releases.md).

## Fronteiras atuais

- **Aplicação:** FastAPI e configuração são código de produção; smoke e Compose
  são suporte ao desenvolvimento e à validação.
- **Persistência:** o banco está disponível localmente, mas não existe cliente,
  repositório, migração ou persistência integrada ao backend.
- **Web:** `apps/web` reserva o limite do workspace; não existe frontend.
- **Dados:** manifesto, fixtures sintéticas, baseline agregada, inventário
  categórico aprovado e visão derivada da política são públicos; originais e
  demais derivados permanecem
  locais e ignorados.
- **Geração:** contratos e providers pertencem ao backend modular; o diagnóstico
  vem do modelo anterior e o domínio usa somente a porta neutra e resultados
  sanitizados, sem importar conceitos do Bedrock.
- **Experimentos:** `experiments/` não constitui código de produção.

## Futuro, não implementado

Os itens abaixo não fazem parte da arquitetura executável atual:

- regras operacionais completas de diagnóstico e manutenção prescritiva;
- ingestão contínua ou pipeline de transformação tabular da aplicação;
- esquema da aplicação, migrações e persistência integrada;
- embeddings, uso de vetores pela aplicação, similaridade, recuperação
  governada de contexto, execução RAG integrada ou configuração operacional de
  LLM;
- autenticação, autorização e endpoint de readiness;
- frontend, experiência de usuário ou assets visuais;
- recursos AWS, deploy, ambiente de produção ou observabilidade operacional.

Uma tarefa futura deve atualizar este inventário somente depois que o
componente correspondente existir e puder ser verificado.

## Decisões relacionadas

- [ADR 0001 — Monorepo e monólito modular](../adr/0001-monorepo-and-modular-monolith.md)
- [ADR 0002 — Repositório público e fronteira dos materiais](../adr/0002-public-repository-and-source-boundary.md)
- [ADR 0003 — Runtimes e ferramentas do workspace](../adr/0003-runtimes-and-workspace-tooling.md)
- [ADR 0004 — GitFlow, CI, proteções, releases e hotfixes](../adr/0004-gitflow-ci-and-releases.md)
