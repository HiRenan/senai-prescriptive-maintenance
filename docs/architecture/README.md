# Arquitetura implementada

Este documento é um inventário do estado atual. Ele registra somente
componentes presentes no repositório e separa explicitamente qualquer direção
futura para evitar diagramas ou integrações fictícias.

## Visão geral

O projeto usa um monorepo. O backend FastAPI em `apps/api` é a aplicação de
produto implementada e está estruturado como base para um monólito modular. A
fronteira `apps/web` possui somente um processo operacional de liveness, sem
interface. PostgreSQL/pgvector existe como serviço local independente; a API
não abre conexão automaticamente, enquanto o módulo de persistência recebe
fábricas injetadas e permanece desacoplado das rotas HTTP. `infra/aws/demo`
descreve recursos Terraform, mas nenhum ambiente AWS foi aplicado.

## Componentes existentes

| Área | Implementação comprovável | Limite atual |
| --- | --- | --- |
| `apps/api/src/prescriptive_maintenance/main.py` | Fábrica `create_app()`, alvo ASGI `app`, `GET /health/live`, `GET /health/ready` e rotas do contrato HTTP v1. | A liveness verifica apenas o processo; a readiness consulta somente a dependência exigida pelo perfil e as rotas de negócio usam fakes sintéticos injetáveis. |
| `apps/api/src/prescriptive_maintenance/{contracts,ports,services,fakes}.py` | União fechada dos cinco resultados de análise, 18 features, ciclo documental, portas tipadas e orquestração determinística. | A aplicação continua injetando fakes; não conecta a baseline nem adapters de recuperação, geração ou persistência reais. |
| `apps/api/src/prescriptive_maintenance/document_lifecycle.py` | Agregado documental versionado, matriz fechada dos sete estados, gates monotônicos de extração/indexação, auditoria append-only com texto seguro, replay semântico exato, relógio UTC injetável e repositório em memória cujo CAS valida o comando e o agregado completos. | Não processa bytes ou chunks, não implementa adapter PostgreSQL e não altera os endpoints do contrato v1. |
| `apps/api/src/prescriptive_maintenance/knowledge_retrieval.py` | Configuração externa de classe canônica para documentos opacos com versão, hash semântico e referências validadas; uma rotina fail-closed seleciona somente a versão aprovada vigente, confere integridade antes do scorer e revalida o mesmo snapshot antes de derivar tanto o ranking content-free quanto o snapshot interno com texto e hash. A conferência pontual posterior valida os mesmos snapshots sem scorer ou reranking. | Não inclui configuração real, scorer semântico, busca pgvector, provider, endpoint, persistência ou geração. O conteúdo enriquecido não cruza a API. |
| `apps/api/src/prescriptive_maintenance/governed_retrieval.py` | Porta RAG interna e exception-total que bloqueia normal/OOD, mapeia ausência, classe não documentada e falha técnica, aplica limiar com identidade SHA-256 e limita o prefixo ranqueado pelos budgets existentes de evidência. | Não chama LLM, não converte para o contrato de geração, não implementa guardrails, não consulta índice diretamente e não está integrada às rotas HTTP. |
| `apps/api/src/prescriptive_maintenance/prescription_orchestration.py` | Composição interna pura que valida o resultado do modelo, chama recuperação apenas para falha documentável, reutiliza os guardrails RAG, limita o provider síncrono por timeout e slot unitário e devolve estados e metadados allowlisted. | Não executa o modelo, não persiste, não configura provider real, não oferece cancelamento do provider síncrono e não está ligada às rotas HTTP. |
| `apps/api/openapi/v1.json` | Snapshot OpenAPI 3.1 determinístico e compatível com geração posterior de cliente. | É a fonte de tipos HTTP; `apps/web` não duplica nem gera o cliente nesta tarefa. |
| `apps/api/src/prescriptive_maintenance/settings.py` | Settings tipados para `environment`, `persistence_backend` e `database_url`, carregados no startup. | `memory` proíbe URL; `postgres` exige URL; `offline` aceita somente memória. |
| `apps/api/src/prescriptive_maintenance/persistence/` | Metadados imutáveis de análise/documento/versão/chunk/evidência, evolução idempotente de versões, repositórios tipados, unidade transacional, adapter em memória, adapter psycopg e migração inicial reversível. | Não persiste conteúdo, features, vetores ou narrativas e ainda não é chamado pelas rotas HTTP. |
| `apps/api/src/prescriptive_maintenance/data/` | Fronteira interna com portas tipadas para abrir `banner.csv` e os seis PDFs autorizados em modo binário read-only, emitir evidências pre/post, extrair PDFs com rastreabilidade e qualidade por página, segmentar a extração estruturada com IDs determinísticos, representar chunks offline, armazená-los em memória ou entregá-los a um writer pgvector injetado, aplicar o contrato v2 estrito das 26 colunas, perfilar um DataFrame, executar a baseline determinística e o inventário categórico normalizado de `fault` em duas rodadas e carregar a política declarativa de qualidade. | Exige caminhos explícitos no acesso às fontes; o indexador não recebe PDFs. Derivados permanecem locais e ignorados, o embedding fake hash de CI não é semântico e a fronteira pgvector não abre conexão nem executa SQL. OCR depende de adapter local explícito e sua ausência produz `ocr_required` apenas em páginas sem texto utilizável. Os artefatos públicos tabulares só são persistidos após integridade, gates, reconciliações e igualdade byte a byte. |
| `apps/api/src/prescriptive_maintenance/generation/` | Diagnóstico imutável de entrada, contratos `prescriptive-generation.v1`, limites de evidência, prompt v2, envelope documental não confiável, gates tipados pré/pós-provider, recusas sanitizadas, validação estrita de citações, provider fake determinístico e adaptador Bedrock com cliente injetado de forma preguiçosa. | Não faz chamada automática, não prova suporte semântico, não elimina a janela após a revalidação final, não lê credenciais e não contém SDK ou configuração de infraestrutura AWS. |
| `apps/api/src/prescriptive_maintenance/modeling/` | Baseline k-NN em memória sobre 18 features, `StandardScaler` de treino, distância euclidiana, suporte heurístico, política versionada de thresholds, abstenção tipada, adapter `ModelPort` e artefato NumPy/JSON íntegro. | Não está ligada às rotas, não calibra probabilidade, não usa o teste no fit e não usa banco, pgvector ou GPU; a avaliação temporal não aprova o modelo para operação. |
| `apps/api/tests/` | Contratos do pacote, aplicação, liveness, OpenAPI v1, configuração, persistência, dados, geração e modelo, incluindo snapshots, PDFs sintéticos, JSON, golden e cenários Unicode inteiramente sintéticos. | A suíte padrão não acessa materiais originais, serviços externos nem credenciais; a integração PostgreSQL é opcional e usa schema descartável. |
| `apps/api/Dockerfile` | Build multi-stage pelo `uv.lock`, runtime Python 3.13 não privilegiado e healthcheck da readiness. | Não inclui dependências de desenvolvimento; a dependência consultada pela readiness segue o backend configurado. |
| `apps/web` | Workspace privado, servidor HTTP sem dependências, Dockerfile multi-stage e `GET /health/live`. | Não contém UI, framework, componentes, estilos, assets ou comportamento visual. |
| `.dockerignore` e `apps/*/Dockerfile.dockerignore` | União segura na raiz e allowlists específicas dos manifests, locks e fontes necessários a cada build. | Excluem todo o restante do monorepo dos contextos; a API inclui somente o README exigido pelos metadados Python. |
| `compose.yaml` | Topologia API, web e PostgreSQL 17/pgvector 0.8.6, binds em loopback, healthchecks e volume nomeado. | É infraestrutura de desenvolvimento local, não ambiente de produção. |
| `infra/postgres/init/001-enable-vector.sql` | Habilita a extensão `vector` na primeira criação do volume. | Não cria esquema ou tabelas da aplicação. |
| `infra/aws/demo/` | Root module Terraform single-AZ com Budget, VPC sem rota pública, endpoints privados, ECR, ECS Fargate, Cloud Map/VPC Link, API Gateway JWT/Cognito, S3/CloudFront OAC, SQS/DLQ, IAM por ação e recurso, logs, alarmes, outputs sanitizados e auditoria do plano JSON. | É somente código não aplicado; ECR, buckets e frontend começam vazios, não há usuário Cognito ou worker executável, e Bedrock fica desabilitado por padrão. |
| `scripts/smoke.py` | Verifica runtimes, importação, `.env.example`, Compose, liveness e readiness offline por HTTP; opcionalmente banco/pgvector, contêineres e igualdade do OpenAPI v1. | Não inicia nem encerra serviços e não valida funcionalidades futuras. |
| `data/source-manifest.json` | Nomes, tamanhos e hashes dos materiais locais. | Não contém nem redistribui os arquivos originais. |
| `data/fixtures/` | Uma fixture tabular e um relato textual, ambos sintéticos. | Não são amostras dos materiais originais nem dados de produção. |
| `data/inventories/banner/<source-sha>/fault-labels.v1.json` | Inventário categórico determinístico dos 151 raws, com frequência global, normalização, slug, colisões, versões, recibos e ID de conteúdo. | Não contém ocorrência, identificador, tempo, sensor ou medição e não define equivalência semântica. |
| `.github/workflows/ci.yml` | Qualidade completa em Ubuntu e teste/smoke essenciais em Windows. | Não faz deploy nem publica release. |
| `.github/workflows/pull-request-policy.yml` | Testa e aplica a política de título, origem e integridade Git de releases. | O gate Git atua somente em `release/*` → `main`; tarefas e hotfixes recebem apenas a validação de metadados. |
| `.github/workflows/security.yml` | CodeQL, revisão de dependências e varredura de segredos. | Revisão de dependências existe somente no evento de pull request. |
| `.github/dependabot.yml` | Atualizações semanais agrupadas para uv, npm, GitHub Actions e Docker Compose. | Todos os pull requests gerados têm `develop` como destino. |

