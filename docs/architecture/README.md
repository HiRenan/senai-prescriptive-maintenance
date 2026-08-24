# Arquitetura implementada

Este documento é um inventário do estado atual. Ele registra somente
componentes presentes no repositório e separa explicitamente qualquer direção
futura para evitar diagramas ou integrações fictícias.

## Visão geral

O projeto usa um monorepo. O backend FastAPI em `apps/api` é a aplicação de
produto implementada e está estruturado como base para um monólito modular. A
fronteira `apps/web` serve o painel de demonstração da análise a partir do
contrato congelado. PostgreSQL/pgvector existe como serviço local independente.
A API abre conexões curtas apenas quando uma rota documental usa explicitamente
o backend `postgres`; importação, liveness e o perfil `offline` não abrem rede.
`infra/aws/demo` descreve recursos Terraform, mas nenhum ambiente AWS foi
aplicado.

## Componentes existentes

| Área | Implementação comprovável | Limite atual |
| --- | --- | --- |
| `apps/api/src/prescriptive_maintenance/{main,analysis_runtime,analysis_artifacts,analysis_artifact_documents}.py` | Fábrica `create_app()`, alvo ASGI, health checks, composition root e fronteiras de manifesto/documentos dos modos explícitos `synthetic_demo`/`artifacts`. | Configuração estrutural inválida bloqueia startup; artefatos incompatíveis mantêm liveness e deixam readiness/análise em 503, sem fallback. |
| `apps/api/src/prescriptive_maintenance/{contracts,ports,services,fakes}.py` | União fechada dos cinco resultados de análise, 18 features, ciclo documental, portas tipadas, erros sanitizados e exemplos sintéticos. | O demo é selecionado explicitamente; o modo artifacts exige manifesto local aprovado e não publica derivados privados. |
| `apps/api/src/prescriptive_maintenance/document_lifecycle.py` | Agregado documental versionado, matriz fechada dos sete estados, gates monotônicos, auditoria append-only, replay semântico exato, relógio UTC injetável e CAS que valida comando e agregado completos. | Não processa bytes ou chunks e não conhece HTTP ou PostgreSQL. |
| `apps/api/src/prescriptive_maintenance/document_registry.py` | Registro de metadados v1 sobre o domínio real, identidade lógica por filename ASCII canônico, IDs opacos por versão, projeções dos sete estados, replay exato e adapter em memória concorrente. | O request não transporta bytes; tamanho e SHA-256 são declarados, sem upload, assinatura, storage, OCR ou indexação. |
| `apps/api/src/prescriptive_maintenance/knowledge_retrieval.py` | Configuração externa de classe canônica para documentos opacos com versão, hash semântico e referências validadas; uma rotina fail-closed seleciona somente a versão aprovada vigente, confere integridade antes do scorer e revalida o mesmo snapshot antes de derivar tanto o ranking content-free quanto o snapshot interno com texto e hash. A conferência pontual posterior valida os mesmos snapshots sem scorer ou reranking. | Não inclui configuração real, scorer semântico, busca pgvector, provider, endpoint, persistência ou geração. O conteúdo enriquecido não cruza a API. |
| `apps/api/src/prescriptive_maintenance/governed_retrieval.py` | Porta RAG interna e exception-total que bloqueia normal/OOD, mapeia ausência, classe não documentada e falha técnica, aplica limiar com identidade SHA-256, expõe o binding efetivo de policy/mapeamento e limita o prefixo ranqueado pelos budgets existentes de evidência. | Não chama LLM, não converte para o contrato de geração, não implementa guardrails e não consulta índice diretamente. |
| `apps/api/src/prescriptive_maintenance/prescription_orchestration.py` | Composição interna pura que valida o resultado do modelo, liga o binding efetivo da recuperação, chama busca apenas para falha documentável, reutiliza os guardrails RAG, limita o provider síncrono por timeout e slot unitário e devolve estados e metadados allowlisted. | Não executa o modelo, não persiste, não configura provider real e não oferece cancelamento do provider síncrono. |
| `apps/api/src/prescriptive_maintenance/analysis_integration.py` | Composição explícita da rota de análise com autorização imutável de dataset/modelo/índice/recuperação/geração/projeção, paridade de vizinhos, cinco estados públicos, citações usadas, UoW, cache posterior ao commit e logs por estágio. | É selecionada somente em `artifacts` após validação integral, mantém o `GET` completo no processo e não armazena features, conteúdo ou narrativas. |
| `apps/api/src/prescriptive_maintenance/analysis_benchmark.py` e `scripts/analysis_benchmark.py` | Harness e CLI sintéticos para medir o `POST /analysis` integrado, com aquecimento isolado, passagem temporizada sem tracing, runtime novo para memória, métricas primárias por cenário, eventos pós-medição, p50/p95, erros, alocações Python por requisição, uso simulado e proveniência dirty vinculada a conteúdo. | É um microbenchmark sequencial de QA; o mix de cenários é apenas secundário e não mede RSS, memória nativa, concorrência, capacidade, artefatos reais, rede, LLM ou custo faturável. |
| `apps/api/openapi/v1.json` | Snapshot OpenAPI 3.1 determinístico e compatível com geração de cliente. | É a fonte de tipos HTTP; `apps/web` deriva seu contrato dele e não duplica tipos à mão. |
| `scripts/generate_web_contract.py` | Gerador determinístico do contrato web de análise, com modo `--check`, a partir do snapshot v1. | Cobre somente as estruturas alcançáveis por `POST /analysis`; não gera cliente do ciclo documental. |
| `apps/api/src/prescriptive_maintenance/settings.py` | Perfis `local`, `offline` e `aws`, backend obrigatório `memory`/`postgres` e URL coerente, carregados no lifespan. | A importação e a liveness não instanciam dependências externas. |
| `apps/api/src/prescriptive_maintenance/persistence/` | Metadados imutáveis de análise/documento/versão/chunk/evidência, registry de ciclo/auditoria, UoW, adapter psycopg, CAS transacional e migrações reversíveis para metadados e índice de similaridade. | As rotas documentais usam somente metadados; conteúdo, features e narrativas não são persistidos. Migrações continuam explícitas. |
| `apps/api/src/prescriptive_maintenance/data/` | Fronteira interna com portas tipadas para abrir `banner.csv` e os seis PDFs autorizados em modo binário read-only, emitir evidências pre/post, extrair PDFs com rastreabilidade e qualidade por página, segmentar a extração estruturada com IDs determinísticos, representar chunks offline, armazená-los em memória ou entregá-los a um writer pgvector injetado, aplicar o contrato v2 estrito das 26 colunas, perfilar um DataFrame, executar a baseline determinística e o inventário categórico normalizado de `fault` em duas rodadas e carregar a política declarativa de qualidade. | Exige caminhos explícitos no acesso às fontes; o indexador não recebe PDFs. Derivados permanecem locais e ignorados, o embedding fake hash de CI não é semântico e a fronteira pgvector não abre conexão nem executa SQL. OCR depende de adapter local explícito e sua ausência produz `ocr_required` apenas em páginas sem texto utilizável. Os artefatos públicos tabulares só são persistidos após integridade, gates, reconciliações e igualdade byte a byte. |
| `apps/api/src/prescriptive_maintenance/generation/` | Diagnóstico imutável de entrada, contratos `prescriptive-generation.v1`, limites de evidência, prompt v2, envelope documental não confiável, gates tipados pré/pós-provider, recusas sanitizadas, validação estrita de citações, provider fake determinístico e adaptador Bedrock com cliente injetado de forma preguiçosa. | Não faz chamada automática, não prova suporte semântico, não elimina a janela após a revalidação final, não lê credenciais e não contém SDK ou configuração de infraestrutura AWS. |
| `apps/api/src/prescriptive_maintenance/modeling/` | Busca k-NN v3 em memória sobre 18 features, `StandardScaler` de treino, distância euclidiana, condição candidata baseada em históricos, política fechada dos cinco estados operacionais, suporte heurístico, abstenção tipada, adapter `ModelPort`, artefato NumPy/JSON íntegro, índice derivado versionado com adapters exatos em memória e PostgreSQL/pgvector e harness temporal com métricas candidatas, seletivas e exatas. | Só é ligada às rotas por `artifacts`, não calibra probabilidade, não usa o teste no fit, não faz tuning, não usa busca aproximada ou GPU; a avaliação é pós-hoc em um teste historicamente observado e não aprova o modelo para operação. |
| `apps/api/tests/` | Contratos do pacote, aplicação, liveness, OpenAPI v1, configuração, persistência, dados, geração e modelo, incluindo snapshots, PDFs sintéticos, JSON, golden e cenários Unicode inteiramente sintéticos. | A suíte padrão não acessa materiais originais, serviços externos nem credenciais; a integração PostgreSQL é opcional e usa schema descartável. |
| `apps/api/Dockerfile` | Build multi-stage pelo `uv.lock`, runtime Python 3.13 não privilegiado e healthcheck da readiness. | Não inclui dependências de desenvolvimento nem executa migrações automaticamente; a readiness segue o backend configurado. |
| `apps/web` | Painel React/TypeScript empacotado pelo Vite, contratos derivados do OpenAPI v1, análise e ciclo documental, servidor/proxy local e perfil AWS com runtime config público, Cognito Code + PKCE e bearer em memória. | Somente a origem final exata ativa AWS; local/LAN e offline permanecem disponíveis. Não há renovação automática nem `GET /analysis/{id}`. |
| `.dockerignore` e `apps/*/Dockerfile.dockerignore` | União segura na raiz e allowlists específicas dos manifests, locks e fontes necessários a cada build. | Excluem todo o restante do monorepo dos contextos; a API inclui somente o README exigido pelos metadados Python. |
| `compose.yaml` | Topologia API, web e PostgreSQL 17/pgvector 0.8.6, binds em loopback, healthchecks e volume nomeado. | É infraestrutura de desenvolvimento local, não ambiente de produção. |
| `infra/postgres/init/001-enable-vector.sql` | Habilita a extensão `vector` na primeira criação do volume. | Não cria esquema ou tabelas da aplicação. |
| `infra/aws/demo/` | Root module Terraform single-AZ com Budget, VPC sem rota pública, endpoints privados, ECR, ECS Fargate, Cloud Map/VPC Link, API Gateway JWT/Cognito, Hosted UI, S3/CloudFront OAC, publicação web allowlisted, SQS/DLQ, IAM por ação e recurso, logs, alarmes, outputs sanitizados e auditoria do plano JSON. | É código não aplicado: a fundação começa sem objetos e o dispatch de runtime publica frontend/imagem; não há usuário Cognito ou worker executável, e Bedrock fica desabilitado por padrão. A execução live pertence à SEN-74. |
| `scripts/smoke.py` | Verifica runtimes, importação, `.env.example`, Compose, liveness e readiness offline por HTTP; opcionalmente banco/pgvector, contêineres, OpenAPI e composição de derivados aprovados. | Não inicia nem encerra serviços; o modo de artefatos imprime somente contagens e marca ausência como indisponível/skip. |
| `data/source-manifest.json` | Nomes, tamanhos e hashes dos materiais locais. | Não contém nem redistribui os arquivos originais. |
| `data/fixtures/` | Uma fixture tabular e um relato textual, ambos sintéticos. | Não são amostras dos materiais originais nem dados de produção. |
| `data/inventories/banner/<source-sha>/fault-labels.v1.json` | Inventário categórico determinístico dos 151 raws, com frequência global, normalização, slug, colisões, versões, recibos e ID de conteúdo. | Não contém ocorrência, identificador, tempo, sensor ou medição e não define equivalência semântica. |
| `.github/workflows/ci.yml` | Qualidade completa em Ubuntu e teste/smoke essenciais em Windows. | Não faz deploy nem publica release. |
| `.github/workflows/pull-request-policy.yml` | Testa e aplica a política de título, origem e integridade Git de releases. | O gate Git atua somente em `release/*` → `main`; tarefas e hotfixes recebem apenas a validação de metadados. |
| `.github/workflows/security.yml` | CodeQL, revisão de dependências e varredura de segredos. | Revisão de dependências existe somente no evento de pull request. |
| `.github/workflows/aws-demo-*.yml` | Validação AWS inteiramente offline em pull request e workflows manuais protegidos para plan, fundação, publicação por digest/allowlist, smoke da API e URL web e teardown, com contrato OIDC/IAM e inventário pós-destroy. | OIDC, roles, environments, backend, domínio, certificado, identidade de smoke e toda execução AWS continuam externos e não comprovados live; essa evidência pertence à SEN-74. |
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
2. `uv run --frozen poe check` verifica formatação, lint, tipos, testes e o
   bundle de produção do painel;
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

