# Scripts

Este diretório contém a automação cross-platform usada pela interface Poe. O
módulo `smoke.py` usa argumentos estruturados do Poe e subprocessos sem shell,
portanto mantém a mesma invocação no Windows e no Ubuntu.

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

O smoke nunca inicia, interrompe ou remove recursos Docker. Esse ciclo pertence
às tarefas `services-up` e `services-down`.
