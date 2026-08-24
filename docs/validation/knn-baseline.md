# Baseline k-NN determinística — SEN-42

## Escopo validado

A SEN-42 implementa uma baseline local sobre exatamente as 18 features
canônicas, na mesma ordem do contrato OpenAPI v1 e das partições da SEN-41. O
pré-processamento usa `StandardScaler`, ajustado somente em `train`; como o
contrato canônico já exige `float64` finito e não nulo, não há imputação. A
busca em memória usa exclusivamente distância euclidiana no espaço
padronizado, com `top_k` entre 1 e 10.

A ordenação é total: distância crescente e, em igualdade, referência opaca
crescente. A classe candidata maximiza a quantidade de votos; empates usam a
menor soma das distâncias e depois o `target_slug` em ordem crescente. O
`support_score` é apenas a fração de votos da classe vencedora no top-k. Ele não
é probabilidade, confiança calibrada ou autorização para agir.

O núcleo mantém o `target_slug` canônico internamente. O adapter da porta de
modelo usa uma tabela bijetiva, validada e serializada, para produzir
`fault_code` público; colisões bloqueiam o fit. `normal_target_labels` é
configuração explícita e deve ser subconjunto das classes de treino. Nesta
execução, o slug `normal` foi confirmado no treino antes de ser configurado.
Classes normais produzem `NORMAL`; as demais produzem `FAULT`. A tarefa não
produz `OUT_OF_DISTRIBUTION`, abstenção ou calibração.

## Artefato seguro e reproduzível

O artefato contém um manifesto JSON canônico e três arrays NumPy: vetores de
treino já padronizados, índices de classe e referências opacas de vizinhos. A
carga usa `allow_pickle=False`, exige o conjunto exato de arquivos, valida
schema, versões, ordem das 18 features, estado completo do `StandardScaler`,
tabela de labels, formas, tipos, finitude e hashes físicos e lógicos. O
`model_id` deriva do conteúdo semântico completo e pode ser fornecido como
âncora externa na carga.

Artefatos reais contêm derivados por registro e, portanto, permanecem somente
em `data/processed/`, ignorados pelo Git. A escrita dentro de uma worktree falha
se o destino não estiver ignorado, se houver link/junction ou se um destino
existente tiver bytes diferentes.

Duas execuções independentes produziram bytes idênticos nos quatro arquivos e
a mesma inferência serializada:

| Evidência | Valor |
| --- | --- |
| Dataset da SEN-41 | `a0c1a7c5141b9b3a8856ad9af458fe09baa7fa04f6b96316ecb52a6d6b426327` |
| Modelo | `model_knn_v1_4101520213027681c40f15e585158b93` |
| Conteúdo do modelo | `4101520213027681c40f15e585158b93b982b11f44a6a3c0dd01346934b8d982` |
| Inferência comparada | `349eb732254abae5d27c6c9f1ec0c4dc50e3cd578905ff0441396da2c84d1f4c` |
| Relatório agregado local | `0a6b524dd27700866163d5fff08709bc606856187fa3ec56a55d6c4a69fba340` |

## Avaliação temporal sem tuning

O build derivado foi verificado offline pela SEN-41 antes do consumo. O treino
usou 116.882 linhas e 17 classes; sua classe majoritária representa 11,1223% do
treino. `k=5` é o padrão declarado, não foi escolhido pela validação e nenhum
parâmetro foi alterado depois de observar validação ou teste.

A reexecução instrumentada cobriu integralmente as duas partições temporais.
`NearestNeighbors` com busca brute-force euclidiana limitou a memória de
trabalho a 64 MiB e processou os cálculos internamente em blocos, sem
materializar a matriz consulta × treino. Empates numéricos na fronteira do
top-k foram resolvidos pela ordenação total da baseline. Todos os agregados
reproduziram o relatório anterior; os números de recursos abaixo são uma
observação deste ambiente, não um benchmark ou SLA:

