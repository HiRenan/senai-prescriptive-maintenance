# Avaliação open-set e gate de promoção — SEN-73

## Decisão

O protocolo `temporal-knn-open-set-exact.v3` corrige a semântica das métricas
de ranking, materializa os recortes de classes conhecidas e ausentes do treino
e acrescenta intervalos de Wilson de 95%. O holdout continua sendo apenas uma
medição pós-freeze: ele não escolhe limiar, modelo ou gate.

O campeão k-NN v3 não foi substituído e continua aprovado somente para
demonstração de similaridade com revisão humana. A busca de política open-set na
validação não encontrou uma configuração que atendesse simultaneamente aos
dois limites pré-registrados. A falha bloqueou a escrita de um novo artefato; o
sistema não promove silenciosamente o melhor candidato inviável.

## Gate congelado antes do holdout

`prescriptive_maintenance.modeling.open_set` pesquisa pares determinísticos de
limiar usando apenas treino e validação. Uma política passa somente quando:

- o limite superior de Wilson 95% do falso aceite de classes desconhecidas é
  menor ou igual a 5%; e
- o limite inferior de Wilson 95% da cobertura de classes conhecidas é maior ou
  igual a 50%.

`require_open_set_gate()` produz erro tipado quando algum limite falha. O
relatório sanitizado registra dataset, modelo, hash da validação, contagens,
denominadores, intervalos, quantidade de políticas e justificativa; não contém
linha, feature ou label. A função de auditoria não recebe holdout nem expõe
parâmetro de teste.

Na execução local autorizada, 39.320 políticas foram avaliadas. Nenhuma passou.
O candidato selecionado apenas para explicar a inviabilidade apresentou:

| Métrica de validação | Contagem | Valor | Wilson 95% |
| --- | ---: | ---: | ---: |
| FAR de classe desconhecida | 21.833 / 23.146 | 94,3273% | 94,0219%–94,6180% |
| Cobertura de classe conhecida | 2.000 / 2.000 | 100% | 99,8083%–100% |
| Acurácia seletiva conhecida | 0 / 2.000 | 0% | 0%–0,1917% |

O JSON dessa execução permanece em `data/processed`, ignorado pelo Git. Nenhum
artefato foi escrito e o teste não participou da busca.

## Fórmulas do ranking

Para cada consulta, relevância significa igualdade exata de `target_slug`. O
relatório publica, para `k=1..5`:

- Hit@K: consultas com ao menos um histórico relevante até K divididas pelas
  consultas do recorte;
- Precision@K: históricos relevantes recuperados até K divididos por todos os
  históricos recuperados até K;
- Recall@K: históricos relevantes recuperados até K divididos por todos os
  históricos relevantes disponíveis no treino para cada consulta;
- MRR@K: média do inverso da posição do primeiro histórico relevante, com zero
  quando não há acerto;
- ganho incremental: primeiro acerto que surge exatamente em cada posição de 2
  a 5.

Classes ausentes do treino contam como erro em Hit@K e MRR, mas possuem
denominador zero em Recall@K; o relatório registra separadamente quantas
consultas não tinham relevância disponível. Os testes sintéticos verificam
fórmula, denominador, classe ausente, empate do ranking e ganho incremental.

O corpus atual ainda ranqueia linhas de treino, não ocorrências independentes.
Portanto, Precision@K e Recall@K são identificadas com `unit=training_rows` e
não são apresentadas como métricas por ocorrência. A deduplicação e o contexto
temporal por ocorrência exigem reconstruir o artefato com metadados autorizados;
essa limitação não é ocultada nem reinterpretada como evidência entregue.

## Diagnóstico open-set pós-hoc do holdout

As contagens históricas do mesmo holdout já observado recebem agora definições
e intervalos explícitos:

| Recorte | Métrica | Contagem | Valor | Wilson 95% |
| --- | --- | ---: | ---: | ---: |
| Classe conhecida | cobertura | 167 / 225 | 74,2222% | 68,1338%–79,4974% |
| Classe conhecida aceita | acurácia exata | 49 / 167 | 29,3413% | 22,9624%–36,6493% |
| Classe desconhecida | falso aceite | 9.676 / 24.543 | 39,4247% | 38,8150%–40,0377% |
| Classe desconhecida | rejeição | 14.867 / 24.543 | 60,5753% | 59,9623%–61,1850% |

Esses números não recalibram a política e não constituem nova generalização
independente. A acurácia binária bruta de 97,3756% também continua abaixo da
baseline sempre-problema de 98,6999%; a leitura relevante é a acurácia
balanceada de 59,4426%, com recall operacional de 20,4969%.

## Challengers avaliados sem promoção oportunista

Um Random Forest foi escolhido somente por validação agrupada e depois medido
uma vez no holdout congelado. Obteve 60,0599% de acurácia balanceada e 21,1180%
de recall operacional, contra 59,4426% e 20,4969% do k-NN: ganho de 0,6173 ponto
percentual e apenas dois estados operacionais adicionais. A melhora não
justifica introduzir outra família, artefato e caminho operacional nesta janela
já observada.

Também foi testada distância de Mahalanobis com filtro de regime e voto
ponderado usando somente treino e validação. O Hit@5 exato conhecido permaneceu
em 0%, assim como na alternativa euclidiana desse experimento. Nenhum challenger
foi promovido; preservar o campeão evita regressão disfarçada de novidade.

## Rastreabilidade persistida

Cada análise passa a persistir o `index_id` autorizado e a sequência exata de
`neighbor_ref` usada na resposta. A migração reversível
`analysis_neighbor_traceability` cria apenas metadados opacos e mantém a ordem
do ranking; vetor, feature, label, conteúdo e narrativa continuam proibidos no
banco. O runtime já confere os vizinhos contra modelo, índice e autorização
antes da transação.

Essa mudança permite provar qual índice e quais referências sustentaram uma
análise. O artefato atual ainda não resolve a referência para ocorrência e
instante autorizados; logo, a entrega não afirma essa capacidade como concluída.

## Validação

```powershell
uv run --frozen pytest apps/api/tests/test_open_set.py `
  apps/api/tests/test_model_evaluation.py `
  apps/api/tests/test_persistence.py `
  apps/api/tests/test_analysis_integration.py --no-cov -q
uv run --frozen poe check
uv run --frozen poe hooks
uv run --frozen poe smoke
```

Testes PostgreSQL usam um schema sintético descartável quando
`PRESCRIPTIVE_MAINTENANCE_TEST_DATABASE_URL` está configurada. Nenhuma
verificação abre os oito materiais originais.
