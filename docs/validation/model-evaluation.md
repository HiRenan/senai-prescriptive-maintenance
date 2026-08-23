# Avaliação reprodutível do motor temporal — SEN-53

## Escopo e decisão

A SEN-53 adiciona um harness offline e determinístico para avaliar o motor k-NN
v2 sobre um holdout temporal. A suíte versionada usa somente fixtures
sintéticas; a execução do harness com os derivados reais aprovados gerou apenas
um JSON sanitizado em `data/processed/`, ignorado pelo Git. Nenhum dos oito
materiais originais foi aberto, copiado ou reproduzido.

O resultado não aprova o motor como classificador ou automação. Ele permanece
uma baseline auditável de similaridade para demonstração com revisão humana.

## Protocolo pré-especificado

Antes da primeira abertura do holdout nesta tarefa, o harness:

1. validou manifesto, gates, dataset, schema, modelo k-NN v2 e índice;
2. vinculou hashes físicos de treino, calibração e teste;
3. confirmou que os limiares vieram da validação, e não do teste;
4. serializou canonicamente o plano, calculou seu SHA-256 e revalidou bytes,
   hash e `plan_id` antes de chamar o opener do holdout;
5. congelou `k=5`, distância euclidiana, busca exata em lote, desempates,
   recortes, métricas, amostragem de paridade, warmup e benchmark.

O plano não oferece argumento de tuning. A avaliação abre o Parquet por um
único descritor regular limitado, calcula o hash sobre os mesmos bytes e faz o
parse a partir desse snapshot imutável. Uma falha de freeze ou de verificação do
plano ocorre antes dessa abertura. O relatório local não sobrescreve evidência
existente.

| Identidade congelada | Valor |
| --- | --- |
| Plano | `evaluation_plan_v1_b564aaa3efaed8b1d2ca0cebc1129e75` |
| SHA-256 da materialização do plano | `3abe695f9d3412e472bb805d96c98bb82c338b068783fbdc6017ccf6f57eb66d` |
| Dataset | `a0c1a7c5141b9b3a8856ad9af458fe09baa7fa04f6b96316ecb52a6d6b426327` |
| Modelo | `model_knn_v2_88e8ea9da70f90e7fa1eeae7461d9192` |
| Índice | `similarity_index_v1_62099354d00a450b0783fb82de2807c9` |
| Partição de calibração | `validation` |
| Holdout | `test`, 24.768 linhas |

O índice é uma âncora de identidade e compatibilidade neste protocolo. Para
evitar 24.768 chamadas O(N) com overhead por consulta sem alterar a semântica,
o harness calcula as distâncias exatas em lotes limitados a 64 MiB contra o
snapshot defensivo do mesmo modelo. A ordenação total usa distância crescente e
referência opaca crescente. Uma auditoria de 16 posições uniformemente
espaçadas confirmou classe candidata, causa de abstenção e ranking contra a
predição direta do modelo.

## Definições e denominadores

Para um escopo de linhas `S`, verdade `y_i`, candidata por votação `c_i` e
ranking dos cinco vizinhos `r_i`:

- top-1 da candidata: `Σ[c_i = y_i] / |S|`;
- Hit@1: `Σ[r_i1 = y_i] / |S|`;
- Hit/Recall@5 categórico: `Σ[y_i ∈ r_i] / |S|`; como há uma relevância
  categórica por consulta, é um recall binário por linha;
- MRR@5: média de `1/rank` da primeira ocorrência de `y_i`, ou zero se ausente;
- baseline majoritária: predição constante da classe mais frequente do treino,
  com empate por slug privado;
- cobertura: linhas sem abstenção divididas por `|S|`;
- acurácia seletiva: candidatas corretas divididas apenas pelas linhas aceitas.

As métricas são calculadas para todas as linhas e, separadamente, para o
subconjunto cuja classe existe no treino. Esse segundo recorte não remove o
primeiro e não transforma o modelo em open-set.

## Resultado congelado

| Métrica | Todas as linhas (n=24.768) | Classe conhecida (n=225) |
| --- | ---: | ---: |
| Candidata top-1 | 49/24.768 = 0,001978 | 49/225 = 0,217778 |
| Hit@1 | 49/24.768 = 0,001978 | 49/225 = 0,217778 |
| Hit/Recall@5 | 49/24.768 = 0,001978 | 49/225 = 0,217778 |
| MRR@5 | soma 49 / 24.768 = 0,001978 | soma 49 / 225 = 0,217778 |
| Baseline majoritária | 0/24.768 = 0 | 0/225 = 0 |
| Cobertura | 9.843/24.768 = 39,7408% | 167/225 = 74,2222% |
| Abstenção | 14.925/24.768 = 60,2592% | 58/225 = 25,7778% |
| Acurácia seletiva | 49/9.843 = 0,4978% | 49/167 = 29,3413% |

As 14.925 abstenções globais se dividem em 12.652 por distância e 2.273 por
votação inconclusiva; nenhuma ocorreu por suporte raro. No recorte conhecido,
são 4 por distância e 54 por votação.

