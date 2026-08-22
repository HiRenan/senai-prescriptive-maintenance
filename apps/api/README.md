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
inclui índices nem valores de células.

Esta primeira versão preserva cada coluna na unidade em que a fonte a publica;
por isso, unidade de origem e canônica são iguais. As colunas paralelas em
`in/s` e `mm/s`, assim como `°F` e `°C`, continuam independentes e nenhuma
conversão ou conferência cruzada é feita. Alterar nome, posição, tipo, unidade,
nulabilidade ou domínio exige incrementar `BANNER_CONTRACT_VERSION`, editar o
catálogo e acrescentar ou ajustar o teste correspondente no mesmo pull request.

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
