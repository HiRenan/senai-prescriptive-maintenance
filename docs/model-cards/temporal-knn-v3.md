# Model card — motor temporal k-NN v3

## Resumo e decisão de uso

O motor v3 é uma busca determinística de similaridade entre históricos. Ele
padroniza 18 features com estatísticas ajustadas somente no treino, recupera
cinco vizinhos por distância euclidiana exata e usa o voto apenas para formar
uma condição candidata. `support_score` combina votos e distância como
heurística; não é probabilidade, confiança calibrada ou autorização de
manutenção.

O uso aprovado continua sendo demonstração de similaridade com revisão humana.
O modelo não foi aprovado como classificador, automação ou decisão industrial.
Uma linha abstida não emite condição. Uma candidata aceita ainda precisa ser
interpretada junto dos históricos recuperados e, quando problemática, da
evidência documental governada.

O [model card v2](temporal-knn-v2.md) e o
[relatório exato da SEN-53](../validation/model-evaluation.md) permanecem como
evidência histórica inalterada. Esta página descreve apenas a semântica e o
artefato v3 introduzidos pela SEN-78.

## Política operacional versionada

`operating-states.v1` reconhece somente os valores inteiros `normal`,
`baseline`, `teste`, `acelerando` e `motor_desligado`. A normalização permitida
é limitada a caixa, remoção de acentos Unicode e conversão de hífen para
underscore. Não há trim, tokenização, substring, descoberta de alias ou regra
por sufixo. Qualquer outro valor continua sendo condição problemática; duas
classes distintas que normalizem para o mesmo estado tornam o fit inválido.

O manifesto schema 3 incorpora o payload completo da política e o vínculo com
as classes operacionais presentes no treino. O runtime aceita apenas
`KNN_ARTIFACT_SCHEMA_VERSION=3` e `KNN_MODEL_VERSION=3`; artefatos v2 exigem
rebuild e falham fechados na leitura.

## Resultado público e OpenAPI v1

O shape do OpenAPI v1 foi preservado. Para uma condição operacional candidata
aceita, o adapter retorna:

- `disposition=NORMAL` e outcome público `normal`;
- `Diagnosis` com código `operating_state_<estado-canônico>` e resumo que a
  identifica como candidata baseada em históricos semelhantes;
- o campo congelado `fault_code` de cada vizinho operacional também projeta
  `operating_state_<estado-canônico>`, sem expor o hash interno `fault_*`;
- `retrieval_key=null`, sem recuperação documental, provider ou prescrição;
- ranking, distâncias e referências opacas inalterados.

Uma condição problemática candidata aceita retorna `FAULT` e só pode alcançar
a recuperação documental se possuir chave governada. Uma abstenção retorna
`OUT_OF_DISTRIBUTION`, sem diagnóstico ou chave. Assim, o objeto `Diagnosis` de
um outcome `normal` explica a condição operacional provável sem representá-la
como falha. Essa projeção não altera distância, rank, referência opaca, tabela
interna ou hash do artefato.

## Artefatos locais verificados

Os artefatos abaixo foram reconstruídos localmente a partir dos derivados
aprovados, permanecem ignorados e foram recarregados em conjunto. Somente
identidades, hashes e contagens agregadas são registradas.

| Item | Valor |
| --- | --- |
| Dataset canônico | `a0c1a7c5141b9b3a8856ad9af458fe09baa7fa04f6b96316ecb52a6d6b426327` |
| Modelo | `model_knn_v3_29cd6c2dc4e85fae0a4ce221b9775431` |
| Conteúdo do modelo | `29cd6c2dc4e85fae0a4ce221b977543137696aea1882bed44d10624bc05851e8` |
| Schema / versão do modelo | `3` / `3` |
| Linhas de treino / features | 116.882 / 18 |
| Classes operacionais vinculadas no artefato | 3 |
| Índice | `similarity_index_v1_906068f66e4ab0ca961f0a9fa29402ff` |
| Conteúdo do índice | `906068f66e4ab0ca961f0a9fa29402ffba1f94d407c7b634faa92d0d1bca9001` |
| Registros do índice | 116.882 |

