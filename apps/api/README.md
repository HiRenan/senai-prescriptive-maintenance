# Backend Python

Este diretório contém o pacote instalável `prescriptive_maintenance` e a
aplicação FastAPI do backend. O alvo ASGI estável é
`prescriptive_maintenance.main:app`, e `create_app()` cria instâncias isoladas
para execução e testes.

## Instalação

A partir da raiz do repositório, sincronize todo o workspace pelo lock:

```powershell
uv run --frozen poe setup
```

## Execução local

Inicie a aplicação em uma interface exclusivamente local:

```powershell
uv run --frozen uvicorn prescriptive_maintenance.main:app --host 127.0.0.1 --port 8000
```

`GET /health/live` responde com status HTTP `200`, conteúdo
`application/json` e corpo `{"status":"ok"}`. A liveness verifica apenas que o
processo está vivo e não acessa banco, arquivos, rede, configurações externas
ou outros serviços.

## Contrato HTTP v1

O contrato público congelado está em [`openapi/v1.json`](openapi/v1.json). Ele
define `POST /analysis` com exatamente 18 features métricas e `top_k` entre 1 e
10, além da consulta de análise e das operações mínimas de registro, consulta,
aprovação, rejeição e reprocessamento de documentos PDF. Os cinco resultados de
análise são `normal`, `documented_fault`, `undocumented_fault`,
`out_of_distribution` e `degraded`; os sete estados documentais também formam
uniões discriminadas fechadas para geração de tipos sem cópia manual no
frontend.

Os vizinhos opacos pertencem exclusivamente à porta do modelo, são preservados
em qualquer resultado quando disponíveis e expõem somente referência, posição,
código normalizado da falha e distância padronizada finita não negativa, sem
limite unitário. Evidências documentais carregam apenas seu suporte documental e
citações governadas; cada citação identifica documento, versão e chunk por
referências opacas e uma página positiva, sem título, caminho ou texto bruto.
`support_score` é uma heurística agregada não calibrada, não uma probabilidade ou
medida de confiança.

A aplicação HTTP usa fakes determinísticos e inteiramente sintéticos: ela não
executa modelo, recuperação, geração, persistência nem leitura de arquivos
reais. O módulo de persistência descrito abaixo é uma fronteira interna ainda
não ligada às rotas. O registro documental recebe somente metadados seguros de
um PDF e nunca implica aprovação. Para regenerar e conferir o snapshot:

```powershell
uv run --frozen python scripts/generate_openapi.py
uv run --frozen python scripts/generate_openapi.py --check
```

A verificação canônica inicia o Uvicorn em loopback e porta efêmera, faz a
requisição HTTP real e encerra o processo ao final, sem exigir banco ou `.env`:

```powershell
uv run --frozen poe smoke
```

## Domínio do ciclo documental

`prescriptive_maintenance.document_lifecycle` implementa a fronteira interna de
governança sem alterar os endpoints ou o snapshot OpenAPI v1. A identidade do
documento é lógica e estável; cada conteúdo recebe um número inteiro sequencial
e um SHA-256 distinto. O trio identidade, versão e hash preserva a identidade
idempotente do registro; um replay também precisa repetir o mesmo ator auditável
para devolver o snapshot existente sem criar versão, revisão ou evento. O
reprocessamento exige o mesmo hash da versão solicitada.

| Estado atual | Próximo estado | Condição |
| --- | --- | --- |
| `received` | `processing` | início explícito do primeiro processamento |
| `processing` | `pending_approval` | extração e indexação concluídas com sucesso |
| `processing` | `failed` | falha sanitizada de uma etapa ainda não concluída |
| `pending_approval` | `approved` | decisão com ator e motivo após os dois gates íntegros |
| `pending_approval` | `rejected` | decisão com ator e motivo obrigatório |
| `rejected` | `processing` | reprocessamento que reinicia os dois gates |
| `failed` | `processing` | retry que preserva etapas concluídas e reinicia somente a falha |
| `approved` | `superseded` | aprovação atômica de uma versão mais nova |
| `superseded` | — | estado terminal |

Uma versão nova em processamento, rejeitada ou com falha não desloca a versão
aprovada vigente. Somente a aprovação da substituta marca a anterior como
`superseded`; assim, nunca há promoção parcial. A elegibilidade exige ao mesmo
tempo estado `approved`, vigência e integridade completa de extração e
indexação. `rejected`, `failed` e `superseded` são sempre inelegíveis, mas seus
eventos e versões permanecem no histórico.

Uma etapa `succeeded` não pode regredir para `failed`. O reprocessamento de uma
falha preserva etapas concluídas e reinicia apenas a etapa que falhou; o
reprocessamento posterior a uma rejeição inicia uma nova passagem dos dois
gates. Ator, motivos, código de falha, identidade e todos os demais textos de
auditoria são validados antes de qualquer retorno idempotente. Controles,
caracteres Unicode de formato, surrogates, noncharacters e texto que não possa
ser codificado estritamente em UTF-8 são recusados com erros sanitizados.

O serviço recebe um relógio injetável, normaliza seus valores para UTC e rejeita
tempo ingênuo ou regressivo. O repositório em memória associa cada agregado a
uma revisão e grava somente por compare-and-swap; uma revisão perdida produz o
erro estável `document_concurrency_conflict`. Para comandos de transição, uma
revisão obsoleta só é idempotente quando a revisão seguinte registra exatamente
a mesma ação, versão, ator, motivo, hash, etapa e código aplicáveis. O CAS
reconstrói o comando a partir do novo sufixo de auditoria e exige igualdade do
agregado inteiro. Assim, não aceita estados fabricados nem uma versão aprovada
marcada `superseded` sem a
aprovação atômica da substituta. O prefixo histórico e as identidades das versões
são append-only. Esse repositório não abre conexão com PostgreSQL, e o domínio
não processa bytes, cria chunks nem expõe novas rotas.

## Execução em contêiner

O Dockerfile multi-stage instala somente as dependências de produção pelo
`uv.lock`, executa o Uvicorn como UID/GID `65532` e verifica
`GET /health/live`. O build parte da raiz porque o lock do workspace é único;
`Dockerfile.dockerignore` limita o contexto aos manifests, lock, fontes do
pacote e ao README exigido pelos metadados Python. Os targets `context-audit` e
`builder-audit`, executados por `uv run --frozen poe applications-audit`, provam
o conteúdo do contexto real e a ausência de resíduos no filesystem do builder.

