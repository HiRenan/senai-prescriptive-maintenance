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
referências opacas, mantém um localizador legível e nunca inclui texto bruto.
`support_score` é uma heurística agregada não calibrada, não uma probabilidade ou
medida de confiança.

A aplicação usa fakes determinísticos e inteiramente sintéticos: ela não executa
modelo, recuperação, geração, persistência nem leitura de arquivos reais. O
registro documental recebe somente metadados seguros de um PDF e nunca implica
aprovação. Para regenerar e conferir o snapshot:

```powershell
uv run --frozen python scripts/generate_openapi.py
uv run --frozen python scripts/generate_openapi.py --check
```

A verificação canônica inicia o Uvicorn em loopback e porta efêmera, faz a
requisição HTTP real e encerra o processo ao final, sem exigir banco ou `.env`:

```powershell
uv run --frozen poe smoke
```

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