O índice preserva o algoritmo `exact-flat.v1`, mas possui conteúdo novo e
vínculo exato ao ID e ao hash do modelo v3. A carga conjunta confirmou dataset,
schema, quantidade, identidade e hash de origem. O índice anterior ligado ao
modelo v2 também falha fechado no runtime atual; não existe rebind silencioso.

Os parâmetros foram reproduzidos sem tuning pelo holdout: limiar de distância
`1,6266179747406075`, limiar de margem `0,0`, mínimo de dois exemplos por classe
e 512 amostras determinísticas de calibração na validação.

## Avaliação do objetivo operacional

A medição abaixo é um diagnóstico pós-hoc no mesmo holdout temporal já
observado historicamente. Ela não é estimativa independente e não escolheu nem
alterou parâmetros.

| Métrica | Candidato antes da abstenção | Seletiva entre aceitas |
| --- | ---: | ---: |
| Linhas | 24.768 | 9.843 |
| Acurácia operacional versus problema | 97,3756% | 96,9928% |
| Baseline sempre-problema | 98,6999% | 97,6938% |
| Acurácia balanceada | 59,4426% | 60,6095% |
| Recall operacional | 20,4969% | 22,4670% |
| Recall de problema | 98,3883% | 98,7521% |

No recorte pré-abstenção, houve 66 estados operacionais candidatos corretos,
256 estados operacionais tratados como problema, 394 problemas tratados como
operacionais e 24.052 problemas candidatos corretos. Entre as linhas aceitas,
essas contagens foram 51, 176, 120 e 9.496, respectivamente.

A cobertura foi 9.843/24.768 (39,7408%). As 14.925 abstenções (60,2592%) se
dividiram em 12.652 por distância e 2.273 por voto inconclusivo. Portanto,
97,3756% é acurácia do candidato binário antes da abstenção, dominada pelo
recall de problema; além de não ser acurácia do comportamento final, fica
abaixo dos 24.446/24.768 (98,6999%) da baseline que trata tudo como problema. A
leitura seletiva também é condicional à cobertura: seus 9.547/9.843 (96,9928%)
ficam abaixo dos 9.616/9.843 (97,6938%) da baseline constante no mesmo recorte.

O único sinal acima do trivial aparece na leitura balanceada e nos recalls. A
baseline sempre-problema tem 50% de acurácia balanceada, 0% de recall
operacional e 100% de recall de problema; o candidato troca parte desse recall
de problema por 20,4969% de recall operacional antes da abstenção e 22,4670%
entre aceitas. O baixo recall operacional e a cobertura de 39,7408% tornam esse
sinal insuficiente para aprovar classificação, automação ou manutenção.

Os 24.543/24.768 rótulos exatos ausentes do treino continuam explicando por que
a avaliação exata histórica é útil como diagnóstico secundário, mas inadequada
como objetivo principal da jornada prescritiva. Fórmulas, identidades e
denominadores estão na
[correção de avaliação da SEN-78](../validation/model-evaluation-v2.md).

A [avaliação open-set v3](../validation/model-evaluation-v3.md) acrescenta
Wilson 95%, Precision@K, Recall@K, ganho incremental de k=2..5 e um gate de
promoção restrito à validação. O gate real não passou; por isso nenhum challenger
ou novo artefato substituiu esta baseline.

## Limitações e trabalho futuro

- O holdout já havia sido observado; uma decisão operacional exige nova janela
  temporal, objetivo e custos aprovados antes da abertura.
- O baixo recall operacional e a cobertura limitada impedem alegar desempenho
  satisfatório mesmo com acurácia bruta alta e desbalanceada.
- Não há probabilidade calibrada, otimização pelo holdout, model zoo, busca
  aproximada, SLA ou autorização de manutenção.
- Os artefatos reais não são selecionados pela factory HTTP padrão. A SEN-79
  ainda precisa compor e autorizar explicitamente o modelo e o índice v3 já
  carregáveis; esta tarefa não apresenta essa integração futura como entregue.
