# Manutenção Prescritiva — Desafio SENAI

[![CI](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/ci.yml?query=branch%3Adevelop)
[![Pull Request Policy](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/pull-request-policy.yml/badge.svg?branch=develop)](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/pull-request-policy.yml?query=branch%3Adevelop)
[![Security](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/security.yml/badge.svg?branch=develop)](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/workflows/security.yml?query=branch%3Adevelop)

Este repositório reúne uma plataforma demonstrável para apoiar manutenção
prescritiva de ativos industriais. Ela organiza medições, histórico e
conhecimento técnico em uma resposta rastreável, mas não autoriza ações de
manutenção nem substitui avaliação humana.

## O problema

Sinais de degradação percebidos tarde reduzem a disponibilidade dos ativos.
Analisar somente uma medição também é insuficiente: uma recomendação precisa
explicar a condição observada, mostrar casos comparáveis e apontar a evidência
documental que sustenta cada ação sugerida.

O projeto entrega as fronteiras técnicas dessa jornada com foco em
reprodutibilidade, falha segura e proteção dos materiais fornecidos. O runtime
exige a escolha explícita entre `synthetic_demo` e `artifacts`; o primeiro é o
modo público do Compose e da demonstração AWS, e o segundo só aceita um conjunto
local integralmente identificado e autorizado.

## Estado atual

| Capacidade | O que existe | Limite que importa |
| --- | --- | --- |
| API | FastAPI, contrato OpenAPI v1, cinco estados de análise, dois modos explícitos, health checks, correlation ID e erros sanitizados. | O runtime local/offline não autentica; no perfil AWS, a HTTP API exige JWT Cognito. O repositório não distribui artefatos privados nem configuração operacional. |
| Dados | Pipeline local auditado, contrato de 18 features, split temporal e checker determinístico. | A fonte e os derivados por registro permanecem locais e ignorados; a CI usa somente fixtures sintéticas. |
| Modelo | Busca k-NN v3 determinística de históricos semelhantes, política operacional exata e abstenção. | O voto sugere uma condição candidata; não é probabilidade, classificação aprovada ou automação. |
| Documentos e RAG | Extração local rastreável, ciclo de sete estados, recuperação governada, contrato de geração e guardrails pré/pós-provider. | A API registra metadados, não recebe bytes; o embedding e o provider padrão são fakes e não provam qualidade semântica. |
| Persistência | Adapters em memória e PostgreSQL/pgvector, UoW e migrações reversíveis. | O runtime offline usa memória; derivados reais não são instalados automaticamente. |
| Web | Painel responsivo de análise e gestão documental em módulos ESM, modo offline sintético dos cinco outcomes, contratos derivados do OpenAPI v1 e testes Node/Chromium. | Local/offline permanecem sem login; a origem AWS exata implementa Cognito Authorization Code + PKCE e JWT somente em memória. A prova live continua na SEN-74. |
| AWS | Perfil Terraform efêmero e workflows manuais protegidos, validados offline; preflight e OIDC do workflow de deploy em modo foundation passaram no run `32725423445`. | O controlador parou antes do Terraform; state e Budget permaneceram ausentes, sem recursos gerenciados pelo perfil, plan remoto, `apply`, deploy, smoke ou teardown. |

Nos environments AWS, região, AZ e domínio são variáveis (`vars`); account ID,
bucket de state, certificado e role exclusiva são segredos (`secrets`), assim como
e-mail do Budget e token de smoke. A migração externa dos quatro identificadores
já foi preparada; as variáveis legadas equivalentes permanecem temporariamente
para rollback até a validação do hotfix. Os workflows referenciam somente os
secrets e nenhum deles aparece antes da action OIDC.

As afirmações públicas usam quatro rótulos:

- **implementado**: comportamento presente em código e coberto por teste;
- **medido**: resultado de uma execução identificada, com ambiente e limites;
- **estimado**: cálculo baseado em hipóteses explícitas, não observação;
- **futuro**: capacidade ausente, que não deve ser apresentada como entrega.

## Quickstart offline

Pré-requisitos: Git, Python `>=3.13,<3.14`, uv, Node.js `>=22,<23`, Corepack,
pnpm `10.15.1` e Docker Compose v2. O primeiro setup pode baixar dependências;
depois de instaladas, o smoke e a demonstração abaixo não acessam AWS,
providers pagos nem materiais locais protegidos.

```powershell
git clone https://github.com/HiRenan/senai-prescriptive-maintenance.git
Set-Location senai-prescriptive-maintenance
git switch develop
uv run --frozen poe setup
uv run --frozen poe check
uv run --frozen poe smoke
uv run --frozen poe golden-e2e
```

- `check` executa formatação somente leitura, lint, tipagem estrita, testes e
  as verificações da web;
- `smoke` valida runtimes, Compose e liveness/readiness reais em loopback, sem
  iniciar serviços;
- `golden-e2e` percorre por HTTP os cinco estados do produto e o ciclo
  documental com dados e providers inteiramente sintéticos.

O [guia de scripts](scripts/README.md) detalha smoke, demonstração e benchmark;
a [infraestrutura local](infra/README.md) cobre os caminhos opcionais com
contêineres.

## Executar a API

Para iniciar somente a API com memória e sem dependência externa:

```powershell
$env:PRESCRIPTIVE_MAINTENANCE_ENVIRONMENT = "offline"
$env:PRESCRIPTIVE_MAINTENANCE_PERSISTENCE_BACKEND = "memory"
$env:PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE = "synthetic_demo"
uv run --frozen uvicorn prescriptive_maintenance.main:app `
  --host 127.0.0.1 --port 8000
```

Os endpoints operacionais ficam em `GET /health/live` e
`GET /health/ready`. O contrato de negócio, os endpoints e o ciclo documental
estão no [README do backend](apps/api/README.md); o snapshot OpenAPI canônico está em
[`apps/api/openapi/v1.json`](apps/api/openapi/v1.json).

## Comandos canônicos

Todos os comandos partem da raiz. Somente `format` reescreve código.

| Comando | Finalidade |
| --- | --- |
| `uv run --frozen poe setup` | Sincroniza os workspaces pelos locks e instala hooks. |
| `uv run --frozen poe format` | Aplica correções seguras e formata Python. |
| `uv run --frozen poe check` | Executa format-check, lint, typecheck, testes e as verificações da web. |
| `uv run --frozen poe hooks` | Executa todos os hooks em todos os arquivos. |
| `uv run --frozen poe web-contract` | Gera o contrato web a partir do snapshot OpenAPI v1. |
| `uv run --frozen poe web-test` | Executa os testes essenciais do fluxo do painel. |
| `uv run --frozen poe web-browser-test` | Valida offline, teclado, foco e reflow em Chromium. |
| `uv run --frozen poe failure-matrix` | Exercita falhas P0/P1 e audita o histórico público. |
| `uv run --frozen poe smoke` | Valida a aplicação offline e o Compose, sem iniciar serviços. |
| `uv run --frozen poe smoke --with-artifacts` | Compõe somente derivados locais já aprovados; ausência é reportada como indisponível/skip. |
| `uv run --frozen poe golden-e2e` | Executa a demonstração sintética ponta a ponta. |
| `uv run --frozen poe services-up` | Inicia somente PostgreSQL/pgvector local. |
| `uv run --frozen poe applications-audit` | Audita contextos e builders das imagens. |
| `uv run --frozen poe applications-build` | Constrói as imagens da API e do painel web. |
| `uv run --frozen poe applications-up` | Inicia PostgreSQL, API e web localmente. |
| `uv run --frozen poe services-down` | Remove contêineres e rede, preservando o volume. |

Para validar a topologia completa:

```powershell
uv run --frozen poe applications-audit
uv run --frozen poe applications-build
uv run --frozen poe applications-up
uv run --frozen poe smoke --with-services --with-applications
uv run --frozen poe services-down
```

O painel fica em `127.0.0.1:3000` e localiza a API por `API_BASE_URL`, cujo
padrão é `http://127.0.0.1:8000` e cujo valor no Compose é `http://api:8000`. O
navegador chama sempre a mesma origem da página: o processo web encaminha a
análise e somente as seis operações documentais publicadas no contrato, então a
API não precisa de exceção de CORS. O [README do painel](apps/web/README.md)
descreve os fluxos, os contratos gerados e os estados apresentados.

## Arquitetura

O backend é um monólito modular em `apps/api`; `apps/web` serve o painel de
análise e gestão documental e o proxy de mesma origem, sem framework, bundler
nem etapa de build.
PostgreSQL/pgvector apoia o perfil local, e os artefatos reais de dados, modelo
e documentos continuam fora do Git. A factory HTTP não descobre esses
artefatos: `artifacts` exige o caminho e o SHA-256 de um
manifesto local aprovado que vincula todas as identidades antes de aceitar uma
análise.

Os [diagramas lógico, local e AWS](docs/architecture/diagrams.md) marcam
explicitamente essa separação. O
[inventário técnico](docs/architecture/README.md) relaciona cada componente ao
código que o comprova.

```text
apps/api       API, domínio, dados, modelo, recuperação, geração e persistência
apps/web       painel de análise e gestão documental e processo que o serve
data           manifesto, fixtures sintéticas e derivados públicos permitidos
docs           arquitetura, cards, decisões, runbooks e evidências
infra          Compose local e perfil Terraform AWS demo
scripts        automação exposta pelo Poe
```

## Perfis de execução

| Perfil | Backend permitido | Uso atual |
| --- | --- | --- |
| `offline` | somente `memory` | Smoke, golden set e execução sem dependências externas. |
| `local` | `memory` ou `postgres` | Desenvolvimento; o Compose seleciona PostgreSQL. |
| `aws` | `memory` ou `postgres` | Contrato de configuração; o perfil Terraform demo seleciona `memory`. |

`memory` proíbe URL de banco; `postgres` exige
`PRESCRIPTIVE_MAINTENANCE_DATABASE_URL`. A configuração falha no startup quando
há campo ausente, extra ou combinação incoerente. `.env.example` usa valores
fictícios exclusivos de desenvolvimento e `.env` permanece ignorado.

`PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE` também é obrigatório. `synthetic_demo`
proíbe referências de artefatos. `artifacts` exige
`PRESCRIPTIVE_MAINTENANCE_ANALYSIS_ARTIFACTS_MANIFEST` e
`PRESCRIPTIVE_MAINTENANCE_ANALYSIS_ARTIFACTS_MANIFEST_SHA256`; ausência,
corrupção ou incompatibilidade deixam a readiness e as rotas de análise em 503,
sem trocar para o demo. Toda resposta HTTP informa somente `X-Analysis-Mode`
com um dos dois valores fechados. O protocolo e o manifesto estão documentados
na [validação do runtime](docs/validation/analysis-runtime.md).

## Dados, modelo e RAG

Os materiais fornecidos não são redistribuídos. O repositório contém somente
identidades públicas de integridade, agregados sanitizados aprovados e fixtures
inteiramente sintéticas. Fontes, extrações, Parquets, vetores, modelos,
mapeamentos e relatórios por registro ficam em destinos locais ignorados.

Fontes canônicas para avaliação:

- [data card do pipeline tabular](docs/data/banner-data-card.md);
- [model card do k-NN temporal v3](docs/model-cards/temporal-knn-v3.md);
- [RAG card da composição prescritiva](docs/rag/prescriptive-rag-card.md).

Resultados **medidos** incluem 166.796 linhas reconciliadas no pipeline local e
24.768 linhas no holdout temporal do modelo. No diagnóstico operacional
pós-hoc, a candidata pré-abstenção atingiu 97,3756% de acurácia bruta, abaixo
dos 98,6999% da baseline trivial que marca todas as linhas como problema. Entre
as aceitas, a acurácia de 96,9928% também ficou abaixo dos 97,6938% da mesma
baseline no recorte. O único sinal acima do trivial está na leitura balanceada
e nos recalls: a candidata obteve 59,4426% de acurácia balanceada e 20,4969% de
recall operacional, contra 50% e 0% da baseline constante, mas isso continua
insuficiente. A cobertura foi 39,7408%; entre as linhas aceitas, o recall
operacional foi 22,4670%. A acurácia bruta inclui linhas depois abstidas, é
dominada por problemas e não representa o comportamento final. A
[avaliação exata histórica](docs/validation/model-evaluation.md) permanece como
diagnóstico secundário, e a
[correção da SEN-78](docs/validation/model-evaluation-v2.md) registra fórmulas e
denominadores. Nenhuma medição é métrica de produção ou aprova automação.

O custo AWS de USD 2,72 com contingência para uma janela de oito horas é uma
**estimativa**, não gasto observado. As hipóteses, a data de referência e a
ausência de execução live estão no
[relatório AWS](docs/validation/aws-demo-evidence.md).

## Segurança

O runtime local não possui autenticação nem deve ser exposto publicamente.
Logs HTTP usam somente campos permitidos e não registram payload, query,
conteúdo documental, prompt, credencial ou texto de exceção. O ciclo documental
e a recuperação validam integridade e vigência, mas esses controles não provam
correção semântica de diagnóstico ou prescrição.

- [Política de segurança](SECURITY.md)
- [Threat model](docs/security/threat-model.md)

Vulnerabilidades devem ser comunicadas de forma privada conforme
[`SECURITY.md`](SECURITY.md).

## Limitações e trabalho futuro

Não estão implementados:

- regras operacionais completas ou autorização automática de manutenção;
- ingestão contínua e orquestração do pipeline pela aplicação;
- upload, armazenamento ou validação de bytes documentais pela API;
- artefatos operacionais distribuíveis, embedding semântico aprovado, pgvector
  preenchido e provider de geração real habilitado;
- autenticação e autorização fora do perfil AWS demo, rate limiting e operação
  de produção;
- infraestrutura AWS aplicada, plan remoto, deploy, smoke ou teardown live.

Esses itens são futuro, não compromisso desta versão.

## Documentação

O [índice técnico](docs/README.md) organiza arquitetura, API, cards, segurança,
runbooks, ADRs e relatórios de validação sem duplicar seus detalhes. GitFlow,
papéis e critérios de revisão estão em [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Acesso e direitos

Este repositório é público exclusivamente para leitura, clone e avaliação pela
banca. A disponibilidade pública não concede autorização para copiar,
modificar, redistribuir ou reutilizar seu conteúdo.

Não há arquivo `LICENSE`, não existe licença implícita de reutilização e todos
os direitos permanecem reservados a Renan Mocelin.
