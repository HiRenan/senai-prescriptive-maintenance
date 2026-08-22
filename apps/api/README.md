# Backend Python

Este diretório contém o pacote instalável `prescriptive_maintenance` e a
aplicação FastAPI do backend. O alvo ASGI estável é
`prescriptive_maintenance.main:app`, e `create_app()` cria instâncias isoladas
para execução e testes.

## Instalação

A partir da raiz do repositório, sincronize todo o workspace pelo lock:

```powershell
uv sync --all-packages --frozen
```

## Execução local

Inicie a aplicação em uma interface exclusivamente local:

```powershell
uv run uvicorn prescriptive_maintenance.main:app --host 127.0.0.1 --port 8000
```

`GET /health/live` responde com status HTTP `200`, conteúdo
`application/json` e corpo `{"status":"ok"}`. A liveness verifica apenas que o
processo está vivo e não acessa banco, arquivos, rede, configurações externas
ou outros serviços.

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

Os comandos disponíveis são executados diretamente a partir da raiz:

```powershell
uv run poe check-api-import
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```
