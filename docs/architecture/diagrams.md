# Diagramas da arquitetura atual

- Baseline documental: `origin/develop` em `7a05487`
- Data de referência: 2026-08-23
- Escopo: componentes versionados, incluindo o painel de análise servido por
  `apps/web`; capacidades ainda não integradas não estão representadas

As setas contínuas representam o caminho executável no contexto indicado.
Setas tracejadas representam uma composição disponível somente por injeção ou
um recurso declarativo ainda não aplicado. “Local” e “AWS” são perfis
diferentes; o diagrama AWS não descreve o ambiente Compose.

## Diagrama lógico

```mermaid
flowchart LR
  Client[Cliente HTTP] --> API[FastAPI e contrato v1]
  API --> Default[Serviços sintéticos padrão]
  Default --> Result[Cinco estados públicos]

  API -. injeção explícita e autorização exata .-> Integrated[IntegratedAnalysisService]
  Integrated --> Model[ModelPort]
  Integrated --> Similarity[SimilarityIndexPort]
  Model --> Orchestration[Orquestração prescritiva]
  Similarity --> Integrated
  Orchestration --> Retrieval[Recuperação governada]
  Retrieval --> Lifecycle[Ciclo documental e snapshots vigentes]
  Retrieval --> Chunks[Chunks e mapping externos]
  Orchestration --> Guardrails[Guardrails RAG]
  Guardrails --> Provider[Provider injetado]
  Integrated --> UoW[Unit of Work]
  UoW --> Memory[(Memória)]
  UoW -. backend postgres .-> Postgres[(PostgreSQL e pgvector)]
  Integrated --> Result

  subgraph Preparation[Preparação local fora do runtime HTTP]
    Tabular[Fonte tabular autorizada] --> AuditData[Porta read-only e fingerprint pre/post]
    AuditData --> Dataset[Dataset canônico local ignorado]
    Dataset --> ModelArtifact[Modelo e índice locais ignorados]

    Documents[Documentos autorizados] --> AuditDocs[Extração local read-only]
    AuditDocs --> Extracted[Extrações locais ignoradas]
    Extracted --> Chunking[Chunking e indexação]
  end

  ModelArtifact -. não descoberto automaticamente .-> Model
  Chunking -. configuração e carga explícitas .-> Chunks
```

O caminho sólido `API → serviços sintéticos` é o runtime entregue por padrão.
A composição integrada é código executável e testado, mas nenhum dataset,
modelo, índice, mapping documental ou provider real é descoberto ou autorizado
automaticamente. O resultado público nunca transporta feature, texto
documental, prompt ou output bruto.

## Diagrama local

```mermaid
flowchart TB
  Developer[Pessoa desenvolvedora]

  subgraph Offline[Perfil offline]
    Uvicorn[Uvicorn em 127.0.0.1:8000]
    OfflineMemory[(Memória do processo)]
    Uvicorn --> OfflineMemory
  end

  subgraph Compose[Perfil local em Docker Compose]
    LocalAPI[API em 127.0.0.1:8000]
    Database[(PostgreSQL 17 e pgvector 0.8.6)]
    Web[Node em 127.0.0.1:3000; painel de análise e proxy]
    LocalAPI -->|conexão interna e readiness| Database
    Web -->|POST /api/analysis encaminhado| LocalAPI
  end

  Developer -->|poe smoke| Uvicorn
  Developer -->|poe applications-up| Compose
  LocalClient[Cliente local] --> LocalAPI
  LocalClient --> Web
  Protected[Materiais e derivados locais ignorados] -. excluídos dos contextos OCI .-> Compose
```

No perfil offline, a readiness não consulta dependências externas. No Compose,
as três portas host ficam presas a loopback; API e web usam filesystem raiz
read-only, `/tmp` efêmero, nenhuma capability Linux e
`no-new-privileges`. O serviço web responde à liveness, serve o painel e
encaminha somente `POST /api/analysis` para a API, de modo que o navegador
permanece na mesma origem da página. O smoke não inicia nem encerra
contêineres.

## Diagrama AWS

> Estado: **planejado e validado offline**. Nenhum bloco abaixo comprova recurso
> existente, conta configurada ou execução live.

```mermaid
flowchart LR
  subgraph External[Pré-requisitos externos não criados pelo módulo]
    GitHub[GitHub environments e reviewers]
    OIDC[OIDC e três roles]
    State[Bucket S3 de state]
    DNS[DNS e certificado ACM us-east-1]
    GitHub --> OIDC
  end

  Viewer[Cliente HTTPS] --> CF[CloudFront com OAC]
  CF --> Frontend[S3 privado; começa vazio]
  DNS -. alias e certificado .-> CF

  Viewer -->|JWT| APIGW[API Gateway HTTP API]
  Cognito[Cognito user pool] --> APIGW

  subgraph VPC[VPC privada single-AZ]
    APIGW --> VPCLink[VPC Link]
    VPCLink --> CloudMap[Cloud Map SRV]
    CloudMap --> ECS[ECS Fargate API]
    ECS --> Endpoints[Endpoints privados ECR, Logs, SQS e S3]
  end

  Endpoints --> ECR[ECR; começa vazio]
  Endpoints --> Documents[S3 documentos]
  Endpoints --> Artifacts[S3 artefatos]
  Endpoints --> Queue[SQS ingestão]
  Queue --> DLQ[DLQ]
  NoWorker[Contrato de worker; sem executável] -. consome no futuro .-> Queue

  OIDC -. plan, deploy e teardown manuais .-> State
  State -. backend parcial .-> VPC
  Budget[Budget e quatro alarmes] -. observam; não interrompem gasto .-> VPC
```

O plano offline auditado contém 73 recursos com ação `create` e usa
`api_desired_count = 0` na fundação. Não há NAT, Internet Gateway, ALB, banco
gerenciado, worker, UI, WAF, alta disponibilidade ou multi-região. Bedrock fica
desabilitado por padrão e habilitá-lo não conecta o adapter Python
automaticamente.

A fonte canônica do perfil é o
[perfil Terraform AWS demo](../../infra/aws/demo/README.md). Estado da prova,
inventário, estimativa e pendências live estão na
[validação AWS](../validation/aws-demo-evidence.md).

## O que os diagramas não prometem

- qualidade preditiva ou semântica;
- aprovação automática de manutenção;
- upload e armazenamento de bytes pela API;
- autenticação no runtime local;
- gestão documental, histórico de análises ou autenticação no painel web;
- deploy ou operação AWS;
- equivalência entre fakes de CI e um provider real.

O [inventário de arquitetura](README.md) é a referência detalhada de componentes
e limites; mudanças futuras devem atualizar os diagramas somente depois de
integradas.
