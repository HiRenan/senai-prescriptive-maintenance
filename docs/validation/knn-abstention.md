# Abstenção diagnóstica do k-NN — SEN-51

## Decisão implementada

A SEN-51 evolui a baseline da SEN-42 sem tratar distância como probabilidade. O
modelo v2 mantém as mesmas 18 features, `StandardScaler` ajustado somente em
treino, busca euclidiana exata, ordenação total de vizinhos e desempate de
classes. Sobre esse resultado, aplica uma política de abstenção versionada e
congelada antes do teste.

O suporte continua explicitamente heurístico. Ele multiplica a fração de votos
da classe vencedora pelo inverso de `1 + distância_mais_próxima / limiar_de_distância`.
O valor permanece entre zero e um, mas não representa probabilidade, confiança
calibrada ou autorização operacional. A margem de voto é a diferença entre os
votos da primeira e da segunda classe dividida pelo número efetivo de vizinhos.

A decisão usa três razões internas tipadas, nesta precedência:

1. `distance_out_of_distribution`, quando a menor distância é estritamente
   maior que o limiar;
2. `rare_class_support`, quando a classe candidata possui menos exemplos de
   treino que o mínimo configurado;
3. `inconclusive_vote`, quando a margem é menor ou igual ao limiar.

O conjunto decisório usa sempre o `default_top_k=5` versionado e calibrado. O
`top_k` recebido pela porta controla somente a cardinalidade da evidência opaca:
a implementação calcula até o maior dos dois limites, decide com os primeiros
cinco vizinhos disponíveis e devolve apenas a quantidade solicitada. Assim,
variar `top_k` de 1 a 10 preserva target, suporte, margem, disposição e razão de
abstenção, sem impedir o cliente de escolher a quantidade de evidências.

Igualdade no limite de distância ainda pertence ao domínio; igualdade no limite
de margem se abstém. Essa assimetria é intencional e coberta por testes. Quando
há abstenção, o adapter retorna `OUT_OF_DISTRIBUTION`, sem diagnóstico e sem
chave de recuperação documental. Assim, indisponibilidade ou ausência de
documentação não influencia a decisão do modelo. A API v1 preserva o motivo
público fechado `out_of_distribution`, enquanto a porta interna mantém a causa
específica para orquestração e auditoria.

## Ajuste sem vazamento do teste

O manifesto v2 exige hashes físicos declarados das partições de treino e de
calibração. O `model_id` deriva das matrizes de treino, estado do scaler,
configuração completa, hashes, quantis, limiares e versão da política. Alterar
qualquer um desses elementos muda a identidade do modelo.

Nesta execução, o scaler foi ajustado em todas as 116.882 linhas de treino. A
calibração usou 512 posições uniformemente espaçadas na ordem canônica da
validação. Apenas as 18 features participaram do ajuste dos limiares; alterar os
targets sob uma nova identidade física, sem alterar as features, preserva os
valores numéricos dos thresholds. O `model_id` ainda muda porque o hash da
partição é parte obrigatória da identidade. Os parâmetros declarados foram:

| Parâmetro | Valor |
| --- | ---: |
| Quantil de distância | 0,95 |
| Limiar de distância padronizada | 1,626617974741 |
| Quantil inferior da margem | 0,10 |
| Limiar de margem | 0,0 |
| Mínimo de exemplos da classe | 2 |
| `default_top_k` decisório | 5 |

O quantil de margem observado foi zero; por isso, apenas empate exato foi
classificado como votação inconclusiva nessa execução. Quando todas as margens
de calibração são unânimes (`1,0`), o limiar usa o maior `float64` menor que um:
votação unânime continua elegível e qualquer margem não observada se abstém.

O arquivo de teste só foi aberto depois do fit, do congelamento da política e da
gravação do artefato. Nenhuma métrica de teste alterou configuração ou limiar.

## Integridade e reprodução local

O build canônico foi verificado offline antes da avaliação: 166.796 linhas,
568 ocorrências e partições de 116.882/25.146/24.768 linhas. A verificação usa o
`uv.lock` de origem registrado pelo próprio manifesto da SEN-41; o lock atual
inclui dependências adicionadas depois e, corretamente, não se passa pelo lock
histórico. O runtime do modelo usa o lock atual congelado.

