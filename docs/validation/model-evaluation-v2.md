# Correção semântica da avaliação temporal — SEN-78

## Relação com a evidência histórica

O [relatório congelado da SEN-53](model-evaluation.md) continua sendo a fonte
histórica da avaliação exata do modelo v2 e não foi reescrito. A SEN-78 adiciona
o protocolo `temporal-knn-exact.v2`, com schema de relatório 2, para avaliar
como objetivo primário a distinção entre estado operacional e problema. As
métricas exatas anteriores permanecem no novo JSON como diagnóstico secundário.

Essa correção não converte o holdout em amostra independente: ele já tinha
agregados conhecidos antes do modelo v2 e desta análise. A leitura operacional é
pós-hoc, sem tuning, seleção de modelo ou alteração de thresholds.

## Ordem executada e vínculo dos artefatos

1. O modelo v3 foi ajustado com treino e validação aprovados, usando os
   parâmetros congelados e sem abrir o holdout.
2. O artefato schema/model 3 foi salvo e recarregado por identidade e hash.
3. Um índice `exact-flat.v1` novo foi derivado desse artefato, salvo, recarregado
   e conferido contra o mesmo dataset, schema, modelo e quantidade.
4. O plano de avaliação v2 foi materializado antes da abertura do holdout.
5. O holdout foi avaliado uma vez; somente o relatório agregado sanitizado foi
   persistido em destino local ignorado.

| Evidência sanitizada | Valor |
| --- | --- |
| Modelo | `model_knn_v3_29cd6c2dc4e85fae0a4ce221b9775431` |
| Conteúdo do modelo | `29cd6c2dc4e85fae0a4ce221b977543137696aea1882bed44d10624bc05851e8` |
| Índice | `similarity_index_v1_906068f66e4ab0ca961f0a9fa29402ff` |
| Conteúdo do índice | `906068f66e4ab0ca961f0a9fa29402ffba1f94d407c7b634faa92d0d1bca9001` |
| Bundle local do modelo antes/depois (4 arquivos) | `780184cfff059ae279b6ababd430141f861c68948be5cd4213989f51019462fb` |
| Bundle local do índice antes/depois (4 arquivos) | `32e8b6cc5daa0fb74628ea30f0baadef7d4dc0a443a873254da1721a24743b10` |
| Plano | `evaluation_plan_v2_b3a00f404054e166bed3d55cd70187df` |
| SHA-256 da materialização | `30a8d50bbcfb5f5c328ae5e14db2db09b3995f9f4d88fd4db4dd72250646d3d7` |
| SHA-256 do relatório local | `9ab4b41421a099b17fdb47e6ed2e858a0917968fde7b59cd4fece38b9590e1b5` |
| Treino / índice | 116.882 / 116.882 registros |
| Holdout | 24.768 linhas |
| Classes exatas conhecidas / ausentes do treino | 225 / 24.543 linhas |

O modelo v3 e o índice foram carregados juntos com o vínculo acima. Na
reexecução deste protocolo, os digests dos quatro arquivos de cada artefato
foram calculados antes e depois e permaneceram idênticos; não houve novo fit,
rebuild ou alteração de bytes. A tentativa de carregar tanto o artefato de
modelo v2 quanto o índice ligado a ele falhou fechada por versão incompatível.
Compatibilidade não significa rebind: produzir a política nova exige rebuild.

## Definições sem ambiguidade de abstenção

Para cada linha, o k-NN sempre calcula uma condição candidata antes de aplicar a
política de abstenção. O relatório separa três leituras:

- `candidate_*`: recorte binário da candidata em todas as linhas, inclusive as
  que depois serão abstidas; não representa condição emitida pelo sistema;
- `selective_*`: o mesmo recorte somente entre linhas aceitas, com denominador
  seletivo explícito;
- `abstained`: quantidade, taxa e motivos das linhas que não emitem condição.

`coverage` é a razão entre aceitas e total. Acurácia balanceada é a média dos
recalls operacional e de problema no respectivo recorte. Nenhuma métrica recebe
o nome `predicted_*`, pois o sistema não produz condição em OOD.

O plano tipa `always_problem` como `constant_class_accuracy` nos dois recortes.
Sua fórmula é `actual_problem_count / scope_row_count`: em
`candidate_all_rows`, o denominador é todo o holdout; em `selective_accepted`,
é somente o total aceito. O JSON agregado materializa estratégia, fórmula,
numerador, denominador e valor, sem inferir condição para linhas abstidas.

