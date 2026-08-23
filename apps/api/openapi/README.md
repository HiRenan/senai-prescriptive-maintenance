# Contrato OpenAPI v1

`v1.json` é o snapshot público e determinístico da aplicação FastAPI. Ele é a
fonte para geração posterior do cliente web; tipos de request e response não
devem ser copiados manualmente para `apps/web`.

Gere e verifique o arquivo a partir da raiz:

```powershell
uv run --frozen python scripts/generate_openapi.py
uv run --frozen python scripts/generate_openapi.py --check
```

O snapshot contém apenas exemplos inteiramente sintéticos. Alterações no
contrato exigem revisão explícita e uma nova versão pública.