| Recurso observado | Valor |
| --- | ---: |
| Tempo de validação completa | 5,905 s |
| Tempo de teste completo | 4,420 s |
| Tempo total, incluindo carga e preparação | 10,581 s |
| Pico de working set do processo | 247,848 MiB |
| Memória de trabalho configurada para a busca | 64 MiB |

| Métrica agregada | Validação | Teste |
| --- | ---: | ---: |
| Linhas | 25.146 | 24.768 |
| Classes observadas | 10 | 130 |
| Classes ausentes do treino | 9 | 128 |
| Linhas com classe conhecida no treino | 2.000 | 225 |
| Linhas com classe ausente do treino | 23.146 | 24.543 |
| Acurácia | 0,000000 | 0,001978 |
| Acurácia balanceada | 0,000000 | 0,007538 |
| F1 macro | 0,000000 | 0,002422 |
| F1 ponderado | 0,000000 | 0,000704 |
| Acurácia apenas em classes conhecidas | 0,000000 | 0,217778 |
| Presença do label verdadeiro entre os 5 vizinhos | 0,000000 | 0,001978 |
| Baseline da classe majoritária do treino | 0,000000 | 0,000000 |
| Fallback exato por empate na fronteira do top-k | 1.840 | 1.411 |

### Matrizes de confusão sanitizadas

Em cada partição, `class_001` a `class_005` representam somente as cinco classes
verdadeiras mais frequentes daquela partição; os aliases não são comparáveis
entre partições e não possuem tabela pública de reversão. `other` agrega todas
as demais classes. As colunas usam o mesmo agrupamento das linhas. Assim, a
matriz comprova contagens e confusões sem publicar labels ou combinações por
registro.

Validação:

| real \ predita | class_001 | class_002 | class_003 | class_004 | class_005 | other |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class_001 | 0 | 0 | 0 | 0 | 0 | 3.900 |
| class_002 | 0 | 0 | 0 | 0 | 0 | 3.012 |
| class_003 | 0 | 0 | 0 | 0 | 0 | 3.000 |
| class_004 | 0 | 0 | 0 | 0 | 0 | 3.000 |
| class_005 | 0 | 0 | 0 | 0 | 0 | 3.000 |
| other | 0 | 0 | 0 | 0 | 0 | 9.234 |

Teste:

| real \ predita | class_001 | class_002 | class_003 | class_004 | class_005 | other |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| class_001 | 0 | 0 | 0 | 0 | 0 | 3.000 |
| class_002 | 0 | 0 | 0 | 0 | 0 | 2.100 |
| class_003 | 0 | 0 | 0 | 0 | 0 | 1.000 |
| class_004 | 0 | 0 | 0 | 0 | 0 | 400 |
| class_005 | 0 | 0 | 0 | 0 | 0 | 300 |
| other | 0 | 0 | 0 | 0 | 0 | 17.968 |

## Análise crítica

O resultado é deliberadamente reportado sem otimização oportunista. A principal
limitação não é apenas desbalanceamento dentro do treino: é a mudança temporal
de suporte. O treino contém 17 classes, enquanto validação e teste introduzem,
respectivamente, 9 e 128 classes que o k-NN não tem como prever. Por isso,
acurácia global e F1 macro são quase nulos, e as matrizes mostram que as classes
mais frequentes dos holdouts são atribuídas a classes fora desses grupos.

Mesmo nas 225 linhas de teste cuja classe existe no treino, a acurácia de
21,7778% é insuficiente para uso operacional. A baseline serve como referência
auditável de distância e recuperação local, não como modelo aprovado para
decisão de manutenção. Tratar classes inéditas, calibrar suporte ou abster-se
pertence a trabalhos posteriores; fazê-lo aqui ocultaria a limitação temporal e
invadiria o escopo da SEN-51.

Nenhum dos oito materiais originais foi aberto nesta tarefa. A avaliação leu
somente os Parquets e o manifesto locais, ignorados e já produzidos pela
SEN-41; nenhum valor de feature, linha, timestamp, identificador de origem ou
label real foi copiado para este documento.
