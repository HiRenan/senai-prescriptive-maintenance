# Scripts

Este diretório contém a automação cross-platform usada pela interface Poe. Os
módulos usam argumentos estruturados e subprocessos sem shell, portanto mantêm
a mesma invocação no Windows e no Ubuntu.

`container_audit.py` exporta pelo BuildKit os targets `context-audit` da API e
da web, compara cada arquivo com sua allowlist esperada e executa os targets
`builder-audit` sem cache. Assim, a auditoria cobre o contexto real enviado ao
builder e o filesystem intermediário, não somente a imagem final:

```powershell
uv run --frozen poe applications-audit
```

Da raiz, execute o smoke padrão sem banco e sem criar `.env`:

```powershell
uv run --frozen poe smoke
```

Ele verifica Python 3.13, Node.js 22, pnpm 10.15.1, importação do backend,
carregamento explícito de `.env.example`, `docker compose config` e a resposta
exata de `GET /health/live` e `GET /health/ready` no perfil `offline`, por HTTP
em loopback e porta efêmera. As duas respostas também precisam devolver um
correlation ID seguro. O Uvicorn do smoke nasce em um diretório temporário vazio
e recebe somente a configuração explícita desse perfil; um `.env` local e
variáveis AWS herdadas não participam do processo.

Para também verificar um PostgreSQL já iniciado, o healthcheck, pgvector 0.8.6
e uma operação vetorial mínima, use:

```powershell
uv run --frozen poe smoke --with-services
```

Quando a topologia completa já foi iniciada por `applications-up`, acrescente a
validação dos estados healthy, das duas liveness, da readiness da API ligada ao
PostgreSQL e da igualdade do OpenAPI servido com o snapshot v1 rastreado:

```powershell
uv run --frozen poe smoke --with-services --with-applications
```

O smoke nunca inicia, interrompe ou remove recursos Docker. Esse ciclo pertence
às tarefas `services-up` e `services-down`.

`generate_openapi.py` renderiza de forma determinística o contrato HTTP v1 em
`apps/api/openapi/v1.json`. A opção `--check` compara os bytes sem reescrever o
snapshot:

```powershell
uv run --frozen python scripts/generate_openapi.py
uv run --frozen python scripts/generate_openapi.py --check
```

`analysis_benchmark.py` executa uma carga curta contra o `POST /analysis` real,
com composição integrada e portas sintéticas temporizadas. O aquecimento não
entra nas distribuições, a falha controlada do provider permanece `degraded` e
não vira latência válida de geração. A visão principal é por cenário e o
`synthetic_scenario_mix` é apenas secundário. Eventos JSON sanitizados são
emitidos somente depois dos timers e do pico de memória para as camadas do
benchmark; o logging operacional real da aplicação permanece dentro de
`http_total` e da janela exclusiva de memória. A passagem de memória usa serviço
e aplicação novos e não alimenta percentis, erros ou uso. Os eventos usam stderr,
enquanto o JSON estável usa stdout:

```powershell
uv run --frozen python -m scripts.analysis_benchmark
uv run --frozen python -m scripts.analysis_benchmark --format markdown
```

O benchmark não acessa materiais originais, rede, AWS ou provider pago. Tokens
do fake são `simulated`, custo fica `not_available` e o maior pico de
`tracemalloc` por requisição cobre somente alocações Python rastreadas, não RSS
ou memória nativa. O protocolo completo está em
[`docs/validation/analysis-benchmark.md`](../docs/validation/analysis-benchmark.md).