O fluxo completo de build, start, smoke da liveness e do snapshot OpenAPI v1 e
stop está em [`infra/README.md`](../../infra/README.md).

## Configuração

`prescriptive_maintenance.settings.Settings` carrega explicitamente dois campos
obrigatórios: `environment`, restrito a `local`, `test` ou `production`, e
`database_url`, validado como URL PostgreSQL. As fontes usam o prefixo
`PRESCRIPTIVE_MAINTENANCE_`; variáveis do processo têm precedência sobre o
arquivo `.env`, lido opcionalmente em UTF-8.

Copie `.env.example` para `.env` conforme [`infra/README.md`](../../infra/README.md)
e carregue a configuração somente no ponto que precisar dela:

```python
from prescriptive_maintenance.settings import Settings

settings = Settings()
```

Não há valores padrão para os campos obrigatórios. Ausências e valores inválidos
produzem `pydantic.ValidationError`; a aplicação e a liveness não instanciam
`Settings` durante a importação ou a criação do app.

## Persistência mínima

`prescriptive_maintenance.persistence` define agregados imutáveis, repositórios
tipados e uma unidade de trabalho explícita. `AnalysisMetadata` registra somente
`analysis_id`, resultado fechado da API v1, `dataset_id`, `model_id`,
`prompt_id`, `configuration_id`, instante e referências ordenadas de evidência.
Documentos registram identidade estável; versões registram SHA-256 e instante;
chunks registram referência opaca e página positiva. A evidência liga a análise
ao trio documento–versão–chunk por chaves estrangeiras compostas.

`dataset_id` preserva sem prefixo ou transformação o SHA-256 minúsculo de 64
caracteres produzido pelo pipeline canônico. `evidence_id` preserva o formato do
contrato de geração e sua unicidade é local à análise, representada pela chave
composta `(analysis_id, evidence_id)`; o mesmo identificador pode ser reutilizado
por outra análise sem perder a origem de cada referência.

O esquema não possui features, linhas, vetores, embeddings, texto, conteúdo
bruto, caminho, nome de arquivo, diagnóstico ou prescrição. Assim, a recuperação
de uma análise devolve todos os IDs de versão usados sem persistir os materiais
originais ou dados privados. Um replay com o mesmo ID e os mesmos metadados é
idempotente; reutilizar o ID com metadados diferentes gera conflito tipado.
`DocumentRepository.add_version()` acrescenta de forma idempotente uma versão
imutável e seus chunks a um documento existente. Ele não reescreve nem remove
histórico: repetir a mesma versão é uma operação vazia, enquanto associar o mesmo
ID a outro hash, o mesmo hash a outro ID no documento ou reutilizar um ID de chunk
gera conflito.

`InMemoryUnitOfWork` é o adapter da suíte padrão e não abre rede. Cada unidade
publica mudanças somente após `commit()` explícito; exceção, saída sem commit ou
conflito transacional descarta todo o estado preparado. A entrada é reconstruída
recursivamente nos tipos mínimos do módulo, de modo que subclasses e campos
adicionais do chamador não sejam retidos. `PostgresUnitOfWork` oferece a mesma
fronteira sobre uma conexão psycopg ociosa com autocommit desabilitado; ela não
assume uma transação externa. Uma violação relacional retorna erro de domínio
sanitizado e marca a unidade como `rollback-only` até `rollback()` ou a saída.

A migração `initial_analysis_metadata`, versão 1, é aplicada por `upgrade()` e
revertida por `downgrade()`. As duas operações são transacionais, verificam o
checksum da versão aplicada, serializam concorrência por lock transacional e são
idempotentes no alvo atual. O bootstrap do Compose continua responsável somente
pelo pgvector; migrações da aplicação são sempre chamadas explicitamente:

```python
from psycopg import Connection
from psycopg.rows import dict_row

from prescriptive_maintenance.persistence import downgrade, upgrade
from prescriptive_maintenance.persistence.migrations import PostgresRow

connection = Connection[PostgresRow].connect(database_url, row_factory=dict_row)
upgrade(connection)
# downgrade(connection)  # retorna o schema ao estado vazio documentado
connection.close()
```

O teste PostgreSQL real cria e remove um schema aleatório isolado. Ele é
opcional e só executa quando
`PRESCRIPTIVE_MAINTENANCE_TEST_DATABASE_URL` aponta explicitamente para um banco
de teste; sem essa variável, apenas esses casos de integração são ignorados e a
suíte padrão permanece integralmente offline.

## Acesso à fonte tabular

`prescriptive_maintenance.data.consume_banner_source()` é a única porta de
entrada autorizada para `banner.csv`. A chamada exige `input_path`,
`manifest_path` e um consumidor binário explicitamente; não há descoberta,
caminho padrão ou busca recursiva.

O componente lê o nome aprovado, o tamanho e o SHA-256 do manifesto público,
abre a fonte com descritor estritamente read-only e só chama o consumidor após
validar o fingerprint inicial. Antes de devolver o resultado, calcula novamente
o fingerprint no mesmo descritor e rejeita qualquer alteração. Os erros tipados
diferenciam ausência, nome inesperado, tamanho, hash, mutação e permissão sem
expor caminho absoluto ou conteúdo. Parsing tabular e interface de linha de
comando não fazem parte deste contrato.

`consume_banner_source_audited()` preserva a mesma fronteira e devolve um recibo
imutável com o resultado do consumidor e os fingerprints de tamanho e SHA-256
realmente observados antes e depois da chamada. A API original permanece
retrocompatível e devolve somente o resultado. O runner de baseline usa os
recibos para impedir que uma alteração coordenada de manifesto e fonte entre
rodadas seja declarada sob uma identidade anterior.

## Inventário e extração dos documentos PDF

`extract_source_documents()` processa exclusivamente `Doc1.pdf` a `Doc6.pdf`.
Os caminhos do diretório de origem, do manifesto e da saída local são
obrigatórios; não há descoberta recursiva nem acesso aos outros materiais. Cada
fonte é aberta por descritor binário read-only, validada por tamanho e SHA-256
antes do parse e verificada novamente no mesmo descritor ao final, inclusive
quando a integridade inicial diverge ou o parse/processamento falha. Uma mudança
da fonte tem precedência sobre a falha intermediária.

