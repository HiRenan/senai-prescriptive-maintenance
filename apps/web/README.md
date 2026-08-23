# Fronteira web

Este diretório reserva a fronteira de workspace e o futuro ponto de integração
web. Nesta etapa ele contém somente um processo HTTP operacional, sem
dependências, que responde `GET /health/live` com `{"status":"ok"}` para o
healthcheck do contêiner. Todas as demais rotas respondem `404`.

Não há interface, componentes, estilos, assets, framework ou comportamento
visual. O processo pode ser executado localmente com Node.js 22:

```powershell
corepack pnpm --filter @senai-prescriptive-maintenance/web start
```

O build e o smoke da imagem fazem parte do fluxo documentado na raiz e em
[`infra/README.md`](../../infra/README.md).

A implementação visual e o comportamento da aplicação pertencem a tarefas
posteriores.