`pandas`, `pandera`, `pyarrow`, `pypdfium2`, `rapidocr`, `onnxruntime` e
`scikit-learn` compõem as dependências de produção das camadas de dados e
modelo. PDFium cobre parse, texto nativo
e rasterização; o adapter RapidOCR inicializa o engine ONNX local somente quando
uma página exige OCR, sem serviço ou binário OCR do sistema. `psycopg` é o
driver do adapter PostgreSQL. `matplotlib` e
`pandas-stubs` permanecem no grupo de desenvolvimento porque apoiam a inspeção
gráfica e a tipagem estática sem ampliar as dependências instaladas do backend
em produção.

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

O caminho containerizado adicional é:

1. `uv run --frozen poe applications-audit` exporta os contextos filtrados reais
   e audita os filesystems dos builders;
2. `uv run --frozen poe applications-build` constrói API e web com bases por
   digest e locks congelados;
3. `uv run --frozen poe applications-up` inicia PostgreSQL, API e web e aguarda
   os três healthchecks;
4. `uv run --frozen poe smoke --with-services --with-applications` verifica o
   banco, as liveness da API e da web e o OpenAPI v1 servido;
5. `uv run --frozen poe services-down` remove contêineres e rede sem apagar o
   volume PostgreSQL.

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

- **Aplicação:** FastAPI e configuração são código de produção; Dockerfiles,
  smoke e Compose são suporte ao empacotamento, desenvolvimento e validação.