A extração nativa é preferencial. Texto nativo não vazio que seja apenas curto
ou tenha baixa proporção alfanumérica é preservado como `native/suspect`, pois
pode representar títulos, tabelas curtas ou localizadores válidos. Páginas sem
texto utilizável podem ser encaminhadas a um `PageOcrAdapter`; sem adapter, elas
permanecem explicitamente em `ocr_required`. Falhas de OCR preservam qualquer
fallback nativo suspeito e registram um código sanitizado por página.
Resultados OCR usam dois gates versionados: confiança média mínima de `0.80` e
confiança pontual mínima de `0.60`. A página fica `suspect` se qualquer um deles
falhar.

`RapidOcrAdapter` é a implementação local baseada em RapidOCR e ONNX Runtime.
O motor e seus modelos são inicializados de forma lazy somente na primeira
página encaminhada ao OCR, com nível de log `error`; nenhuma chamada externa é
feita. A factory ou o engine podem ser injetados para testes sintéticos.

Se a saída estiver dentro de qualquer worktree Git, o próprio artefato precisa
ser confirmado como ignorado antes de cada escrita; destino não ignorado ou
erro nessa verificação falha fechado. Caminhos explícitos fora de repositórios
continuam permitidos para testes sintéticos isolados, sem aceitar symlinks,
junctions, outros reparse points ou segmentos de escape.

```python
from pathlib import Path

from prescriptive_maintenance.data import RapidOcrAdapter, extract_source_documents

result = extract_source_documents(
    source_directory=Path(r"C:\caminho\local\autorizado"),
    manifest_path=Path("data/source-manifest.json"),
    output_directory=Path("data/processed/documents"),
    ocr_adapter=RapidOcrAdapter(),
)
```

O inventário `inventory.v1.json` registra as seis identidades, a versão PDF,
contagens de método e os estados agregados, incluindo uma avaliação explícita
do `Doc1.pdf`. Cada `extraction.v1.json` registra texto, método, estado, sinais
de qualidade e falha sanitizada por página. Reexecuções idênticas preservam os
mesmos bytes e não substituem os artefatos; falhas continuam visíveis e são
tentadas novamente na próxima execução. Esses JSON contêm derivados reais e
devem permanecer somente sob um caminho local ignorado, como `data/processed/`.

## Segmentação e indexação das extrações

`chunk_extracted_document()` aceita somente o mapping estruturado de um
`extraction.v1.json`; a fronteira não recebe caminho de PDF, não descobre fontes
e não reabre materiais originais. O parser valida a versão do schema, a
identidade SHA-256, a ordem das páginas, os métodos e os estados emitidos pela
SEN-43 antes de segmentar. Página é uma fronteira obrigatória; headings Markdown
e linhas curtas em caixa alta formam seções conservadoras, sem remover o heading
do conteúdo.

A configuração padrão `document-chunking.v1` limita cada chunk a 1.600
caracteres, usa overlap de até 200 caracteres e identifica separadamente as
versões da limpeza e da detecção de seção. A limpeza normaliza fins de linha,
remove somente controles técnicos delimitados, apara whitespace ao fim das
linhas e limita sequências de linhas vazias; Unicode válido, espaços internos e
significado não são normalizados. O `chunk_id` é o SHA-256 canônico prefixado por
`chunk_` sobre hash do conteúdo, documento, versão da fonte, página, seção,
posição e configuração. `document_id` permanece estável pelo nome lógico e
`document_version` deriva do SHA-256 observado pela extração.

`character_start` e `character_end` usam índices Python baseados em zero e o
intervalo semiaberto `[start, end)` sobre o valor original de `pages[].text` no
`extraction.v1.json`, antes da limpeza. Um mapa interno transporta cada limite
pela remoção de NUL/DEL, normalização de CRLF, descarte de whitespace ao fim da
linha e colapso de linhas vazias. Por isso o trecho fonte pode ter comprimento e
bytes diferentes de `content`; reaplicar a limpeza ao trecho apontado produz o
conteúdo do chunk. Tamanho máximo e overlap continuam medidos no texto limpo.

`index_extracted_document()` usa uma porta de embeddings com resultado por
chunk e persiste tanto sucessos quanto falhas. `LocalHashEmbeddingProvider`
produz vetores `fake-local-hash` determinísticos e offline para CI; ele é
explicitamente não semântico e sua dimensão integra a versão da representação. O
`InMemoryChunkRepository` é ordenado, idempotente e rejeita colisões.
`PgVectorChunkRepository` apenas traduz registros para linhas tipadas e exige um
`PgVectorWriter` injetado: não abre conexão, não executa SQL e não exige serviço
na suíte padrão.

Os limites são medidos em caracteres, não em tokens, e sempre avançam por um
limite seguro de grafema. A implementação usa somente a biblioteca padrão para
manter juntos caractere-base, marcas Unicode, variation selectors, modificadores
de emoji e cadeias ligadas por ZWJ; um grafema isolado maior que o teto é mantido
inteiro e pode produzir o único chunk acima desse teto. A detecção de headings é
heurística; por isso a configuração favorece rastreabilidade e repetibilidade,
não uma granularidade semanticamente ótima. O overlap padrão de 12,5% aumenta
proporcionalmente o volume armazenado, e cada registro retém conteúdo e
proveniência completos. O provider hash comprova a integração offline, mas não
serve para avaliar qualidade de recuperação.

Quando uma página não produz chunk, seu código `page.*` original tem precedência
no resultado e acompanha número da página, método, estado, sinais e demais
proveniências sanitizadas; o código genérico de chunking é usado somente quando
a extração não forneceu uma falha. Colisões e falhas de provider também
permanecem explícitas. Chunks sem embedding continuam armazenados com vetor nulo
e estado `failed`; estados `completed`, `attention_required`, `partial` e
`failed` descrevem somente a indexação e nunca aprovam, rejeitam ou ocultam um
documento. Busca vetorial, recuperação governada, lifecycle, API e UI não fazem
parte desta fronteira.

## Recuperação documental aprovada

`prescriptive_maintenance.knowledge_retrieval` liga o lifecycle e os chunks por
IDs opacos sem reabrir PDFs nem descobrir arquivos. A configuração
`fault-knowledge-mapping.v1` contém `schema_version`, uma `mapping_version`
auditável, `mapping_sha256` e a lista ordenada de classes canônicas com seus
`document_ids`. O SHA-256 é calculado sobre a semântica normalizada; classes ou
referências duplicadas, campos extras, referência documental desconhecida e
alteração sem atualização do hash falham fechado.

O arquivo real não é fornecido pelo pacote porque a associação pode ser derivada
dos materiais locais. Ele deve ser informado por caminho explícito, por exemplo
`data/external/knowledge/fault-knowledge-mapping.v1.json`; `data/external/` já é
ignorado. `load_fault_knowledge_mapping()` apenas lê esse caminho, enquanto
`fault_knowledge_mapping_json_bytes()` oferece serialização determinística para
auditoria local. Nenhum mapeamento real é publicado pelo repositório.