## Resultado pós-hoc agregado

### Candidata antes da abstenção

| Verdade \ candidata | Operacional | Problema | Total |
| --- | ---: | ---: | ---: |
| Operacional | 66 | 256 | 322 |
| Problema | 394 | 24.052 | 24.446 |
| Total | 460 | 24.308 | 24.768 |

| Métrica `candidate_*` | Numerador / denominador | Valor |
| --- | ---: | ---: |
| Acurácia | 24.118 / 24.768 | 97,3756% |
| Baseline sempre-problema | 24.446 / 24.768 | 98,6999% |
| Acurácia balanceada | — | 59,4426% |
| Recall operacional | 66 / 322 | 20,4969% |
| Recall de problema | 24.052 / 24.446 | 98,3883% |

### Leitura seletiva entre aceitas

| Verdade \ candidata aceita | Operacional | Problema | Total |
| --- | ---: | ---: | ---: |
| Operacional | 51 | 176 | 227 |
| Problema | 120 | 9.496 | 9.616 |
| Total | 171 | 9.672 | 9.843 |

| Métrica `selective_*` | Numerador / denominador | Valor |
| --- | ---: | ---: |
| Acurácia | 9.547 / 9.843 | 96,9928% |
| Baseline sempre-problema | 9.616 / 9.843 | 97,6938% |
| Acurácia balanceada | — | 60,6095% |
| Recall operacional | 51 / 227 | 22,4670% |
| Recall de problema | 9.496 / 9.616 | 98,7521% |

### Cobertura e abstenção

| Estado | Contagem | Taxa |
| --- | ---: | ---: |
| Aceita | 9.843 | 39,7408% |
| Abstida | 14.925 | 60,2592% |
| └ por distância | 12.652 | 51,0812% do total |
| └ por voto inconclusivo | 2.273 | 9,1780% do total |
| └ por suporte raro | 0 | 0% |

A acurácia `candidate_*` de 97,3756% inclui as 14.925 linhas abstidas e é
dominada pelas 24.446 linhas de problema. Ela não pode ser apresentada como
acurácia final e ainda fica 1,3243 ponto percentual abaixo da baseline
sempre-problema. A acurácia `selective_*` descreve apenas 39,7408% do holdout e
fica 0,7010 ponto abaixo da baseline no mesmo recorte.

O único sinal acima do trivial está na leitura balanceada e nos recalls. A
baseline constante teria 50% de acurácia balanceada, 0% de recall operacional
e 100% de recall de problema; o candidato alcança 59,4426%/20,4969%/98,3883%
antes da abstenção e 60,6095%/22,4670%/98,7521% entre aceitas. Essa troca ainda
recupera pouco mais de um quinto dos estados operacionais e é insuficiente para
aprovar o modelo ou automatizar manutenção.

## Política e diagnóstico secundário

A política oficial reconhece somente `normal`, `baseline`, `teste`,
`acelerando` e `motor_desligado`, após normalização do valor inteiro por caixa,
acentos e hífen/underscore. Substring e aliases de dataset são proibidos. O
payload exato de `operating-states.v1` integra o plano e o artefato.

O relatório mantém top-1 exato, Hit@1, Hit@5, MRR@5, baseline majoritária,
cobertura, abstenção e acurácia seletiva exata para todas as linhas e para
classes conhecidas. Esses números reproduzem o diagnóstico histórico da
SEN-53; não foram usados para escolher o objetivo operacional ou ajustar o
modelo.

## Reprodução segura

O CLI não expõe flags de tuning. Os caminhos abaixo são placeholders e devem
apontar somente para derivados aprovados e destinos ignorados:

```powershell
$dataset = "<diretorio-derivado-aprovado>"
$model = "<artefato-knn-v3-aprovado>"
$index = "<indice-v3-vinculado-aprovado>"
$report = "data/processed/sen-78-evaluation/evaluation-report.v2.json"

uv run --frozen python -m prescriptive_maintenance.modeling.evaluation `
  --dataset-manifest "$dataset/manifest.json" `
  --holdout "$dataset/test.parquet" `
  --model-artifact $model `
  --index-artifact $index `
  --report-output $report
```

O JSON contém apenas identidades técnicas, hashes, contagens, métricas,
hardware e limitações. Nenhum conteúdo privado, path local, label fora da
política, linha, feature, timestamp ou identificador da fonte é publicado.