- **Contrato de análise:** schemas, portas e orquestração são executáveis, mas os
  resultados vêm somente de fakes sintéticos; vizinhos pertencem ao modelo e
  citações pertencem à evidência documental, com referências opacas de documento,
  versão e chunk e página positiva, sem título, caminho ou texto bruto.
- **Modelo:** a baseline e o adapter são executáveis e carregáveis localmente,
  mas a aplicação HTTP não os instancia; seus artefatos reais permanecem
  ignorados e somente as métricas agregadas sanitizadas são públicas.
- **Persistência:** o backend oferece repositórios e unidade de trabalho com
  adapters em memória e PostgreSQL, além de migração reversível para metadados;
  o ciclo documental possui separadamente repositório em memória com CAS e a
  indexação mantém um repositório em memória e um contrato de escrita pgvector
  injetável, ainda sem cliente ou SQL. As rotas HTTP continuam usando fakes e
  não abrem conexão automaticamente.
- **Web:** `apps/web` reserva o limite do workspace e oferece somente liveness
  operacional para o contêiner; não existe frontend ou rota visual.
- **Dados:** manifesto, fixtures sintéticas, baseline agregada, inventário
  categórico aprovado e visão derivada da política são públicos; originais e
  extrações dos PDFs e demais derivados permanecem locais e ignorados.
