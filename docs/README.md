# Documentação do projeto

O README raiz apresenta o problema, o estado implementado e o caminho de
execução. Os documentos desta área aprofundam responsabilidades específicas
sem repetir extensamente a mesma orientação.

## Índice

| Documento | Responsabilidade |
| --- | --- |
| [`../README.md`](../README.md) | Orientação inicial, quickstart e limites do produto atual. |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | GitFlow, papéis, branches, commits, pull requests e promoção verificável de releases e hotfixes. |
| [`../SECURITY.md`](../SECURITY.md) | Reporte privado, segredos, vazamentos e práticas proibidas em produção. |
| [`security/threat-model.md`](security/threat-model.md) | Ativos, fronteiras de confiança, riscos P0/P1, controles e riscos residuais. |
| [`adr/README.md`](adr/README.md) | Índice das decisões arquiteturais aceitas. |
| [`architecture/README.md`](architecture/README.md) | Inventário dos componentes que realmente existem. |
| [`architecture/diagrams.md`](architecture/diagrams.md) | Diagramas lógico, local e AWS com estado e limites explícitos. |
| [`../apps/api/README.md`](../apps/api/README.md) | API v1, ciclo documental, perfis, persistência e contratos internos. |
| [`../scripts/README.md`](../scripts/README.md) | Smoke, auditoria pública, OpenAPI e benchmark offline. |
| [`delivery/demo-script-base.md`](delivery/demo-script-base.md) | Roteiro final pré-release da SEN-70: três jornadas, login PKCE validado localmente, contingência offline e aceite temporal técnico cronometrado. |
| [`data/banner-quality-policy.md`](data/banner-quality-policy.md) | Visão derivada da política de qualidade e comparação agregada com a baseline rastreada. |
| [`data/banner-data-card.md`](data/banner-data-card.md) | Proveniência, composição, uso, privacidade e limitações do dataset canônico. |
| [`validation/foundation-clean-room.md`](validation/foundation-clean-room.md) | Evidências históricas da validação da Foundation em clone público limpo e adendo sobre o fluxo de promoção. |
| [`validation/public-repository-clean-room.md`](validation/public-repository-clean-room.md) | Auditoria final SEN-71 da entrega pública em clone HTTPS anônimo, completo e isolado. |
| [`validation/data-pipeline.md`](validation/data-pipeline.md) | Encerramento da EPIC 2: decisões, gates e evidências sanitizadas do pipeline canônico SEN-41. |
| [`validation/document-pipeline.md`](validation/document-pipeline.md) | Encerramento do recorte MVP da SEN-4: extração, indexação, ciclo de vida e recuperação documental governada. |
| [`validation/dynamic-rag-e2e.md`](validation/dynamic-rag-e2e.md) | Roteiro e evidência sintética da jornada dinâmica de chunk, aprovação, ranking e citação SEN-77. |
| [`validation/knn-baseline.md`](validation/knn-baseline.md) | Relatório histórico da baseline k-NN SEN-42, preservado como evidência da Epic 3. |
| [`validation/knn-abstention.md`](validation/knn-abstention.md) | Relatório histórico da política de abstenção do k-NN v2 na SEN-51. |
| [`model-cards/temporal-knn-v2.md`](model-cards/temporal-knn-v2.md) | Model card histórico do motor temporal k-NN v2. |
| [`model-cards/temporal-knn-v3.md`](model-cards/temporal-knn-v3.md) | Estado atual, política operacional, resultado, riscos e artefatos do k-NN v3. |
| [`rag/prescriptive-rag-card.md`](rag/prescriptive-rag-card.md) | Contratos, guardrails, evidências disponíveis e limites da composição RAG. |
| [`validation/model-evaluation.md`](validation/model-evaluation.md) | Relatório histórico congelado da avaliação exata SEN-53. |
| [`validation/model-evaluation-v2.md`](validation/model-evaluation-v2.md) | Correção da Epic 3 na SEN-78: objetivo operacional, leitura seletiva e evidência v3. |
| [`validation/analysis-benchmark.md`](validation/analysis-benchmark.md) | Método, métricas, rastreabilidade e limites do benchmark local sintético SEN-65. |
| [`validation/prescription-orchestration.md`](validation/prescription-orchestration.md) | Decisões, timeout limitado, metadados, estados e riscos residuais da composição prescritiva SEN-59. |
| [`validation/similarity-index.md`](validation/similarity-index.md) | Contrato, integridade e paridade sintética do índice de similaridade SEN-52. |
| [`validation/analysis-integration.md`](validation/analysis-integration.md) | Binding autorizado, projeção dos cinco estados, persistência e evidências da integração ponta a ponta SEN-46. |
| [`validation/analysis-dashboard.md`](validation/analysis-dashboard.md) | Decisões, disponibilidade da prescrição, cobertura de testes e limites do painel de análise SEN-47. |
| [`validation/analysis-runtime.md`](validation/analysis-runtime.md) | Modos explícitos, manifesto fail-closed, semântica de startup/readiness e evidência HTTP sintética SEN-79. |
| [`validation/aws-demo-evidence.md`](validation/aws-demo-evidence.md) | Arquitetura, inventário, custo e evidências offline sanitizadas do perfil AWS demo SEN-69. |
| [`../infra/aws/demo/README.md`](../infra/aws/demo/README.md) | Perfil Terraform AWS demo, estado não aplicado, custo e teardown. |
| [`../data/README.md`](../data/README.md) | Preparação local, integridade e fronteira dos materiais e fixtures. |

## Idioma

Código, nomes de pacotes, módulos, tarefas, branches, commits e títulos de pull
request são escritos em inglês. Documentação, ADRs, apresentações, instruções,
explicações e descrições de pull request são escritas em português claro.

## Separação de conteúdo

- Código-fonte versionável pertence a `apps/`, `infra/` e `scripts`, conforme a
  responsabilidade de cada área.
- Materiais fornecidos permanecem fora do Git e, quando usados localmente,
  ficam em `data/raw/original/`.
- Fixtures públicas ficam em `data/fixtures/` e devem ser inteiramente
  sintéticas, sem reprodução dos materiais originais.
- Dados intermediários ou processados, caches, builds e demais artefatos
  gerados ficam somente nos caminhos ignorados pelo Git.
- Experimentos ficam em `experiments/` e não constituem código de produção.

Novos módulos devem respeitar os limites do monólito modular e ser adicionados
somente quando a tarefa responsável definir comportamento e critérios
verificáveis. Visões futuras devem estar identificadas como não implementadas;
o inventário de arquitetura descreve apenas o que pode ser comprovado no
repositório.
