# Model card — motor temporal k-NN v2

## Resumo

O motor é uma baseline de similaridade local, determinística e auditável. Ele
padroniza 18 features com estatísticas ajustadas somente no treino e recupera
cinco vizinhos por distância euclidiana exata. A classe candidata resulta de
votação com desempates totais; uma política versionada pode abster por distância,
votação inconclusiva ou suporte raro.

**Decisão de uso:** não aprovado como classificador, automação ou autorização de
manutenção. O uso aceitável nesta versão é demonstração e recuperação de casos
similares com revisão humana. `support_score` é uma heurística de votos e
distância, não probabilidade nem confiança calibrada.

## Identidade e configuração avaliadas

| Item | Valor |
| --- | --- |
| Dataset canônico | `a0c1a7c5141b9b3a8856ad9af458fe09baa7fa04f6b96316ecb52a6d6b426327` |
| Modelo | `model_knn_v2_88e8ea9da70f90e7fa1eeae7461d9192` |
| Índice vinculado | `similarity_index_v1_62099354d00a450b0783fb82de2807c9` |
| Features | 18, na ordem do contrato canônico |
| Treino | 116.882 linhas |
| Holdout temporal | 24.768 linhas |
| Busca avaliada | exata em memória, euclidiana, `k=5` |
| Limiar de distância | 1,626617974741, calibrado na validação |
| Limiar de margem | 0,0, calibrado na validação |
| Suporte mínimo da classe | 2 exemplos de treino |

O índice operacional foi carregado e vinculado ao mesmo dataset, schema e
modelo por identidade e hash. O benchmark mediu o k-NN exato em memória; não
mediu PostgreSQL/pgvector nem deve ser apresentado como latência desse adapter.

## Resultado temporal

O teste contém forte mudança de suporte: somente 225 de 24.768 linhas possuem
uma classe que existe no treino. Por isso os dois recortes são obrigatórios.

| Métrica | Todas as linhas (n=24.768) | Classe conhecida (n=225) |
| --- | ---: | ---: |
| Candidata top-1 | 49/24.768 (0,1978%) | 49/225 (21,7778%) |
| Hit@1 do vizinho | 49/24.768 (0,1978%) | 49/225 (21,7778%) |
| Hit/Recall@5 | 49/24.768 (0,1978%) | 49/225 (21,7778%) |
| MRR@5 | 0,001978 | 0,217778 |
| Baseline majoritária do treino | 0/24.768 (0%) | 0/225 (0%) |
| Cobertura | 9.843/24.768 (39,7408%) | 167/225 (74,2222%) |
| Acurácia seletiva | 49/9.843 (0,4978%) | 49/167 (29,3413%) |

A baseline majoritária é uma referência particularmente fraca neste corte,
pois sua classe não aparece no teste. A candidata por votação em cinco vizinhos
também não superou a referência trivial 1-NN: ambas acertaram as mesmas 49
linhas, e nenhum label verdadeiro adicional apareceu entre as posições 2–5.

## Abstenção e risco open-set

O motor absteve em 14.925/24.768 linhas (60,2592%): 12.652 por distância e
2.273 por votação inconclusiva. No recorte de classes conhecidas, absteve em
58/225 (25,7778%). Não houve abstenção por suporte raro.

Como diagnóstico pós-hoc rotulado, 9.676 das 24.543 linhas cuja classe não
existe no treino foram aceitas (39,4247%). A distância identifica parte da
novidade geométrica, mas não é um detector confiável de classe inédita quando
ela ocupa uma região já observada. Esse falso aceite open-set é material e
impede uso automático.

## Benchmark curto

Em Windows 11 AMD64, 12 CPUs lógicas, Python 3.13.5, NumPy 2.5.2, pandas 3.0.5
e scikit-learn 1.9.0, a avaliação exata das 24.768 linhas levou 53,537 s. Após
cinco warmups e com cache aquecido pela avaliação completa, 64 consultas
individuais apresentaram p50 de 53,950 ms e p95 de 82,666 ms.

O maior working set observado na vida do processo foi 520,160 MiB; o high-water
mark cresceu 187,434 MiB entre as observações imediatamente anterior e posterior
ao lote. Esse número inclui alocações anteriores do processo e não é um pico
isolado da função. `tracemalloc` observou adicionalmente 192,766 MiB de
alocações rastreáveis, mas não cobre todos os buffers nativos de NumPy/BLAS.

## Limitações e condições de uso

- O mesmo `test.parquet` já teve agregados publicados pela SEN-42 antes desta
  tarefa. O protocolo da SEN-53 foi congelado antes de sua única execução, e os
  limiares v2 usam somente validação, mas o resultado é uma reavaliação de teste
  previamente observado, não uma estimativa independente de generalização.
- A mudança temporal introduz 128 classes que não existem no treino; o modelo
  fechado não pode predizê-las. As classes `target_slug` são one-to-one, sem
  hierarquia ou consolidação semântica de labels possivelmente relacionados.
- Não existe calibração probabilística, estudo de custo de erro, SLA, avaliação
  independente em uma janela posterior ou validação operacional.
- A busca exata é O(N). Os números de memória e latência valem apenas para o
  hardware e o artefato identificados.
- Nenhum label, linha, feature, timestamp ou identificador privado foi
  publicado. Testes versionados usam somente dados sintéticos.

Uma nova decisão de uso exige uma janela temporal realmente intocada,
representatividade de classes futuras, objetivo e custos aprovados antes da
avaliação e um gate explícito para falso aceite open-set. Até lá, toda saída
precisa de revisão humana e não pode disparar manutenção.

O protocolo, as fórmulas, os denominadores e a evidência de reprodução estão no
[relatório de avaliação da SEN-53](../validation/model-evaluation.md).
