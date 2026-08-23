# Integração da análise prescritiva — SEN-46

## Objetivo entregue

`IntegratedAnalysisService` conecta, por injeção explícita, a decisão do modelo,
o índice de similaridade versionado, a recuperação documental governada, os
guardrails de geração e a unidade de trabalho de persistência. A composição usa
o contrato HTTP v1 existente sem alterar o snapshot OpenAPI.

A fábrica padrão `create_app()` continua deliberadamente ligada aos fakes
sintéticos. A integração só entra na rota quando uma instância completa do
serviço é fornecida pelo composition root. Essa separação impede que a simples
importação da aplicação descubra artefatos, credenciais ou serviços externos.

## Binding autorizado e fail-closed

Uma `AnalysisRuntimeAuthorization` identifica por SHA-256 um conjunto exato de:

- dataset, modelo e índice de similaridade;
- política de recuperação e mapeamento entre falhas e documentos;
- prompt, provider e timeout;
- política de projeção de prioridade.

O binding da recuperação não é uma declaração repetida pelo chamador. O
`GovernedKnowledgeRetrievalService` obtém a policy validada que mantém
internamente e a identidade da `FaultKnowledgeMapping` efetivamente carregada
pelo `ApprovedKnowledgeRetrievalService`. A orquestração copia esse snapshot no
construtor, e a integração o compara com a autorização antes de aceitar qualquer
jornada, inclusive `normal`, `out_of_distribution` e falha sem chave documental.

Quando existe uma recuperação, policy e mapeamento são conferidos novamente no
resultado antes de qualquer provider. A integração repete a validação na saída
da orquestração. Essa defesa em profundidade evita geração ou persistência com
uma dependência que mudou depois da composição.

O modelo e o índice também precisam concordar em quantidade, identidade, rank,
classe e distância de cada vizinho. As distâncias devem ser finitas e iguais com
tolerâncias relativa e absoluta de `1e-6`. Qualquer divergência torna a porta
indisponível; não há fallback para um ranking diferente.

## Projeção dos cinco estados

| Estado público | Condição segura | Provider | Citações públicas |
| --- | --- | --- | --- |
| `normal` | decisão normal autorizada | não chamado | vazias |
| `documented_fault` | falha, evidência vigente, geração aceita e prioridade explicitamente mapeada | uma chamada limitada | somente a união das evidências citadas pelo diagnóstico gerado e pelas prescrições incluídas |
| `undocumented_fault` | falha sem chave, classe não mapeada ou ausência de evidência elegível | não chamado | vazias |
| `out_of_distribution` | abstenção do modelo | não chamado | vazias |
| `degraded` | dependência opcional, geração ou projeção não produz resultado seguro | zero ou uma chamada | vazias, exceto quando uma geração aceita usou evidência mas a projeção pública de prioridade falhou |

Diagnóstico e vizinhos permanecem disponíveis em `degraded` quando o modelo os
produziu de forma válida. A prioridade não é inferida: uma falha ausente da
`PrescriptionProjectionPolicy` degrada sem fallback. Resumo e ações vêm apenas
dos campos validados da geração, sem truncar texto para fazê-lo caber no contrato.

As citações da resposta não representam todo o contexto recuperado. Elas são
somente o subconjunto efetivamente usado pela geração aceita, preservado na
ordem da recuperação. Já a persistência registra todas as referências
recuperadas, sem conteúdo, para permitir auditar o contexto que foi oferecido ao
guardrail.

## Persistência e correlação

A jornada prepara a projeção pública, duas cópias defensivas e os metadados antes
de abrir a unidade de trabalho. O commit ocorre antes da publicação no cache
local. Assim:

1. falha de projeção ou metadados não inicia persistência;
2. falha transacional não publica `GET /analysis/{analysis_id}` no processo;
3. falha de cache depois do commit emite `analysis_cache_unavailable`, mas não
   converte um registro já persistido em uma resposta 503 enganosa;
4. `analysis_completed` registra `cache_published: true` no caminho normal e
   `cache_published: false` quando apenas a publicação local falha.

Os estágios `model`, `authorization`, `orchestration`, `projection`, `metadata`,
`persistence` e `cache` usam o mesmo correlation ID. Os logs contêm somente IDs,
hashes e estados allowlisted; features, conteúdo documental, prompt, output e
texto de exceção não são registrados.

O `GET` completo continua process-local e desaparece após reinício. O banco
preserva metadados de auditoria, não a resposta pública completa. Documentos,
versões e chunks referenciados precisam existir antes do commit da análise.

## Validação reproduzível

Os testes inteiramente sintéticos cobrem os cinco estados, falha e timeout do
provider, ausência documental, projeção sem fallback, subconjunto e união de
citações, corrupção de identidade/rank/classe/distância, divergência do binding
em todas as jornadas, rollback, classificação dos estágios de observabilidade,
concorrência do cache e round-trip PostgreSQL opcional.

```powershell
uv run --frozen pytest apps/api/tests/test_analysis_integration.py --no-cov -q
uv run --frozen poe check
uv run --frozen python scripts/generate_openapi.py --check
```

O teste PostgreSQL usa um schema descartável somente quando
`PRESCRIPTIVE_MAINTENANCE_TEST_DATABASE_URL` está explicitamente configurada.
Nenhum teste acessa os materiais originais, Bedrock ou a rede.

## Limites e decisão operacional

- nenhum artefato de modelo real está autorizado por esta composição;
- a baseline real avaliada permanece somente como demonstração de similaridade
  e apoio humano, não como classificador, automação ou autorização de manutenção;
- o provider padrão de teste é sintético e Bedrock permanece desabilitado até
  existir configuração e autorização operacional explícitas;
- a dupla conferência do ranking prioriza integridade e será medida pelo
  benchmark da camada antes de qualquer otimização;
- não há fila distribuída, retry automático, cancelamento cooperativo do
  provider ou armazenamento da resposta pública completa.

Esses limites preservam a demonstração ponta a ponta sem transformar métricas
insuficientes ou uma dependência opcional em decisão industrial automática.
