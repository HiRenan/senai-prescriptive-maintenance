# Encerramento da SEN-4 — pipeline documental e base de conhecimento

- Data: 2026-08-23
- Escopo encerrado: recorte MVP formado por SEN-43, SEN-54, SEN-55 e SEN-56
- Resultado: recorte MVP implementado e integrado em `develop`
- Baseline técnica avaliada: `develop` em
  `e2f16124fd0d6397b87cbab20e9df1ed05b5c815`

Este relatório consolida o que pode ser comprovado no repositório e nos
relatórios aprovados das quatro tarefas do recorte MVP. Ele não reabre os
materiais originais, não reproduz texto extraído e não transforma planos da
descrição inicial do épico em funcionalidades concluídas.

As afirmações de capacidade e ausência deste documento são uma fotografia do
recorte SEN-4 nessa baseline. Entregas posteriores devem ser avaliadas pela sua
própria documentação e não tornam esta fotografia retroativamente imprecisa.

## Resultado executivo

O recorte entregue forma uma cadeia documental governada:

1. os seis PDFs autorizados foram inventariados e extraídos localmente, em modo
   somente leitura, com integridade e qualidade por página;
2. a extração estruturada pode ser segmentada e representada por chunks com
   identidades determinísticas, sem reabrir os PDFs;
3. documento e versão passam por um ciclo de vida fechado antes de se tornarem
   elegíveis;
4. a recuperação limita o universo pela classe documental, valida aprovação,
   vigência e integridade antes do ranking e devolve referências navegáveis.

A primeira etapa possui evidência privada sanitizada dos seis documentos reais.
As etapas seguintes possuem implementação e validação pública sintética, mas não
há relatório aprovado de execução ponta a ponta delas sobre os seis documentos.
Essa distinção é deliberada: não são alegados quantidade real de chunks,
embeddings semânticos, carga real em pgvector, aprovação efetiva dos seis
documentos ou mapeamentos industriais publicados.