Os sete workflows rastreados se dividem entre três gates gerais e quatro fluxos
AWS:

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
  relevante;
- **AWS demo offline validation** executa em pull requests para `develop` que
  alteram o perfil ou seus workflows, sem environment, segredo ou OIDC. Ele
  valida e planeja somente com placeholders isolados e executa as políticas de
  segurança e entrega;
- **AWS demo plan, deploy e teardown** são três workflows exclusivamente
  manuais, limitados ao HEAD atual de `main`, com confirmação literal,
  environments e roles OIDC independentes. Eles existem como operação
  protegida versionada; nenhum deles foi executado na AWS.

Todas as actions de terceiros são fixadas por SHA. O checkout da política não
persiste credenciais, e o gate invoca Git por argumentos estruturados, sem
dependência npm adicional e com prompts do Git e do gerenciador de credenciais
desabilitados. Não existe deploy automático, publicação de release ou envio de
cobertura para um serviço externo. A validação e os limites AWS estão registrados
na [evidência SEN-69](../validation/aws-demo-evidence.md); as proteções, os oito
checks e a diferença intencional de histórico linear entre `develop` e `main`
estão registrados no [ADR 0004](../adr/0004-gitflow-ci-and-releases.md).

## Fronteiras atuais

- **Aplicação:** FastAPI e configuração são código de produção; Dockerfiles,
  smoke e Compose são suporte ao empacotamento, desenvolvimento e validação.
