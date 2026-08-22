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
