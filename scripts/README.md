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
exata de `GET /health/live` por HTTP em loopback e porta efêmera.

Para também verificar um PostgreSQL já iniciado, o healthcheck, pgvector 0.8.6
e uma operação vetorial mínima, use:

```powershell
uv run --frozen poe smoke --with-services
```

Quando a topologia completa já foi iniciada por `applications-up`, acrescente a
validação dos estados healthy, das duas liveness e da igualdade do OpenAPI
servido pela API com o snapshot v1 rastreado:

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
