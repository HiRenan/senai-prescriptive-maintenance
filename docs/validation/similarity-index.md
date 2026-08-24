# Índice de similaridade versionado — SEN-52

## Decisão técnica

A recuperação de vizinhos usa um artefato próprio, derivado somente depois que o
artefato k-NN v2 (baseline SEN-42 com a política SEN-51) foi integralmente
validado. Essa separação mantém o
modelo de origem imutável e permite fixar, em uma identidade independente, tudo
que precisa coincidir entre memória e PostgreSQL: dataset, schema, contrato das
18 features, pré-processador, estratégia do índice, configuração, métrica,
quantidade e hashes. O build captura uma única cópia defensiva dos quatro
arquivos da SEN-42, valida essa cópia e usa exatamente os mesmos bytes para
derivar o novo índice, impedindo mistura por troca concorrente da origem.

O índice usa `float32` porque esse é o formato nativo de `vector(18)` no
pgvector. A conversão ocorre uma única vez durante o build e passa a fazer parte
dos hashes do novo artefato; portanto, os dois adapters consultam exatamente os
mesmos valores. A estratégia permanece `exact-flat.v1`, sem HNSW ou IVFFlat.
Para esta baseline pequena, a busca exata oferece uma prova de paridade mais
forte e evita parâmetros aproximados antes de existir uma necessidade medida.

## Integridade e compatibilidade

O diretório contém exatamente quatro arquivos:

- `manifest.json`: identidade, compatibilidade, contagem e hashes;
- `preprocessor.json`: estado numérico do `StandardScaler`, sem objeto Python;
- `records.json`: somente ID opaco e `fault_code` seguro;
- `vectors.npy`: matriz `float32` contígua com 18 colunas.

A carga rejeita arquivos ausentes, extras, links, JSON não canônico, chaves
duplicadas, números não finitos, versão/configuração divergente, dimensão ou
tipo incompatível, hash alterado e identidade de conteúdo inconsistente. Todos
os hashes físicos são conferidos antes de `np.load`, que é chamado somente com
`allow_pickle=False`; não há pickle, import dinâmico ou execução de código.
Cada arquivo regular é aberto uma única vez por descritor limitado, com
identidade, tamanho e metadados de alteração conferidos antes e depois da
leitura. Hash, JSON e `np.load(BytesIO(...))` usam o mesmo snapshot imutável;
troca de path ou escrita concorrente durante a leitura falha de forma fechada.
O mesmo leitor protege a comparação usada no reaproveitamento idempotente de um
destino já existente.

O manifesto completo também é persistido na tabela `similarity_indexes`. Cada
entrada em `similarity_index_entries` liga o ID do índice a um ID opaco,
`fault_code`, `public.vector(18)` e hash do vetor. Instalação e replay são uma
única transação. O adapter PostgreSQL valida manifesto, contagem e conteúdo
contra o artefato carregado antes de ficar disponível. Em cada consulta,
manifesto, contagem e top-k pgvector são lidos no mesmo snapshot transacional
`READ ONLY`/`REPEATABLE READ`; o resultado integral é comparado ao top-k
canônico vetorizado do `LoadedSimilarityIndex` imutável. Assim, até um vetor
adulterado para sair do top-k bloqueia a resposta.

A validação completa custa O(n) no startup e na instalação. Repeti-la por
consulta transferiria todos os 116.882 vetores da baseline pela conexão e
inviabilizaria o objetivo do adapter. A comparação escolhida mantém O(n) local
vetorizado para a referência exata e transfere somente O(k) registros do banco.
Uma corrupção posterior que não altere o resultado da consulta pode permanecer
até a próxima validação integral, mas não muda a resposta aceita; qualquer
diferença observável em cardinalidade, identidade, ordem, metadado ou distância
é fail-closed. Esse custo é deliberado para `exact-flat.v1` e deve ser reavaliado
antes de introduzir busca aproximada ou remover a referência em memória.

## Semântica comum dos adapters

`SimilarityIndexPort` recebe as 18 features cruas e um seletor com o `index_id`,
o `model_id` de origem e toda a compatibilidade de dataset, schema, versões e
configuração. O pré-processamento usa somente o estado JSON verificado. A
ordenação total é distância euclidiana crescente e, em empate, ID opaco
crescente. O filtro opcional aceita apenas `fault_code` ordenado e sem repetição;
`top_k` continua limitado a 1–10. O PostgreSQL usa o operador `<->` qualificado
da extensão e devolve a distância canônica calculada sobre os mesmos valores
`float32`, preservando igualdade com o adapter em memória.

## Evidências sintéticas

A suíte focada cobre build determinístico, round-trip, equivalência com a
ordenação da SEN-42, empates, filtros, vazio, dimensão, finitude, `top_k`,
seletor, versões, configuração, hashes, arquivos inseguros e proibição de
pickle. A integração opcional usa PostgreSQL/pgvector real em schema aleatório
descartável e cobre:

- migração 2 em ciclo `up/down/up`;
- replay idempotente e duas instalações concorrentes;
- rollback integral quando uma validação falha após escritas parciais;
- paridade exata memória/PostgreSQL para empate, filtro e resultado vazio;
- rejeição de vetor adulterado mesmo quando ele deixaria o `top_k`;
- rejeição de drift de configuração antes da busca.

Os testes não abrem nenhum dos oito materiais originais e não gravam linha real
no repositório ou no banco. Todo DataFrame, vetor, rótulo, ID e schema usado como
evidência é obviamente sintético.

## Validação privada sanitizada com o derivado real

O CLI atual revalidou os derivados canônicos ignorados da SEN-41 com o lock
histórico registrado pelo próprio manifesto: 166.796 linhas canônicas, 116.882
de treino e 25.146 de validação, todas com identidade física preservada. Os dois
artefatos k-NN locais anteriores tinham as formas esperadas, mas ainda eram
schema/model v1 e não possuíam a política de abstenção obrigatória. O loader v2
os rejeitou antes dos arrays, como deve ocorrer; nenhum fallback legado foi
adicionado.

Uma reconstrução local pelo contrato atual produziu e recarregou de forma
idempotente o k-NN v2 com 116.882 registros e 18 features. A partir dele, o
build da SEN-52 produziu e recarregou um índice schema 1 com 116.882 registros,
18 dimensões e métrica euclidiana; uma consulta em memória retornou os três
ranks esperados sem publicar IDs, distâncias ou conteúdo por registro. Os tempos
observados neste ambiente foram 0,048 s para carregar os Parquets, 30,199 s para
o fit k-NN e 2,906 s para build e reload do índice. São evidências de execução,
não benchmark ou SLA.

Essa validação não abriu materiais originais e manteve modelo e índice somente
em destinos locais ignorados. PostgreSQL/pgvector real continuou restrito aos
testes sintéticos descritos acima; nenhum vetor real foi instalado no banco.

## Limites explícitos

Esta entrega não implementa abstenção, detecção OOD, suporte documental, RAG,
endpoint final, AWS, UI, tuning ou índice aproximado. Ela também não instala um
artefato real automaticamente: derivados reais permanecem locais e ignorados,
e a conexão PostgreSQL é criada e encerrada pelo chamador.