| Tarefa | Entrega consolidada | Evidência de integração |
| --- | --- | --- |
| SEN-43 | Inventário, extração nativa/OCR, qualidade por página e derivados locais protegidos. | [PR #24](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/24), squash `d069c51`. |
| SEN-54 | Segmentação e indexação determinísticas, provider hash offline e portas de repositório. | [PR #29](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/29), squash `afa614d`. |
| SEN-55 | Ciclo documental idempotente, histórico append-only, gates e CAS. | [PR #30](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/30), squash `72193a1`. |
| SEN-56 | Mapeamento externo auditável e recuperação somente de conhecimento aprovado e vigente. | [PR #32](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/32), squash `7c7c5b9`. |

## Fluxo implementado e fronteiras

```text
PDFs locais autorizados
        │ leitura binária somente leitura + SHA-256/tamanho pre/post
        ▼
inventory.v1.json + extraction.v1.json locais e ignorados
        │ contrato estruturado; o indexador não recebe PDF
        ▼
chunks determinísticos + resultado de embedding por chunk
        │ IDs opacos, página, seção, offsets, hash e estado explícito
        ├──────────────► repositório em memória
        └──────────────► porta pgvector com writer injetado
                                │
documento/versão ──► ciclo de vida com gates, auditoria e CAS
                                │ apenas approved + vigente + íntegro
                                ▼
mapeamento externo classe→documentos ──► filtro antes do scorer
                                │ ranking determinístico + releitura final
                                ▼
evidência content-free: documento, versão, chunk, página, seção e score
```

As responsabilidades são separadas para que nenhuma camada ganhe autoridade por
acidente:

- a extração é a única etapa deste recorte que abre os PDFs;
- o indexador recebe apenas o mapping de `extraction.v1.json` e não descobre
  arquivos;
- o ciclo de vida governa estados e versões, mas não processa bytes nem chunks;
- a recuperação não abre PDFs, não aprova documentos e não escolhe outra classe
  quando a configuração exata não possui cobertura;
- o resultado público de recuperação não transporta texto, nome de arquivo,
  caminho, vetor ou conteúdo bruto;
- no recorte SEN-4 desta baseline, nenhuma dessas camadas está ligada às rotas
  HTTP ou ao frontend.

Os contratos detalhados estão no
[README do backend](../../apps/api/README.md) e no
[inventário de arquitetura](../architecture/README.md).

## Decisões técnicas e por que foram escolhidas

### Integridade antes e depois da leitura

Cada fonte é aberta por descritor binário não gravável. Tamanho e SHA-256 são
comparados com o [manifesto público](../../data/source-manifest.json) antes do
parse e conferidos novamente no mesmo descritor ao final, inclusive quando uma
etapa intermediária falha. Isso detecta substituição concorrente da fonte e
evita declarar um resultado sob uma identidade que mudou durante o consumo.

### Extração nativa antes de OCR

Texto nativo utilizável é preservado, evitando custo e incerteza desnecessários.
OCR é local, lazy e usado somente para páginas sem texto utilizável. Os gates
versionados combinam confiança média mínima de `0.80` e confiança pontual mínima
de `0.60`: uma boa média não esconde um trecho local de baixa confiança. O
resultado `suspect` continua rastreável e não equivale a aprovação.

### Derivados reais locais e escrita fail-closed

Inventário, extrações, chunks, vetores e configuração real de mapeamento ficam
em destinos explicitamente informados e ignorados pelo Git. Dentro de uma
worktree, destino rastreável, escape, symlink, junction, reparse point ou falha
na verificação impede a escrita. A escolha reduz a possibilidade de publicar
conteúdo industrial por engano em um repositório público.

### Página como fronteira e identidade por conteúdo

Um chunk nunca atravessa páginas. A configuração `document-chunking.v1` usa até
1.600 caracteres e overlap de até 200, preserva seções conservadoras e mantém
limites de grafema Unicode. Documento, versão, página, seção, posição, hash do
conteúdo e versão da configuração participam das identidades canônicas. Assim,
a mesma entrada e configuração reproduzem os mesmos IDs, enquanto uma mudança
relevante gera outra identidade auditável.

Os offsets `[character_start, character_end)` apontam para o texto-fonte da
extração antes da limpeza. Um mapeamento interno reconcilia remoção de controles,
CRLF, whitespace e linhas vazias com o conteúdo limpo. Isso permite navegar da
citação ao local de origem sem fingir que o texto não foi normalizado.

### Embedding fake como prova de porta, não de semântica

`LocalHashEmbeddingProvider` é determinístico, offline e explicitamente não
semântico. Ele prova contrato, repetibilidade, tratamento de falhas e integração
com repositórios em CI. No recorte SEN-4 desta baseline, a fronteira pgvector
recebe um writer injetado; ela não abre conexão nem executa SQL. Essa decisão
mantém a suíte reproduzível sem alegar qualidade de busca ou armazenamento
operacional ainda não demonstrados nesse recorte.

### Aprovação separada de processamento

Extração ou indexação bem-sucedida não autorizam uso. O ciclo de vida exige
transições explícitas, ator, motivo e sucesso dos dois gates antes de
`approved`. Uma nova versão só substitui a anterior no mesmo comando que a
aprova. Rejeição, falha e supersessão retiram elegibilidade sem apagar histórico.

### Mapeamento antes do ranking e revalidação depois

A configuração classe→documentos é externa, versionada e identificada por hash
semântico. A recuperação filtra classe exata, revisão, aprovação, vigência,
extração, indexação e integridade antes de chamar o scorer. Depois do ranking,
relê lifecycle e índice e exige o mesmo snapshot. Isso reduz recuperação
irrelevante e fecha a troca concorrente observável; não substitui uma transação
ou lease distribuído.

## Evidências quantitativas sanitizadas dos seis documentos

### Fonte das métricas

As colunas de bytes vêm exclusivamente de
[`data/source-manifest.json`](../../data/source-manifest.json). Páginas,
métodos, sinais e estados vêm da seção de validação privada sanitizada e aprovada
do [PR #24](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/24).
Cada número final desta consolidação foi reconciliado com uma fonte versionada
ou com o relatório do PR que o aprovou. Nenhum PDF, `extraction.v1.json`, outro
artefato derivado com texto ou snippet foi aberto para produzir esta
consolidação.

| Documento | Bytes no manifesto | Páginas | Nativa | OCR | Suspect | Failed | Estado da extração |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `Doc1.pdf` | 374.101 | 17 | 0 | 17 | 1 | 0 | `attention_required` |
| `Doc2.pdf` | 149.163 | 6 | 6 | 0 | 0 | 0 | `completed` |
| `Doc3.pdf` | 152.526 | 10 | 10 | 0 | 0 | 0 | `completed` |
| `Doc4.pdf` | 101.173 | 9 | 9 | 0 | 0 | 0 | `completed` |
| `Doc5.pdf` | 150.699 | 10 | 10 | 0 | 0 | 0 | `completed` |
| `Doc6.pdf` | 102.473 | 10 | 10 | 0 | 0 | 0 | `completed` |
| **Total** | **1.030.135** | **62** | **45** | **17** | **1** | **0** | 5 concluídos; 1 requer atenção |

O total de bytes é uma soma reproduzível dos seis `size_bytes` públicos, não um
campo independente. O manifesto registra seis SHA-256 distintos. Na execução
aprovada, todas as páginas tinham resultado: nenhuma ficou `ocr_required` e
nenhuma falhou.

No documento processado por OCR, a confiança média agregada superou o gate de
`0.80`, mas a menor confiança pontual ficou abaixo de `0.60`; por isso uma página
foi corretamente sinalizada e o documento terminou em `attention_required`.
Esse resultado comprova que o gate não confunde “OCR executado” com “texto
automaticamente confiável”.

Duas execuções privadas produziram os mesmos agregados. Os fingerprints,
tamanhos e tempos de modificação das seis fontes permaneceram iguais antes e
depois; a segunda execução também preservou bytes e tempos de modificação dos
artefatos derivados já existentes.

### Evidência, inferência e afirmações não autorizadas

| Classificação | O que pode ser afirmado |
| --- | --- |
| Evidência real sanitizada | Seis identidades no manifesto; 1.030.135 bytes; 62 páginas; 45 extrações nativas; 17 por OCR; uma página suspeita; zero falhas; repetição idempotente. |
| Evidência pública sintética | Chunking, embeddings, repositórios, lifecycle, concorrência e recuperação foram exercitados com dados inteiramente sintéticos e portas offline. |
| Inferência arquitetural | Os contratos permitem encadear extração, indexação, aprovação e recuperação quando adapters e configuração operacional forem compostos. |
| Não comprovado | Quantidade real de chunks/vetores dos seis PDFs, carga real deles em pgvector, embeddings semânticos, mapeamento real publicado, aprovação vigente dos seis ou execução RAG ponta a ponta. |

## Garantias por camada

### Inventário e extração

- allowlist fechada aos seis PDFs previstos; não há descoberta recursiva;
- leitura binária não gravável e verificação pre/post por tamanho e SHA-256;
- método, estado, sinais de qualidade e falha sanitizada por página;
- OCR local, lazy e sem serviço externo;
- falhas parciais continuam visíveis e podem ser tentadas novamente;
- reexecução idêntica não substitui bytes iguais;
- derivados reais permanecem em destino local ignorado;
- escrita bloqueada para destino rastreável ou caminho inseguro.

### Chunking e indexação

- entrada limitada ao contrato estruturado da extração, sem acesso aos PDFs;
- página nunca é cruzada e página sem chunk preserva sua falha original;
- limpeza remove apenas ruído técnico delimitado, sem reescrever significado;
- configuração, IDs, hashes, seções, offsets e ordenação são determinísticos;
- Unicode é dividido apenas em limites seguros de grafema;
- sucesso ou falha de embedding é registrado por chunk;
- chunk sem vetor permanece explícito, em vez de desaparecer;
- repositório em memória é idempotente e rejeita colisões;
- no recorte SEN-4 desta baseline, a porta pgvector não implica conexão ou
  persistência operacional.

### Ciclo de vida

O domínio possui sete estados: `received`, `processing`, `pending_approval`,
`approved`, `rejected`, `failed` e `superseded`.

- somente `approved`, vigente e com os dois gates íntegros é elegível;
- etapa concluída não regride para falha;
- retry preserva etapa já concluída e reinicia a etapa falha;
- replay exige identidade, versão, hash, ator e comando semanticamente iguais;
- revisão otimista usa compare-and-swap e conflito tipado;
- histórico e identidades de versão são append-only;
- aprovação da sucessora e supersessão da anterior são atômicas;
- relógio injetável é normalizado para UTC e não pode regredir;
- textos de auditoria inseguros são recusados sem ecoar o valor.

### Recuperação aprovada

- configuração externa possui schema, versão e hash semântico determinístico;
- classe e referências duplicadas, desconhecidas ou adulteradas falham fechado;
- ausência de mapeamento não usa fallback para outra classe;
- filtros de lifecycle e integridade acontecem antes do scorer;
- IDs de seção e chunk são recalculados a partir dos metadados canônicos;
- corrupção de versão declarada íntegra bloqueia toda a recuperação;
- scorer recebe cópia isolada e sua mutação invalida o ranking;
- revisão e snapshot são conferidos novamente depois do score;
- score deve ser finito, desempate é total e `top_k` é limitado a 10;
- evidência pública contém apenas IDs opacos, página, seção e score.

## Validações históricas verificáveis

Os números abaixo são os resultados finais registrados nos PRs integrados. Não
são somados entre si, porque as suítes agregadas de tarefas posteriores já
incluem testes anteriores.

| Entrega | Testes focados e adversariais | Suíte agregada final | Outros gates finais |
| --- | --- | --- | --- |
| SEN-43 | 35 aprovados; 1 skip condicional | 577 aprovados; 2 skips; 86,82% | Ruff, Pyright, hooks, locks, diff, path safety e 8/8 checks. |
| SEN-54 | 21 aprovados | 625 aprovados; 3 skips; 85,29% | Unicode/grafemas, locks congelados e 8/8 checks. |
| SEN-55 | 25 aprovados; 11/11 sondas independentes | 650 aprovados; 3 skips; 85,05% | CAS, replay, monotonicidade, OpenAPI, Gitleaks e 8/8 checks. |
| SEN-56 | 29 aprovados; 75 combinados com lifecycle/indexação | 751 aprovados; 31 skips; 82,24% | 38 testes PostgreSQL, smokes com pgvector, locks, OpenAPI, Gitleaks e 8/8 checks. |

Comandos reproduzíveis sobre dados públicos e sintéticos:

```powershell
uv run --frozen pytest apps/api/tests/test_source_documents.py `
  apps/api/tests/test_rapidocr_adapter.py -q --no-cov
uv run --frozen pytest apps/api/tests/test_document_indexing.py -q --no-cov
uv run --frozen pytest apps/api/tests/test_document_lifecycle.py `
  apps/api/tests/test_knowledge_retrieval.py -q --no-cov
uv run --frozen poe check
uv run --frozen poe hooks
uv run --frozen poe smoke
uv lock --check
```

Os testes PostgreSQL são opcionais e exigem uma URL explícita para um banco de
teste. A validação privada dos seis documentos exige os derivados locais
autorizados; ela não é executada por CI e não pode ser reproduzida a partir do
clone público sem os materiais fornecidos.

## Limitações e riscos conhecidos

- OCR é probabilístico. Uma confiança alta não prova fidelidade semântica; a
  página sinalizada do primeiro documento requer revisão humana.
- O chunking usa caracteres e headings conservadores, não tokens nem otimização
  semântica. O overlap aumenta armazenamento, e um grafema maior que o teto é
  preservado inteiro.
- O embedding hash é somente um fake de integração. Não mede similaridade
  semântica nem aprova um modelo de embedding.
- No recorte SEN-4 desta baseline, o adapter pgvector depende de writer injetado
  e não contém conexão, SQL ou migração para os chunks. Não há prova de carga
  real dos seis documentos nesse recorte.
- No recorte SEN-4 desta baseline, o lifecycle entregue usa repositório em
  memória; sua semântica CAS ainda precisa ser preservada por um adapter
  persistente e distribuído.
- O mapeamento real classe→documentos é externo e não é publicado. Sua revisão
  de domínio continua necessária em cada versão.
- A releitura final reduz TOCTOU, mas não cria transação ou lease entre
  lifecycle, índice e consumidor posterior.
- Upload, processamento assíncrono, S3, SQS, Textract, reindexação distribuída,
  integração HTTP e UI não fazem parte do recorte SEN-4 nesta baseline.
- No recorte SEN-4 desta baseline, não há scorer semântico operacional nem
  integração ponta a ponta entre os seis documentos, pgvector, RAG e uma
  recomendação.

## Próximos passos

1. Revisar humanamente a página marcada e registrar uma nova evidência
   sanitizada antes de considerar o gate de extração satisfeito para aquela
   versão.
2. Executar chunking e indexação privados sobre as extrações autorizadas e
   publicar somente contagens, estados e identidades sanitizadas, nunca texto ou
   vetor.
3. Selecionar e avaliar embedding/scorer semântico com conjunto de avaliação
   autorizado, mantendo o fake hash restrito a testes.
4. Revisar e versionar externamente o mapeamento real de classes com especialista
   de domínio.
5. Implementar adapter persistente do lifecycle e writer pgvector preservando
   idempotência, CAS, atomicidade e falha fechada.
6. Integrar as portas à API somente depois de autenticação, autorização,
   timeouts e políticas operacionais explícitas.
7. Tratar upload assíncrono e equivalentes AWS em tarefas próprias, sem
   apresentá-los como parte desta entrega.

## Perguntas prováveis da banca

### Por que separar extração, indexação, aprovação e recuperação?

Porque cada etapa responde a uma pergunta diferente. Extração diz o que foi
lido; indexação cria unidades recuperáveis; aprovação concede autoridade a uma
versão; recuperação seleciona somente evidência elegível. Unir tudo permitiria
que um PDF recém-processado se tornasse fonte normativa sem revisão.

### Os seis documentos foram realmente processados?

Há evidência aprovada de inventário e extração dos seis: 62 páginas, 45 nativas
e 17 por OCR. Há uma página suspeita e nenhuma falha. Não há evidência aprovada
de chunking, vetorização e carga pgvector real desses seis; essas fronteiras
foram validadas publicamente com dados sintéticos.

### Por que o primeiro documento não foi considerado simplesmente concluído?

Porque a menor confiança pontual ficou abaixo do gate, apesar da média alta. O
pipeline preservou as 17 páginas, marcou uma para revisão e devolveu
`attention_required`. Tornar essa condição visível é parte do requisito de
qualidade, não uma falha a esconder.

### Como é comprovado que a fonte não mudou durante a leitura?

O mesmo descritor read-only é fingerprintado antes e depois do processamento.
Se tamanho ou SHA-256 divergir, a mudança da fonte prevalece sobre qualquer
resultado intermediário e a execução falha fechado.

### Como uma citação volta ao local correto?

O chunk mantém IDs de documento e versão, página, seção e intervalo no texto da
extração. Os offsets são relativos ao texto-fonte, e a limpeza conserva um mapa
entre esse intervalo e o conteúdo normalizado.

### Por que os IDs são hashes determinísticos?

Para que a mesma entrada e configuração gerem a mesma identidade, permitindo
idempotência, auditoria e detecção de adulteração. Mudança de conteúdo,
localização ou configuração produz uma identidade diferente.

### O embedding fake melhora a busca?

Não. Ele existe para provar contrato, determinismo e tratamento de falhas sem
rede. Qualidade semântica exige provider e avaliação próprios; o relatório não
atribui essa capacidade ao hash local.

### Um documento rejeitado ou substituído pode aparecer no ranking?

Pelo contrato implementado, não. A recuperação exige a versão `approved`
vigente, gates completos e chunk íntegro antes de chamar o scorer. Ela relê o
mesmo estado depois do ranking e falha fechado se houver mudança.

### O que acontece em uma atualização concorrente?

O lifecycle usa revisão CAS e rejeita gravação sobre revisão perdida. Na
recuperação, uma nova revisão ou troca do snapshot durante o score invalida o
resultado. Ainda assim, não há lease após a última conferência, que permanece
um risco operacional explícito.

### O projeto já grava os chunks reais em PostgreSQL/pgvector?

No recorte SEN-4 desta baseline, não. Existe uma fronteira
`PgVectorChunkRepository` que transforma registros e chama um writer injetado,
mas ela não abre conexão nem executa SQL. A infraestrutura pgvector é validada
separadamente; isso não comprova carga real dos seis documentos nesse recorte.

### Por que o mapeamento classe→documento não está no repositório?

Porque a associação pode revelar conhecimento derivado dos materiais locais. O
contrato público define schema, versão e hash auditável; a configuração real
permanece externa, explícita e revisável sem expor conteúdo ou associações.

### Então por que a SEN-4 pode ser encerrada?

Porque o P.O. definiu formalmente o recorte MVP pelas quatro tarefas entregues:
extração rastreável dos seis documentos e contratos executáveis de indexação,
governança e recuperação aprovada. O encerramento não declara concluídos os
itens retirados — processamento distribuído, AWS, integração HTTP, embedding
semântico e execução ponta a ponta permanecem listados como próximos passos.

### Algum conteúdo industrial foi publicado neste relatório?

Não. As únicas métricas reais são contagens, estados, tamanhos e sinais de
qualidade já sanitizados e aprovados. Texto, snippets, caminhos locais, vetores,
mapeamentos reais e artefatos derivados não são reproduzidos.