`ApprovedKnowledgeRetrievalService` resolve somente a classe exata configurada e
valida, para cada documento, a versão vigente `approved`, os gates completos de
extração e indexação e a coerência do registro indexado. Versões `rejected`,
`failed`, `superseded`, candidatas ainda não aprovadas, versões antigas, páginas
com falha e embeddings ausentes são removidos antes de qualquer chamada ao
`KnowledgeChunkScorer`. O serviço não procura outra classe ou documento quando
o conjunto fica vazio. Estados explícitos de página ou embedding inelegível não
se confundem com corrupção: qualquer quebra estrutural, de identidade ou de
SHA-256 em uma versão declarada íntegra pelo lifecycle aborta toda a recuperação
antes do scorer.

Cada candidato validado é congelado em tipos básicos antes do ranking. O scorer
recebe uma cópia isolada, e qualquer mutação dessa cópia invalida o ranking; a
evidência final é materializada somente do snapshot anterior à fronteira.

Os vazios distinguem por enum classe sem mapeamento, ausência de cobertura
aprovada e ranking sem hits; indisponibilidade, integridade inválida e falha do
scorer também permanecem tipadas. Scores precisam ser finitos, o desempate usa
IDs e localização em ordem total e o top-k respeita o limite interno de 10. Cada
`RankedKnowledgeEvidence` contém exclusivamente `document_id`,
`document_version`, `chunk_id`, `page_number`, `section_id` e `score`, sem texto,
título, caminho, nome de fonte ou vetor.

Essa fronteira não implementa scorer semântico, consulta pgvector, provider,
endpoint, persistência nova, geração ou recuperação RAG integrada. Essas
integrações permanecem fora da SEN-56.

## Recuperação governada para RAG

`prescriptive_maintenance.governed_retrieval` define a porta interna consumível
pela futura orquestração RAG. `GovernedKnowledgeRetrievalService` recebe a
disposição tipada do modelo e encerra `normal` e `out_of_distribution` como
`no_evidence`, sem consultar a recuperação aprovada. Uma falha sem classe
documental encerra como `unmapped_fault`; uma classe canônica configurada é
delegada exclusivamente ao `ApprovedKnowledgeRetrievalService`, sem fallback
para outra classe ou busca genérica.

A SEN-56 mantém o resultado content-free existente e oferece, para essa porta
interna, `retrieve_snapshots()`. Os dois resultados derivam da mesma rotina de
filtro, scoring, ordenação e revalidação final. O snapshot interno acrescenta o
texto exato e seu `content_sha256` aos IDs de documento, versão, chunk, página e
seção. Não existe uma segunda leitura depois do ranking: se lifecycle, revisão,
identidade ou conteúdo mudarem durante o scorer, nada é materializado. O texto
não entra no contrato HTTP, em logs ou na persistência desta tarefa.

O limiar mínimo é obrigatório em `GovernedRetrievalPolicy`; versão, valor e
schema formam um SHA-256 semântico, com o `float.hex()` do limiar para identidade
inequívoca. Evidência com score exatamente igual ao limiar é aceita. O resultado
preserva a ordem total e o top-k da SEN-56, copia texto e metadados na fronteira
e aplica os mesmos limites já definidos para geração: quantidade máxima,
4.000 caracteres por item e 24.000 no total. Conteúdo individual maior não é
truncado e produz `retrieval_unavailable`; quando apenas o total seria excedido,
permanece o maior prefixo ranqueado que cabe integralmente no orçamento.

Ausência de cobertura aprovada, ranking vazio e itens abaixo do limiar tornam-se
`no_evidence`; classe não mapeada torna-se `unmapped_fault`; indisponibilidade,
corrupção, quebra do contrato do adapter e falha de ranking tornam-se
`retrieval_unavailable`. Assim, ausência legítima não é confundida com falha
técnica. Nenhum limiar operacional, mapeamento real ou conteúdo documental é
versionado. Esta camada não chama geração/LLM, não implementa guardrails, não
altera endpoints e não adiciona banco, consulta pgvector ou integração com o
fluxo HTTP.

## Contrato tabular de `banner`

`prescriptive_maintenance.data.BANNER_COLUMN_CATALOG` é a fonte versionada e
revisável dos metadados das 26 colunas. Cada entrada declara posição, nome,
tipo lógico, unidade de origem, unidade canônica, nulabilidade, domínio e
descrição operacional. A ordem do catálogo é exatamente a ordem pública de
`data/fixtures/banner.synthetic.csv`.

`BANNER_DATAFRAME_SCHEMA` materializa esse catálogo como um `DataFrameSchema`
Pandera com `strict=True`, `ordered=True` e `coerce=False`. A função
`validate_banner_dataframe()` devolve um relatório sanitizado: violações do
contrato são bloqueantes e têm código estável e severidade `error`, enquanto
`statistical_findings` permanece separado e vazio nesta etapa. O relatório não
inclui índices nem valores de células. O validador offline aceita somente os
códigos de `ContractViolationCode` com essa severidade canônica e exige
`statistical_finding_count = 0` com a sequência vazia.

O contrato v2 aceita `created_at` somente no perfil ISO 8601 zonado suportado,
com `T` maiúsculo ou um único espaço ASCII entre a data e a hora completas e
zona explícita em `Z` ou deslocamento numérico no formato `±HH:MM`. O deslocamento
`-00:00` é recusado porque representa offset local desconhecido; `+00:00`
permanece válido. Textos sem zona, offsets fora do intervalo, `t`/`z` minúsculos,
espaços repetidos ou nas extremidades, segundos intercalares, datas civis
impossíveis e demais variações são bloqueados.
O parser preserva toda a fração decimal e normaliza o instante para UTC somente na
representação interna usada por igualdade, ordenação, período e cadência; o
texto do `DataFrame` não é alterado. A versão foi incrementada após uma auditoria
demonstrar que a regra anterior rejeitava uma forma zonada válida, sem registrar
no repositório nenhum valor observado na fonte.

O contrato preserva cada coluna na unidade em que a fonte a publica;
por isso, unidade de origem e canônica são iguais. As colunas paralelas em
`in/s` e `mm/s`, assim como `°F` e `°C`, continuam independentes e nenhuma
conversão é aplicada. O contrato não faz conferência cruzada; o profiler
agregado descrito abaixo apenas mede a coerência observada. Alterar nome,
posição, tipo, unidade, nulabilidade ou domínio exige incrementar
`BANNER_CONTRACT_VERSION`, editar o catálogo e acrescentar ou ajustar o teste
correspondente no mesmo pull request.