| Evidência | SHA-256 ou identidade |
| --- | --- |
| Dataset canônico | `a0c1a7c5141b9b3a8856ad9af458fe09baa7fa04f6b96316ecb52a6d6b426327` |
| Treino físico | `5cd162f27afff80191374ee008349a4cc29ec3f89ce6bdf760d99e277d3662f6` |
| Validação física | `9dc026744712d8ab005a15a1c4c5f20e00de9af16c3a51c437809f327c558693` |
| Teste físico | `7f5bfec103f85e8a481cbbe7d2468c208fd2759ec29aea48f84e023830e1e993` |
| Modelo | `model_knn_v2_88e8ea9da70f90e7fa1eeae7461d9192` |
| Conteúdo do modelo | `88e8ea9da70f90e7fa1eeae7461d91928f75b0cc632e8bbceb0ea6cc937c0f13` |
| Relatório agregado local | `cb5ad8b24d262e31f393276cf4f455be730e4e47ac8a174a11750a8785551f1c` |

O artefato real continua somente em `data/processed/`, ignorado. A serialização
mantém JSON canônico e arrays NumPy com `allow_pickle=False`; a carga rejeita
limiar não finito ou negativo, campos inesperados, versão incompatível, hash
alterado e identidade divergente. Ela também valida o vínculo cruzado da
política: `train_leave_one_out` exige o mesmo hash e exatamente
`min(linhas_de_treino, 512)` amostras, enquanto `validation` exige hash distinto
do treino. Sem validação, treino unitário falha fechado porque não existe
leave-one-out real. A política é copiada no ingresso e na saída do modelo para
que mutação externa não altere a decisão sem mudar o `model_id`.

## Avaliação temporal agregada

As métricas abaixo medem o candidato bruto e o resultado seletivo depois da
abstenção. Uma linha abstida não entra na acurácia seletiva. Labels permanecem
privados; somente contagens agregadas são registradas. A reexecução após separar
o conjunto decisório da cardinalidade de evidências preservou, campo a campo,
todos os agregados anteriores em `default_top_k=5`; somente a identidade do
modelo e o hash do relatório mudaram devido à configuração semântica adicional.

| Métrica | Validação | Teste |
| --- | ---: | ---: |
| Linhas | 25.146 | 24.768 |
| Linhas com classe conhecida no treino | 2.000 | 225 |
| Linhas com classe ausente do treino | 23.146 | 24.543 |
| Acurácia bruta | 0,000000 | 0,001978 |
| Acurácia balanceada bruta | 0,000000 | 0,007538 |
| F1 macro bruto | 0,000000 | 0,002422 |
| Linhas aceitas | 19.953 | 9.843 |
| Cobertura | 79,3486% | 39,7408% |
| Acurácia seletiva | 0,000000 | 0,004978 |
| Aceitas com classe conhecida | 1.655 | 167 |
| Aceitas com classe ausente | 18.298 | 9.676 |
| Abstenção em classes ausentes | 20,9453% | 60,5753% |
| Abstenções por distância | 1.232 | 12.652 |
| Abstenções por votação | 3.961 | 2.273 |
| Abstenções por classe rara | 0 | 0 |
| Fallbacks por empate na fronteira do top-k | 1.828 | 1.406 |

## Análise crítica

A política reconhece a forte mudança geométrica entre validação e teste: a taxa
de abstenção por distância sobe de 4,90% para 51,08%. Ela também elimina toda
votação exatamente empatada e preserva causas estáveis para o downstream. Isso
é comportamento de segurança verificável, não evidência de qualidade preditiva.

O resultado continua inadequado para operação. Entre as linhas aceitas no
teste, a acurácia é apenas 0,4978%; 9.676 aceites pertencem a classes que nem
existem no treino. Distância e votação não conseguem identificar de forma
confiável novidade semântica quando uma classe inédita ocupa uma região já
observada. A política reduz cobertura, mas não corrige o deslocamento temporal
nem transforma a baseline em classificador aprovado.

Por isso, nenhuma métrica foi usada para afrouxar ou endurecer limiares depois
de olhar o teste. O motor deve permanecer como baseline auditável e sempre
permitir abstenção; uma futura decisão operacional exigirá dados com suporte
temporal, objetivo de validação aprovado e avaliação independente. Nenhum dos
oito materiais originais foi aberto. A tarefa leu somente os Parquets e o
manifesto locais, ignorados e previamente validados, e não publicou linha,
feature, timestamp, label ou identificador da fonte.
