# Backend Python

Este diretório contém o único projeto Python instalável desta etapa. O pacote
`prescriptive_maintenance` estabelece apenas o limite do monólito modular e
o namespace reservado aos módulos internos.

Endpoints, regras de negócio, processamento de dados, similaridade, RAG,
persistência e integrações serão implementados somente nas tarefas próprias.

A partir da raiz, use `uv sync --frozen` para sincronizar o workspace e
`uv run poe check-api-import` para confirmar a instalação do pacote.