`fault` é deliberadamente um rótulo bruto não vazio. O contrato não enumera o
vocabulário real nem normaliza categorias; uma allowlist só é aplicada quando o
chamador a fornece explicitamente. Para a fixture pública, os únicos rótulos
autorizados nesse modo são `synthetic_healthy`, `synthetic_imbalance` e
`synthetic_bearing_warning`. Essa lista é exclusivamente sintética e não
representa, aproxima ou substitui as categorias da fonte original.

### Factory sintética de testes

`apps/api/tests/synthetic_banner_factory.py` cria tabelas pequenas diretamente
em memória, sem ler a fixture CSV estática e sem usar aleatoriedade. O relógio,
os identificadores e os valores são fixos e obviamente fictícios. Os writers
CSV e Parquet exigem um diretório existente informado explicitamente pelo teste;
nenhum caminho de saída padrão existe.

| Cenário | Regra exercitada |
| --- | --- |
| `valid` | Produz as 26 colunas na ordem, tipos e domínios aceitos pelo contrato. |
| `missing_column` | Remove somente uma coluna obrigatória. |
| `extra_column` | Acrescenta somente uma coluna não declarada. |
| `renamed_column` | Renomeia somente uma coluna declarada. |
| `reordered_columns` | Troca apenas a ordem das duas primeiras colunas. |
| `invalid_dtype` | Preserva os valores de rotação, mas troca somente seu tipo lógico. |
| `null_value` | Introduz somente um rótulo nulo. |
| `nan_value` | Introduz somente um `NaN` numérico. |
| `infinite_value` | Introduz somente um valor numérico infinito. |
| `invalid_timestamp` | Altera somente `created_at` para um texto fora do formato UTC declarado. |
| `empty_fault` | Altera somente `fault` para um rótulo vazio. |
| `physical_violation` | Coloca somente uma velocidade abaixo do limite físico inequívoco. |
| `identical_duplicate` | Repete integralmente uma linha. |
| `conflicting_duplicate` | Parte da duplicata idêntica e diverge somente na rotação. |
| `coherent_unit_pairs` | Mantém relações exatas entre `in/s` e `mm/s`, e entre °C e °F. |
| `incoherent_unit_pairs` | Parte dos pares coerentes e altera somente uma contraparte em `mm/s`. |
| `irregular_cadence` | Altera somente um instante para produzir intervalos desiguais. |
| `long_gap` | Altera somente um instante para produzir uma lacuna de oito horas. |
| `label_transition` | Troca somente o rótulo da linha final. |
| `boundary_24_hours` | Posiciona instantes exatamente dos dois lados de 24 horas. |
| `label_unicode_nfkc` | Oferece rótulos distintos que se equivalem sob Unicode NFKC. |
| `label_case_variants` | Oferece o mesmo texto sintético em caixas distintas. |
| `label_space_variants` | Oferece espaços externos e internos distintos. |
| `label_separator_variants` | Oferece hífen, sublinhado e barra como separadores. |
| `label_collision` | Oferece dois valores brutos distintos com colisão potencial. |
| `unknown_category` | Oferece uma categoria fora da allowlist sintética explícita. |

`contract.check_failed` não possui cenário: ele é o fallback defensivo interno
para checks Pandera não declarados e não é reproduzível por uma entrada pública
do contrato.

A factory apenas constrói entradas intencionais. Ela não faz parsing,
normalização, limpeza, taxonomia, perfil estatístico ou divisão de dados; essas
responsabilidades permanecem fora deste escopo.

## Profiler determinístico agregado

`profile_banner_dataframe()` recebe somente um `DataFrame` já carregado. A
função não abre arquivos, não conhece caminhos locais, não chama a porta de
acesso à fonte e não altera a tabela. A chave de análise deve ser declarada pelo
chamador porque o contrato não presume unicidade de `id`; no exemplo abaixo,
`id` com `created_at` é uma escolha exclusivamente sintética:

```python
from prescriptive_maintenance.data import (
    banner_profile_json_bytes,
    profile_banner_dataframe,
    render_banner_profile_markdown,
)

profile = profile_banner_dataframe(
    dataframe,
    key_columns=("id", "created_at"),
    allowed_fault_categories=frozenset({"synthetic_nominal", "synthetic_warning"}),
)
json_bytes = banner_profile_json_bytes(profile)
markdown = render_banner_profile_markdown(profile)
```

O argumento `allowed_fault_categories` é opcional. Quando ausente, cada categoria
observada é publicada sem nome (`label = null`) e recebe apenas um
`unapproved_ordinal` positivo na ordem das contagens. Quando presente, somente esse
vocabulário explicitamente confiável pode ser nomeado; categorias não aprovadas
continuam anônimas, e categorias permitidas com contagem zero aparecem na
distribuição. Empates produzem registros públicos indistinguíveis: o texto bruto
jamais escolhe nomes, desempata ou reserva ordinais, portanto histogramas iguais
produzem os mesmos bytes quando os demais indicadores e a configuração são
mantidos. Cardinalidade, contagens e balanceamento permanecem verificáveis sem
expor o valor bruto.

O vocabulário confiável aceita somente textos não vazios codificáveis em UTF-8 e
sem caracteres Unicode de controle, formato ou surrogate. Configuração inválida,
inclusive item não textual, gera `BannerProfileConfigurationError`; uma célula não
hashable que impeça a agregação de duplicatas gera `BannerProfileInputError`, sempre
com mensagem sanitizada. `key_columns` aceita somente uma sequência textual
ordenada; string única, `set` e `frozenset` são rejeitados por serem ambíguos.

### Inventário de indicadores

