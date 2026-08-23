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
| [`adr/README.md`](adr/README.md) | Índice das decisões arquiteturais aceitas. |
| [`architecture/README.md`](architecture/README.md) | Inventário dos componentes que realmente existem. |
| [`data/banner-quality-policy.md`](data/banner-quality-policy.md) | Visão derivada da política de qualidade e comparação agregada com a baseline rastreada. |
| [`validation/foundation-clean-room.md`](validation/foundation-clean-room.md) | Evidências históricas da validação da Foundation em clone público limpo e adendo sobre o fluxo de promoção. |
| [`validation/data-pipeline.md`](validation/data-pipeline.md) | Encerramento da EPIC 2: decisões, gates e evidências sanitizadas do pipeline canônico SEN-41. |
| [`validation/document-pipeline.md`](validation/document-pipeline.md) | Encerramento do recorte MVP da SEN-4: extração, indexação, ciclo de vida e recuperação documental governada. |
| [`validation/knn-baseline.md`](validation/knn-baseline.md) | Decisões, integridade e avaliação temporal sanitizada da baseline k-NN SEN-42. |
| [`validation/knn-abstention.md`](validation/knn-abstention.md) | Política versionada de suporte, novidade e abstenção do k-NN SEN-51, com avaliação temporal agregada. |
| [`model-cards/temporal-knn-v2.md`](model-cards/temporal-knn-v2.md) | Condições de uso, resultado, riscos e decisão do motor temporal k-NN v2. |
| [`validation/model-evaluation.md`](validation/model-evaluation.md) | Protocolo, métricas, benchmark e limitações da avaliação reprodutível SEN-53. |
| [`validation/analysis-benchmark.md`](validation/analysis-benchmark.md) | Método, métricas, rastreabilidade e limites do benchmark local sintético SEN-65. |
| [`validation/prescription-orchestration.md`](validation/prescription-orchestration.md) | Decisões, timeout limitado, metadados, estados e riscos residuais da composição prescritiva SEN-59. |
| [`validation/similarity-index.md`](validation/similarity-index.md) | Contrato, integridade e paridade sintética do índice de similaridade SEN-52. |
| [`validation/analysis-integration.md`](validation/analysis-integration.md) | Binding autorizado, projeção dos cinco estados, persistência e evidências da integração ponta a ponta SEN-46. |
| [`validation/aws-demo-evidence.md`](validation/aws-demo-evidence.md) | Arquitetura, inventário, custo e evidências offline sanitizadas do perfil AWS demo SEN-69. |
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