- **Contrato de análise:** schemas, portas e integração são executáveis; a factory
  exige `synthetic_demo` ou `artifacts`, e o segundo só configura uma composição
  integralmente autorizada. Vizinhos pertencem ao modelo e
  citações pertencem à evidência documental, com referências opacas de documento,
  versão e chunk e página positiva, sem título, caminho ou texto bruto.
- **Modelo:** a baseline e o adapter são executáveis e carregáveis localmente. A
  aplicação HTTP os instancia em `artifacts` após conferir o manifesto; os
  derivados permanecem ignorados e somente métricas agregadas são públicas.
- **Persistência:** o backend oferece repositórios e unidade de trabalho com
  adapters em memória e PostgreSQL, além de migrações reversíveis. As rotas do
  ciclo documental selecionam o registry real por `Settings`, reconstroem a
  auditoria e usam CAS transacional. A integração de análise usa uma UoW
  injetada para metadados e referências, sem persistir resposta completa,
  features, narrativas ou bytes documentais.
- **Web:** `apps/web` implementa o fluxo de análise sobre tipos gerados do
  contrato v1. A disponibilidade da prescrição é decidida pela tabela de
  desfechos derivada do contrato, e todo estado que não seja prescrição emitida
  aparece explicitamente como não emitida.
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
  retry, fila, cache global ou persistência nessa camada; a integração explícita
  coordena essas fronteiras sem mudar suas responsabilidades.
- **Integração da análise:** autorização e bindings são conferidos antes de todas
  as jornadas e novamente por resultado. O ranking do modelo deve coincidir com
  o índice; projeção e cópias precedem a transação; o cache local vem somente
  depois do commit. Compose e AWS declaram `synthetic_demo`; não existe seleção
  implícita.
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
- upload multipart/streaming, validação de bytes e armazenamento documental;
- composição operacional autorizada com artefato de modelo aprovado, scorer real,
  embedding semântico, conexão pgvector e configuração de LLM;
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