- **Geração:** contratos e providers pertencem ao backend modular; o diagnóstico
  vem do modelo anterior e o domínio usa somente a porta neutra e resultados
  sanitizados, sem importar conceitos do Bedrock. O gate RAG encapsula documento
  não confiável, recusa estado inseguro antes da chamada e só aceita prescrições
  com schema, diagnóstico, citações e snapshots atuais revalidados.
- **Orquestração prescritiva:** a composição interna recebe um `ModelPrediction`
  já produzido, preserva diagnóstico e vizinhos content-free e só permite que
  `FAULT` com evidência governada chegue ao provider. O slot unitário evita
  crescimento de chamadas síncronas órfãs; timeout não cancela a chamada tardia,
  que permanece descartada e mantém a instância ocupada até retornar. Não há
  retry, fila, cache global, persistência nem integração com a API.
- **Recuperação para RAG:** a decisão governada consome o snapshot já filtrado e
  revalidado pela recuperação documental, nunca faz uma segunda busca por
  conteúdo e mantém texto somente na fronteira interna. Política e mapeamento
  permanecem explícitos e auditáveis; não existe configuração operacional real
  no repositório. A conferência pré/pós-provider revisita apenas as identidades
  exatas já recuperadas e a política governada, sem novo ranking; ela não
  substitui uma transação ou lease operacional.
- **AWS demo:** o Terraform descreve uma implantação removível da imagem atual,
  sem tornar AWS, SQS, Cognito ou Bedrock dependências do monólito Python e sem
  afirmar que qualquer recurso exista antes de um apply autorizado futuro.
- **Experimentos:** `experiments/` não constitui código de produção.

## Futuro, não implementado

Os itens abaixo não fazem parte da arquitetura executável atual:

- regras operacionais completas de diagnóstico e manutenção prescritiva;
- ingestão contínua ou pipeline de transformação tabular da aplicação;
- integração das rotas HTTP com os repositórios persistentes;
- integração operacional do adapter de modelo, scorer real, embedding semântico,
  índice vetorial, conexão pgvector e uso de vetores pela aplicação, busca
  semântica por similaridade, integração da composição RAG com adapters reais e
  rotas HTTP ou configuração operacional de LLM;
- autenticação e autorização;
- frontend, experiência de usuário ou assets visuais;
- recursos AWS aplicados, deploy, ambiente de produção ou observabilidade
  operacional contínua.

Uma tarefa futura deve atualizar este inventário somente depois que o
componente correspondente existir e puder ser verificado.

## Decisões relacionadas

- [ADR 0001 — Monorepo e monólito modular](../adr/0001-monorepo-and-modular-monolith.md)
- [ADR 0002 — Repositório público e fronteira dos materiais](../adr/0002-public-repository-and-source-boundary.md)
- [ADR 0003 — Runtimes e ferramentas do workspace](../adr/0003-runtimes-and-workspace-tooling.md)
- [ADR 0004 — GitFlow, CI, proteções, releases e hotfixes](../adr/0004-gitflow-ci-and-releases.md)