| Indicador | Finalidade e definição |
| --- | --- |
| Volume e estrutura | Registra linhas, colunas observadas e esperadas, ausências, excedentes e aderência à ordem das 26 colunas sem publicar nomes inesperados. |
| Período e ordenação | Usa somente timestamps válidos no formato UTC do contrato; publica limites agregados do período e classifica a sequência de entrada como constante, não decrescente, não crescente ou desordenada. |
| Cadência e lacunas | Ordena instantes UTC distintos, calcula os intervalos positivos e escolhe a moda como cadência nominal; empates escolhem o menor intervalo. Lacuna é cada intervalo estritamente maior que a cadência, e sua duração é somente o excesso agregado. |
| Qualidade por coluna | Para cada uma das 26 posições, inclusive ausentes ou sem achados, informa presença, aderência de tipo e contagens/percentuais separados de `null`, `NaN`, infinito, domínio e categoria desconhecida. |
| Duplicatas completas | Conta grupos idênticos e linhas excedentes além da primeira, sem devolver índices, valores ou registros. |
| Conflitos por chave | Para a chave explicitamente declarada, conta grupos repetidos e aqueles cujos demais campos divergem; linhas com chave incompleta são apenas contabilizadas e excluídas dos grupos. |
| Estatística numérica | Para medições `float64`, usa somente valores finitos e publica contagem, mínimo, máximo, média, desvio, três quantis, IQR, cercas e quantidade fora das cercas. `id` não recebe estatísticas descritivas para não expor distribuição de identificadores. |
| Distribuição de rótulos | Nomeia somente categorias da allowlist confiável, representa as demais por `label = null` e ordinal agregado, inclui categorias permitidas ausentes e calcula maioria, minoria, razão maioria/minoria e entropia normalizada sem publicar valores observados não aprovados. |
| Pares redundantes | Compara agregadamente quatro pares `in/s`–`mm/s` pela relação `mm/s = in/s × 25,4` e o par de temperatura por `°F = °C × 1,8 + 32`, publicando disponibilidade, consistência e erro absoluto máximo. |

### Definições reproduzíveis

- os quantis são `0,25`, `0,50` e `0,75` pelo método linear tipo 7;
- o desvio é populacional (`ddof = 0`); as cercas são
  `Q1 - 1,5 × IQR` e `Q3 + 1,5 × IQR`, e somente valores estritamente fora delas
  contam como outliers;
- estatísticas incluem valores finitos mesmo quando violam domínio, mantendo
  observação separada de decisão; `null`, `NaN` e infinito não entram nelas;
- cálculos de estatísticas, quantis, IQR, desvio e pares de unidade convertem cada
  `float64` finito para sua representação decimal exata e usam precisão interna
  suficiente para toda a faixa do tipo, evitando overflow e cancelamento em
  somas de sinais opostos. Cada operação usa um contexto decimal completo com
  precisão calculada, arredondamento, expoentes, flags e traps definidos
  internamente, sem herdar o contexto decimal do processo;
- `None`, `pd.NA` e `pd.NaT` são `null`; `NaN` IEEE é contado separadamente;
  infinito e violação de domínio também são dimensões separadas;
- a tolerância dos pares é o maior valor entre `1e-6` absoluto e `1e-6`
  relativo ao maior módulo comparado; a comparação ocorre no domínio decimal
  mesmo quando o valor convertido não caberia em `float64`;
- todo instante aceito pelo perfil ISO 8601 zonado é normalizado para UTC com a
  fração decimal completa aceita pelo contrato. Formas equivalentes com offsets diferentes
  representam o mesmo instante; ordenação, distinção, cadência e lacunas usam
  essa precisão exata;
  limites de período usam ISO 8601 canônico com ao menos seis casas e preservam
  todas as casas significativas adicionais;
- números derivados e percentuais são arredondados a seis casas pelo modo
  decimal half-even. Se um derivado finito exceder `float64` ou um valor não zero
  ficar abaixo dessa resolução pública, o campo recebe `null`, nunca infinito,
  `NaN` ou zero enganoso; contagens e classificações continuam calculadas com a
  precisão interna;
- percentuais por coluna usam células observadas, percentuais de rótulo usam
  rótulos válidos e percentuais de pares usam comparações disponíveis. Sem
  denominador válido, inclusive para entropia, a métrica é `null`;
- colunas seguem o catálogo; categorias confiáveis seguem pontos de código
  Unicode e categorias não aprovadas seguem contagem decrescente e ordinal
  sequencial, sem nome nem qualquer ordenação derivada do valor bruto. Chaves JSON
  seguem a declaração do esquema público.

`PUBLIC_BANNER_PROFILE_SCHEMA` classifica recursivamente cada campo como
agregado, configuração ou esquema. A mesma proteção é executada antes do JSON e
do Markdown e recusa qualquer campo classificado como linha, caminho local,
amostra, identificador individual, timestamp individual ou combinação
reidentificável. O JSON usa UTF-8, indentação fixa, LF final e ordem declarada,
de modo que a mesma entrada e configuração produzam exatamente os mesmos bytes.
Além da classificação estrutural, os publicadores validam os valores, recusam
texto Unicode inseguro em perfis construídos manualmente e exigem `label = null`
com ordinal sequencial para toda categoria não aprovada. O Markdown usa uma
descrição fixa derivada somente do ordinal para categorias anônimas e codifica
sintaxe ativa, inclusive links e HTML, mesmo para rótulos explicitamente
confiáveis.

O profiler mede; ele não limpa, remove, imputa, converte unidades, normaliza
rótulos nem define limiares finais de qualidade.

## Baseline auditada de `banner`

`run_banner_baseline()` faz um único parse por descritor em cada uma de duas
rodadas independentes. A política CSV é integralmente registrada: UTF-8 estrito,
linhas malformadas bloqueantes, nenhuma inferência de datas ou chunks, tokens NA
padrão desativados e somente a célula exatamente vazia reconhecida como ausente.
`id` é lido como `Int64` anulável para que a ausência alcance contrato e profiler,
e só é convertido para o `int64` NumPy contratual quando está completo; entradas
completas mantêm os tipos finais `int64`, `float64` e `string`.

Cada rodada liga contrato, perfil e reconciliações ao recibo pre/post efetivo da
porta segura. Os dois recibos devem coincidir entre si e com a identidade
inicial do manifesto. Somente depois de todos os gates, da sanitização e da
igualdade byte a byte, o runner grava atomicamente `baseline.v1.json` e
`summary.md` em `data/baselines/banner/<sha256-da-fonte>/`. O Markdown é sempre
regenerado do JSON sanitizado; o validador offline também exige definições do
profiler canônicas e coerência entre o resultado contratual e sua contagem de
violações. O diretório canônico deve conter exclusivamente esses dois arquivos,
ambos regulares e sem links simbólicos; ausência, entrada extra ou tipo diferente
é bloqueante inclusive no caminho idempotente de escrita.

## Inventário categórico de rótulos de falha

