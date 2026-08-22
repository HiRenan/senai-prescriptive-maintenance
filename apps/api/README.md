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