A comparação primária não sustenta superioridade. A classe majoritária do
treino não aparece neste teste e, portanto, produz uma baseline zero pouco
informativa. A candidata `k=5` e o primeiro vizinho acertam exatamente as mesmas
49 linhas; Hit@5 e MRR também mostram que as posições 2–5 não recuperam labels
verdadeiros adicionais.

## Diagnóstico pós-hoc, sem ajuste

Depois do relatório congelado, a decomposição agregada mostrou que 24.543
linhas pertencem a classes ausentes do treino. Destas, 9.676 foram aceitas:
39,4247% de falso aceite open-set. Esse cálculo é diagnóstico pós-hoc rotulado,
não um novo objetivo usado para escolher ou alterar threshold. Nenhum parâmetro,
ranking, escopo ou seleção foi ajustado e o holdout não foi reexecutado.

Cada `target_slug` é tratado como classe one-to-one. O contrato não representa
hierarquia, equivalência ou proximidade semântica entre labels possivelmente
relacionados; portanto, uma confusão entre classes não pode ser reinterpretada
como acerto parcial.

A política detecta novidade geométrica, mas uma classe semanticamente inédita
pode ocupar uma região conhecida. Cobertura menor, isoladamente, não torna a
decisão segura. O modelo não deve produzir diagnóstico automático nem ação de
manutenção.

## Benchmark e metodologia

O lote exato das 24.768 consultas levou 53,537451 s. Em seguida, com cache
aquecido pela avaliação completa, cinco warmups precederam 64 consultas
uniformemente espaçadas: p50 de 53,950300 ms e p95 de 82,665655 ms. Esses tempos
medem consultas individuais ao k-NN em memória, não pgvector, rede ou API.

| Ambiente | Valor |
| --- | --- |
| Sistema | Windows 11 AMD64 |
| CPU lógica | 12 |
| Python | 3.13.5 |
| NumPy | 2.5.2 |
| pandas | 3.0.5 |
| scikit-learn | 1.9.0 |
| Pico vitalício de working set | 545.427.456 bytes (520,160156 MiB) |
| Working set máximo observado antes do lote | 348.889.088 bytes |
| Aumento do high-water mark | 196.538.368 bytes (187,433594 MiB) |
| Pico complementar do `tracemalloc` | 202.129.528 bytes (192,765739 MiB) |

O working set é o maior valor observado na vida do processo, não uma amostra
instantânea exclusiva da função. O delta mostra quanto o high-water mark cresceu
durante o intervalo; `tracemalloc` é apenas complementar porque não enxerga
todos os buffers nativos de NumPy/BLAS. Em plataforma sem RSS/working set
portável, o relatório declara indisponibilidade em vez de substituir essa
métrica por `tracemalloc`.

O ranking expandiu candidatos numericamente próximos ao limite em 1.411 linhas
e reaplicou a distância canônica e o desempate opaco. Essa contagem descreve o
mecanismo interno do harness, não qualidade preditiva nem o mesmo contador de
implementações anteriores. A auditoria direta passou nas 16/16 posições
pré-especificadas.

## Estado histórico do holdout

O protocolo da SEN-53 foi materializado antes da única abertura feita por esta
tarefa, mas o holdout não era historicamente virgem: a SEN-42 já havia publicado
agregados do mesmo `test.parquet`, incluindo as contagens total/conhecida e a
acurácia conhecida. O modelo v2 e sua política de abstenção foram criados depois
dessa exposição; seus thresholds permanecem vinculados exclusivamente à
validação. Ainda assim, esta medição é uma reavaliação confirmatória de teste
previamente observado, e não uma estimativa independente de generalização.

Uma avaliação futura defensável precisa registrar objetivo, custo, limiares e
critérios antes de abrir uma nova janela temporal não observada. Os resultados
atuais não devem ser usados para tuning retroativo.

## Reprodução segura

O CLI exige caminhos explícitos para derivados aprovados e não possui flags de
hiperparâmetro. O destino do relatório deve ser novo e permanecer ignorado:

```powershell
$dataset = "<diretorio-derivado-aprovado>"
$model = "<artefato-knn-v2-aprovado>"
$index = "<indice-versionado-aprovado>"
$report = "data/processed/sen-53-evaluation/evaluation-report.json"

uv run --frozen python -m prescriptive_maintenance.modeling.evaluation `
  --dataset-manifest "$dataset/manifest.json" `
  --holdout "$dataset/test.parquet" `
  --model-artifact $model `
  --index-artifact $index `
  --report-output $report
```

A evidência real local teve SHA-256
`b4680a3f5f68957f615168fc01dea95170d729c5df3e67880f60a482ce36b4b3`.
Ela contém somente plano, identidades técnicas, agregados, hardware e limitações,
sem path, label, linha, feature ou identificador da fonte. O JSON permanece
ignorado; a suíte pública cobre o mesmo protocolo com dados inteiramente
sintéticos.

O resumo de decisão está no
[model card do k-NN v2](../model-cards/temporal-knn-v2.md).