`normalize_fault_label()` aplica somente transformações textuais, na ordem
versionada: Unicode NFKC, trim, colapso da allowlist explícita de whitespace,
`casefold`, normalização dos separadores `-`, `/`, `\` e `_` para espaço e slug
estável. A versão Unicode é fixada em `15.1.0`. O slug preserva letras e dígitos
ASCII, representa espaço por hífen e codifica todos os demais bytes UTF-8 como
`%XX`; nenhum caractere é transliterado ou removido silenciosamente.

Nulo, tipo não textual, vazio, controles, caracteres de formato — inclusive
bidi e zero-width —, surrogate, noncharacter e texto UTF-8 inválido produzem
erros tipados com mensagens sanitizadas. A forma normalizada e o slug são
campos distintos. A API de lookup exige texto válido e compara o `raw_label`
exato; qualquer valor não inventariado gera `UnknownFaultLabelError`, mesmo
quando sua normalização coincidiria com uma categoria conhecida.

`run_fault_label_inventory()` usa a porta auditada em duas rodadas independentes.
Cada consumidor recebe somente o `BinaryIO` não gravável e chama o parser CSV
uma vez, com projeção explícita exclusiva de `fault`; o DataFrame de uma coluna
é descartado dentro da rodada. O runner reconcilia a soma das frequências e a
cardinalidade com a baseline pública, compara os bytes categóricos das rodadas e
só então grava atomicamente
`data/inventories/banner/<source-sha>/fault-labels.v1.json`.

Colisões de raws no mesmo `normalized_label` e de formas normalizadas distintas
no mesmo slug são detectadas antes da escrita. Uma colisão normalizada exige a
aprovação explícita do fingerprint exato do grupo e fica marcada como
`approved_textual_equivalence`; colisão de slug permanece bloqueante. A
aprovação é somente da equivalência textual produzida pelo pipeline e não cria
taxonomia, classe operacional ou equivalência semântica.

Quando há colisão normalizada ainda não aprovada, o resultado bloqueado oferece
`FaultLabelCollisionGroup` imutável com `group_id`, versão, destino normalizado
e membros categóricos, sem frequências ou dados por linha. O `group_id` vincula
esses quatro elementos; assim, o P.O. pode revisar o grupo exato antes de
fornecer uma allowlist do tipo `Set`. Sequências ordenadas ou outros tipos são
recusados por um gate tipado.

O JSON contém exatamente os 151 mapeamentos categóricos autorizados com
frequência global, versões de schema/normalização/Unicode, marca
`approved_categorical_only`, `source_sha256` da baseline, recibos pre/post,
reconciliações, decisões de colisão aprovadas e `inventory_id` por SHA-256 do
corpo canônico. Cada decisão persistida repete o grupo categórico exato e sua
resolução; o validador a compara com os grupos recalculados, além de conferir o
ID. O arquivo não contém linha, ocorrência, identificador, timestamp, sensor,
medição, caminho local, host ou usuário. `load_fault_label_inventory()` e
`validate_fault_label_inventory()` verificam chaves duplicadas, campos extras,
tipos exatos, ordem, serialização canônica, números não finitos, ID, conteúdo e
localização somente com o inventário, manifesto e baseline públicos; nenhuma
delas abre `banner.csv`.

## Política de qualidade e outliers

`data/policies/banner_quality_policy.v1.json` é a única fonte canônica das regras
de qualidade do banner. `load_banner_quality_policy()` carrega e valida o esquema
estrito contra o contrato v2 e as definições do profiler v1;
`identify_banner_quality_policy()` calcula o SHA-256 da serialização JSON canônica
de toda a semântica, sem incluir o próprio identificador. Reordenações
equivalentes não alteram o ID, mas qualquer mudança semântica exige outro ID.

As enums públicas `ReasonCode` e `Action`, a ordem total dos motivos e o índice
imutável `rule_id` → motivo + ação permitem consulta sem duplicar regras em
código. A ação efetiva é única na ordem `reject` > `correct_deterministically` >
`map` > `flag` > `keep`, enquanto `resolve_quality_rules()` preserva todos os
motivos e `QualityMatch` concorrentes. Cada match contém somente contexto
allowlisted de coluna, relação e coluna confiável, sem valores por registro; uma
correção unitária exige relação conhecida, alvo e contraparte confiável
compatíveis. Depois da deduplicação exata, cada relação admite no máximo uma
prova determinística e não pode misturá-la com um match ambíguo. A API revalida
a política e retorna `effective_action`, sem antecipar uma disposição de ledger
ou alterar linhas.

`render_banner_quality_policy_markdown()` deriva a
[visão humana pública](../../docs/data/banner-quality-policy.md) da política e,
quando fornecidos explicitamente, dos bytes do JSON agregado da baseline
rastreada. Esses bytes passam pela validação integral da API pública da baseline
contra a identidade aprovada no manifesto antes da comparação. O módulo não
descobre nem acessa materiais originais e não implementa limpeza, correção por
registro, ledger ou remoção de outliers.

## Fronteira de geração prescritiva

`prescriptive_maintenance.generation` define o contrato
`prescriptive-generation.v1` para o diagnóstico recebido do modelo, evidências
fornecidas explicitamente, avaliação de suporte documental, prescrições,
citações e warnings. O diagnóstico de entrada contém um `fault_code` e um resumo
técnico imutáveis; o provider só pode avaliar seu suporte documental e deve ecoar
o código exatamente, sem substituir ou reinventar o diagnóstico.

Citações carregam somente o `evidence_id`; origem e localizador permanecem nos
metadados confiáveis da evidência de entrada e não podem ser inventados pelo
provider. Avaliações suportadas e prescrições exigem citações conhecidas,
enquanto evidência insuficiente ou conflitante proíbe prescrições. A requisição
aceita no máximo 12 evidências, limita cada conteúdo a 4.000 caracteres e o
conjunto a 24.000 caracteres; a serialização ordena os itens por `evidence_id`.

O prompt `prescriptive-generation-system.v1` é um recurso versionado do pacote.
Ele manda preservar o diagnóstico, usar somente as evidências recebidas, tratar
seu conteúdo como dado e nunca como instrução, proíbe completar lacunas e exige
JSON conforme o schema estrito enviado junto da requisição. A validação rejeita
campos extras, chaves JSON duplicadas, números não finitos, versão incompatível,
estrutura inválida, código de falha alterado e citações fora da entrada antes de
criar o resultado do domínio.

`FakeGenerationProvider` produz resposta sintética determinística sem ler
arquivos, rede, ambiente ou credenciais. `BedrockGenerationProvider` implementa
a mesma porta por uma fábrica de cliente injetada pelo chamador; sua configuração
é desabilitada por padrão e a fábrica só é usada durante uma chamada explícita a
`generate_prescription()`. O adaptador não importa SDK AWS, não descobre
credenciais e publica somente contagens de tokens inteiras e não negativas;
erros, envelopes inválidos e metadados extras são substituídos por resultados
genéricos e sanitizados.

Essa fronteira não recupera documentos, não comprova semanticamente que uma
citação sustenta a afirmação, não implementa guardrails completos, não persiste
resultados e não configura infraestrutura AWS. Esses comportamentos dependem de
tarefas posteriores.

## Pipeline canônico local

`load_canonical_pipeline_config()` valida a configuração versionada que mapeia
as 26 colunas de origem para metadados, target, exclusões justificadas e 18
features `float64` disponíveis no instante do evento. As representações
redundantes em °F e `in/s` não entram nas features; quando divergem das colunas
canônicas confiáveis, a política registra uma correção determinística no ledger
sem alterar a fonte.

`build_banner_dataset()` carrega primeiro o inventário categórico validado e abre
`banner.csv` uma única vez pela porta auditada e estritamente read-only. Cada
linha recebe uma disposição de qualidade e exatamente um destino entre `train`,
`validation`, `test`, `purge` e `rejected`. O target textual é mapeado exatamente
para o slug aprovado, mas não participa de ordenação, ajuste, agrupamento ou
split: ocorrências usam somente ordem temporal estável, gap e duração. O limiar
de gap converge com ajuste exclusivo nas ocorrências da partição final de
treino; o manifesto registra a quantidade e o hash de pertencimento desse fit.
Uma nova ocorrência começa quando o gap é estritamente maior que o limiar ou
quando sua duração alcança exatamente 86.400 segundos, portanto toda ocorrência
tem duração estritamente menor que 24 horas. A divisão 70/15/15 preserva
ocorrências inteiras, purga as fronteiras pelo mesmo gap e ajusta cercas IQR
somente em treino. Apenas depois da partição o target entra nos três artefatos de
modelagem, sob o nome `y`.

O destino contém exatamente `canonical.parquet`, `dispositions.parquet`,
`train.parquet`, `validation.parquet`, `test.parquet` e `manifest.json`. O
manifesto vincula fonte, configuração, schemas, política, inventário e
`uv.lock`; também reconcilia linhas, ocorrências, disposições, destinos,
partições, hashes físicos/lógicos e gates de leakage. `check_banner_dataset()`
carrega as identidades públicas aprovadas e refaz essas provas offline com
schema estrito, referências cruzadas e cobertura exata de destinos, sem
reescrever arquivos.

Os comandos exigem todos os caminhos explicitamente. Antes de qualquer escrita,
o próprio build exige que um destino dentro de uma worktree Git esteja realmente
ignorado; erros de consulta, escapes e componentes que sejam links ou junctions
bloqueiam a operação. Temporários externos a worktrees Git continuam permitidos.
Nunca copie a fonte para a worktree:

```powershell
git check-ignore data/processed/banner/run-local
uv run --frozen poe data-build `
  --input C:/caminho/autorizado/banner.csv `
  --manifest data/source-manifest.json `
  --inventory data/inventories/banner/<source-sha>/fault-labels.v1.json `
  --baseline-json data/baselines/banner/<source-sha>/baseline.v1.json `
  --baseline-markdown data/baselines/banner/<source-sha>/summary.md `
  --lock uv.lock `
  --output data/processed/banner/run-local
uv run --frozen poe data-check `
  --manifest data/source-manifest.json `
  --inventory data/inventories/banner/<source-sha>/fault-labels.v1.json `
  --baseline-json data/baselines/banner/<source-sha>/baseline.v1.json `
  --baseline-markdown data/baselines/banner/<source-sha>/summary.md `
  --lock uv.lock `
  --output data/processed/banner/run-local
```

As saídas do CLI são somente agregados sanitizados. Testes e CI exercitam o
pipeline exclusivamente com dados sintéticos; derivados reais permanecem
locais e ignorados.

## Baseline k-NN local

`prescriptive_maintenance.modeling` implementa a baseline determinística da
SEN-42. `fit_knn_model()` aceita somente uma partição com as 18 features na ordem
canônica e `y`; qualquer coluna, ordem, tipo ou número não finito divergente é
recusado. O contrato canônico não admite ausências, portanto a baseline não
imputa. `StandardScaler` é ajustado exclusivamente no DataFrame de treino
recebido, e o mesmo estado serializado transforma toda inferência.

A busca exata em memória usa apenas distância euclidiana no espaço padronizado.
`top_k` respeita o limite público de 1 a 10 e a quantidade de linhas disponível;
distâncias empatadas usam a referência opaca e votos empatados usam soma de
distâncias e depois o target canônico. O suporte é somente a proporção de votos
da classe vencedora no top-k, explicitamente não probabilística.

O núcleo preserva `target_slug` internamente. `KnnModelPortAdapter` traduz cada
classe por uma tabela bijetiva de `fault_code` seguro, construída no fit,
validada contra colisões e serializada. `normal_target_labels` é configuração
explícita e deve ser subconjunto das classes de treino. O adapter produz apenas
`NORMAL` ou `FAULT`; não implementa abstenção ou `OUT_OF_DISTRIBUTION`.

`save_knn_model()` grava somente `manifest.json` e três arrays `.npy`, sempre em
destino ignorado quando está dentro de uma worktree. O manifesto fixa schema,
compatibilidade, configuração, labels, estado completo do `StandardScaler`,
hashes e `model_id`. `load_knn_model()` usa `allow_pickle=False`, rejeita arquivo
ausente ou extra, bytes alterados, campos duplicados, versões incompatíveis,
arrays inválidos e identidade divergente. Os arrays reais contêm derivados por
registro e nunca devem ser versionados ou publicados.

A aplicação HTTP continua injetando fakes sintéticos; a baseline e seu adapter
não são conectados às rotas nesta tarefa. As decisões e métricas temporais
sanitizadas estão em
[`docs/validation/knn-baseline.md`](../../docs/validation/knn-baseline.md).

## Verificações

As verificações canônicas são executadas a partir da raiz:

```powershell
uv run --frozen poe format-check
uv run --frozen poe lint
uv run --frozen poe typecheck
uv run --frozen poe test
uv run --frozen poe check
```

`format` aplica correções seguras e formatação Ruff; é a única tarefa Poe de
qualidade que escreve. `check` executa as quatro verificações somente leitura em
sequência fail-fast.
